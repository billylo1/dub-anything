"""Put the finished dubs into the source video itself, renamed "<name> (dubbed)".

Usage: deliver.py <source video> <voice>=<assembled.mp4> [<voice>=<assembled.mp4> ...] [--default VOICE]

Each <assembled.mp4> is assemble.py's output for one voice ("actor" or
"american"), with its .en.srt and .ja.srt beside it, and its .en.ass and
.signs.ass when the parts translate text in the picture. The result keeps every
stream of the source as it is (video, audio, its own subtitles) and adds, in
this order:
  - audio: one English dub per voice, the default voice's first and marked
    default (some players ignore the flag and play the first track), then the
    source's own audio;
  - subtitles: English per voice (the default voice's first and on by
    default), the corrected Japanese transcript, then the source's own.
With signs, the English tracks are the styled ones (signs placed by the text
they translate), a signs-only track follows them, marked forced for players
that show forced subtitles when subtitles are off, and the file becomes
Matroska ("<name> (dubbed).mkv"): MP4's subtitles can't be placed.
Nothing is re-encoded; the new subtitles are written in the container's format.
Every copied stream is checked against its input (MD5) before the new file
replaces the source; the source's name gets " (dubbed)" (once). Delivering
again to a "(dubbed)" file replaces the dub tracks it already has.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

from core import VOICE_LABEL

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SUFFIX = " (dubbed)"
JA_TITLE = "日本語 (transcript)"
SIGNS_TITLE = "English (signs only)"
OURS = re.compile(r"^(English dub \(.*\)|English \(.* dub\)|English \(signs only\)|日本語 \(transcript\))$")


def dub_title(voice):
    return f"English dub ({VOICE_LABEL[voice]})"


def sub_title(voice):
    return {"actor": "English (actor-voice dub)", "american": "English (American dub)"}[voice]


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration:stream=index,codec_type,codec_name:stream_tags=language,title,handler_name"
                          ":stream_disposition=attached_pic", "-of", "json", str(path)],
                         capture_output=True, encoding="utf-8", check=True).stdout
    return json.loads(out)


GENERIC = {"SoundHandler", "VideoHandler", "SubtitleHandler", "Core Media Audio", "Core Media Video",
           "ISO Media file produced by Google Inc.", "Mainconcept MP4 Sound Media Handler"}


def name_of(stream):
    """A track's name, or "" for the generic handler names muxers write."""
    tags = stream.get("tags") or {}
    name = tags.get("title") or tags.get("handler_name") or ""
    return "" if name in GENERIC else name


def md5(path, index):
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", f"0:{index}", "-c", "copy", "-f", "md5", "-"],
                          capture_output=True, encoding="utf-8", check=True).stdout.strip()


def delivered_name(source, mkv=False):
    stem = source.stem[:-len(SUFFIX)] if source.stem.endswith(SUFFIX) else source.stem
    ext = source.suffix.lower() if source.suffix.lower() in (".mp4", ".m4v", ".mov", ".mkv") and not mkv else ".mkv"
    return source.with_name(f"{stem}{SUFFIX}{ext}")


