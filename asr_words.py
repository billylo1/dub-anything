"""Careful Japanese transcription of a wav excerpt with word timings (beam 5, no VAD).

Usage: asr_words.py <wav> [offset_seconds]   (offset is added to printed times)
"""
import sys

from dub import whisper_setup

whisper_setup()  # VoiceStudio's downloaded Whisper + cuDNN 8
from faster_whisper import WhisperModel  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
path, offset = sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
model = WhisperModel("Systran/faster-whisper-large-v3", device="auto", compute_type="default")
segs, _ = model.transcribe(path, language="ja", beam_size=5, vad_filter=False,
                           condition_on_previous_text=False, word_timestamps=True)
for s in segs:
    words = " ".join(f"{w.word}[{w.start + offset:.2f}]" for w in s.words)
    print(f"{s.start + offset:6.2f}-{s.end + offset:6.2f}  {s.text}\n        {words}")
