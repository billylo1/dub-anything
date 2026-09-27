"""Group the lines of one or more clips by voice, to cast consistently across parts.

Usage: cast.py <name> [<name> ...] [--min 0.9] [--threshold 0.38]   (after draft.py)

Every draft line of at least --min seconds gets a speaker embedding (WeSpeaker,
as in draft.py). Average-linkage clustering by cosine similarity then puts one
voice in each cluster. Clusters are printed with their lines and the nearest
existing cast member, so a recurring character keeps one voice and a new one is
identified once (from the dialogue and frames.py), not part by part. Short
lines end up as singletons: settle them with draft.py once the cast is built.
"""
import argparse
import sys

import numpy as np

import draft
from dub import JOBS, WORK, load_json

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def cluster(sim, threshold):
    groups = [[k] for k in range(len(sim))]
    while len(groups) > 1:
        best, pair = -1.0, None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                s = sim[np.ix_(groups[i], groups[j])].mean()
                if s > best:
                    best, pair = s, (i, j)
        if best < threshold:
            break
        i, j = pair
        groups[i] += groups.pop(j)
    return sorted(groups, key=len, reverse=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="+")
    ap.add_argument("--min", type=float, default=0.9, help="shortest line to embed (s)")
    ap.add_argument("--threshold", type=float, default=0.38, help="merge clusters above this similarity")
    args = ap.parse_args()

    embed = draft.embedder()
    if embed is None:
        raise SystemExit("needs the pyannote models (docs/setup.md, step 5)")
    cast = draft.cast_centroids(embed, None)
    items = []
    for name in args.names:
        job = load_json(WORK / name / "segments.json")["job_id"]
        audio = draft.decode_audio(str(JOBS / job / "vocals.wav"), sampling_rate=draft.SR)
        for i, line in enumerate(load_json(WORK / name / "draft.json")["lines"]):
            if line["end"] - line["start"] >= args.min and not line.get("suspect"):
                v = embed(audio[int(line["start"] * draft.SR):int(line["end"] * draft.SR)])
                items.append((name, i, line, v))
    if not items:
        raise SystemExit("no lines to cluster (run draft.py first)")
    vecs = np.stack([it[3] for it in items])
    for c, members in enumerate(cluster(vecs @ vecs.T, args.threshold)):
        centre = vecs[members].mean(0)
        centre /= np.linalg.norm(centre)
        near = max(((float(centre @ v), k) for k, v in cast.items()), default=None)
        hint = f"nearest cast: {near[1]} {near[0]:.2f}" if near else "no cast yet"
        print(f"\n=== C{c}  {len(members)} lines  {hint}")
        for k in sorted(members, key=lambda k: (items[k][0], items[k][2]["start"])):
            name, i, line, v = items[k]
            print(f"  {name} L{i:02d} {line['start']:7.2f}-{line['end']:7.2f} {float(v @ centre):4.2f}  {line['ja']}")


if __name__ == "__main__":
    main()
