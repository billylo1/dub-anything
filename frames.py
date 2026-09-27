"""Labelled grid of video frames, for casting: who is on screen at each line?

Usage: frames.py <out.jpg> <video> <seconds>[:label] ... [--cols 5] [--width 400]

One frame per time, stamped with the time and label, tiled into one image.
Close-ups, visible speech and on-screen name captions help identify speakers.
"""
import argparse
import os
import pathlib
import subprocess
import tempfile

# Escaped twice: once for the drawtext option, once for the filtergraph.
FONT = "C\\\\:/Windows/Fonts/arial.ttf" if os.name == "nt" else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("video")
    ap.add_argument("times", nargs="+", help="seconds, optionally with :label")
    ap.add_argument("--cols", type=int, default=5)
    ap.add_argument("--width", type=int, default=400)
    args = ap.parse_args()

    tmp = pathlib.Path(tempfile.mkdtemp())
    frames = []
    for k, spec in enumerate(args.times):
        t, _, label = spec.partition(":")
        text = f"{float(t):.1f} {label}".strip().replace(":", "\\:").replace("'", "")
        draw = f"drawtext={'fontfile=' + FONT + ':' if FONT else ''}text='{text}':x=4:y=4:fontsize=17" \
               f":fontcolor=yellow:box=1:boxcolor=black"
        f = tmp / f"{k:03d}.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", t, "-i", args.video, "-frames:v", "1",
                        "-vf", f"scale={args.width}:-2,{draw}", str(f)], check=True)
        frames.append(f)
    while len(frames) % args.cols:
        frames.append(frames[-1])
    height = int(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=height", "-of", "csv=p=0",
                                 str(frames[0])], capture_output=True, text=True).stdout.strip())
    layout = "|".join(f"{(i % args.cols) * args.width}_{(i // args.cols) * height}" for i in range(len(frames)))
    inputs = [a for f in frames for a in ("-i", str(f))]
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex",
                    f"xstack=inputs={len(frames)}:layout={layout}", args.out], check=True)
    print(args.out)


if __name__ == "__main__":
    main()
