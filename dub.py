"""Drive VoiceStudio's dubbing backend (http://127.0.0.1:3900) headlessly.

  prep <video> [--speakers N]   upload, separate vocals, transcribe + diarize;
                                writes work/<name>/segments.json and prints the transcript
  generate <name>               reads work/<name>/script.json (hand-directed lines:
                                start/end/voice/text, optional instruct/effect;
                                voices from work/voices.json) or else en.json
                                ({segment_id: English text}), synthesizes cloned
                                English speech, exports MP4 + SRT
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
import time

import numpy as np
import requests
import soundfile as sf

API = os.environ.get("VOICESTUDIO_API", "http://127.0.0.1:3900")
HERE = pathlib.Path(__file__).resolve().parent
WORK = HERE / "work"
# Same resolution as VoiceStudio's backend/core/config.py.
JOBS = pathlib.Path(os.environ.get("OMNIVOICE_DATA_DIR")
                    or pathlib.Path(os.environ.get("APPDATA", "")) / "OmniVoice") / "dub_jobs"
HF_CACHE = (os.environ.get("HF_HUB_CACHE") or os.environ.get("OMNIVOICE_CACHE_DIR")
            or str(pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "OmniVoice" / "hf_cache"))


def whisper_setup():
    """Prepare a standalone faster-whisper: reuse VoiceStudio's downloaded models
    and preload the cuDNN 8 DLLs CTranslate2 needs on CUDA (PyTorch ships cuDNN 9;
    the backend does the same in backend/core/cudnn8.py)."""
    os.environ.setdefault("HF_HUB_CACHE", HF_CACHE)
    lib = pathlib.Path(sys.prefix) / "Lib" / "site-packages" / "cudnn8_compat" / "nvidia" / "cudnn" / "bin"
    if sys.platform == "win32" and lib.is_dir():
        import ctypes
        whisper_setup.cookie = os.add_dll_directory(str(lib))  # must stay alive
        for dll in sorted(lib.glob("cudnn*64_8.dll")):
            ctypes.CDLL(str(dll))


def out_dir(doc):
    """Finished dubs go to a 'dubbed' folder beside the source video (or $DUB_OUT)."""
    return pathlib.Path(os.environ.get("DUB_OUT") or pathlib.Path(doc["video"]).parent / "dubbed")

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def sse(resp):
    """Yield (event, data) pairs from a server-sent-events response."""
    resp.encoding = "utf-8"
    event, data = None, []
    for line in resp.iter_lines(decode_unicode=True):
        if line is None:
            continue
        if line == "":
            if data:
                raw = "\n".join(data)
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = raw
                yield event or "message", payload
            event, data = None, []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())


def wait_task(task_id, terminal):
    """Follow /tasks/stream until a terminal event; raise on error/cancel."""
    t0 = time.time()
    with requests.get(f"{API}/tasks/stream/{task_id}", params={"after_seq": 0},
                      stream=True, timeout=(10, 3600)) as r:
        r.raise_for_status()
        for _, d in sse(r):
            kind = d.get("type") if isinstance(d, dict) else None
            if kind in terminal:
                print(f"  [{time.time() - t0:5.0f}s] {kind}")
                return d
            if kind in ("error", "cancelled"):
                raise RuntimeError(f"task {task_id} {kind}: {json.dumps(d, ensure_ascii=False)}")
            if kind == "progress":
                print(f"  [{time.time() - t0:5.0f}s] {d.get('current')}/{d.get('total')} {d.get('text', '')[:60]}")
            elif kind in ("warning", "routing_notice"):
                print(f"  [{time.time() - t0:5.0f}s] {kind}: {json.dumps(d, ensure_ascii=False)[:300]}")
            elif kind and kind != "demucs_progress":
                print(f"  [{time.time() - t0:5.0f}s] {kind}")
    raise RuntimeError(f"task {task_id} stream closed without {terminal}")


def srt_time(t):
    ms = round(t * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def ensure_diarizer(prefer="pyannote"):
    """VoiceStudio can fall back to Sortformer after installs or restarts, which
    can't handle clips over 120 s; switch to pyannote when it is installed."""
    try:
        d = requests.get(f"{API}/engines/diarisation", timeout=30).json()
    except (requests.RequestException, ValueError):
        return
    opt = next((o for o in d.get("options", []) if o.get("id") == prefer), None)
    if d.get("active") != prefer and opt and opt.get("installed"):
        requests.post(f"{API}/engines/diarisation/select", json={"engine_id": prefer}, timeout=30)
        print(f"  diarisation engine: {d.get('active')} -> {prefer}")


