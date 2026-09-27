"""Transcribe every rendered line on its own and flag the ones that don't match.

Usage: lineqa.py <name> [--all]   (after `dub.py generate`)

Full-mix QA (qa.py) hides problems that show up clearly per line:
  - extra words before the first expected one: voice-reference leakage
    (a voice clip in voices.json that cuts a word off or holds someone else)
  - dropped or changed words (fast lists, accent: "can" heard as "can't")
Numbers written as digits ("3 billion") are flagged too; glance and move on.
"""
import argparse
import difflib
import json
import re
import sys

import soundfile as sf

from dub import JOBS, WORK, line_id, whisper_setup

whisper_setup()
from faster_whisper import WhisperModel  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def words(text):
    return re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--all", action="store_true", help="print every line, not just flagged ones")
    args = ap.parse_args()
    work = WORK / args.name
    job = json.loads((work / "segments.json").read_text(encoding="utf-8"))["job_id"]
    lines = json.loads((work / "script.json").read_text(encoding="utf-8"))["lines"]
    model = WhisperModel("Systran/faster-whisper-large-v3", device="auto", compute_type="default")

    flagged = 0
    for i, line in enumerate(lines):
        wav = JOBS / job / f"seg_en_{line_id(line)}.wav"
        if not wav.exists():
            print(f"L{i:02d} {line['start']:7.2f}  (not rendered)  {line['text']}")
            flagged += 1
            continue
        segs, _ = model.transcribe(str(wav), language="en", beam_size=5, vad_filter=False,
                                   condition_on_previous_text=False)
        got = " ".join(s.text.strip() for s in segs)
        want_w, got_w = words(line["text"]), words(got)
        notes = []
        if want_w and want_w[0] in got_w[:4] and got_w.index(want_w[0]) > 0:
            notes.append(f"leading extra: '{' '.join(got_w[:got_w.index(want_w[0])])}'")
        ratio = difflib.SequenceMatcher(None, want_w, got_w).ratio()
        if ratio < 0.85:
            notes.append(f"match {ratio:.2f}")
        if notes or args.all:
            flagged += bool(notes)
            dur = sf.info(wav).duration
            print(f"L{i:02d} {line['start']:7.2f} {line['voice']:10s} {dur:4.2f}s  {'; '.join(notes) or 'ok'}\n"
                  f"      want: {line['text']}\n      got:  {got}")
    print(f"{flagged} of {len(lines)} lines flagged")


if __name__ == "__main__":
    main()
