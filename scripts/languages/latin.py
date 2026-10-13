"""What the languages written in Latin letters, with spaces between words, share
(en.py, es.py)."""
import difflib
import re
import unicodedata

MATCH = 0.85  # a take's words against its line's, below which QA flags it


def word_units(text):
    """What second.py compares two transcripts by: lowercase words, without their
    accents (Whisper writes "esta" where another model writes "está") and apostrophes."""
    text = unicodedata.normalize("NFD", text.lower().replace("'", "").replace("’", ""))
    return re.findall(r"[a-z0-9]+", "".join(c for c in text if not unicodedata.combining(c)))


def as_written(text):
    """A subtitle cue's text as it is: players wrap a long line at its spaces."""
    return text


def compare_words(want, got, split, readings, negations, is_number):
    """lineqa.py's check of a take, word by word: (notes, similarity). split(text,
    reading) gives a text's normalized words with its numbers spelled out in that
    reading, and each side is read in whichever of `readings` matches best ("1979"
    is "nineteen seventy nine", "03" "zero three"). Notes: leading junk, the better
    of word- or letter-level match, a changed count of `negations`, and a number
    (a word is_number says one) heard as another."""
    want_w, got_w = max(((split(want, a), split(got, b)) for a in readings for b in readings),
                        key=lambda pair: difflib.SequenceMatcher(None, *pair).ratio())
    notes = []
    if want_w and want_w[0] in got_w[:4] and got_w.index(want_w[0]) > 0:
        notes.append(f"leading extra: '{' '.join(got_w[:got_w.index(want_w[0])])}'")
    ratio = max(difflib.SequenceMatcher(None, want_w, got_w).ratio(),
                difflib.SequenceMatcher(None, "".join(want_w), "".join(got_w)).ratio())
    if not got_w:
        notes.append("silent or unintelligible")
    elif ratio < MATCH:
        notes.append(f"match {ratio:.2f}")
    # A close match can still flip the meaning ("can" heard as "can't", "seventy" as "forty").
    if got_w and sum(w in negations for w in want_w) != sum(w in negations for w in got_w):
        notes.append("negation differs")
    nums = lambda ws: [w for w in ws if is_number(w)]  # noqa: E731
    if got_w and nums(want_w) != nums(got_w):
        notes.append(f"number differs: '{' '.join(nums(want_w))}' -> '{' '.join(nums(got_w))}'")
    return notes, ratio
