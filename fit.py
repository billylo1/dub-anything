"""Compare each generated English clip's speech length with its script window.

Usage: fit.py <name> [--only-flags]   (reads work/<name>/script.json and the job's seg_en_L*.wav)
Lines not rendered yet get an estimate (~) from the voice's rate on lines
already rendered, flagged "OVER?" or "risk", so they can be fixed before the
first render. `dub.py generate` prints the same table for the failing lines
when a line overflows.
"""
import argparse

from dub import fit_rows

ap = argparse.ArgumentParser()
ap.add_argument("name")
ap.add_argument("--only-flags", action="store_true", help="print only lines that don't fit or are at risk")
args = ap.parse_args()
total_chars = total_secs = 0.0
for row in fit_rows(args.name, predict=True):
    if args.only_flags and not row["flag"]:
        continue
    if row["speech"] is None:
        est = f"~{row['est']:4.2f}s" if "est" in row else "(no clip)"
        print(f"L{row['i']:02d} {est:>6s} / window {row['window']:5.2f}s (room {row['room']:5.2f})  "
              f"{row['flag']:5s} {row['text']}")
        continue
    total_chars += len(row["text"])
    total_secs += row["speech"]
    print(f"L{row['i']:02d} {row['speech']:5.2f}s / window {row['window']:5.2f}s (room {row['room']:5.2f})  "
          f"{row['flag']:5s} {row['text']}")
if total_secs:
    print(f"average rate: {total_chars / total_secs:.1f} chars/s")