def cmd_prep(args):
    video = pathlib.Path(args.video)
    work = WORK / video.stem
    work.mkdir(parents=True, exist_ok=True)

    print(f"upload {video.name}")
    with open(video, "rb") as f:
        r = requests.post(f"{API}/dub/upload",
                          files={"video": (video.name, f, "video/mp4")},
                          data={"source_lang": "ja", "input_type": "video"}, timeout=300)
    r.raise_for_status()
    job = r.json()
    job_id = job["job_id"]
    print(f"  job {job_id}")
    wait_task(job["task_id"], {"ready"})

    print("transcribe + diarize")
    ensure_diarizer()
    params = {"per_segment_refs": "true"}
    if args.speakers:
        params["num_speakers"] = args.speakers
    final, t0 = None, time.time()
    with requests.get(f"{API}/dub/transcribe-stream/{job_id}", params=params,
                      stream=True, timeout=(10, 3600)) as r:
        r.raise_for_status()
        for ev, d in sse(r):
            if ev == "final":
                final = d
            elif ev == "warning":
                print(f"  [{time.time() - t0:5.0f}s] warning: {json.dumps(d, ensure_ascii=False)[:300]}")
            elif ev == "error":
                raise RuntimeError(f"transcribe error: {json.dumps(d, ensure_ascii=False)}")
            elif ev == "start":
                print(f"  [{time.time() - t0:5.0f}s] start {d}")
            elif ev == "done":
                break
    if final is None:
        raise RuntimeError("transcription ended without a final event")
    print(f"  [{time.time() - t0:5.0f}s] final: {len(final['segments'])} segments")

    doc = {"job_id": job_id, "video": str(video), "source_lang": final.get("source_lang"),
           "speaker_clones": final.get("speaker_clones"), "segments": final["segments"]}
    (work / "segments.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    with open(work / "ja.srt", "w", encoding="utf-8") as f:
        for i, s in enumerate(final["segments"], 1):
            f.write(f"{i}\n{srt_time(s['start'])} --> {srt_time(s['end'])}\n"
                    f"[{s.get('speaker_id')}] {s['text']}\n\n")
    for s in final["segments"]:
        print(f"{s['id']}  {s['start']:6.2f}-{s['end']:6.2f}  {s.get('speaker_id')}  "
              f"{s.get('profile_id') or '-'}  {s['text']}")


def load_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def voice_sig(key):
    """Fingerprint of a voice's spec in work/voices.json (clips, text, source)."""
    spec = load_json(WORK / "voices.json", {})[key]
    return hashlib.sha1(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def voice_clips(spec):
    """A voice's reference clips as (clip, start, end). An entry is [start, end]
    in the voice's "from" clip, or ["clip", start, end] from any prepped clip,
    so a bit part can pool its clean lines from several parts."""
    return [tuple(c) if len(c) == 3 else (spec["from"], *c) for c in spec["clips"]]


def ensure_voice(key):
    """Resolve a voice key from work/voices.json to a profile id, building a
    VoiceStudio voice profile from hand-picked clean clips when needed."""
    spec = load_json(WORK / "voices.json", {})[key]
    if "profile" in spec:
        return spec["profile"]
    sig = voice_sig(key)
    cache_path = WORK / "profiles.json"
    cache = load_json(cache_path, {})
    old = cache.get(key)
    if old and old["sig"] == sig and requests.get(f"{API}/profiles/{old['id']}", timeout=30).ok:
        return old["id"]

    sources, parts, gap = {}, [], None
    for clip, a, b in voice_clips(spec):
        if clip not in sources:
            job = load_json(WORK / clip / "segments.json")["job_id"]
            sources[clip] = sf.read(JOBS / job / "vocals.wav", dtype="float32")
        audio, sr = sources[clip]
        gap = np.zeros((int(0.3 * sr),) + audio.shape[1:], dtype="float32")
        parts += [audio[int(a * sr):int(b * sr)], gap]
    ref = WORK / "voices" / f"{key}.wav"
    ref.parent.mkdir(parents=True, exist_ok=True)
    sf.write(ref, np.concatenate(parts[:-1]), sr)

    with open(ref, "rb") as f:
        r = requests.post(f"{API}/profiles", files={"ref_audio": (ref.name, f, "audio/wav")},
                          data={"name": f"dub-{key}", "ref_text": spec.get("ref_text", "")},
                          timeout=300)
    if r.status_code >= 400:
        raise RuntimeError(f"create profile {key}: {r.status_code} {r.text[:300]}")
    pid = r.json()["id"]
    if old:
        requests.delete(f"{API}/profiles/{old['id']}", timeout=30)
    cache[key] = {"id": pid, "sig": sig}
    cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    print(f"  voice {key}: profile {pid} ({len(spec['clips'])} clips)")
    return pid


def speech_db(audio):
    """Active-speech level: RMS over 20 ms frames within 20 dB of the loudest frame."""
    mono = audio.mean(axis=1) if audio.ndim > 1 else audio
    n = len(mono) // 480 * 480
    if n == 0:
        return None
    frames = 10 * np.log10((mono[:n].reshape(-1, 480) ** 2).mean(axis=1) + 1e-12)
    active = frames[frames > frames.max() - 20]
    return 10 * np.log10(np.mean(10 ** (active / 10)))


def match_gains(job_id, lines, ids):
    """Per-line gain toward the original actor's level: the clip-wide offset is
    matched fully, each line's deviation from it by half, so whispered lines stay
    quieter than shouted ones without dropping out."""
    vocals, sr = sf.read(JOBS / job_id / "vocals.wav", dtype="float32")
    diffs = []
    for line, sid in zip(lines, ids):
        clip = JOBS / job_id / f"seg_en_{sid}.wav"
        orig = speech_db(vocals[int(line["start"] * sr):int(line["end"] * sr)])
        gen = speech_db(sf.read(clip, dtype="float32")[0]) if clip.exists() else None
        diffs.append(None if orig is None or gen is None else orig - gen)
    known = [d for d in diffs if d is not None]
    if not known:
        return [None] * len(lines)
    offset = float(np.median(known))
    gains = []
    for sid, diff in zip(ids, diffs):
        if diff is None:
            gains.append(None)
            continue
        target = offset + 0.5 * (diff - offset)
        gains.append(round(float(np.clip(10 ** (target / 20), 0.05, 2.0)), 3))
        print(f"  level {sid}: exact match {diff:+6.1f} dB, applied {target:+6.1f} dB (gain {gains[-1]:.2f})")
    return gains


# OmniVoice only accepts these instruct tags (omnivoice/utils/voice_design.py);
# anything else, including VoiceStudio's free-text `direction`, aborts the dub.
INSTRUCT_TAGS = {
    "male", "female", "child", "teenager", "young adult", "middle-aged", "elderly",
    "very low pitch", "low pitch", "moderate pitch", "high pitch", "very high pitch", "whisper",
    *(f"{a} accent" for a in ("american", "british", "australian", "chinese", "canadian",
                              "indian", "korean", "portuguese", "russian", "japanese")),
}
EFFECTS = ["broadcast", "cinematic", "podcast", "warm", "bright", "raw", "echo"]  # echo: local patch


def check_instruct(line):
    tags = [t.strip() for t in line.get("instruct", "").split(",") if t.strip()]
    bad = [t for t in tags if t not in INSTRUCT_TAGS]
    if bad:
        raise SystemExit(f"line {line['start']}: unsupported instruct {bad}; allowed: {sorted(INSTRUCT_TAGS)}")
    return ", ".join(tags)


def speech_length(wav):
    """A take's length as VoiceStudio times it: generated edge silence trimmed
    to the first/last sample above -50 dBFS, plus 50 ms either side
    (services/audio_dsp.trim_speech_padding)."""
    audio, sr = sf.read(wav, dtype="float32")
    env = np.abs(audio).max(axis=1) if audio.ndim > 1 else np.abs(audio)
    voiced = np.nonzero(env > 10 ** (-50 / 20))[0]
    if not len(voiced):
        return len(env) / sr
    margin = int(sr * 0.05)
    return (min(len(env), voiced[-1] + 1 + margin) - max(0, voiced[0] - margin)) / sr


def _length_features(text):
    return [1.0, len(re.sub(r"[^A-Za-z0-9]", "", text)), len(re.findall(r"[.,!?;:—-]", text))]


def length_model():
    """Predict a line's rendered length from its text, per voice: seconds =
    a + b * letters + c * punctuation, fitted on every line already rendered in
    the show's directed clips. Voices with few rendered lines lean on the
    all-voice fit. Estimates depend on available renders and vary between takes."""
    data = {}
    for script in WORK.glob("*/script.json"):
        seg = load_json(script.parent / "segments.json")
        if not seg:
            continue
        for line in load_json(script).get("lines", []):
            wav = JOBS / seg["job_id"] / f"seg_en_{line_id(line)}.wav"
            if wav.exists():
                data.setdefault(line["voice"], []).append((_length_features(line["text"]), speech_length(wav)))
    rows = [r for rs in data.values() for r in rs]
    if len(rows) < 10:
        return None
    X_all, y_all = np.array([r[0] for r in rows]), np.array([r[1] for r in rows])

    def solve(X, y, w):
        coef, *_ = np.linalg.lstsq(X * np.sqrt(w)[:, None], y * np.sqrt(w), rcond=None)
        return coef

    overall = solve(X_all, y_all, np.ones(len(rows)))
    coefs = {}
    for voice, rs in data.items():
        X, y = np.array([r[0] for r in rs]), np.array([r[1] for r in rs])
        if len(rs) < 15:  # blend in the whole cast, worth 15 lines
            X, y = np.r_[X, X_all], np.r_[y, y_all]
            w = np.r_[np.full(len(rs), 3.0), np.full(len(rows), 15 / len(rows))]
        else:
            w = np.ones(len(rs))
        coefs[voice] = solve(X, y, w)
    return lambda voice, text: float(np.array(_length_features(text)) @ coefs.get(voice, overall))


def fit_rows(name, overflow=0.3, predict=False):
    """Each rendered clip's length vs its window, and the room concise timing
    allows: VoiceStudio's own rule (dub_generate.py) of the window, plus up to
    0.25 s of the gap before the next line (less a 0.05 s buffer), plus the
    overflow budget. Takes are measured with their edge silence trimmed, as
    VoiceStudio does. With `predict`, lines not rendered yet get an estimated
    length ("est") and a flag: "OVER?" if the estimate exceeds the room, "risk"
    if within 0.3 s of it."""
    job = load_json(WORK / name / "segments.json")["job_id"]
    lines = load_json(WORK / name / "script.json")["lines"]
    estimate = length_model() if predict else None
    rows = []
    for i, line in enumerate(lines):
        wav = JOBS / job / f"seg_en_{line_id(line)}.wav"
        row = {"i": i, "start": line["start"], "text": line["text"], "window": line["end"] - line["start"]}
        gap = lines[i + 1]["start"] - line["end"] if i + 1 < len(lines) else 0.0
        borrow = min(gap - 0.05, 0.25) if gap > 0.05 else 0.0
        row["room"] = row["window"] + borrow + overflow
        if wav.exists():
            row["speech"] = speech_length(wav)
            row["flag"] = "OVER" if row["speech"] > row["room"] else (
                "tight" if row["speech"] > row["window"] else "")
        else:
            row["speech"], row["flag"] = None, ""
            if estimate:
                row["est"] = estimate(line["voice"], line["text"])
                row["flag"] = "OVER?" if row["est"] > row["room"] else (
                    "risk" if row["est"] > row["room"] - 0.3 else "")
        rows.append(row)
    return rows


def line_id(line):
    """Stable id from voice + text, so retiming a line reuses its cached speech;
    changing the voice's clips in voices.json re-synthesizes its lines."""
    key = f"{line['voice']}|{voice_sig(line['voice'])}|{line['text']}"
    return "L" + hashlib.sha1(key.encode()).hexdigest()[:10]


def cmd_generate(args):
    work = WORK / args.name
    doc = json.loads((work / "segments.json").read_text(encoding="utf-8"))
    job_id = doc["job_id"]

    segments, ids, regen, unleveled = [], [], None, False
    script = load_json(work / "script.json")

    def set_gains():
        """Level each line against the actor; True if some line has no clip to measure yet."""
        gains = match_gains(job_id, script["lines"], ids)
        for seg, gain in zip(segments, gains):
            if gain is not None:
                seg["gain"] = gain
        return any(g is None for g in gains)

    rendered_path = work / "rendered.json"  # synthesis settings each cached line was made with
    rendered, settings = load_json(rendered_path, {}), {}
    if script:
        # Hand-directed lines: own timing, speaker and English text per line.
        for line in script["lines"]:
            seg = {"start": line["start"], "end": line["end"], "text": line["text"],
                   "profile_id": ensure_voice(line["voice"]), "target_lang": "en",
                   "effect_preset": line.get("effect", args.effect)}
            if check_instruct(line):
                seg["instruct"] = check_instruct(line)
            segments.append(seg)
            ids.append(line_id(line))
            settings[ids[-1]] = {"steps": args.steps, "effect": seg["effect_preset"],
                                 "instruct": seg.get("instruct", "")}
        for line in script["lines"]:
            if re.search(r"(\.\.\.|…)\s*$", line["text"]):
                print(f"  warning {line['start']}: ends in '…'; the last word tends to get swallowed")
        # Takes lineqa.py --reroll flagged: synthesize them again for a fresh roll.
        reroll = {r["id"] for r in load_json(work / "reroll.json", [])} & set(ids)
        if not args.full:
            # Missing clips, clips made with other settings, or queued re-rolls;
            # an empty list re-mixes cached speech without synthesizing anything.
            regen = [i for i in ids if not (JOBS / job_id / f"seg_en_{i}.wav").exists() or i in reroll
                     or rendered.get(i, {"steps": 16, "effect": "broadcast", "instruct": ""}) != settings[i]]
        if args.match_level:
            unleveled = set_gains()
    else:
        english = json.loads((work / "en.json").read_text(encoding="utf-8"))
        for s in doc["segments"]:
            text = english.get(s["id"], "").strip()
            if not text:
                continue
            segments.append({"start": s["start"], "end": s["end"], "text": text,
                             "profile_id": s.get("profile_id") or "", "target_lang": "en"})
            ids.append(s["id"])

    body = {"segments": segments, "segment_ids": ids, "language": "English",
            "language_code": "en", "timing_strategy": args.timing,
            "voice_match": args.voice_match, "num_step": args.steps}
    if args.timing == "concise":
        body["overflow_budget_s"] = args.overflow
    if regen is not None:
        body["regen_only"] = regen
    print(f"generate {args.name}: {len(segments)} segments "
          f"({len(regen) if regen is not None else len(segments)} to synthesize), "
          f"{args.timing}, {args.voice_match}, {args.steps} steps, {args.effect}")

    def run():
        r = requests.post(f"{API}/dub/generate/{job_id}", json=body, timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(f"generate {r.status_code}: {r.text}")
        return wait_task(r.json()["task_id"], {"done"})

    t0 = time.time()
    try:
        done = run()
    except RuntimeError as e:
        if "dub_timing_overflow" not in str(e) or not script:
            raise
        # VoiceStudio stops at the first overflow; report them all so one
        # pass of edits fixes every line.
        stopped = re.search(r'"segment": (\d+)', str(e))
        print(f"\nVoiceStudio stopped at L{int(stopped.group(1)):02d}" if stopped else "")
        print("lines that don't fit (clip / window / room with borrowed gap and overflow budget):")
        for row in fit_rows(args.name, args.overflow):
            if row["flag"]:
                print(f"  L{row['i']:02d} {row['start']:7.2f}  {row['speech']:4.2f}s / {row['window']:4.2f}s "
                      f"/ {row['room']:4.2f}s  {row['flag']:5s} {row['text']}")
        raise SystemExit("shorten the text or widen the window of each OVER line, then generate again")
    finally:
        # Record what each clip was made with, even when assembly fails after
        # synthesis (e.g. dub_timing_overflow), so a retry doesn't redo them.
        for i, s in settings.items():
            wav = JOBS / job_id / f"seg_en_{i}.wav"
            if wav.exists() and (wav.stat().st_mtime >= t0 - 1 or (regen is not None and i not in regen)):
                rendered[i] = s
        if settings:
            rendered_path.write_text(json.dumps(rendered, indent=1), encoding="utf-8")
    if unleveled:
        # Freshly synthesized lines had nothing to measure before this render;
        # level them now and re-mix the cached speech (no synthesis).
        print("re-mix: levels for newly synthesized lines")
        set_gains()
        body["regen_only"] = []
        done = run()
    (work / "generate-result.json").write_text(json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8")
    if script and reroll:
        (work / "reroll.json").unlink(missing_ok=True)

    out = out_dir(doc)
    out.mkdir(parents=True, exist_ok=True)
    suffix = f".{args.tag}" if args.tag else ""
    print("export mp4")
    r = requests.get(f"{API}/dub/download/{job_id}",
                     params={"include_tracks": "en", "default_track": "en", "preserve_bg": "true"},
                     timeout=1800)
    if r.status_code >= 400:
        raise RuntimeError(f"download {r.status_code}: {r.text[:500]}")
    mp4 = out / f"{args.name}.en{suffix}.mp4"
    mp4.write_bytes(r.content)
    r = requests.get(f"{API}/dub/srt/{job_id}", params={"lang": "en"}, timeout=120)
    r.raise_for_status()
    (out / f"{args.name}.en{suffix}.srt").write_text(r.text, encoding="utf-8")
    print(f"  wrote {mp4}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("prep")
    a.add_argument("video")
    a.add_argument("--speakers", type=int)
    a.set_defaults(fn=cmd_prep)
    b = sub.add_parser("generate")
    b.add_argument("name")
    b.add_argument("--timing", default="concise",
                   choices=["concise", "stretch_video", "strict_slot", "smart_fit"])
    b.add_argument("--voice-match", default="consistent", choices=["per_line", "consistent"])
    b.add_argument("--steps", type=int, default=32,
                   help="OmniVoice diffusion steps (32 = its quality preset; VoiceStudio's default is 16)")
    b.add_argument("--effect", default="broadcast", choices=EFFECTS,
                   help="VoiceStudio voice processing per line; 'raw' skips compression/EQ/normalizing")
    b.add_argument("--tag", default="")
    b.add_argument("--full", action="store_true", help="re-synthesize every line")
    b.add_argument("--match-level", action="store_true",
                   help="set each line's gain to the original actor's speech level")
    b.add_argument("--overflow", type=float, default=0.3,
                   help="seconds a concise-mode line may run past its window")
    b.set_defaults(fn=cmd_generate)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
