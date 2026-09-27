"""Cut a long video into ~N-minute parts at pauses in speech, to dub part by part.

Usage: split.py <video> [--minutes 3] [--out DIR] [--encoder auto|h264_nvenc|libx264]
Silero VAD over the full mix finds the gaps between speech; near each N-minute
mark (+-30 s) the longest gap wins and the cut goes in its middle, so no line
is split. Parts are re-encoded so cuts are frame-accurate and dubbed parts can
be joined back without drift. Writes <out>/<stem>-NN.mp4 and <out>/parts.json
(default out: <stem>-parts beside the video).
"""
import argparse
import json
import pathlib
import subprocess
import sys

from faster_whisper.audio import decode_audio
from faster_whisper.vad import VadOptions, get_speech_timestamps

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SR = 16000

p = argparse.ArgumentParser()
p.add_argument("video")
p.add_argument("--minutes", type=float, default=3)
p.add_argument("--out")
# auto: NVENC when it works (current FFmpeg builds need NVIDIA driver >= 610), else x264.
p.add_argument("--encoder", default="auto")
args = p.parse_args()
if args.encoder == "auto":
    nvenc = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=size=256x256:duration=0.1",
                            "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True)
    args.encoder = "h264_nvenc" if nvenc.returncode == 0 else "libx264"
quality = (["-preset", "p5", "-cq", "20"] if "nvenc" in args.encoder
           else ["-preset", "veryfast", "-crf", "18"])
video = pathlib.Path(args.video).resolve()
out = pathlib.Path(args.out) if args.out else video.parent / f"{video.stem}-parts"
# Snap cuts to whole video frames of the source (29.97, 30, 25 fps...), so parts join without drift.
rate = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                       "-of", "csv=p=0", str(video)], capture_output=True, text=True).stdout.strip() or "30000/1001"
num, den = (int(x) for x in rate.split("/"))
FRAME = den / num
out.mkdir(parents=True, exist_ok=True)

print(f"voice activity in {video.name}")
audio = decode_audio(str(video), sampling_rate=SR)
duration = len(audio) / SR
speech = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=300, speech_pad_ms=100))
gaps = [(a["end"] / SR, b["start"] / SR) for a, b in zip(speech, speech[1:])]

step, cuts, t = args.minutes * 60, [0.0], args.minutes * 60
while t < duration - step / 2:
    near = [g for g in gaps if abs((g[0] + g[1]) / 2 - t) <= 30]
    a, b = max(near, key=lambda g: g[1] - g[0]) if near else (t, t)
    cuts.append(round(round((a + b) / 2 / FRAME) * FRAME, 3))
    print(f"  cut {cuts[-1]:7.2f}s  (pause {b - a:4.2f}s, target {t:.0f}s)")
    t = cuts[-1] + step
cuts.append(duration)

parts = []
for i, (a, b) in enumerate(zip(cuts, cuts[1:]), 1):
    name = f"{video.stem}-{i:02d}"
    dst = out / f"{name}.mp4"
    print(f"{name}: {a:7.2f}-{b:7.2f}s")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-i", str(video), "-t", f"{b - a:.3f}",
                    "-map", "0:v:0", "-map", "0:a:0", "-c:v", args.encoder, *quality, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dst)], check=True)
    parts.append({"name": name, "file": str(dst), "start": a, "end": b})
(out / "parts.json").write_text(json.dumps({"video": str(video), "parts": parts}, indent=2), encoding="utf-8")
print(f"wrote {len(parts)} parts to {out}")