def deliver(source, dubs, default, source_lang="jpn"):
    """dubs: {voice: assembled mp4}. Returns the delivered file's path."""
    source = pathlib.Path(source)
    voices = sorted(dubs, key=lambda v: v != default)
    src = probe(source)
    duration = float(src["format"]["duration"])
    for v in voices:
        d = float(probe(dubs[v])["format"]["duration"])
        if abs(d - duration) > 0.1:
            raise SystemExit(f"{dubs[v]} runs {d:.2f} s, the source {duration:.2f} s: assemble it from the same video")
    keep = [s for s in src["streams"] if not OURS.match(name_of(s))]
    dropped = len(src["streams"]) - len(keep)
    vids = [s for s in keep if s["codec_type"] == "video"]
    auds = [s for s in keep if s["codec_type"] == "audio"]
    subs = [s for s in keep if s["codec_type"] == "subtitle"]
    signs = dubs[voices[0]].with_suffix(".signs.ass")
    signs = signs if signs.exists() else None
    out = delivered_name(source, mkv=bool(signs))
    tmp = out.with_name(out.stem + ".delivering" + out.suffix)
    mkv = out.suffix == ".mkv"

    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(source)]
    for v in voices:
        cmd += ["-i", str(dubs[v])]
    srts = [(dubs[v].with_suffix(".en.ass" if signs else ".en.srt"), "eng", sub_title(v)) for v in voices]
    if signs:
        srts.append((signs, "eng", SIGNS_TITLE))
    ja = dubs[voices[0]].with_suffix(".ja.srt")
    if ja.exists():
        srts.append((ja, "jpn", JA_TITLE))
    for path, _, _ in srts:
        if not path.exists():
            raise SystemExit(f"no subtitles at {path}")
        cmd += ["-i", str(path)]
    for s in vids:
        cmd += ["-map", f"0:{s['index']}"]
    for k in range(len(voices)):
        cmd += ["-map", f"{1 + k}:a:0"]
    for s in auds:
        cmd += ["-map", f"0:{s['index']}"]
    for k in range(len(srts)):
        cmd += ["-map", f"{1 + len(voices) + k}:0"]
    for s in subs:
        cmd += ["-map", f"0:{s['index']}"]
    cmd += ["-c", "copy"]
    for k, (path, _, _) in enumerate(srts):  # ours are .srt and .ass files
        cmd += [f"-c:s:{k}", "ass" if path.suffix == ".ass" else "srt" if mkv else "mov_text"]
    for k, s in enumerate(subs, len(srts)):  # the source's own stay as they are, unless Matroska can't hold them
        if mkv and s["codec_name"] == "mov_text":
            cmd += [f"-c:s:{k}", "srt"]

    def label(spec, lang, title):
        return [f"-metadata:s:{spec}", f"language={lang}", f"-metadata:s:{spec}", f"title={title}",
                f"-metadata:s:{spec}", f"handler_name={title}"]
    for k, v in enumerate(voices):
        cmd += label(f"a:{k}", "eng", dub_title(v)) + [f"-disposition:a:{k}", "default" if k == 0 else "0"]
    for k, s in enumerate(auds, len(voices)):
        tags = s.get("tags") or {}
        lang = tags.get("language") if tags.get("language") not in (None, "und") else source_lang
        cmd += label(f"a:{k}", lang, name_of(s) or "日本語 (original)") + [f"-disposition:a:{k}", "0"]
    for k, (_, lang, title) in enumerate(srts):
        cmd += label(f"s:{k}", lang, title) + [f"-disposition:s:{k}", "default" if k == 0 else
                                               "forced" if title == SIGNS_TITLE else "0"]
    for k, s in enumerate(subs, len(srts)):
        cmd += [f"-disposition:s:{k}", "0"]
    if not mkv:
        cmd += ["-movflags", "+faststart"]
    cmd.append(str(tmp))
    print(f"deliver {', '.join(voices)} into {source.name}" + (f" (replacing {dropped} earlier dub tracks)" if dropped else ""))
    subprocess.run(cmd, check=True)

    # Every stream copied, not converted, must match its input exactly.
    got = probe(tmp)["streams"]
    by_type = {t: [s for s in got if s["codec_type"] == t] for t in ("video", "audio", "subtitle")}
    checks = [(source, s["index"], by_type["video"][k]["index"]) for k, s in enumerate(vids)]
    checks += [(dubs[v], "a:0", by_type["audio"][k]["index"]) for k, v in enumerate(voices)]
    checks += [(source, s["index"], by_type["audio"][len(voices) + k]["index"]) for k, s in enumerate(auds)]
    for path, index, out_index in checks:
        if md5(path, index) != md5(tmp, out_index):
            tmp.unlink(missing_ok=True)
            raise SystemExit(f"stream {index} of {path.name} changed in the copy; the source is untouched")
    if len(by_type["subtitle"]) != len(srts) + len(subs):
        tmp.unlink(missing_ok=True)
        raise SystemExit("subtitle tracks went missing; the source is untouched")
    tmp.replace(out)
    if out != source:
        source.unlink()
    for s in got:
        print(f"  #{s['index']} {s['codec_type']:8s} {s['codec_name']:10s} {(s.get('tags') or {}).get('language', ''):4s}"
              f" {name_of(s)}")
    print(f"  wrote {out} (every copied stream verified)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("dubs", nargs="+", metavar="voice=assembled.mp4")
    ap.add_argument("--default", help="the voice that plays first (default: actor if given, else the first)")
    args = ap.parse_args()
    dubs = {}
    for d in args.dubs:
        voice, _, path = d.partition("=")
        if voice not in VOICE_LABEL or not path:
            raise SystemExit(f"{d}: expected actor=FILE or american=FILE")
        dubs[voice] = pathlib.Path(path)
    default = args.default or ("actor" if "actor" in dubs else next(iter(dubs)))
    deliver(args.source, dubs, default)


if __name__ == "__main__":
    main()
