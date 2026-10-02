"""First draft of a clip's script: careful transcript, real timings, suggested cast.

Usage: draft.py <name> [<name> ...] [--cast key,key,...] [--recast]   (after `dub.py prep`)

1. Whisper large-v3 (beam 5, word timings) over the separated vocals.
2. Silero VAD finds where speech really is. Whisper's times run ~0.4 s early,
   so each phrase is snapped to the VAD onset/offset near it, then pulled back
   to the true attack when the VAD missed a soft onset (see edges.py). Speech
   that no phrase covers is re-transcribed on its own, which recovers lines
   the full pass skips.
3. Each phrase is scored against the show's cast in work/voices.json (WeSpeaker
   embeddings, the model that ships with pyannote): the voices built from the
   show's own parts (<show>-NN), or every voice while the show has none yet;
   --cast picks them by hand. A voice is represented by
   its reference clips plus every line already cast to it in the show's other
   scripts, so casting gets surer with each directed part. The best match and
   runner-up are shown, and weak or short matches are flagged with '?'.
4. Speech already in English is found by Whisper's language detection and
   re-transcribed in English (forced Japanese, Whisper silently *translates*
   it). Such lines are tagged [English]: keep the original, caption it. So is
   speech in a third language (Finnish in a film set in Finland), when Whisper
   is also surer of it in that language than in Japanese.
6. When subs.py has read the video's burned-in captions (work/<name>/subs.json),
   each phrase is shown with the captions on screen during it: the
   broadcaster's own text, often with the speaker's name or colour. Phrases
   during a song (♪ captions) are marked to keep the original.
5. A long phrase that is one of Whisper's stock hallucinations usually hides
   real speech, so it's re-transcribed one stretch of speech at a time, in
   Japanese or English, whichever Whisper is surer of.

--recast keeps an existing draft.json's transcript and windows and only
suggests the speakers again: for a new show, draft the first parts, build their
voices (cast.py), then recast them without transcribing again.

Prints the draft and writes work/<name>/draft.json ({"lines": [...]} with start,
end, voice, ja, text="" plus score fields). The director still decides: fix
mishearings, confirm the cast, merge or split lines, then write script.json.
"""
import argparse
import glob
import json
import os
import re
import sys

import numpy as np

import edges
from core import HF_CACHE, JOBS, WORK, load_json, voice_clips, whisper_setup
from gpulock import hold_gpu

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
LANGUAGES = {"en": "English", "fi": "Finnish", "sv": "Swedish", "de": "German", "fr": "French", "it": "Italian",
             "es": "Spanish", "pt": "Portuguese", "ru": "Russian", "zh": "Chinese", "ko": "Korean"}


def vad_spans(audio):
    opts = VadOptions(threshold=0.5, min_silence_duration_ms=120, speech_pad_ms=30)
    return [(s["start"] / SR, s["end"] / SR) for s in get_speech_timestamps(audio, opts)]


def hallucination(text):
    return any(h in text for h in HALLUCINATIONS)


def stretches(spans, a, b, pause=1.5, longest=8.0):
    """The VAD spans inside [a, b], joined across pauses shorter than `pause` s
    into stretches of at most `longest` s."""
    out = []
    for s, e in spans:
        s, e = max(s, a), min(e, b)
        if e - s < 0.2:
            continue
        if out and s - out[-1][1] < pause and e - out[-1][0] <= longest:
            out[-1][1] = e
        else:
            out.append([s, e])
    return out


def spoken_language(model, clip):
    """Whisper's language for a phrase, when it is sure (else None)."""
    lang, prob, _ = model.detect_language(clip)
    return lang if prob >= 0.6 else None


def transcribe(model, audio, offset=0.0, language="ja"):
    """Whisper phrases with their words, as [(start, end, text)] in clip time."""
    segs, _ = model.transcribe(audio, language=language, beam_size=5, vad_filter=False,
                               condition_on_previous_text=False, word_timestamps=True)
    out = []
    for s in segs:
        words = [(w.start + offset, w.end + offset, w.word) for w in (s.words or []) if w.word.strip()]
        if words:
            out.append({**phrase(words), "logprob": s.avg_logprob})
    return out


def confidence(found):
    """Whisper's mean log-probability over transcribed phrases, weighted by length."""
    chars = sum(len(p["ja"]) for p in found)
    return sum(p["logprob"] * len(p["ja"]) for p in found) / chars if chars else -9.0


def surer(model, clip, offset):
    """Transcribe in Japanese, in English and in any third language Whisper
    detects, and keep the language Whisper is surer of, as (language, phrases). Its language detection calls heavily
    accented English Korean or Breton; its confidence doesn't lie that way."""
    best = None
    # A third language competes too, or Whisper translates it into English.
    other = spoken_language(model, clip)
    for lang in ("ja", "en") + ((other,) if other and other not in ("ja", "en") else ()):
        found = transcribe(model, clip, offset, language=lang)
        score = confidence(found)
        if best is None or score > best[0]:
            best = (score, lang, found)
    return best[1], best[2]


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
    hold_gpu()
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


