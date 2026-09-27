"""First draft of a clip's script: careful transcript, real timings, suggested cast.

Usage: draft.py <name> [--cast key,key,...]   (after `dub.py prep`)

1. Whisper large-v3 (beam 5, word timings) over the separated vocals.
2. Silero VAD finds where speech really is. Whisper's times run ~0.4 s early,
   so each phrase is snapped to the VAD onset/offset near it. Speech that no
   phrase covers is re-transcribed on its own, which recovers lines the full
   pass skips.
3. Each phrase is scored against every cast voice in work/voices.json (WeSpeaker
   embeddings, the model that ships with pyannote). The best match and runner-up
   are shown, and weak or short matches are flagged with '?'.

Prints the draft and writes work/<name>/draft.json ({"lines": [...]} with start,
end, voice, ja, text="" plus score fields). The director still decides: fix
mishearings, confirm the cast, merge or split lines, then write script.json.
"""
import argparse
import glob
import json
import os
import sys

import numpy as np

from dub import HF_CACHE, JOBS, WORK, load_json, whisper_setup

whisper_setup()
import torch  # noqa: E402
from faster_whisper import WhisperModel  # noqa: E402
from faster_whisper.audio import decode_audio  # noqa: E402
from faster_whisper.vad import VadOptions, get_speech_timestamps  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SR = 16000
EARLY = 0.35  # typical Whisper lead when no VAD span is near
# Whisper's stock hallucinations on hums, music and silence.
HALLUCINATIONS = ("ご視聴ありがとうございました", "チャンネル登録", "ご覧いただき", "字幕")


def vad_spans(audio):
    opts = VadOptions(threshold=0.5, min_silence_duration_ms=120, speech_pad_ms=30)
    return [(s["start"] / SR, s["end"] / SR) for s in get_speech_timestamps(audio, opts)]


def transcribe(model, audio, offset=0.0):
    """Whisper phrases with their words, as [(start, end, text)] in clip time."""
    segs, _ = model.transcribe(audio, language="ja", beam_size=5, vad_filter=False,
                               condition_on_previous_text=False, word_timestamps=True)
    out = []
    for s in segs:
        words = [(w.start + offset, w.end + offset, w.word) for w in (s.words or []) if w.word.strip()]
        if words:
            out.append(phrase(words))
    return out


def phrase(words):
    return {"start": words[0][0], "end": words[-1][1], "ja": "".join(w[2] for w in words).strip(),
            "words": words}


def snap(p, spans, duration):
    """Move a Whisper phrase onto the speech the VAD found near it."""
    near = [s for s in spans if s[1] > p["start"] - 0.3 and s[0] < p["end"] + 0.8]
    starts = [a for a, _ in near if p["start"] - 0.3 <= a <= p["start"] + 0.8]
    ends = [b for a, b in near if a < p["end"] + 0.4]
    # The onset nearest where Whisper's (early) start says speech should begin.
    expect = p["start"] + EARLY
    p["start"] = round(min(starts, key=lambda a: abs(a - expect)) if starts else expect, 2)
    p["end"] = round(min(duration, max([p["end"] + EARLY] + ends)), 2)
    return p


def settle(phrases):
    """Time order, no overlaps."""
    phrases.sort(key=lambda p: p["start"])
    for a, b in zip(phrases, phrases[1:]):
        a["end"] = min(a["end"], b["start"])
    return phrases


def uncovered(phrases, spans, min_len=0.3):
    gaps = []
    for a, b in spans:
        cur = a
        for p in phrases:
            if p["end"] <= cur or p["start"] >= b:
                continue
            if p["start"] - cur >= min_len:
                gaps.append((cur, p["start"]))
            cur = max(cur, p["end"])
        if b - cur >= min_len:
            gaps.append((cur, b))
    return gaps


def embedder():
    from pyannote.audio import Inference, Model
    from pyannote.audio.core.task import Problem, Resolution, Specifications
    # pyannote 3.4's from_pretrained breaks on huggingface_hub 1.x: load the file.
    torch.serialization.add_safe_globals([torch.torch_version.TorchVersion, Problem, Resolution, Specifications])
    ckpt = glob.glob(os.path.join(HF_CACHE, "models--pyannote--wespeaker-voxceleb-resnet34-LM",
                                  "snapshots", "*", "pytorch_model.bin"))
    if not ckpt:
        return None
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    inf = Inference(Model.from_pretrained(ckpt[0]), window="whole", device=dev)

    def embed(audio16k):
        wav = torch.from_numpy(np.ascontiguousarray(audio16k)).float()[None]
        v = np.asarray(inf({"waveform": wav, "sample_rate": SR})).reshape(-1)
        return v / np.linalg.norm(v)
    return embed


