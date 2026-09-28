"""Drive VoiceStudio's dubbing backend (http://127.0.0.1:3900) headlessly.

  prep <video> [--speakers N]   upload, separate vocals, transcribe + diarize;
                                writes work/<name>/segments.json and prints the transcript
  generate <name>               reads work/<name>/script.json (hand-directed lines:
                                start/end/voice/text, optional instruct/effect;
                                voices from work/voices.json) or else en.json
                                ({segment_id: English text}), synthesizes cloned
                                English speech, exports MP4 + SRT. By default (the
                                script's "render" block) each line's reference ends
                                with the actor's own original of it, and takes are
                                re-timed to end where the actor stops.
"""
import argparse
import functools
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


def use_tts_engine(engine):
    """Select VoiceStudio's TTS engine (a global setting); returns the one it replaced."""
    prev = requests.get(f"{API}/engines/tts", timeout=30).json().get("active")
    if engine and engine != prev:
        r = requests.post(f"{API}/engines/select", json={"family": "tts", "backend_id": engine}, timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(f"select tts engine {engine}: {r.status_code} {r.text[:300]}")
        print(f"  tts engine: {prev} -> {engine}")
    return prev


def emo_refs(work, job_id, lines, ids, alpha):
    """Cut each line's original delivery from the job's separated vocals, for an
    engine that takes an emotion reference (IndexTTS 2). A line's own "emo_alpha"
    overrides the default; 0 turns it off. Returns {id: (path, alpha)}."""
    vocals = JOBS / job_id / "vocals.wav"
    info = sf.info(str(vocals))
    (work / "emo").mkdir(exist_ok=True)
    refs = {}
    for line, i in zip(lines, ids):
        a = line.get("emo_alpha", alpha)
        if not a:
            continue
        path = work / "emo" / f"{i}_{line['start']:.2f}-{line['end']:.2f}.wav"
        if not path.exists():
            audio, sr = sf.read(str(vocals), start=int(line["start"] * info.samplerate),
                                stop=int(line["end"] * info.samplerate), dtype="float32")
            sf.write(str(path), audio, sr)
        refs[i] = (str(path), a)
    return refs


LINE_REF_MAX = 19.5  # s; OmniVoice refuses a reference over 20 s that has a transcript
ROOM_EFFECTS = {"echo", "pa"}  # their original carries the room, which a reference would clone


# How far Whisper's hearing of a window may be from the line's `ja` (character error
# rate). Kanji written as kana counts against it, so only gross mismatches fail.
LINE_REF_CER = 0.5


def unclean_windows(work, job_id, lines, ids):
    """Why each line's window can't be its own reference, or None if it can: speech
    running on past the window's end (the reference would end mid-sound, which
    OmniVoice finishes at the start of the take), Whisper hearing something else
    than the line's `ja` (crosstalk, another voice), or a voice nearer another of
    the clip's characters. Cached per window in refs/clean.json."""
    cache_path = work / "refs" / "clean.json"
    cache = load_json(cache_path, {})
    keys = [f"{i}_{line['start']:.2f}-{line['end']:.2f}" for line, i in zip(lines, ids)]
    if any(k not in cache for k in keys):
        import draft
        from register import cer
        sr = draft.SR
        audio = draft.decode_audio(str(JOBS / job_id / "vocals.wav"), sampling_rate=sr)
        # 50 ms: a turn change in fast dialogue often leaves no longer pause, and a
        # longer minimum would merge the two speakers into one span.
        opts = draft.VadOptions(threshold=0.5, min_silence_duration_ms=50, speech_pad_ms=0)
        spans = [(s["start"] / sr, s["end"] / sr) for s in draft.get_speech_timestamps(audio, opts)]
        model = draft.WhisperModel("Systran/faster-whisper-large-v3", device="auto", compute_type="default")
        embed = draft.embedder()
        voices = load_json(WORK / "voices.json", {})
        who = lambda v: voices[v].get("register_of", v)  # noqa: E731
        cast = {v: embed(draft.decode_audio(str(WORK / "voices" / f"{v}.wav"), sampling_rate=sr))
                for v in {line["voice"] for line in lines}}
        strip = lambda t: re.sub(r"[\s、。,.!?！？…・「」『』()（）ー〜~\-—]", "", t)  # noqa: E731
        for line, key in zip(lines, keys):
            if key in cache:
                continue
            a, b = line["start"], line["end"]
            x = audio[int(a * sr):int(b * sr)]
            why = None
            if any(s < b - 0.05 and e > b + 0.05 for s, e in spans):
                why = "speech runs on past the window's end"
            else:
                heard = "".join(p["ja"] for p in draft.transcribe(model, x))
                err = cer(strip(line["ja"]), strip(heard))
                if err > LINE_REF_CER:
                    why = f"heard as '{heard}' ({err:.0%} off)"
                elif b - a >= 0.6:
                    e = embed(x)
                    best = max(cast, key=lambda v: float(e @ cast[v]))
                    if who(best) != who(line["voice"]):
                        why = f"voice nearer {best}"
            cache[key] = why
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return [cache[k] for k in keys]


def line_refs(work, job_id, lines, ids, verbose=True):
    """Per-line references: the voice's own clips (work/voices/<key>.wav), a 0.3 s
    pause, then this line's original from the job's separated vocals, with the
    line's `ja` after the voice's transcript. OmniVoice copies its reference's
    delivery, so the take follows the actor on this line while the clips hold the
    voice steady. Lines with a room effect or "line_ref": false keep the voice's
    reference, as do lines whose window isn't clean (unclean_windows) and lines
    too long to fit OmniVoice's limit with the clips.
    Returns {id: (path, transcript)}."""
    voices = load_json(WORK / "voices.json", {})
    vocals, sr = sf.read(JOBS / job_id / "vocals.wav", dtype="float32")
    (work / "refs").mkdir(exist_ok=True)
    refs = {}
    for line, i, why in zip(lines, ids, unclean_windows(work, job_id, lines, ids)):
        if not line.get("line_ref", True) or line.get("effect") in ROOM_EFFECTS:
            continue
        if why:
            if verbose:
                print(f"  line ref skipped at {line['start']}: {why}")
            continue
        base_path = WORK / "voices" / f"{line['voice']}.wav"
        base, base_sr = sf.read(base_path, dtype="float32")
        own = vocals[int(line["start"] * sr):int(line["end"] * sr)]
        if base_sr != sr or base.shape[1:] != own.shape[1:]:
            print(f"  line ref skipped at {line['start']}: {base_path.name} isn't in the vocals' format")
            continue
        if (len(base) + len(own)) / sr + 0.3 > LINE_REF_MAX:
            if verbose:
                print(f"  line ref skipped at {line['start']}: over {LINE_REF_MAX:.0f} s with the voice's clips")
            continue
        path = work / "refs" / f"{i}_{line['start']:.2f}-{line['end']:.2f}.wav"
        if not path.exists():
            gap = np.zeros((int(0.3 * sr),) + own.shape[1:], dtype="float32")
            sf.write(path, np.concatenate([base, gap, own]), sr)
        refs[i] = (str(path), f"{voices[line['voice']]['ref_text']} {line['ja']}")
    return refs


FILL_TOL = 0.25           # s; a take ending this close to the actor's last sound is left alone
FILL_SPEED = (0.8, 1.25)  # OmniVoice speeds allowed when re-timing a take (judge by ear)


def actor_ends(job_id, lines):
    """Seconds from each window's start to the actor's last sound in it (Silero VAD
    on the separated vocals, as draft.py uses), or None where it hears nothing."""
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    audio = decode_audio(str(JOBS / job_id / "vocals.wav"), sampling_rate=16000)
    opts = VadOptions(threshold=0.5, min_silence_duration_ms=120, speech_pad_ms=30)
    spans = [(s["start"] / 16000, s["end"] / 16000) for s in get_speech_timestamps(audio, opts)]
    ends = []
    for line in lines:
        inside = [min(b, line["end"]) for a, b in spans if b > line["start"] and a < line["end"]]
        ends.append(max(inside) - line["start"] if inside else None)
    return ends


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


def voice_wav(key, rebuild=False):
    """work/voices/<key>.wav: the voice's clips from voices.json joined with 0.3 s
    pauses. Built when missing, when rebuild is set, or when the voice's entry no
    longer matches the profile it was last uploaded as."""
    spec = load_json(WORK / "voices.json", {})[key]
    ref = WORK / "voices" / f"{key}.wav"
    uploaded = load_json(WORK / "profiles.json", {}).get(key, {})
    if ref.exists() and not rebuild and uploaded.get("sig") == voice_sig(key):
        return ref
    sources, parts = {}, []
    for clip, a, b in voice_clips(spec):
        if clip not in sources:
            job = load_json(WORK / clip / "segments.json")["job_id"]
            sources[clip] = sf.read(JOBS / job / "vocals.wav", dtype="float32")
        audio, sr = sources[clip]
        gap = np.zeros((int(0.3 * sr),) + audio.shape[1:], dtype="float32")
        parts += [audio[int(a * sr):int(b * sr)], gap]
    ref.parent.mkdir(parents=True, exist_ok=True)
    sf.write(ref, np.concatenate(parts[:-1]), sr)
    return ref


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

    ref = voice_wav(key, rebuild=True)
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
EFFECTS = ["broadcast", "cinematic", "podcast", "warm", "bright", "raw", "echo", "pa"]  # echo, pa: local patch


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


OMNIVOICE_SR, OMNIVOICE_FPS = 24000, 25  # OmniVoice's audio tokenizer: 24 kHz, 960-sample frames
STRETCH_USED = 0.5    # share of OmniVoice's stretch of short lines that a take actually speaks
SPEED_EXPONENT = 0.7  # a take asked for at speed s changes length by 1/s**0.7, not 1/s


def at_speed(natural, speed):
    """A take's length at `speed`, from its length at natural pace. It changes
    less than the speed asks: slowed, OmniVoice often says the words at its own
    pace and leaves the extra time silent, which is trimmed; sped up, it
    shortens less too."""
    return natural / speed ** SPEED_EXPONENT


@functools.lru_cache(maxsize=1)
def _omnivoice():
    """OmniVoice's length rule and reference helpers, and VoiceStudio's text
    normalization, from the VoiceStudio checkout this Python runs from."""
    import importlib.util

    import omnivoice
    from omnivoice.utils.audio import remove_silence_safe
    from omnivoice.utils.duration import RuleDurationEstimator
    from omnivoice.utils.text import add_punctuation
    path = pathlib.Path(omnivoice.__file__).parents[1] / "backend" / "services" / "text_normalization.py"
    spec = importlib.util.spec_from_file_location("voicestudio_text_normalization", path)
    norm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(norm)
    return RuleDurationEstimator(), add_punctuation, remove_silence_safe, norm.normalize_text


@functools.lru_cache(maxsize=512)
def _ref_frames(path, mtime):
    """A reference's length in OmniVoice frames, prepared as OmniVoice prepares it
    (create_voice_clone_prompt): mono at 24 kHz, pauses over 0.2 s shortened and
    the edges trimmed. (OmniVoice also raises a quiet clip's level before
    trimming; leaving that out predicted rendered takes better.)"""
    import torch
    import torchaudio
    audio, sr = sf.read(path, dtype="float32")
    wav = torch.from_numpy(audio.mean(axis=1) if audio.ndim > 1 else audio)[None]
    wav = torchaudio.functional.resample(wav, sr, OMNIVOICE_SR)
    wav = _omnivoice()[2](wav, OMNIVOICE_SR, mid_sil=200, lead_sil=100, trail_sil=200)
    return wav.size(-1) * OMNIVOICE_FPS // OMNIVOICE_SR


def take_length(ref_audio, ref_text, text, speed=1.0):
    """How long a take of `text` cloned from this reference will be, in seconds of
    trimmed speech (as speech_length measures it). OmniVoice fixes a take's length
    before generating it (omnivoice/utils/duration.py): the reference's length
    times the text's weight over the transcript's weight. A kanji weighs 3, kana
    2.2, a digit 3.5, a Latin letter 1 and a space 0.2, so English follows how
    dense the actor's Japanese is and how fast they said it. Anything under 2 s
    is stretched towards 2 s; takes speak about half of that stretch. Fitted on
    about 1,000 takes: typically within 0.2 s, nine in ten within 0.4 s, about
    as close as two takes of one line are to each other."""
    estimator, add_punctuation, _, normalize = _omnivoice()
    ref = pathlib.Path(ref_audio)
    frames = _ref_frames(str(ref), ref.stat().st_mtime)
    text, ref_text = normalize(text, "en"), add_punctuation(ref_text)
    plain = estimator.estimate_duration(text, ref_text, frames, low_threshold=None)
    stretched = estimator.estimate_duration(text, ref_text, frames)
    return at_speed((stretched - STRETCH_USED * (stretched - plain)) / OMNIVOICE_FPS, speed)


SHORT_BY = 0.5  # s of the actor's speech left uncovered that makes a line "SHORT"


def fit_rows(name, overflow=0.3, predict=False, short=False):
    """Each rendered clip's length vs its window, and the room concise timing
    allows: VoiceStudio's own rule (dub_generate.py) of the window, plus up to
    0.25 s of the gap before the next line (less a 0.05 s buffer), plus the
    overflow budget. Takes are measured with their edge silence trimmed, as
    VoiceStudio does. With `predict`, lines not rendered yet get the length
    OmniVoice will give them at natural pace ("est", take_length), from the
    reference the render will use: the line's own original when the part renders
    with per-line references and its window is clean (fallbacks are printed, as
    generate prints them), otherwise the voice's clips. They're flagged "OVER?"
    if the estimate exceeds the room, "risk" if within 0.3 s of it. When the part
    renders with fill, a line only overflows if it would even at fill's fastest
    speed. With `short`, a line whose English ends more than SHORT_BY before the
    actor stops is flagged "SHORT" (the take as rendered) or "SHORT?" (the
    estimate at natural pace), with the actor's end in "actor": it needs fuller
    text. Estimates get no credit for fill's slowing: slowed takes often come
    out little longer, so the text has to fill the time by itself."""
    work = WORK / name
    job = load_json(work / "segments.json")["job_id"]
    script = load_json(work / "script.json")
    lines = script["lines"]
    rendered = load_json(work / "rendered.json", {})
    mode = script.get("render", {})
    fastest = FILL_SPEED[1] if mode.get("fill", not rendered) else 1.0
    ids = [line_id(line) for line in lines]
    own, base = {}, {}  # per-line references by line id, voices' references by key
    if predict:
        voices = load_json(WORK / "voices.json", {})
        todo = [k for k, i in enumerate(ids) if not (JOBS / job / f"seg_en_{i}.wav").exists()
                and "clips" in voices[lines[k]["voice"]]]
        base = {key: (voice_wav(key), voices[key].get("ref_text", "")) for key in {lines[k]["voice"] for k in todo}}
        if todo and mode.get("line_ref", not rendered):
            own = line_refs(work, job, [lines[k] for k in todo], [ids[k] for k in todo])
    ends = actor_ends(job, lines) if short else [None] * len(lines)
    rows = []
    for i, (line, sid, end) in enumerate(zip(lines, ids, ends)):
        wav = JOBS / job / f"seg_en_{sid}.wav"
        row = {"i": i, "start": line["start"], "text": line["text"], "window": line["end"] - line["start"],
               "actor": end}
        gap = lines[i + 1]["start"] - line["end"] if i + 1 < len(lines) else 0.0
        borrow = min(gap - 0.05, 0.25) if gap > 0.05 else 0.0
        row["room"] = row["window"] + borrow + overflow
        length = None
        if wav.exists():
            length = row["speech"] = speech_length(wav)
            row["flag"] = "OVER" if row["speech"] > row["room"] else (
                "tight" if row["speech"] > row["window"] else "")
        else:
            row["speech"], row["flag"] = None, ""
            ref = own.get(sid) or base.get(line["voice"])
            if ref:
                length = row["est"] = take_length(*ref, line["text"])
                row["flag"] = "OVER?" if at_speed(length, fastest) > row["room"] else (
                    "risk" if at_speed(length, fastest) > row["room"] - 0.3 else "")
        if short and end and length and not row["flag"] and length < end - SHORT_BY:
            row["flag"] = "SHORT" if row["speech"] is not None else "SHORT?"
        rows.append(row)
    return rows


def line_id(line):
    """Stable id from voice + text, so retiming a line reuses its cached speech;
    changing the voice's clips in voices.json re-synthesizes its lines. A line
    repeated word for word needs `"take": n` on the repeats to get ids of its own."""
    key = f"{line['voice']}|{voice_sig(line['voice'])}|{line['text']}"
    if line.get("take"):
        key += f"|take {line['take']}"
    return "L" + hashlib.sha1(key.encode()).hexdigest()[:10]


FILL_PASSES = 2  # re-timing passes after the natural-pace render; a third changed nothing in trials


def render_mode(work, script, args):
    """How a part is rendered: script.json's "render" block, {"line_ref": .., "fill": ..}.
    A part without one gets the defaults (both on) if it has never been rendered,
    and the earlier behaviour (both off) if it has, so approved takes don't change
    under a new default; the block is then written so later runs agree. The
    --[no-]line-ref and --[no-]fill flags override it for one run."""
    mode = script.get("render")
    if mode is None:
        fresh = not load_json(work / "rendered.json")
        mode = script["render"] = {"line_ref": fresh, "fill": fresh}
        (work / "script.json").write_text(json.dumps(script, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  render mode written to script.json: {mode}"
              + ("" if fresh else " (this part was rendered before they became the default)"))
    mode = {"line_ref": False, "fill": False, **mode}
    for key in ("line_ref", "fill"):
        if getattr(args, key) is not None:
            mode[key] = getattr(args, key)
    return mode


def cmd_generate(args):
    work = WORK / args.name
    doc = json.loads((work / "segments.json").read_text(encoding="utf-8"))
    job_id = doc["job_id"]
    script = load_json(work / "script.json")
    mode = render_mode(work, script, args) if script else {"line_ref": False, "fill": False}
    rendered_path = work / "rendered.json"  # synthesis settings each cached line was made with
    rendered = load_json(rendered_path, {})
    # Takes lineqa.py --reroll flagged: synthesized again (on the first pass) for a fresh roll.
    reroll = {r["id"] for r in load_json(work / "reroll.json", [])} if script else set()
    ends = actor_ends(job_id, script["lines"]) if mode["fill"] else None
    for line in script["lines"] if script else []:
        if re.search(r"(\.\.\.|…)\s*$", line["text"]):
            print(f"  warning {line['start']}: ends in '…'; the last word tends to get swallowed")

    def plan(first):
        """One pass: the segments to send, their ids, the settings each take is made
        with, and the ids to synthesize (None: all)."""
        segments, ids, settings = [], [], {}
        if not script:
            english = json.loads((work / "en.json").read_text(encoding="utf-8"))
            for s in doc["segments"]:
                text = english.get(s["id"], "").strip()
                if text:
                    segments.append({"start": s["start"], "end": s["end"], "text": text,
                                     "profile_id": s.get("profile_id") or "", "target_lang": "en"})
                    ids.append(s["id"])
            return segments, ids, settings, None
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
            if args.engine != "omnivoice":
                settings[ids[-1]]["engine"] = args.engine
        # Emotion references: each line's own original, for engines that take one.
        emo = emo_refs(work, job_id, script["lines"], ids, args.emo_alpha) if args.engine != "omnivoice" else {}
        for seg, i in zip(segments, ids):
            if i in emo:
                seg["emo_audio"], seg["emo_alpha"] = emo[i]
                settings[i]["emo_alpha"] = emo[i][1]
        # Per-line references: the voice's clips plus the line's own original.
        refs = line_refs(work, job_id, script["lines"], ids, verbose=first) if mode["line_ref"] else {}
        for seg, i in zip(segments, ids):
            if i in refs:
                seg["ref_audio"], seg["ref_text"] = refs[i]
                settings[i]["line_ref"] = f"{seg['start']:.2f}-{seg['end']:.2f}"
        if mode["fill"]:
            # Re-time takes that stop well short of the actor or run past them:
            # render them at the speed that lands their end on the actor's last sound.
            for seg, i, end in zip(segments, ids, ends):
                prev = rendered.get(i, {}).get("speed")
                speed, wav = prev, JOBS / job_id / f"seg_en_{i}.wav"
                if end and wav.exists() and abs(speech_length(wav) - end) > FILL_TOL:
                    speed = min(max((prev or 1.0) * speech_length(wav) / end, FILL_SPEED[0]), FILL_SPEED[1])
                if speed and abs(speed - 1) >= 0.01:
                    seg["speed"] = settings[i]["speed"] = round(speed, 2)
        if args.full and first:
            return segments, ids, settings, None
        # Missing clips, clips made with other settings, or queued re-rolls;
        # an empty list re-mixes cached speech without synthesizing anything.
        regen = [i for i in ids if not (JOBS / job_id / f"seg_en_{i}.wav").exists() or (first and i in reroll)
                 or rendered.get(i, {"steps": 16, "effect": "broadcast", "instruct": ""}) != settings[i]]
        return segments, ids, settings, regen

    def request(segments, ids, regen):
        body = {"segments": segments, "segment_ids": ids, "language": "English",
                "language_code": "en", "timing_strategy": args.timing,
                "voice_match": args.voice_match, "num_step": args.steps}
        if args.timing == "concise":
            body["overflow_budget_s"] = args.overflow
        if regen is not None:
            body["regen_only"] = regen
        return body

    def level(segments, ids):
        """Level each line against the actor, from the takes on disk."""
        for seg, gain in zip(segments, match_gains(job_id, script["lines"], ids)):
            if gain is not None:
                seg["gain"] = gain

    def run(body):
        r = requests.post(f"{API}/dub/generate/{job_id}", json=body, timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(f"generate {r.status_code}: {r.text}")
        return wait_task(r.json()["task_id"], {"done"})

    prev_engine = use_tts_engine(args.engine)
    done, synthesized = None, False
    try:
        for n in range(1 + (FILL_PASSES if mode["fill"] else 0)):
            segments, ids, settings, regen = plan(first=n == 0)
            if n and not regen:
                break  # every take ends on the actor, or is as fast or slow as fill allows
            if args.match_level and script:
                level(segments, ids)
            print(f"{'generate' if n == 0 else f'  fill pass {n}:'} {args.name}: {len(segments)} segments "
                  f"({len(regen) if regen is not None else len(segments)} to synthesize), "
                  f"{args.timing}, {args.voice_match}, {args.steps} steps, {args.effect}, {args.engine}"
                  + (f", emotion from the original at {args.emo_alpha}" if args.engine != "omnivoice" and args.emo_alpha else "")
                  + (f", {sum('ref_audio' in s for s in segments)} lines with their own reference" if mode["line_ref"] else "")
                  + (f", {sum('speed' in s for s in segments)} lines re-timed to the actor" if mode["fill"] else ""))
            t0 = time.time()
            try:
                done = run(request(segments, ids, regen))
            except RuntimeError as e:
                if "dub_timing_overflow" not in str(e) or not script:
                    raise
                done, stopped = None, re.search(r'"segment": (\d+)', str(e))
            finally:
                # Record what each clip was made with, even when assembly fails after
                # synthesis (e.g. dub_timing_overflow), so a retry doesn't redo them.
                for i, s in settings.items():
                    wav = JOBS / job_id / f"seg_en_{i}.wav"
                    if wav.exists() and (wav.stat().st_mtime >= t0 - 1 or (regen is not None and i not in regen)):
                        rendered[i] = s
                if settings:
                    rendered_path.write_text(json.dumps(rendered, indent=1), encoding="utf-8")
            synthesized |= regen is None or bool(regen)
            if done is None:
                if not mode["fill"] or n == FILL_PASSES:
                    break
                print("  a line overflows its room; the next fill pass re-times it")
        if done is None:
            # VoiceStudio stops at the first overflow; report them all so one
            # pass of edits fixes every line.
            print(f"\nVoiceStudio stopped at L{int(stopped.group(1)):02d}" if stopped else "")
            print("lines that don't fit (clip / window / room with borrowed gap and overflow budget):")
            for row in fit_rows(args.name, args.overflow):
                if row["flag"] in ("OVER", "tight"):
                    print(f"  L{row['i']:02d} {row['start']:7.2f}  {row['speech']:4.2f}s / {row['window']:4.2f}s "
                          f"/ {row['room']:4.2f}s  {row['flag']:5s} {row['text']}")
            raise SystemExit("shorten the text or widen the window of each OVER line, then generate again")
        if args.match_level and script and synthesized:
            # Levels are measured on the takes: level the new ones and re-mix (no synthesis).
            print("re-mix: levels for newly synthesized lines")
            segments, ids, _, _ = plan(first=False)
            level(segments, ids)
            done = run(request(segments, ids, []))
    finally:
        if prev_engine and prev_engine != args.engine:
            use_tts_engine(prev_engine)
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
    b.add_argument("--engine", default="omnivoice",
                   help="VoiceStudio TTS engine for this render (e.g. indextts2); the previous one is restored after")
    b.add_argument("--emo-alpha", type=float, default=0.0,
                   help="engines with emotion control: follow each line's original delivery at this "
                        "strength (0-1; a line's own emo_alpha overrides; 0 = off)")
    b.add_argument("--line-ref", action=argparse.BooleanOptionalAction, default=None,
                   help="override the script's render mode for this run: give each line a reference of its "
                        "voice's clips plus its own original, so the take follows the actor's delivery on that "
                        "line (\"line_ref\": false opts a line out)")
    b.add_argument("--fill", action=argparse.BooleanOptionalAction, default=None,
                   help=f"override the script's render mode for this run: re-render takes that end more than "
                        f"{FILL_TOL} s from the actor's last sound, at the speed ({FILL_SPEED[0]}-{FILL_SPEED[1]}) "
                        f"that lands them on it")
    b.set_defaults(fn=cmd_generate)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
