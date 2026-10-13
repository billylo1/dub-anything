"""Transcribe every rendered line on its own and flag the ones that don't match.

Usage: scripts/lineqa.py <name> [<name> ...] [--all] [--reroll]   (after `dub.py generate`)

Full-mix QA (qa.py) hides problems that show up clearly per line:
  - extra words before the first expected one: voice-reference leakage
    (a voice clip in voices.json that cuts a word off or holds someone else)
  - dropped or changed words (fast lists), and flipped meanings ("can" heard
    as "can't": any change in negation is flagged, and so is a number heard as
    another, which a long line otherwise hides)
  - words a voice can't say, and silent takes
Both sides are normalized before comparing (numbers spelled out, contractions
expanded, "Dr." = "doctor", compounds joined), so digits and spelling don't
count as failures. Takes are heard in the part's target language. Spanish is
compared word by word like English, without accents (Whisper writes "esta" or
"está"), with its own number words, abbreviations and negations. Japanese is
compared by its reading in kana (kana.py), so kanji or kana, katakana or
hiragana and digits or kanji numerals don't count either; a take with English
in it (the actor's original leaking from the per-line reference) is flagged.
--reroll writes the flagged lines to work/<name>/reroll.json;
the next `dub.py generate` gives them fresh takes. That fixes random glitches,
but a word a voice can't say needs rewording. A fresh take can also come out
worse, so each take a re-roll replaces is kept (work/<name>/takes/), and the
next check puts the best one back if the new take transcribes worse; the next
`generate` then re-mixes it without synthesizing anything.
In the "american" voice most flags come from the conversion, not from IndexTTS:
when IndexTTS's own take of a flagged line says it right, the line is queued to
be converted again (conversion is random too), keeping the take; after
RECONVERTS failed tries the take is re-rolled instead.
"""
import argparse
import difflib
import hashlib
import json
import pathlib
import re
import shutil
import sys
import unicodedata

import soundfile as sf

from core import WORK, langs, line_id, load_json, take_wav, whisper_setup
from gpulock import hold_gpu

whisper_setup()
from faster_whisper import WhisperModel  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
        "sixteen seventeen eighteen nineteen").split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
ABBREV = {"dr": "doctor", "mr": "mister", "mrs": "missus", "ms": "miss", "ok": "okay", "vs": "versus",
          "nope": "no", "nah": "no", "cm": "centimeter", "mm": "millimeter", "km": "kilometer",
          "kg": "kilogram", "centimeters": "centimeter", "millimeters": "millimeter"}
NEGATIONS = {"not", "no", "never", "nothing", "nobody", "none", "neither", "nor"}
# Words that say a number; "one" and "zero" are left out, as words too ("this one").
NUMBER_WORDS = set(ONES[2:] + TENS[2:] + ["hundred", "thousand", "million", "billion"])
NUMBER_WORDS |= {w[:-1] + "ies" for w in TENS[2:]} | {w + "s" for w in ("hundred", "thousand", "million", "billion")}


