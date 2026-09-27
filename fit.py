"""Compare each generated English clip's speech length with its script window.

Usage: fit.py <name>   (reads work/<name>/script.json and the job's seg_en_L*.wav)
`dub.py generate` prints the same table for the failing lines when a line overflows.
"""
import sys

from dub import fit_rows

total_chars = total_secs = 0.0
for row in fit_rows(sys.argv[1]):
    if row["speech"] is None:
        print(f"L{row['i']:02d}  (no clip)  {row['text']}")
        continue
    total_chars += len(row["text"])
    total_secs += row["speech"]
    print(f"L{row['i']:02d} {row['speech']:5.2f}s / window {row['window']:5.2f}s (room {row['room']:5.2f})  "
          f"{row['flag']:5s} {row['text']}")
if total_secs:
    print(f"average rate: {total_chars / total_secs:.1f} chars/s")
