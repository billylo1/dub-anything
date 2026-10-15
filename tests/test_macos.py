"""Unit tests for macOS cross-platform support and fallbacks."""

import os
import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import backend
import core
import dub_anything
import frames


class PlatformPathTests(unittest.TestCase):
    def test_default_data_dir_resolution(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("sys.platform", "darwin"):
                p = core.default_data_dir()
                self.assertEqual(p, pathlib.Path.home() / "Library" / "Application Support" / "OmniVoice")

            with patch("sys.platform", "win32"):
                with patch.dict(os.environ, {"APPDATA": "/mock/AppData"}):
                    p = core.default_data_dir()
                    self.assertEqual(p, pathlib.Path("/mock/AppData/OmniVoice"))

            with patch("sys.platform", "linux"):
                p = core.default_data_dir()
                self.assertEqual(p, pathlib.Path.home() / ".local" / "share" / "OmniVoice")

    def test_default_cache_dir_resolution(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("sys.platform", "darwin"):
                p = core.default_cache_dir()
                self.assertEqual(p, str(pathlib.Path.home() / "Library" / "Caches" / "OmniVoice" / "hf_cache"))

            with patch("sys.platform", "win32"):
                with patch.dict(os.environ, {"LOCALAPPDATA": "/mock/LocalAppData"}):
                    p = core.default_cache_dir()
                    self.assertEqual(p, str(pathlib.Path("/mock/LocalAppData/OmniVoice/hf_cache")))

    def test_explicit_env_overrides_paths(self):
        with patch.dict(
            os.environ,
            {"OMNIVOICE_DATA_DIR": "/custom/data", "OMNIVOICE_CACHE_DIR": "/custom/cache"},
        ):
            self.assertEqual(core.default_data_dir(), pathlib.Path("/custom/data"))
            self.assertEqual(core.default_cache_dir(), "/custom/cache")


class BackendPlatformTests(unittest.TestCase):
    def test_backend_log_path_darwin_default(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("sys.platform", "darwin"):
                log = backend.log_path({})
                self.assertEqual(
                    log,
                    pathlib.Path.home() / "Library" / "Application Support" / "OmniVoice" / "backend.log",
                )

    def test_backend_health_fallback_urllib(self):
        # Even if requests is not installed, health() handles urllib cleanly
        with patch("sys.modules", {"requests": None}):
            h = backend.health(timeout=0.1)
            # Backend may be down, but it should return None rather than raising an uncaught exception
            self.assertIsNone(h)

    def test_full_env_includes_homebrew_on_darwin(self):
        with patch("sys.platform", "darwin"):
            env = backend.full_env()
            if os.path.isdir("/opt/homebrew/bin"):
                self.assertIn("/opt/homebrew/bin", env.get("PATH", "").split(":"))


class DubAnythingDependencyTests(unittest.TestCase):
    def test_darwin_separate_dependencies_do_not_contain_cu128(self):
        if sys.platform == "darwin":
            cmd_str = " ".join(dub_anything.SEPARATE)
            self.assertNotIn("cu128", cmd_str)
            self.assertNotIn("torch==2.8.0", cmd_str)
            self.assertIn("audio-separator", cmd_str)

    def test_darwin_subs_dependencies_do_not_contain_onnxruntime_gpu(self):
        if sys.platform == "darwin":
            cmd_str = " ".join(dub_anything.SUBS)
            self.assertNotIn("onnxruntime-gpu", cmd_str)
            self.assertIn("onnxruntime", cmd_str)
            self.assertIn("rapidocr", cmd_str)


class FramesFallbackTests(unittest.TestCase):
    def test_has_drawtext_boolean(self):
        res = frames.has_drawtext()
        self.assertIsInstance(res, bool)


if __name__ == "__main__":
    unittest.main()