def cast_centroids(embed, keys, exclude=None, per_voice=30):
    """Mean embedding per cast voice, from its reference clips plus every line
    already cast to it in other clips' script.json. The directed parts of a show
    act as a registry that grows with each part (longest lines first, up to
    `per_voice`). A character's extra references (register.py) count as the
    character."""
    voices, audio, samples = load_json(WORK / "voices.json", {}), {}, {}
    base = {k: s.get("register_of", k) for k, s in voices.items()}

    def vocals(clip):
        if clip not in audio:
            seg = load_json(WORK / clip / "segments.json")
            wav = JOBS / seg["job_id"] / "vocals.wav" if seg else None
            audio[clip] = decode_audio(str(wav), sampling_rate=SR) if wav and wav.exists() else None
        return audio[clip]

    wanted = [k for k in voices if base[k] == k and (not keys or k in keys)]
    for key in voices:
        if base[key] in wanted and voices[key].get("clips"):
            samples.setdefault(base[key], []).extend(voice_clips(voices[key]))
    for script in sorted(WORK.glob("*/script.json")):
        if script.parent.name == exclude:
            continue
        for line in load_json(script).get("lines", []):
            who = base.get(line.get("voice"))
            if who in wanted and line["end"] - line["start"] >= 1.0:
                samples.setdefault(who, []).append((script.parent.name, line["start"], line["end"]))
    cents = {}
    for key, items in samples.items():
        vecs = [embed(vocals(clip)[int(a * SR):int(b * SR)])
                for clip, a, b in sorted(items, key=lambda s: s[1] - s[2])[:per_voice]
                if vocals(clip) is not None and b - a >= 0.5]
        if vecs:
            c = np.mean(vecs, axis=0)
            cents[key] = c / np.linalg.norm(c)
            cast_centroids.counts[key] = len(vecs)
    return cents


cast_centroids.counts = {}


def show_of(name):
    return re.sub(r"-\d+$", "", name)


def show_cast(name):
    """The voices built from this show's own parts: a new show's drafts shouldn't be
    scored against every other show's cast once it has a cast of its own."""
    return {k for k, s in load_json(WORK / "voices.json", {}).items()
            if s.get("from") and show_of(s["from"]) == show_of(name)}


def on_screen(subs, p):
    """Captions (from subs.py) shown during a phrase; song markers left out."""
    return [e for e in subs if e["text"] != "♪" and e["start"] < p["end"] + 0.3 and e["end"] > p["start"] - 0.3]


def in_song(subs, p):
    return any(e["text"] == "♪" and e["start"] < p["end"] and e["end"] > p["start"] for e in subs)


def caption(e):
    who = ("≪" if e["off"] else "") + (f"({e['name']})" if e["name"] else "")
    return f"{who}{e['text']} [{e['color']}]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="+", metavar="name", help="one or more prepped clips (the models load once)")
    ap.add_argument("--cast", help="comma-separated voice keys to consider (default: the show's own voices, "
                                   "or all while it has none)")
    ap.add_argument("--model", default="Systran/faster-whisper-large-v3",
                    help="Whisper model (default: large-v3)")
    ap.add_argument("--recast", action="store_true",
                    help="keep each clip's draft.json transcript and only suggest speakers again (after "
                         "building voices for a new show's first drafts)")
    args = ap.parse_args()
    hold_gpu()
    model = None if args.recast else WhisperModel(args.model, device="auto", compute_type="default")
    loaded = []

    def embed_once():
        if not loaded:
            loaded.append(embedder())
        return loaded[0]

    for name in args.names:
        if len(args.names) > 1:
            print(f"=== {name}")
        draft(name, args.cast, model, embed_once)