def cast_centroids(embed, keys):
    """Mean embedding of each voice's reference clips (from their source jobs)."""
    voices, cents = load_json(WORK / "voices.json", {}), {}
    for key, spec in voices.items():
        if keys and key not in keys:
            continue
        seg = load_json(WORK / spec.get("from", "") / "segments.json") if "from" in spec else None
        wav = JOBS / seg["job_id"] / "vocals.wav" if seg else None
        if not wav or not wav.exists():
            continue
        audio = decode_audio(str(wav), sampling_rate=SR)
        vecs = [embed(audio[int(a * SR):int(b * SR)]) for a, b in spec["clips"] if b - a >= 0.5]
        if vecs:
            c = np.mean(vecs, axis=0)
            cents[key] = c / np.linalg.norm(c)
    return cents


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--cast", help="comma-separated voice keys to consider (default: all usable)")
    args = ap.parse_args()
    work = WORK / args.name
    job = load_json(work / "segments.json")["job_id"]
    audio = decode_audio(str(JOBS / job / "vocals.wav"), sampling_rate=SR)
    duration = len(audio) / SR

    model = WhisperModel("Systran/faster-whisper-large-v3", device="auto", compute_type="default")
    spans = vad_spans(audio)
    phrases = settle([snap(p, spans, duration) for p in transcribe(model, audio)])
    # Focused passes (with 1 s of context) over speech the full pass skipped;
    # keep only the words that land in the gap.
    for a, b in uncovered(phrases, spans):
        lo, hi = max(0.0, a - 1.0), min(duration, b + 1.0)
        for p in transcribe(model, audio[int(lo * SR):int(hi * SR)], lo):
            words = [w for w in p["words"] if a - 0.2 <= (w[0] + w[1]) / 2 + EARLY <= b + 0.2]
            if words:
                phrases.append({**snap(phrase(words), spans, duration), "recovered": True})
    phrases = settle(phrases)
    del model

    embed = embedder()
    cents = cast_centroids(embed, set(args.cast.split(",")) if args.cast else None) if embed else {}
    for p in phrases:
        if not cents or p["end"] - p["start"] < 0.3:
            continue
        v = embed(audio[int(p["start"] * SR):int(p["end"] * SR)])
        ranked = sorted(((float(v @ c), k) for k, c in cents.items()), reverse=True)
        p["voice"], p["score"] = ranked[0][1], round(ranked[0][0], 2)
        if len(ranked) > 1:
            p["alt"], p["alt_score"] = ranked[1][1], round(ranked[1][0], 2)

    print(f"cast considered: {', '.join(cents) or '(none – no usable voices yet)'}")
    for i, p in enumerate(phrases):
        sure = p.get("score", 0) >= 0.3 and p["end"] - p["start"] >= 0.6 and \
            p.get("score", 0) - p.get("alt_score", -1) >= 0.15
        who = f"{p.get('voice', '-'):>10s} {p.get('score', 0):4.2f}{' ' if sure else '?'}"
        alt = f"({p['alt']} {p['alt_score']:.2f})" if "alt" in p else ""
        tag = " [recovered]" if p.get("recovered") else ""
        if any(h in p["ja"] for h in HALLUCINATIONS):
            p["suspect"] = True
            tag += " [likely Whisper hallucination]"
        print(f"L{i:02d} {p['start']:7.2f}-{p['end']:7.2f}  {who} {alt:18s} {p['ja']}{tag}")
    gaps = uncovered(phrases, spans)
    if gaps:
        print("speech not in any line (hums/laughs, or missed words):",
              " ".join(f"{a:.2f}-{b:.2f}" for a, b in gaps))
    lines = [{"start": p["start"], "end": p["end"], "voice": p.get("voice", ""), "ja": p["ja"], "text": "",
              **{k: p[k] for k in ("score", "alt", "alt_score", "recovered", "suspect") if k in p}}
             for p in phrases]
    (work / "draft.json").write_text(json.dumps({"lines": lines}, ensure_ascii=False, indent=1),
                                     encoding="utf-8")


if __name__ == "__main__":
    main()
