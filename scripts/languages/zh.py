"""Chinese: a source for English dubs, checked on Mandarin (docs/languages/zh.md).
Compare transcripts by characters: recognisers don't agree on word spacing.
Simplified and traditional spellings are kept as written, not transliterated.
"""

import difflib
import re
import unicodedata

NAME, ISO3, NATIVE = "Chinese", "zho", "中文"

# ---- heard: the source ----

# Stock credits and channel calls on music/silence. Real thanks are common, so
# THANKS only makes a recovered phrase containing nothing else suspect.
HALLUCINATIONS = (
    "感谢观看",
    "感謝觀看",
    "谢谢观看",
    "謝謝觀看",
    "感谢收看",
    "感謝收看",
    "欢迎订阅",
    "歡迎訂閱",
    "请订阅",
    "請訂閱",
    "字幕由",
    "字幕提供",
    "点赞订阅",
    "點讚訂閱",
    "amara.org",
)
THANKS = ("谢谢", "謝謝", "谢谢大家", "謝謝大家")
SECOND_OPINIONS = {
    "zipformer_ctc": "csukuangfj/sherpa-onnx-zipformer-ctc-zh-int8-2025-07-03",
    "sensevoice": "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
}
OCR = ("CH", "SERVER")


def units(text):
    """Character units with width, case, spacing and punctuation normalized.
    Homophones, numbers and simplified/traditional variants remain review signals.
    """
    return "".join(c for c in unicodedata.normalize("NFKC", text).lower() if c.isalnum())


# ---- spoken: a future dub into Chinese (not an enabled pair) ----

VOICES = ("actor",)
JOIN = ""
MATCH = 0.8
NEGATION = re.compile(r"不|没|沒|无|無|未|别|別")


DIGITS = str.maketrans("零〇一二兩两三四五六七八九", "0012223456789")
NUMBER = re.compile(
    r"[-負负]?[\d零〇一二兩两三四五六七八九十百千萬万億亿廿卅卌]+(?:[.點点][\d零〇一二兩两三四五六七八九]+)?"
)
PLACES = {"十": 10, "百": 100, "千": 1000, "萬": 10000, "万": 10000, "億": 100000000, "亿": 100000000}


def normalize_numbers(text):
    """Canonicalize digit sequences, decimal and place-value numerals for QA."""
    numbers = []

    def replace(match):
        token = match.group().replace("廿", "二十").replace("卅", "三十").replace("卌", "四十")
        sign = "-" if token[0] in "-負负" else ""
        token = token.lstrip("-負负").translate(DIGITS).replace("點", ".").replace("点", ".")
        whole, dot, fraction = token.partition(".")
        if any(c in PLACES for c in whole):
            total, section, current = 0, 0, 0
            for c in whole:
                if c.isdecimal():
                    current = current * 10 + int(c)
                else:
                    place = PLACES[c]
                    if place < 10000:
                        section += (current or 1) * place
                    elif place == 10000:
                        total += (section + current) * place
                        section = 0
                    else:
                        total = (total + section + current) * place
                        section = 0
                    current = 0
            whole = str(total + section + current)
        fraction = fraction.rstrip("0")
        value = sign + whole + ("." + fraction if dot and fraction else "")
        numbers.append(value)
        return value

    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", text)
    return NUMBER.sub(replace, text), numbers


def speakable(text):
    return text


def compare(want, got):
    """Compare desired Mandarin text with transcription from QA."""
    want_normal, want_numbers = normalize_numbers(want)
    got_normal, got_numbers = normalize_numbers(got)
    want_s, got_s = units(want_normal), units(got_normal)
    notes = []
    head = want_s[:3]
    k = got_s.find(head) if len(head) == 3 else -1
    if 1 < k <= 8:
        notes.append(f"leading extra: '{got_s[:k]}'")
    ratio = difflib.SequenceMatcher(None, want_s, got_s).ratio()
    if not got_s:
        notes.append("silent or unintelligible")
    elif ratio < MATCH:
        notes.append(f"match {ratio:.2f}")
    if got_s and len(NEGATION.findall(want_normal)) != len(NEGATION.findall(got_normal)):
        notes.append("negation differs")
    if got_s and want_numbers and want_numbers != got_numbers:
        notes.append(f"number differs: {want_numbers} -> {got_numbers}")
    return notes, ratio


# ---- written: track names and subtitles ----

TITLES = {
    "dub": {"actor": "中文配音（演员原声）"},
    "subs": {"actor": "中文（演员原声配音）"},
    "signs": "中文（画面文字）",
    "transcript": "中文 (transcript)",
    "original": "中文 (original)",
}
FONT = "Microsoft YaHei"


def subtitle(text):
    """Wrap a long unspaced cue near its middle, preferably at punctuation."""
    if len(text) <= 22 or "\n" in text:
        return text
    mid = len(text) / 2
    stops = "，。！？；：、!?…）】」』"
    breaks = [i + 1 for i, c in enumerate(text[:-1]) if c in stops + " 　" and text[i + 1] not in stops]
    cut = min(breaks, key=lambda i: abs(i - mid)) if breaks else round(mid)
    if abs(cut - mid) > len(text) / 4:
        cut = round(mid)
    return text[:cut].rstrip() + "\n" + text[cut:].lstrip()
