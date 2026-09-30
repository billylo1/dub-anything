"""Shared by the dubrama scripts: where things live (work/, VoiceStudio's job
folders, the model cache), voices and their reference clips, line ids, takes and
their lengths, each actor's last sound in a window, per-line references and the
check that a window is clean, and the length OmniVoice will give a take
(take_length, fit_rows). dub.py drives VoiceStudio with these; the other tools
import them from here.
"""
import functools
import hashlib
import json
import os
import pathlib
import re
import sys

import numpy as np
import soundfile as sf

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


def srt_time(t):
    ms = round(t * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


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
    the clip's characters. Cached in refs/clean.json by what the check reads (window,
    voice and its clips, `ja`), not by the take: rewording the English keeps it."""
    cache_path = work / "refs" / "clean.json"
    cache = load_json(cache_path, {})
    keys = [f"{line['start']:.2f}-{line['end']:.2f}_" + hashlib.sha1(
            f"{line['voice']}|{voice_sig(line['voice'])}|{line['ja']}".encode()).hexdigest()[:12]
            for line in lines]
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
