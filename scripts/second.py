"""Second opinions on a clip's transcript from two other recognisers, to catch Whisper's mishearings.

Usage (needs sherpa-onnx, so run it in its own env):
  uv run --no-project --python 3.12 --with sherpa-onnx --with soundfile --with scipy
      --with pykakasi --with huggingface_hub python scripts/second.py <clip> [<clip> ...] [--script] [--all]
  ... scripts/second.py <clip> <t0>:<t1> [<t0>:<t1> ...]      any stretches of the clip
It runs on the CPU, so it can run beside GPU work (drafting, rendering).

Runs ReazonSpeech k2 v2 (trained on Japanese TV) and SenseVoice over each line of
work/<clip>/draft.json (or script.json with --script) on the separated vocals,
and compares them with the line's `ja` as kana, so spelling choices don't count.
A part dubbed from English (its segments.json's source_lang) gets NVIDIA's
Parakeet TDT 0.6B v2 and SenseVoice in English instead, compared with the line's
`en` word by word. A line is flagged when the two agree with each other more
than either agrees with Whisper, and differ from it by 30% or more. Treat
agreement as a review signal: re-listen with asr_words.py and decide from context.
The models (~850 MB for Japanese, ~1 GB for English) download to VoiceStudio's
model cache on first use.
"""
import argparse
import json
import os
import pathlib
import re
import sys

import numpy as np
import pykakasi
import sherpa_onnx
import soundfile as sf
from huggingface_hub import snapshot_download
from scipy.signal import resample_poly

# Same resolution as core.py.
WORK = pathlib.Path(__file__).resolve().parents[1] / "work"
JOBS = pathlib.Path(os.environ.get("OMNIVOICE_DATA_DIR")
                    or pathlib.Path(os.environ.get("APPDATA", "")) / "OmniVoice") / "dub_jobs"
HF_CACHE = (os.environ.get("HF_HUB_CACHE") or os.environ.get("OMNIVOICE_CACHE_DIR")
            or str(pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "OmniVoice" / "hf_cache"))
SR = 16000
PAD = 0.15  # s around each line
FLAG = 0.3
PUNCT = "、。,.!?！？…・「」『』()（）ー〜~-—:：;；\"' 　"
sys.stdout.reconfigure(encoding="utf-8")
kks = pykakasi.kakasi()


def recognisers(lang):
    """Two recognisers for the language, by name."""
    sv = snapshot_download("csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17", cache_dir=HF_CACHE,
                           allow_patterns=["model.int8.onnx", "tokens.txt"])
    part = lambda d, f: os.path.join(d, f)  # noqa: E731
    sensevoice = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=part(sv, "model.int8.onnx"), tokens=part(sv, "tokens.txt"), num_threads=8, language=lang, use_itn=True)
    if lang == "en":
        pk = snapshot_download("csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8", cache_dir=HF_CACHE,
                               allow_patterns=["*.int8.onnx", "tokens.txt"])
        return {"parakeet": sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=part(pk, "encoder.int8.onnx"), decoder=part(pk, "decoder.int8.onnx"),
            joiner=part(pk, "joiner.int8.onnx"), tokens=part(pk, "tokens.txt"), num_threads=8, sample_rate=SR,
            feature_dim=128, model_type="nemo_transducer", decoding_method="greedy_search"),
            "sensevoice": sensevoice}
    rz = snapshot_download("reazon-research/reazonspeech-k2-v2", cache_dir=HF_CACHE,
                           allow_patterns=["*-epoch-99-avg-1.onnx", "tokens.txt"])
    return {
        "reazon": sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=part(rz, "encoder-epoch-99-avg-1.onnx"), decoder=part(rz, "decoder-epoch-99-avg-1.onnx"),
            joiner=part(rz, "joiner-epoch-99-avg-1.onnx"), tokens=part(rz, "tokens.txt"), num_threads=8,
            sample_rate=SR, feature_dim=80, decoding_method="modified_beam_search", max_active_paths=4),
        "sensevoice": sensevoice,
    }


def kana(text):
    return "".join(c for c in "".join(x["hira"] for x in kks.convert(text)) if c not in PUNCT)


def units(text, lang):
    """What two transcripts are compared by: kana for Japanese, lowercase words else."""
    return kana(text) if lang == "ja" else re.findall(r"[a-z0-9]+", text.lower().replace("'", "").replace("’", ""))


def dist(a, b, lang="ja"):
    """Edit distance between two texts (as kana, or words), relative to the longer."""
    a, b = units(a, lang), units(b, lang)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] / max(1, len(a), len(b))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="+", metavar="clip",
                    help="one or more clips (the models load once), or one clip followed by t0:t1 stretches")
    ap.add_argument("--script", action="store_true", help="check script.json instead of draft.json")
    ap.add_argument("--all", action="store_true", help="print every line, not just flagged ones")
    args = ap.parse_args()
    clips = [t for t in args.targets if ":" not in t]
    spans = [t for t in args.targets if ":" in t]
    if spans and len(clips) != 1:
        ap.error("t0:t1 stretches need exactly one clip")
    recs = {}
    for clip in clips:
        lang = source_lang(clip)
        if lang not in ("ja", "en"):
            print(f"{clip}: no second opinions for {lang}")
            continue
        if lang not in recs:
            recs[lang] = recognisers(lang)
        check(clip, spans, recs[lang], lang, args.script, args.all)


def source_lang(clip):
    """The language a part speaks, as dub.py prep recorded it (core.langs)."""
    return json.loads((WORK / clip / "segments.json").read_text(encoding="utf-8")).get("source_lang") or "ja"


def check(clip, spans, recs, lang, script, show_all):
    job = json.loads((WORK / clip / "segments.json").read_text(encoding="utf-8"))["job_id"]
    x, sr = sf.read(JOBS / job / "vocals.wav", dtype="float32")
    x = resample_poly(x.mean(1) if x.ndim > 1 else x, SR, sr).astype(np.float32)
    if spans:
        lines = [{**dict(zip(("start", "end"), map(float, s.split(":")))), lang: ""} for s in spans]
    else:
        name = "script.json" if script else "draft.json"
        lines = json.loads((WORK / clip / name).read_text(encoding="utf-8"))["lines"]
    flagged = 0
    for i, line in enumerate(lines):
        a, b = max(0.0, line["start"] - PAD), line["end"] + PAD
        clip_audio = x[int(a * SR):int(b * SR)]
        if len(clip_audio) < 0.1 * SR or line.get("lang"):
            continue  # a draft line at (or past) the clip's very end, or not in the source language
        got = {}
        for name, rec in recs.items():
            s = rec.create_stream()
            s.accept_waveform(SR, clip_audio)
            try:
                rec.decode_stream(s)
                got[name] = s.result.text.strip()
            except RuntimeError:  # the model rejects an odd-shaped stretch; skip that opinion
                got[name] = ""
        whisper = line.get(lang, "")
        one, two = list(recs)
        agree = dist(got[one], got[two], lang)
        gap = min(dist(whisper, got[one], lang), dist(whisper, got[two], lang))
        flag = bool(whisper) and gap >= FLAG and agree < gap
        flagged += flag
        if flag or show_all or spans:
            print(f"{'FLAG' if flag else '    '} L{i:02d} {line['start']:7.2f}-{line['end']:7.2f}  {whisper}")
            print(f"{'':25s}{one}: {got[one]}   {two}: {got[two]}")
    if not spans:
        print(f"{clip}: {flagged} of {len(lines)} lines where the other two agree against the transcript")


if __name__ == "__main__":
    main()
