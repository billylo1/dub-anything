"""Compare each generated English clip's speech length with its script window.

Usage: fit.py <name>   (reads work/<name>/script.json and the job's seg_en_L*.wav)
"""
import json
import pathlib
import sys

import numpy as np
import soundfile as sf

from dub import JOBS, WORK, line_id

name = sys.argv[1]
job = json.loads((WORK / name / "segments.json").read_text(encoding="utf-8"))["job_id"]
lines = json.loads((WORK / name / "script.json").read_text(encoding="utf-8"))["lines"]
jobdir = JOBS / job

total_chars = total_secs = 0.0
for i, line in enumerate(lines):
    wav = jobdir / f"seg_en_{line_id(line)}.wav"
    if not wav.exists():
        print(f"L{i:02d}  (no clip)  {line['text']}")
        continue
    audio, sr = sf.read(wav)
    mono = np.abs(audio if audio.ndim == 1 else audio.mean(axis=1))
    loud = np.nonzero(mono > 10 ** (-40 / 20))[0]  # trim edge silence below -40 dBFS
    speech = (loud[-1] - loud[0]) / sr if len(loud) else 0.0
    window = line["end"] - line["start"]
    nxt = lines[i + 1]["start"] if i + 1 < len(lines) else line["end"] + 1.0
    room = min(nxt, line["end"] + 0.25) - line["start"]  # concise may borrow <=0.25 s of gap
    total_chars += len(line["text"])
    total_secs += speech
    flag = "OVER" if speech > room else ("tight" if speech > window else "")
    print(f"L{i:02d} {speech:5.2f}s / window {window:5.2f}s (room {room:5.2f})  {flag:5s} {line['text']}")
print(f"average rate: {total_chars / total_secs:.1f} chars/s")
