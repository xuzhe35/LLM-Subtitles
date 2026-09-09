"""Property-based invariants for the aligner and display extension.

Each seed builds a randomized scenario (Thai-like or Latin words, garbled /
missing / hallucinated timing segments, overlaps, watermark loops) and checks
invariants that must hold for every input:

  A. align_transcripts never raises.
  B. Cues are ordered by start, preserve evidence overlaps, end > start,
     and contain non-empty text.
  C. No canonical token is displayed twice across canonical + rescue cues.
  D. Cue text tokens appear in canonical order (combining marks excluded:
     NFKC canonical ordering may legally reorder them when a token join
     glues marks across a dropped space).
  E. extend_display_times never shrinks evidence, never creates a new overlap,
     never moves an unflagged start, and consumes its marker.
"""
import random
import unittest
import unicodedata
from collections import Counter

from utils import subtitle_formatter as sf
from utils import transcript_aligner as ta

VOCAB = ("alpha bravo charlie delta echo foxtrot golf hotel india juliett "
         "kilo lima mike november oscar papa quebec romeo sierra tango "
         "uniform victor whiskey xray yankee zulu one two three four five "
         "six seven eight nine ten").split()
THAI = list("สวัสดีครับทุกคนวันนี้อากาศมากจิตใจธรรม")
COMBINING = list("ัิีึื่้๊๋์ำ")
GARBLE = ("zzz qqq www yyy xxx vvv ttt sss rrr ppp nnn mmm lll kkk jjj "
          "hhh ggg fff ddd bbb").split()
WATERMARK = "watermark credit line"
SEEDS = 200


def make_scenario(rng):
    thai_mode = rng.random() < 0.4
    n_words = rng.randint(3, 120)
    if thai_mode:
        words = []
        for _ in range(n_words):
            ch = rng.choice(THAI)
            if rng.random() < 0.3:
                ch += rng.choice(COMBINING)
            words.append(ch)
    else:
        words = [rng.choice(VOCAB) for _ in range(n_words)]

    sep = "" if thai_mode else " "
    parts = []
    for i, word in enumerate(words):
        parts.append(word)
        if rng.random() < 0.12:
            parts.append(rng.choice([". ", "! ", "? ", ". "]))
        elif not thai_mode and i < len(words) - 1:
            parts.append(" ")
        elif thai_mode and rng.random() < 0.05:
            parts.append(" ")
    canonical = "".join(parts)

    segments = []
    t = rng.uniform(0.0, 5.0)
    i = 0
    while i < len(words):
        take = rng.randint(1, 8)
        chunk = words[i:i + take]
        i += take
        mode = rng.random()
        if mode < 0.15:
            continue  # timing heard nothing here
        if mode < 0.35:
            chunk = [rng.choice(GARBLE) for _ in chunk]
        elif mode < 0.42:
            chunk = []
        dur = rng.uniform(0.2, 1.0) * max(1, len(chunk))
        text = " ".join(chunk) if chunk else ""
        seg = {"start": round(t, 3), "end": round(t + dur, 3),
               "text": text, "speaker": rng.choice([None, "spk_0", "spk_1"])}
        if rng.random() < 0.5 and chunk:
            step = dur / max(1, len(chunk))
            seg["words"] = [
                {"text": c, "start": round(t + k * step, 3),
                 "end": round(t + (k + 1) * step, 3)}
                for k, c in enumerate(chunk)
            ]
        if rng.random() < 0.1:
            seg["suspect_hallucination"] = True
        segments.append(seg)
        t += dur + (rng.uniform(0.0, 6.0) if rng.random() < 0.3 else 0.0)
        if rng.random() < 0.12:
            for _ in range(rng.randint(1, 4)):
                wdur = rng.uniform(0.1, 0.5)
                segments.append({"start": round(t, 3), "end": round(t + wdur, 3),
                                 "text": WATERMARK, "speaker": None})
                t += wdur + rng.uniform(1.0, 10.0)
    return canonical, segments


def _is_subsequence(needle, haystack):
    it = iter(haystack)
    return all(any(tok == h for h in it) for tok in needle)


def _solid_norms(text):
    return [
        "".join(ch for ch in t.norm
                if not unicodedata.category(ch).startswith("M"))
        for t in ta.tokenize(text)
        if any(not unicodedata.category(ch).startswith("M") for ch in t.norm)
    ]


class TestAlignerProperties(unittest.TestCase):
    def test_randomized_scenarios_hold_invariants(self):
        for seed in range(SEEDS):
            rng = random.Random(seed)
            canonical, segments = make_scenario(rng)
            if not segments:
                continue
            with self.subTest(seed=seed):
                self._check(rng, canonical, segments)

    def _check(self, rng, canonical, segments):
        result = ta.align_transcripts(canonical, segments)
        cues = result["cues"]

        previous_start = -1.0
        for cue in cues:
            self.assertTrue(cue["text"].strip())
            self.assertGreater(cue["end"], cue["start"])
            self.assertGreaterEqual(cue["start"], previous_start - 1e-6)
            previous_start = cue["start"]

        canonical_counts = Counter(_solid_norms(canonical))
        displayed = Counter()
        for cue in cues:
            if cue["source"] in ("canonical", "unresolved_rescue"):
                displayed.update(_solid_norms(cue["text"]))
        for norm, count in displayed.items():
            self.assertLessEqual(count, canonical_counts[norm], norm)

        canonical_norms = _solid_norms(canonical)
        for cue in cues:
            if cue["source"] in ("canonical", "unresolved_rescue"):
                self.assertTrue(
                    _is_subsequence(_solid_norms(cue["text"]), canonical_norms),
                    cue["text"][:60],
                )

        display_in = []
        for cue in cues:
            seg = {"start": cue["start"], "end": cue["end"],
                   "text": "中" * rng.randint(0, 60)}
            if "unhosted_lead_run" in cue["flags"]:
                seg["extend_lead"] = True
            display_in.append(seg)
        out = sf.extend_display_times(display_in)
        self.assertEqual(len(display_in), len(out))
        for idx, (before, after) in enumerate(zip(display_in, out)):
            self.assertGreaterEqual(after["end"], before["end"] - 1e-9)
            self.assertLessEqual(after["start"], before["start"] + 1e-9)
            if "extend_lead" not in before:
                self.assertAlmostEqual(after["start"], before["start"])
            if (idx + 1 < len(out)
                    and before["end"] <= display_in[idx + 1]["start"]):
                self.assertLessEqual(after["end"], out[idx + 1]["start"] + 1e-9)
            self.assertNotIn("extend_lead", after)


if __name__ == "__main__":
    unittest.main()
