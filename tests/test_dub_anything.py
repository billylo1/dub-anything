"""Direct job creation, checkpoint pauses, and resumed watcher failures."""

import contextlib
import io
import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import dub_anything as runner


class TextEvidenceTests(unittest.TestCase):
    def test_cli_preserves_text_modes_and_routes_caption_reading(self):
        for flag, mode, signs, extra in (
            (None, False, False, None),
            ("--captions", True, False, []),
            ("--subtitles", "open", False, ["--open"]),
            ("--auto-text", "auto", True, ["--auto"]),
        ):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                source = root / "sample.mp4"
                source.touch()
                work = root / "work"
                folder = work / "sample-01"
                folder.mkdir(parents=True)
                (folder / "segments.json").write_text('{"job_id": "sample"}', encoding="utf-8")
                (root / "separator.json").write_text("{}", encoding="utf-8")
                argv = ["dub_anything.py", "new", str(source)] + ([flag] if flag else [])
                with (
                    patch.object(runner, "VIDEOS", root / "videos"),
                    patch.object(runner, "JOB_DIR", work / "jobs"),
                    patch.object(runner, "WORK", work),
                    patch.object(sys, "argv", argv),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    with self.assertRaises(SystemExit) as stopped:
                        runner.main()
                    self.assertEqual(stopped.exception.code, 0)
                    job = runner.load_job("sample")
                    self.assertEqual(job["captions"], mode)
                    self.assertEqual(job["signs"], signs)
                    with (
                        patch.object(runner, "job_dir_of", return_value=root),
                        patch.object(runner, "tool", return_value=(True, "")) as tool,
                    ):
                        self.assertEqual(runner.prepare_step(job, [{"name": "sample-01"}]), "busy")
                    command = tool.call_args.args[2]
                    if extra is None:
                        self.assertIn("scripts/draft.py", command)
                    else:
                        self.assertEqual(command, runner.SUBS + ["sample-01"] + extra)


class PauseCheckpointTests(unittest.TestCase):
    def test_pause_waits_for_background_checks_and_skips_the_next_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = pathlib.Path(tmp) / "pause"
            marker.touch()
            background = Mock()
            with (
                patch.dict(runner.os.environ, {"DUB_PAUSE_FILE": str(marker)}),
                patch.object(runner, "SECOND_RUN", {"sample": (background, [])}),
                patch.object(runner.subprocess, "run") as launch,
            ):
                with self.assertRaises(SystemExit) as stopped:
                    runner.tool({"slug": "sample"}, "next step", [], "next")
                self.assertEqual(stopped.exception.code, 75)
                background.wait.assert_called_once()
                launch.assert_not_called()

    def test_direct_dubs_have_no_pause_marker(self):
        with patch.dict(runner.os.environ, {"DUB_PAUSE_FILE": ""}):
            runner.pause_checkpoint()


class WatchBlockTests(unittest.TestCase):
    def run_changed_block(self, before, after, known=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            part = {"name": "sample-01"}
            path = root / part["name"] / "state.json"
            path.parent.mkdir()
            path.write_text(json.dumps(before), encoding="utf-8")

            def advance(job, name):
                path.write_text(json.dumps(after), encoding="utf-8")
                return "blocked"

            with (
                patch.object(runner, "WORK", root),
                patch.object(runner, "load_job", return_value={"slug": "sample", "voices": ["actor"]}),
                patch.object(runner.backend, "ensure", return_value=True),
                patch.object(runner, "split", return_value=True),
                patch.object(runner, "parts", return_value=[part]),
                patch.object(runner, "save_job"),
                patch.object(runner, "second"),
                patch.object(runner, "ready", return_value=True),
                patch.object(runner, "original_only", return_value=False),
                patch.object(runner, "drafted", return_value=True),
                patch.object(runner, "variant", return_value=part["name"]),
                patch.object(runner, "advance", side_effect=advance),
                patch.object(runner, "prepare_step", return_value="idle"),
                patch.object(runner, "finished", return_value=False),
                patch.object(runner, "report") as report,
                patch.object(runner.time, "sleep", side_effect=AssertionError("watcher waited on a new block")),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                args = types.SimpleNamespace(slug="sample", watch=True)
                if known:
                    with self.assertRaisesRegex(AssertionError, "watcher waited on a new block"):
                        runner.cmd_run(args)
                    report.assert_not_called()
                else:
                    result = runner.cmd_run(args)
                    report.assert_called_once()
                    self.assertEqual(result, 2)

    def test_changed_failure_on_previously_blocked_part_stops_watch(self):
        self.run_changed_block(
            {"script": "old", "problem": "lines don't fit: L01"},
            {"script": "old", "problem": "lines don't fit: L05"},
        )

    def test_same_failure_after_script_change_stops_watch(self):
        self.run_changed_block(
            {"script": "old", "problem": "lines don't fit: L01"},
            {"script": "new", "problem": "lines don't fit: L01"},
        )

    def test_unchanged_known_block_keeps_waiting(self):
        state = {"script": "old", "problem": "lines don't fit: L01"}
        self.run_changed_block(state, state, known=True)