def spell(n):
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])
    for div, name in ((10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand"), (100, "hundred")):
        if n >= div:
            q, r = divmod(n, div)
            return f"{spell(q)} {name}" + ("" if r == 0 else f" {spell(r)}")


def spell_pairs(n):
    """A four-digit number read in pairs, as years and model numbers are:
    1979 "nineteen seventy nine", 5410 "fifty four ten", 3400 "thirty four hundred"."""
    hi, lo = divmod(n, 100)
    return f"{spell(hi)} " + ("hundred" if lo == 0 else f"oh {spell(lo)}" if lo < 10 else spell(lo))


COLLOQUIAL = {"wanna": "want to", "gonna": "going to", "gotta": "got to", "gimme": "give me", "lemme": "let me",
              "kinda": "kind of", "sorta": "sort of", "dunno": "do not know"}


def words(text, reading="plain"):
    """Normalized words. Numbers read "plain" (1979 "one thousand nine hundred seventy
    nine"), in "pairs" (years, model numbers: "nineteen seventy nine") or as "digits"
    (phone numbers: "zero three three zero ...")."""
    t = text.lower().replace("’", "'")
    t = re.sub(r"(\d+)\s*%", r"\1 percent", t)
    t = re.sub(r"\bno\.\s*(?=\d)", "number ", t)  # "No.1" is "number one", not a negation
    t = re.sub(r"(?<=\d),(?=\d{3})", "", t)
    t = re.sub(r"(\d)\.(\d)", r"\1 point \2", t)
    # Decades: "the 70s" and "the 1970s" are "the seventies" and "the nineteen seventies".
    t = re.sub(r"\b(1[0-9]|20)?([2-9]0)'?s\b",
               lambda m: (spell(int(m.group(1))) + " " if m.group(1) else "") + spell(int(m.group(2)))[:-1] + "ies", t)
    t = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", t)  # "a2.5cm" -> "a 2.5 cm"
    t = re.sub(r"\bnevermind\b", "never mind", t)  # one word or two, same meaning
    t = re.sub(r"\b(" + "|".join(COLLOQUIAL) + r")\b", lambda m: COLLOQUIAL[m.group(1)], t)
    for a, b in (("won't", "will not"), ("can't", "can not"), ("n't", " not"), ("'re", " are"),
                 ("'ve", " have"), ("'ll", " will"), ("'m", " am"), ("'d", " would"), ("'s", " is")):
        t = t.replace(a, b)
    out = []
    for w in re.findall(r"[a-z0-9]+", t):
        if w.isdigit() and reading == "digits":
            out += ["zero" if d == "0" else spell(int(d)) for d in w]
        elif w.isdigit() and len(w) < 13:
            n = int(w)
            out += (spell_pairs(n) if reading == "pairs" and 1000 < n < 10000 and n % 1000 else spell(n)).split()
        else:
            out.append(ABBREV.get(w, w))
    return out


ES_ABBREV = {"sr": "senor", "sra": "senora", "srta": "senorita", "dr": "doctor", "dra": "doctora",
             "ud": "usted", "uds": "ustedes", "okey": "ok", "okay": "ok", "vs": "versus"}
ES_NEGATIONS = {"no", "nunca", "nada", "nadie", "ni", "ningun", "ninguno", "ninguna", "jamas", "tampoco"}
# Words that say a number, accents aside; "uno", "una" and "un" are left out, as articles too.
ES_NUMBER = re.compile(r"dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|trece|catorce|quince|"
                       r"dieci[a-z]+|veinte|veinti[a-z]+|treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|"
                       r"noventa|cien|ciento|[a-z]+cientos|quinientos|mil|millon|millones")


def words_es(text, reading="plain"):
    """words() for Spanish, without accents (Whisper writes "esta" or "está", "si" or
    "sí"): numbers spelled out in Spanish (num2words), read "plain" or as "digits"."""
    from num2words import num2words
    plain = lambda s: re.sub(r"[\u0300-\u036f]", "", unicodedata.normalize("NFD", s))  # noqa: E731
    t = plain(text.lower())
    t = re.sub(r"(\d+)\s*%", r"\1 por ciento", t)
    t = re.sub(r"(?<=\d)[.,](?=\d{3}\b)", "", t)  # 1.000 or 1,000
    t = re.sub(r"(\d)[.,](\d)", r"\1 punto \2", t)
    t = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", t)
    out = []
    for w in re.findall(r"[a-z0-9]+", t):
        if w.isdigit() and reading == "digits":
            out += [plain(num2words(int(d), lang="es")) for d in w]
        elif w.isdigit() and len(w) < 13:
            out += plain(num2words(int(w), lang="es")).split()
        else:
            out.append(ES_ABBREV.get(w, w))
    return out


JA_PUNCT = re.compile(r"[\s、。,.!?！？…・「」『』()（）ー〜~\-—:：;；\"'’“”]")
JA_NEGATION = re.compile(r"ない|なかっ|ません")
JA_MATCH = 0.8  # kana are finer than words: one misheard mora in a short line costs more


def compare_ja(want, got):
    """compare() for Japanese, by reading: leading junk, the kana-level match, a
    flipped negation (ない, ません), and English heard in the take."""
    import kana
    want_k, got_k = (JA_PUNCT.sub("", r) for r in kana.readings([want, got]))
    notes = []
    head = want_k[:3]
    k = got_k.find(head) if len(head) == 3 else -1
    if 1 < k <= 8:
        notes.append(f"leading extra: '{got_k[:k]}'")
    ratio = difflib.SequenceMatcher(None, want_k, got_k).ratio()
    if not got_k:
        notes.append("silent or unintelligible")
    elif ratio < JA_MATCH:
        notes.append(f"match {ratio:.2f}")
    # Negative endings are written in kana: counted in the text, as readings run words together
    # (ほな、行って reads ほないって).
    if got_k and len(JA_NEGATION.findall(want)) != len(JA_NEGATION.findall(got)):
        notes.append("negation differs")
    # Numbers the script writes in digits or kanji (a number spelled in kana is compared by its reading only).
    if got_k and kana.numbers(want) and sorted(kana.numbers(want)) != sorted(kana.numbers(got)):
        notes.append(f"number differs: {kana.numbers(want)} -> {kana.numbers(got)}")
    latin = re.findall(r"[A-Za-z]+", got)
    if sum(map(len, latin)) >= 3 and not re.search(r"[A-Za-z]{3}", want):
        notes.append(f"English in the take: '{' '.join(latin)}'")
    return notes, ratio


def compare(want, got, lang="en"):
    """(notes, similarity): leading junk, and the better of word- or letter-level match.
    Numbers count in any reading ("1979" is "nineteen seventy nine", "03" "zero three")."""
    if lang == "ja":
        return compare_ja(want, got)
    split, readings = (words_es, ("plain", "digits")) if lang == "es" else (words, ("plain", "pairs", "digits"))
    negations, is_number = (ES_NEGATIONS, ES_NUMBER.fullmatch) if lang == "es" else (NEGATIONS, NUMBER_WORDS.__contains__)
    want_w, got_w = max(((split(want, a), split(got, b)) for a in readings for b in readings),
                        key=lambda pair: difflib.SequenceMatcher(None, *pair).ratio())
    notes = []
    if want_w and want_w[0] in got_w[:4] and got_w.index(want_w[0]) > 0:
        notes.append(f"leading extra: '{' '.join(got_w[:got_w.index(want_w[0])])}'")
    ratio = max(difflib.SequenceMatcher(None, want_w, got_w).ratio(),
                difflib.SequenceMatcher(None, "".join(want_w), "".join(got_w)).ratio())
    if not got_w:
        notes.append("silent or unintelligible")
    elif ratio < 0.85:
        notes.append(f"match {ratio:.2f}")
    # A close match can still flip the meaning ("can" heard as "can't", "seventy" as "forty").
    if got_w and sum(w in negations for w in want_w) != sum(w in negations for w in got_w):
        notes.append("negation differs")
    nums = lambda ws: [w for w in ws if is_number(w)]  # noqa: E731
    if got_w and nums(want_w) != nums(got_w):
        notes.append(f"number differs: '{' '.join(nums(want_w))}' -> '{' '.join(nums(got_w))}'")
    return notes, ratio


def file_sha(path):
    return hashlib.sha1(pathlib.Path(path).read_bytes()).hexdigest()[:16]


def better(a, b):
    """Whether take a transcribes better than take b: no flags first, then the closer match."""
    return (not a["notes"], a["ratio"]) > (not b["notes"], b["ratio"])


def check(name, model, show_all, reroll):
    work = WORK / name
    job = json.loads((work / "segments.json").read_text(encoding="utf-8"))["job_id"]
    tgt = langs(name)[1]
    lines = json.loads((work / "script.json").read_text(encoding="utf-8"))["lines"]
    # Takes re-rolled before, so a worse roll never replaces a better one: before a
    # flagged line is re-rolled its take is kept in takes/, with the settings it was
    # made with, and the best of them is put back if the fresh take is worse.
    rendered = load_json(work / "rendered.json", {})
    converted = load_json(work / "converted.json", {})  # "american": the take is a converted copy
    kept_path = work / "takes" / "kept.json"
    kept = load_json(kept_path, {})
    flagged, restored = [], []
    for i, line in enumerate(lines):
        lid = line_id(line)
        wav, src = take_wav(job, lid, tgt), take_wav(job, lid, tgt, "src")
        if not wav.exists():
            print(f"L{i:02d} {line['start']:7.2f}  (not rendered)  {line['text']}")
            continue
        got = hear(model, wav, tgt)
        notes, ratio = compare(line["text"], got, tgt)
        take = {"notes": notes, "ratio": round(ratio, 3), "got": got, "sha": file_sha(wav),
                "settings": rendered.get(lid, {})}
        # Only takes made the same way count (a moved window re-renders the line anyway);
        # a seed only tells two rolls apart.
        same = lambda k: {**k["settings"], "speed": None, "seed": None} ==             {**take["settings"], "speed": None, "seed": None}  # noqa: E731
        earlier = [k for k in kept.get(lid, []) if same(k) and k["sha"] != take["sha"]]
        best = max(earlier, key=lambda k: (not k["notes"], k["ratio"]), default=None)
        if best and better(best, take):
            shutil.copy2(work / "takes" / best["file"], wav)
            if best.get("src"):
                shutil.copy2(work / "takes" / best["src"], src)
                converted[lid] = file_sha(wav)
            rendered[lid] = best["settings"]
            restored.append(lid)
            print(f"L{i:02d} {line['start']:7.2f} put back an earlier take (match {best['ratio']:.2f} "
                  f"over {take['ratio']:.2f}: {got!r})")
            take, notes, got = best, best["notes"], best["got"]
        if notes:
            flagged.append({"id": lid, "start": line["start"], "text": line["text"], "got": got})
            seed = take["settings"].get("seed")
            tries = sum(k.get("reconverted", 0) for k in kept.get(lid, []) if k["settings"].get("seed") == seed)
            if reroll and lid in converted and src.exists() and tries < RECONVERTS:
                engine_got = hear(model, src, tgt)
                if not compare(line["text"], engine_got, tgt)[0]:
                    flagged[-1]["reconvert"] = True
                    notes = notes + [f"IndexTTS's take is fine ({engine_got!r}): converting it again"]
            if reroll and not any(k["sha"] == take["sha"] for k in kept.get(lid, [])):
                (work / "takes").mkdir(exist_ok=True)
                n = len(kept.get(lid, []))
                take["file"] = f"{lid}_{n}.wav"
                shutil.copy2(wav, work / "takes" / take["file"])
                if lid in converted and src.exists():
                    take["src"] = f"{lid}_{n}.src.wav"
                    shutil.copy2(src, work / "takes" / take["src"])
                kept.setdefault(lid, []).append(take)
            if flagged[-1].get("reconvert"):
                entry = next(k for k in kept[lid] if k["sha"] == take["sha"])
                entry["reconverted"] = entry.get("reconverted", 0) + 1
        if notes or show_all:
            dur = sf.info(wav).duration
            print(f"L{i:02d} {line['start']:7.2f} {line['voice']:10s} {dur:4.2f}s  {'; '.join(notes) or 'ok'}\n"
                  f"      want: {line['text']}\n      got:  {got}")
    print(f"{len(flagged)} of {len(lines)} lines flagged")
    if kept:
        kept_path.write_text(json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8")
    if restored:
        (work / "rendered.json").write_text(json.dumps(rendered, indent=1), encoding="utf-8")
        if converted:
            (work / "converted.json").write_text(json.dumps(converted, indent=1), encoding="utf-8")
        print(f"put back {len(restored)} earlier takes: the next `scripts/dub.py generate {name}` re-mixes them")
    if reroll and flagged:
        (work / "reroll.json").write_text(json.dumps(flagged, ensure_ascii=False, indent=1), encoding="utf-8")
        again = sum(1 for f in flagged if f.get("reconvert"))
        what = [f"{n} {kind}" for n, kind in ((len(flagged) - again, "for fresh takes"),
                                              (again, "to be converted again")) if n]
        print(f"queued {' and '.join(what)} on the next `scripts/dub.py generate {name}` "
              f"(their current takes are kept, and put back if the new ones are worse)")


RECONVERTS = 2  # conversions of one IndexTTS take tried again before it gets a fresh take


def hear(model, wav, lang="en"):
    segs, _ = model.transcribe(str(wav), language=lang, beam_size=5, vad_filter=False,
                               condition_on_previous_text=False)
    return ("" if lang == "ja" else " ").join(s.text.strip() for s in segs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="+", metavar="name", help="one or more rendered parts (Whisper loads once)")
    ap.add_argument("--all", action="store_true", help="print every line, not just flagged ones")
    ap.add_argument("--reroll", action="store_true", help="queue flagged lines for fresh takes")
    args = ap.parse_args()
    hold_gpu()
    model = WhisperModel("Systran/faster-whisper-large-v3", device="auto", compute_type="default")
    for name in args.names:
        if len(args.names) > 1:
            print(f"=== {name}")
        check(name, model, args.all, args.reroll)


if __name__ == "__main__":
    main()
