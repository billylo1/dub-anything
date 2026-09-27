"""Drive VoiceStudio's dubbing backend (http://127.0.0.1:3900) headlessly.

  prep <video> [--speakers N]   upload, separate vocals, transcribe + diarize;
                                writes work/<name>/segments.json and prints the transcript
  generate <name>               reads work/<name>/script.json (hand-directed lines:
                                start/end/voice/text; voices from work/voices.json)
                                or else en.json ({segment_id: English text}),
                                synthesizes cloned English speech, exports MP4 + SRT
"""
import argparse
import hashlib
import json
import os
import pathlib
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

    src_job = load_json(WORK / spec["from"] / "segments.json")["job_id"]
    audio, sr = sf.read(JOBS / src_job / "vocals.wav", dtype="float32")
    gap = np.zeros((int(0.3 * sr),) + audio.shape[1:], dtype="float32")
    parts = []
    for a, b in spec["clips"]:
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

    if script:
        # Hand-directed lines: own timing, speaker and English text per line.
        for line in script["lines"]:
            segments.append({"start": line["start"], "end": line["end"], "text": line["text"],
                             "profile_id": ensure_voice(line["voice"]), "target_lang": "en"})
            ids.append(line_id(line))
        if not args.full:
            # An empty list re-mixes cached speech without synthesizing anything.
            regen = [i for i in ids if not (JOBS / job_id / f"seg_en_{i}.wav").exists()]
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
            "voice_match": args.voice_match}
    if args.steps:
        body["num_step"] = args.steps
    if args.timing == "concise":
        body["overflow_budget_s"] = args.overflow
    if regen is not None:
        body["regen_only"] = regen
    print(f"generate {args.name}: {len(segments)} segments "
          f"({len(regen) if regen is not None else len(segments)} to synthesize), "
          f"{args.timing}, {args.voice_match}")

    def run():
        r = requests.post(f"{API}/dub/generate/{job_id}", json=body, timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(f"generate {r.status_code}: {r.text}")
        return wait_task(r.json()["task_id"], {"done"})

    done = run()
    if unleveled:
        # Freshly synthesized lines had nothing to measure before this render;
        # level them now and re-mix the cached speech (no synthesis).
        print("re-mix: levels for newly synthesized lines")
        set_gains()
        body["regen_only"] = []
        done = run()
    (work / "generate-result.json").write_text(json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8")

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
    b.add_argument("--steps", type=int)
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
