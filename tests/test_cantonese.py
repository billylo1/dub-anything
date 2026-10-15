"""Cantonese (yue) language module tests."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from languages import ISO3, LANGUAGES, NAMES, PAIRS, pair_of


class CantoneseLanguageTests(unittest.TestCase):
    def test_cantonese_metadata(self):
        self.assertIn("yue", LANGUAGES)
        yue = LANGUAGES["yue"]
        self.assertEqual(yue.NAME, "Cantonese")
        self.assertEqual(yue.ISO3, "yue")
        self.assertEqual(yue.NATIVE, "粵語")
        self.assertEqual(NAMES["yue"], "Cantonese")
        self.assertEqual(ISO3["yue"], "yue")
        self.assertIn(("en", "yue"), PAIRS)
        self.assertEqual(pair_of({"source_lang": "en", "target_lang": "yue"}), ("en", "yue"))
        self.assertEqual(yue.VOICES, ("actor",))
        self.assertEqual(yue.JOIN, "")

    def test_cantonese_units(self):
        units = LANGUAGES["yue"].units
        self.assertEqual(units("你 好，ＡＢＣ１２！"), units("你好abc12"))
        self.assertNotEqual(units("我哋去"), units("我哋唔去"))

    def test_cantonese_compare(self):
        compare = LANGUAGES["yue"].compare
        # Exact match
        notes, ratio = compare("你好，歡迎收睇。", "你好歡迎收睇")
        self.assertEqual(notes, [])
        self.assertGreaterEqual(ratio, 0.8)

        # Negation check
        notes_neg, _ = compare("我得閒", "我唔得閒")
        self.assertTrue(any("negation differs" in n for n in notes_neg))

        # Number check
        notes_num, _ = compare("有三個", "有四個")
        self.assertTrue(any("match" in n or "number" in n for n in notes_num))

    def test_cantonese_subtitles(self):
        yue = LANGUAGES["yue"]
        text = "我哋今日準備出發，聽日到咗之後再一齊討論跟住落嚟嘅計劃。"
        wrapped = yue.subtitle(text)
        self.assertEqual(wrapped.replace("\n", ""), text)
        self.assertEqual(wrapped.count("\n"), 1)