def draft(name, cast, model, embed_once):
    work = WORK / name
    job = load_json(work / "segments.json")["job_id"]
    audio = decode_audio(str(JOBS / job / "vocals.wav"), sampling_rate=SR)
    duration = len(audio) / SR
    if model is None:
        # --recast: the transcript and windows stay; only the speakers are suggested again.
        phrases = [{k: v for k, v in line.items() if k not in ("voice", "score", "alt", "alt_score", "text")}
                   for line in load_json(work / "draft.json")["lines"]]
        return suggest(name, work, audio, phrases, vad_spans(audio), cast, embed_once)

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
    # A hallucination over a long stretch hides real speech (a whole phone call
    # in accented English became "ご視聴ありがとうございました"): redo it a
    # stretch at a time, in whichever language Whisper is surer of.
    for p in [p for p in phrases if hallucination(p["ja"]) and p["end"] - p["start"] > 3.0]:
        phrases.remove(p)
        for a, b in stretches(spans, p["start"], p["end"]):
            lang, found = surer(model, audio[int(a * SR):int(b * SR)], a)
            for q in found:
                if not hallucination(q["ja"]):
                    phrases.append({**snap(q, spans, duration), "recovered": True,
                                    **({"lang": lang} if lang != "ja" else {})})
    phrases = settle(phrases)
    # Speech already in English, or in a third language (a film set abroad):
    # forced Japanese, Whisper translates it.
    for p in phrases:
        if p.get("lang") or p["end"] - p["start"] < 1.0:
            continue
        clip = audio[int(p["start"] * SR):int(p["end"] * SR)]
        lang = spoken_language(model, clip)
        if lang == "en":
            p["lang"] = "en"
            p["ja"] = " ".join(q["ja"] for q in transcribe(model, clip, language="en")) or p["ja"]
        elif lang and lang != "ja":
            # Less common languages get a second opinion: kept only when Whisper
            # transcribes the phrase more surely in that language than in Japanese.
            found = transcribe(model, clip, language=lang)
            if found and confidence(found) > confidence(transcribe(model, clip)):
                p["lang"] = lang
                p["ja"] = " ".join(q["ja"] for q in found)
    # Start windows at the true attack. VAD misses soft onsets, and a window
    # that opens mid-sound chops the actor audibly (edges.py has the rule).
    db = edges.frames_db(audio)
    for i, p in enumerate(phrases):
        floor_t = max(phrases[i - 1]["end"] if i else 0.0, p["start"] - 0.3)
        level = edges.line_level(db, p["start"], p["end"])
        onset = edges.voiced_onset(db, p["start"], floor_t, level)
        k = int(p["start"] * SR / edges.HOP)
        if onset is not None and edges.MIN_SHIFT <= p["start"] - onset <= edges.MAX_SHIFT \
                and max(db[k - 2:k]) - level >= edges.CUT_DB:
            p["start"] = round(onset, 2)
    suggest(name, work, audio, phrases, spans, cast, embed_once)


def suggest(name, work, audio, phrases, spans, cast, embed_once):
    """Score each phrase against the cast, print the draft and write draft.json."""
    embed = embed_once()
    cents = cast_centroids(embed, set(cast.split(",")) if cast else show_cast(name) or None, exclude=name) \
        if embed else {}
    for p in phrases:
        if not cents or p["end"] - p["start"] < 0.3:
            continue
        v = embed(audio[int(p["start"] * SR):int(p["end"] * SR)])
        ranked = sorted(((float(v @ c), k) for k, c in cents.items()), reverse=True)
        p["voice"], p["score"] = ranked[0][1], round(ranked[0][0], 2)
        if len(ranked) > 1:
            p["alt"], p["alt_score"] = ranked[1][1], round(ranked[1][0], 2)

    subs = load_json(work / "subs.json", {}).get("events", [])
    print("cast considered: " + (", ".join(f"{k} ({cast_centroids.counts[k]})" for k in cents)
                                 or "(none – no usable voices yet)"))
    for i, p in enumerate(phrases):
        sure = p.get("score", 0) >= 0.3 and p["end"] - p["start"] >= 0.6 and \
            p.get("score", 0) - p.get("alt_score", -1) >= 0.15
        who = f"{p.get('voice', '-'):>10s} {p.get('score', 0):4.2f}{' ' if sure else '?'}"
        alt = f"({p['alt']} {p['alt_score']:.2f})" if "alt" in p else ""
        tag = " [recovered]" if p.get("recovered") else ""
        if p.get("lang"):
            tag += f" [{LANGUAGES.get(p['lang'], p['lang'])}: keep the original, caption it]"
        if hallucination(p["ja"]):
            p["suspect"] = True
            tag += " [likely Whisper hallucination]"
        if in_song(subs, p):
            # Lyrics stay in the original audio; don't copy them into the notes.
            p["song"] = True
            print(f"L{i:02d} {p['start']:7.2f}-{p['end']:7.2f}  [song: keep the original]")
            continue
        print(f"L{i:02d} {p['start']:7.2f}-{p['end']:7.2f}  {who} {alt:18s} {p['ja']}{tag}")
        if on_screen(subs, p):
            p["cc"] = " / ".join(caption(e) for e in on_screen(subs, p))
            print(f"{'':45s}cc: {p['cc']}")
    shown = {id(e) for p in phrases for e in on_screen(subs, p)}
    missed = [e for e in subs if e["text"] != "♪" and id(e) not in shown and not in_song(subs, e)]
    if missed:
        print("captions outside every line (missed speech, or a caption shown early/late):")
        for e in missed:
            print(f"    {e['start']:7.2f}-{e['end']:7.2f}  {caption(e)}")
    gaps = uncovered(phrases, spans)
    if gaps:
        print("speech not in any line (hums/laughs, or missed words):",
              " ".join(f"{a:.2f}-{b:.2f}" for a, b in gaps))
    lines = [{"start": p["start"], "end": p["end"], "voice": p.get("voice", ""), "ja": p["ja"], "text": "",
              **{k: p[k] for k in ("score", "alt", "alt_score", "recovered", "suspect", "lang", "cc", "song") if k in p}}
             for p in phrases]
    (work / "draft.json").write_text(json.dumps({"lines": lines}, ensure_ascii=False, indent=1),
                                     encoding="utf-8")


if __name__ == "__main__":
    main()
