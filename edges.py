"""Find lines whose window opens after the actor has already started speaking.

Usage: edges.py <name> [--apply]   (after `dub.py prep`; works on script.json)

At each window start VoiceStudio crossfades from the original mix, voice
included, to the voice-free bed in 10 ms. Speech detection misses soft attacks
(a breath, a consonant) by a few tens of ms. If the actor is already sounding
when the window opens, that sliver of the original plays and is then chopped,
which is heard as a click just before the English starts. This finds every
window start where the original voice is active and walks back to where that
sound began. For a clear soft attack (see MIN_SHIFT/MAX_SHIFT/CUT_DB) it moves
the window to start 20 ms before it, never before the previous window's end;
the rest are listed as 'skip'. --apply writes the moves to script.json. It's
timing only: the next `dub.py generate` re-mixes without re-synthesizing, and
the take starts a little earlier, closer to the actor's mouth.
Window ends where the original voice is still sounding are noted, not changed.
"""
import argparse
import json
import sys

import numpy as np

from dub import JOBS, WORK, load_json
from faster_whisper.audio import decode_audio

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SR, HOP = 16000, 80  # 5 ms frames


def frames_db(audio):
    n = len(audio) // HOP
    x = audio[:n * HOP].reshape(n, HOP)
    return 20 * np.log10(np.sqrt((x ** 2).mean(1)) + 1e-9)


def voiced_onset(db, t, floor_t, level, below=20.0, lead=0.02):
    """If the voice is sounding just before time t, the time it began (minus
    `lead`), not earlier than floor_t; else None. `level` is the line's speech
    level; 'sounding' means within `below` dB of it."""
    k = int(t * SR / HOP)
    thresh = level - below
    if k < 2 or max(db[k - 2:k]) < thresh:  # the 10 ms before the edge
        return None
    j = k - 1
    floor_k = int(floor_t * SR / HOP)
    while j - 1 >= floor_k and db[j - 1] >= thresh:
        j -= 1
    return max(floor_t, j * HOP / SR - lead)


def line_level(db, a, b):
    seg = db[int(a * SR / HOP):int(b * SR / HOP)]
    return float(np.percentile(seg, 90)) if len(seg) else -90.0


# Move a start only for a real, audible soft attack. The voice must be clearly
# sounding at the cut and must have begun 20-130 ms before the window (shift
# includes the 20 ms lead). Shorter fragments weren't heard on a line nobody
# flagged. Longer ones are usually someone else's sound (a tail, a laugh),
# where moving the window would start the English too early.
MIN_SHIFT, MAX_SHIFT, CUT_DB = 0.04, 0.15, -16.0


def check(name, apply=False, min_shift=0.015):
    path = WORK / name / "script.json"
    script = load_json(path)
    lines = script["lines"]
    audio = decode_audio(str(JOBS / load_json(WORK / name / "segments.json")["job_id"] / "vocals.wav"),
                         sampling_rate=SR)
    db = frames_db(audio)
    moved, tails = [], []
    for i, line in enumerate(lines):
        level = line_level(db, line["start"], line["end"])
        prev_end = lines[i - 1]["end"] if i else 0.0
        onset = voiced_onset(db, line["start"], max(prev_end, line["start"] - 0.3), level)
        if onset is not None and line["start"] - onset >= min_shift:
            k = int(line["start"] * SR / HOP)
            cut = float(max(db[k - 2:k])) - level  # how loud the voice is where it gets chopped
            fix = MIN_SHIFT - 1e-6 <= line["start"] - onset <= MAX_SHIFT + 1e-6 and cut >= CUT_DB
            moved.append((i, line["start"], round(onset, 2), line["text"], cut, fix))
            if apply and fix:
                line["start"] = round(onset, 2)
        k = int(line["end"] * SR / HOP)
        nxt = lines[i + 1]["start"] if i + 1 < len(lines) else line["end"] + 1
        if nxt - line["end"] > 0.05 and k + 2 < len(db) and min(db[k:k + 2]) >= level - 20:
            tails.append((i, line["end"], line["text"]))
    for i, old, new, text, cut, fix in moved:
        print(f"  {'FIX ' if fix else 'skip'} L{i:02d} start {old:7.2f} -> {new:7.2f}  ({(old - new) * 1000:3.0f} ms "
              f"earlier, voice {cut:+5.1f} dB at the cut)  {text[:40]}")
    for i, end, text in tails:
        print(f"  note L{i:02d} end   {end:7.2f}  original voice still sounding at the window end  {text[:36]}")
    fixes = sum(m[5] for m in moved)
    print(f"{name}: {fixes} starts to move{' (applied)' if apply else ''}, {len(moved) - fixes} left alone, "
          f"{len(tails)} ends noted")
    if apply and fixes:
        body = ",\n".join("    " + json.dumps(l, ensure_ascii=False) for l in lines)
        caps = ",\n".join("    " + json.dumps(l, ensure_ascii=False) for l in script.get("captions", []))
        path.write_text('{\n  "lines": [\n' + body + "\n  ]" + (',\n  "captions": [\n' + caps + "\n  ]" if caps else "")
                        + "\n}\n", encoding="utf-8")
    return moved


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--apply", action="store_true", help="write the earlier starts to script.json")
    args = ap.parse_args()
    check(args.name, args.apply)
