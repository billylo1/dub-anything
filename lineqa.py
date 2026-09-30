"""Transcribe every rendered line on its own and flag the ones that don't match.

Usage: lineqa.py <name> [<name> ...] [--all] [--reroll]   (after `dub.py generate`)

Full-mix QA (qa.py) hides problems that show up clearly per line:
  - extra words before the first expected one: voice-reference leakage
    (a voice clip in voices.json that cuts a word off or holds someone else)
  - dropped or changed words (fast lists), and flipped meanings ("can" heard
    as "can't": any change in negation is flagged)
  - words a voice can't say, and silent takes
Both sides are normalized before comparing (numbers spelled out, contractions
expanded, "Dr." = "doctor", compounds joined), so digits and spelling don't
count as failures. --reroll writes the flagged lines to work/<name>/reroll.json;
the next `dub.py generate` gives them fresh takes. That fixes random glitches,
but a word a voice can't say needs rewording.
"""
import argparse
import difflib
import json
import re
import sys

import soundfile as sf

from core import JOBS, WORK, line_id, whisper_setup

whisper_setup()
from faster_whisper import WhisperModel  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
        "sixteen seventeen eighteen nineteen").split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
ABBREV = {"dr": "doctor", "mr": "mister", "mrs": "missus", "ms": "miss", "ok": "okay", "vs": "versus",
          "nope": "no", "nah": "no", "cm": "centimeter", "mm": "millimeter", "km": "kilometer",
          "kg": "kilogram", "centimeters": "centimeter", "millimeters": "millimeter"}
NEGATIONS = {"not", "no", "never", "nothing", "nobody", "none", "neither", "nor"}


def spell(n):
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])
    for div, name in ((10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand"), (100, "hundred")):
        if n >= div:
            q, r = divmod(n, div)
            return f"{spell(q)} {name}" + ("" if r == 0 else f" {spell(r)}")


def words(text):
    t = text.lower().replace("’", "'")
    t = re.sub(r"(\d+)\s*%", r"\1 percent", t)
    t = re.sub(r"\bno\.\s*(?=\d)", "number ", t)  # "No.1" is "number one", not a negation
    t = re.sub(r"(?<=\d),(?=\d{3})", "", t)
    t = re.sub(r"(\d)\.(\d)", r"\1 point \2", t)
    t = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", t)  # "a2.5cm" -> "a 2.5 cm"
    t = re.sub(r"\bnevermind\b", "never mind", t)  # one word or two, same meaning
    for a, b in (("won't", "will not"), ("can't", "can not"), ("n't", " not"), ("'re", " are"),
                 ("'ve", " have"), ("'ll", " will"), ("'m", " am"), ("'d", " would"), ("'s", " is")):
        t = t.replace(a, b)
    out = []
    for w in re.findall(r"[a-z0-9]+", t):
        out += spell(int(w)).split() if w.isdigit() and len(w) < 13 else [ABBREV.get(w, w)]
    return out


def compare(want, got):
    """(notes, similarity): leading junk, and the better of word- or letter-level match."""
    want_w, got_w = words(want), words(got)
    notes = []
    if want_w and want_w[0] in got_w[:4] and got_w.index(want_w[0]) > 0:
        notes.append(f"leading extra: '{' '.join(got_w[:got_w.index(want_w[0])])}'")
    ratio = max(difflib.SequenceMatcher(None, want_w, got_w).ratio(),
                difflib.SequenceMatcher(None, "".join(want_w), "".join(got_w)).ratio())
    if not got_w:
        notes.append("silent or unintelligible")
    elif ratio < 0.85:
        notes.append(f"match {ratio:.2f}")
    # A close match can still flip the meaning ("can" heard as "can't").
    if got_w and sum(w in NEGATIONS for w in want_w) != sum(w in NEGATIONS for w in got_w):
        notes.append("negation differs")
    return notes, ratio


def check(name, model, show_all, reroll):
    work = WORK / name
    job = json.loads((work / "segments.json").read_text(encoding="utf-8"))["job_id"]
    lines = json.loads((work / "script.json").read_text(encoding="utf-8"))["lines"]
    flagged = []
    for i, line in enumerate(lines):
        lid = line_id(line)
        wav = JOBS / job / f"seg_en_{lid}.wav"
        if not wav.exists():
            print(f"L{i:02d} {line['start']:7.2f}  (not rendered)  {line['text']}")
            continue
        segs, _ = model.transcribe(str(wav), language="en", beam_size=5, vad_filter=False,
                                   condition_on_previous_text=False)
        got = " ".join(s.text.strip() for s in segs)
        notes, _ = compare(line["text"], got)
        if notes:
            flagged.append({"id": lid, "start": line["start"], "text": line["text"], "got": got})
        if notes or show_all:
            dur = sf.info(wav).duration
            print(f"L{i:02d} {line['start']:7.2f} {line['voice']:10s} {dur:4.2f}s  {'; '.join(notes) or 'ok'}\n"
                  f"      want: {line['text']}\n      got:  {got}")
    print(f"{len(flagged)} of {len(lines)} lines flagged")
    if reroll and flagged:
        (work / "reroll.json").write_text(json.dumps(flagged, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"queued {len(flagged)} for fresh takes on the next `dub.py generate {name}`")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="+", metavar="name", help="one or more rendered parts (Whisper loads once)")
    ap.add_argument("--all", action="store_true", help="print every line, not just flagged ones")
    ap.add_argument("--reroll", action="store_true", help="queue flagged lines for fresh takes")
    args = ap.parse_args()
    model = WhisperModel("Systran/faster-whisper-large-v3", device="auto", compute_type="default")
    for name in args.names:
        if len(args.names) > 1:
            print(f"=== {name}")
        check(name, model, args.all, args.reroll)


if __name__ == "__main__":
    main()
