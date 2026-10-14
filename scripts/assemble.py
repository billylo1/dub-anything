"""Package dubbed clips as one video: dub + original audio, subtitles in both languages.

Usage: scripts/assemble.py <stem> [--voice actor|american] [--parts 1-4] [--out FILE] [--sub FILE.srt:lang[:title]] ...

<stem> is a split video (videos/<stem>-parts/parts.json from split.py) or a
single prepped clip (work/<stem>). --voice american assembles the parts' American
renders (work/<part>.us, see core.voice_part). The result has:
  - the source video (stream-copied when starting at 0, so no seams at part cuts)
  - audio 1: the dub (default), audio 2: the original soundtrack
  - soft subtitles: the dub's text and the corrected original from script.json
    (its "lines" plus any caption-only "captions" left in the original audio),
    any text subtitle tracks already in the source, and any --sub files
    (timed to the source video)
plus sidecar .srt files named for their language (.en.srt, .ja.srt), and when
any part has "signs" (translations of text in the picture) the styled track of
the dub's language with them (.en.ass, or .ja.ass), and the signs alone,
.signs.ass (see ass.py). The languages are the parts' (core.langs), and the
track names theirs (TITLES in languages/<code>.py). Output
defaults to the parts' `dubbed` folder.
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys

import ass
from core import ROOT, VOICE_LABEL, WORK, langs, load_json, out_dir, said, srt_time, voice_part
from languages import ISO3, LANGUAGES, PAIR

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TEXT_SUBS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration:stream=index,codec_type,codec_name,width,height:stream_tags=language,title,handler_name",
                          "-of", "json", str(path)], capture_output=True, encoding="utf-8", check=True).stdout  # fmt: skip
    return json.loads(out)


def pick(parts, spec):
    if not spec:
        return parts
    a, _, b = spec.partition("-")
    return parts[int(a) - 1 : int(b or a)]


def write_srt(path, cues):
    with open(path, "w", encoding="utf-8") as f:
        for n, (a, b, text) in enumerate(cues, 1):
            f.write(f"{n}\n{srt_time(a)} --> {srt_time(b)}\n{text}\n\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stem")
    ap.add_argument(
        "--voice",
        choices=list(VOICE_LABEL),
        default="actor",
        help="which voice's renders to assemble (default: the actor's voice)",
    )
    ap.add_argument("--parts", help="range of split parts, e.g. 1-4 (default: all)")
    ap.add_argument("--out")
    ap.add_argument(
        "--sub",
        action="append",
        default=[],
        help="extra subtitle file timed to the source video: FILE.srt:lang[:title]",
    )
    ap.add_argument(
        "--encoder", default="h264_nvenc", help="video encoder when the range doesn't start at 0 (else stream copy)"
    )
    args = ap.parse_args()

    manifest = ROOT / "videos" / f"{args.stem}-parts" / "parts.json"
    if manifest.exists():
        pj = load_json(manifest)
        source, parts = pathlib.Path(pj["video"]), pick(pj["parts"], args.parts)
    else:
        doc = load_json(WORK / args.stem / "segments.json")
        source = pathlib.Path(doc["video"])
        parts = [{"name": args.stem, "start": 0.0, "end": float(probe(source)["format"]["duration"])}]
    parts = [{**p, "name": voice_part(p["name"], args.voice)} for p in parts]
    t0, t1 = parts[0]["start"], parts[-1]["end"]
    src, tgt = next((langs(p["name"]) for p in parts if (WORK / p["name"] / "segments.json").exists()), PAIR)

    dubs, dubbed, original, signs = [], [], [], []
    for p in parts:
        # A part left as it is needs no prep: its own file stands in for the job's.
        doc = load_json(WORK / p["name"] / "segments.json") or {"video": p.get("file", source)}
        mp4 = out_dir(doc) / f"{p['name']}.{tgt}.mp4"
        script = load_json(WORK / p["name"] / "script.json")
        if not mp4.exists() and script is not None and not script["lines"] and p.get("file"):
            # A part directed to stay as it is (commercials, credits): its original audio.
            print(f"  {p['name']}: no lines to dub, original audio")
            mp4 = pathlib.Path(p["file"])
        if not mp4.exists():
            raise SystemExit(f"{p['name']}: no dub yet ({mp4})")
        dubs.append((mp4, p["end"] - p["start"]))
        off = p["start"] - t0
        # "captions": speech left in the original (another language already,
        # names, whispers) that still belongs in the subtitles.
        for line in sorted(script["lines"] + script.get("captions", []), key=lambda l: l["start"]):
            dubbed.append((line["start"] + off, line["end"] + off, LANGUAGES[tgt].subtitle(line["text"])))
            if said(line, src):
                original.append((line["start"] + off, line["end"] + off, LANGUAGES[src].subtitle(said(line, src))))
        signs += [{**s, "start": s["start"] + off, "end": s["end"] + off} for s in script.get("signs", [])]

    first_doc = load_json(WORK / parts[0]["name"] / "segments.json") or {"video": parts[0].get("file", source)}
    dest = out_dir(first_doc)
    suffix = f".p{args.parts}" if args.parts else ""
    variant = "" if args.voice == "actor" else f".{args.voice}"
    out = pathlib.Path(args.out) if args.out else dest / f"{args.stem}{variant}.dubbed{suffix}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    subs = []
    for lang, cues in ((tgt, dubbed), (src, original)):
        if cues:
            srt = out.with_suffix(f".{lang}.srt")
            write_srt(srt, cues)
            subs.append((srt, ISO3[lang], LANGUAGES[lang].NATIVE, 0.0))
    styled = [out.with_suffix(f".{tgt}.ass"), out.with_suffix(".signs.ass")]
    if signs:
        video = next(s for s in probe(source)["streams"] if s["codec_type"] == "video")
        ass.write(styled[0], video["width"], video["height"], dubbed, signs, lang=tgt)
        ass.write(styled[1], video["width"], video["height"], (), signs, lang=tgt)
        print(f"  {len(signs)} signs: {styled[0].name}, {styled[1].name}")
    else:
        for path in styled:  # left from an earlier assembly: they would deliver signs no part has now
            path.unlink(missing_ok=True)
    for s in args.sub:
        m = re.match(r"^(.+\.(?:srt|ass|ssa|vtt))(?::([^:]+))?(?::(.+))?$", s, re.IGNORECASE)
        if not m:
            raise SystemExit(f"--sub {s}: expected FILE.srt:lang[:title]")
        path, lang, title = m.groups()
        lang3 = ISO3.get(lang or "und", lang or "und")
        native = LANGUAGES[lang].NATIVE if lang in LANGUAGES else ""
        subs.append((pathlib.Path(path), lang3, title or native, -t0))

    info = probe(source)
    src_subs = [s for s in info["streams"] if s["codec_type"] == "subtitle"]
    skipped = [s for s in src_subs if s["codec_name"] not in TEXT_SUBS]
    src_subs = [s for s in src_subs if s["codec_name"] in TEXT_SUBS]
    if skipped:
        print(f"  skipping {len(skipped)} image-based subtitle track(s) (MP4 can't hold them)")

    copy_video = t0 == 0
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", str(source)]
    for mp4, _ in dubs:
        cmd += ["-i", str(mp4)]
    for path, _, _, shift in subs:
        cmd += (["-itsoffset", f"{shift:.3f}"] if shift else []) + ["-i", str(path)]
    chain = "".join(
        f"[{i + 1}:a:0]atrim=duration={d:.3f},asetpts=PTS-STARTPTS,apad=whole_dur={d:.3f}[a{i}];"
        for i, (_, d) in enumerate(dubs)
    )
    chain += "".join(f"[a{i}]" for i in range(len(dubs))) + f"concat=n={len(dubs)}:v=0:a=1[dub]"
    cmd += ["-filter_complex", chain, "-map", "0:v:0", "-map", "[dub]", "-map", "0:a:0"]
    for s in src_subs:
        cmd += ["-map", f"0:{s['index']}"]
    for k in range(len(subs)):
        cmd += ["-map", f"{1 + len(dubs) + k}:0"]
    cmd += (
        ["-c:v", "copy"]
        if copy_video
        else ["-c:v", args.encoder, "-cq", "19"]
        if "nvenc" in args.encoder
        else ["-c:v", args.encoder, "-crf", "18"]
    )
    cmd += [
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-c:s",
        "mov_text",
        "-t",
        f"{t1 - t0:.3f}",
        "-disposition:a:0",
        "default",
        "-disposition:a:1",
        "0",
    ]

    def label(spec, lang, title):
        # MP4 players show handler_name as the track name; title for everyone else.
        return [f"-metadata:s:{spec}", f"language={lang}"] + (
            [f"-metadata:s:{spec}", f"title={title}", f"-metadata:s:{spec}", f"handler_name={title}"] if title else []
        )

    cmd += label("a:0", ISO3[tgt], LANGUAGES[tgt].TITLES["dub"][args.voice]) + label(
        "a:1", ISO3[src], LANGUAGES[src].TITLES["original"]
    )
    sub_meta = [
        ((s.get("tags") or {}).get("language", "und"), (s.get("tags") or {}).get("title", "")) for s in src_subs
    ]
    sub_meta += [(lang, title) for _, lang, title, _ in subs]
    for k, (lang, title) in enumerate(sub_meta):
        cmd += label(f"s:{k}", lang, title) + [f"-disposition:s:{k}", "0"]
    cmd += ["-movflags", "+faststart", str(out)]

    print(f"assemble {len(parts)} part(s), {t0:.2f}-{t1:.2f}s -> {out}")
    subprocess.run(cmd, check=True)
    info = probe(out)
    for s in info["streams"]:
        tags = s.get("tags") or {}
        name = tags.get("title") or tags.get("handler_name", "")
        print(f"  #{s['index']} {s['codec_type']:8s} {s['codec_name']:10s} {tags.get('language', ''):4s} {name}")
    print(f"  duration {float(info['format']['duration']):.2f}s")


if __name__ == "__main__":
    main()
