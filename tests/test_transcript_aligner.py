import unittest

from utils import transcript_aligner


def _segments(*items, words=False):
    """Build timing segments: (start, end, text[, speaker])."""
    segments = []
    for item in items:
        start, end, text = item[0], item[1], item[2]
        speaker = item[3] if len(item) > 3 else None
        segment = {"start": start, "end": end, "text": text, "speaker": speaker}
        if words:
            tokens = text.split()
            step = (end - start) / max(1, len(tokens))
            segment["words"] = [
                {
                    "text": token,
                    "start": round(start + index * step, 3),
                    "end": round(start + (index + 1) * step, 3),
                }
                for index, token in enumerate(tokens)
            ]
        segments.append(segment)
    return segments


def _assert_monotonic(testcase, cues):
    previous_end = 0.0
    for cue in cues:
        testcase.assertGreaterEqual(cue["start"], 0.0)
        testcase.assertGreater(cue["end"], cue["start"])
        testcase.assertGreaterEqual(cue["start"], previous_end - 1e-9)
        previous_end = cue["end"]


class TestCleanAlignment(unittest.TestCase):
    def test_identical_transcripts_align_with_full_confidence(self):
        timing = _segments(
            (0.0, 2.0, "the quick brown fox"),
            (2.0, 4.0, "jumps over the lazy dog"),
            words=True,
        )
        result = transcript_aligner.align_transcripts(
            "The quick brown fox jumps over the lazy dog.", timing
        )

        self.assertEqual(1, len(result["cues"]))
        cue = result["cues"][0]
        self.assertEqual("The quick brown fox jumps over the lazy dog.", cue["text"])
        self.assertEqual(0.0, cue["start"])
        self.assertEqual(4.0, cue["end"])
        self.assertEqual("canonical", cue["source"])
        self.assertEqual(1.0, cue["alignment_confidence"])
        self.assertEqual([], result["unresolved_spans"])
        _assert_monotonic(self, result["cues"])

    def test_punctuation_only_differences_still_align(self):
        timing = _segments((0.0, 3.0, "hello world how are you"), words=True)
        result = transcript_aligner.align_transcripts(
            "Hello, world! How are you?", timing
        )

        self.assertEqual(2, len(result["cues"]))
        self.assertEqual("Hello, world!", result["cues"][0]["text"])
        self.assertEqual("How are you?", result["cues"][1]["text"])
        for cue in result["cues"]:
            self.assertGreaterEqual(cue["alignment_confidence"], 0.9)
        _assert_monotonic(self, result["cues"])

    def test_word_timestamps_give_precise_sentence_bounds(self):
        timing = _segments((0.0, 10.0, "one two three four five"), words=True)
        result = transcript_aligner.align_transcripts(
            "One two. Three four five.", timing
        )

        first, second = result["cues"]
        self.assertEqual(0.0, first["start"])
        self.assertEqual(4.0, first["end"])
        self.assertEqual(4.0, second["start"])
        self.assertEqual(10.0, second["end"])


class TestInsertionsAndDeletions(unittest.TestCase):
    def test_canonical_insertion_stays_within_supporting_bounds(self):
        # Canonical heard an extra word the timing transcript missed.
        timing = _segments(
            (0.0, 2.0, "we visited the museum"),
            (2.0, 4.0, "yesterday afternoon together"),
            words=True,
        )
        result = transcript_aligner.align_transcripts(
            "We visited the famous museum yesterday afternoon together.", timing
        )

        self.assertEqual(1, len(result["cues"]))
        cue = result["cues"][0]
        self.assertIn("famous", cue["text"])
        self.assertEqual(0.0, cue["start"])
        self.assertEqual(4.0, cue["end"])
        _assert_monotonic(self, result["cues"])

    def test_long_missing_semantic_span_falls_back_to_timing_text(self):
        # The middle timing segment has speech the canonical transcript lost.
        timing = _segments(
            (0.0, 2.0, "alpha bravo charlie"),
            (2.0, 6.0, "delta echo foxtrot golf hotel"),
            (6.0, 8.0, "india juliett kilo"),
            words=True,
        )
        result = transcript_aligner.align_transcripts(
            "Alpha bravo charlie. India juliett kilo.", timing
        )

        fallbacks = [cue for cue in result["cues"] if cue["source"] == "timing_fallback"]
        self.assertEqual(1, len(fallbacks))
        self.assertEqual("delta echo foxtrot golf hotel", fallbacks[0]["text"])
        self.assertEqual(2.0, fallbacks[0]["start"])
        self.assertEqual(6.0, fallbacks[0]["end"])
        self.assertIn("canonical_timing_disagreement", fallbacks[0]["flags"])
        self.assertEqual(1, result["stats"]["fallback_cues"])
        _assert_monotonic(self, result["cues"])

    def test_unmatched_canonical_sentence_is_recorded_not_dropped(self):
        timing = _segments((0.0, 2.0, "actual spoken words"), words=True)
        result = transcript_aligner.align_transcripts(
            "Actual spoken words. Completely hallucinated sentence here.", timing
        )

        self.assertEqual(1, len(result["cues"]))
        self.assertEqual(1, len(result["unresolved_spans"]))
        self.assertIn("hallucinated", result["unresolved_spans"][0]["text"])


class TestRepeatedPhrases(unittest.TestCase):
    def test_repeated_phrases_stay_monotonic(self):
        timing = _segments(
            (0.0, 2.0, "good morning everyone"),
            (2.0, 4.0, "good morning everyone"),
            (4.0, 6.0, "welcome to the show"),
            words=True,
        )
        result = transcript_aligner.align_transcripts(
            "Good morning everyone. Good morning everyone. Welcome to the show.",
            timing,
        )

        _assert_monotonic(self, result["cues"])
        texts = [cue["text"] for cue in result["cues"]]
        self.assertIn("Welcome to the show.", texts[-1])

    def test_numbers_and_names_anchor_alignment(self):
        timing = _segments(
            (0.0, 2.0, "the price is 42 dollars"),
            (2.0, 4.0, "said Johnson yesterday"),
            words=True,
        )
        result = transcript_aligner.align_transcripts(
            "The price is 42 dollars, said Johnson yesterday.", timing
        )

        cue = result["cues"][0]
        self.assertIn("42", cue["text"])
        self.assertIn("Johnson", cue["text"])
        self.assertEqual(0.0, cue["start"])
        self.assertEqual(4.0, cue["end"])


class TestMultilingual(unittest.TestCase):
    def test_thai_without_whitespace_aligns(self):
        # Thai characters tokenize individually; identical text aligns fully.
        timing = _segments(
            (0.0, 2.5, "สวัสดีครับทุกคน"),
            (2.5, 5.0, "วันนี้อากาศดีมาก"),
        )
        result = transcript_aligner.align_transcripts(
            "สวัสดีครับทุกคน วันนี้อากาศดีมาก", timing
        )

        self.assertTrue(result["cues"])
        _assert_monotonic(self, result["cues"])
        joined = "".join(cue["text"] for cue in result["cues"])
        self.assertIn("สวัสดีครับทุกคน", joined)
        self.assertIn("อากาศดีมาก", joined)
        self.assertEqual([], result["unresolved_spans"])

    def test_chinese_text_aligns_per_character(self):
        timing = _segments(
            (0.0, 2.0, "今天天气很好"),
            (2.0, 4.0, "我们一起去公园"),
        )
        result = transcript_aligner.align_transcripts(
            "今天天气很好。我们一起去公园。", timing
        )

        self.assertEqual(2, len(result["cues"]))
        self.assertEqual("今天天气很好。", result["cues"][0]["text"])
        self.assertEqual(0.0, result["cues"][0]["start"])
        self.assertEqual(2.0, result["cues"][0]["end"])
        _assert_monotonic(self, result["cues"])

    def test_mixed_thai_english_code_switching(self):
        timing = _segments(
            (0.0, 3.0, "ผมใช้ iPhone ทุกวัน"),
            (3.0, 6.0, "มันมี AI assistant ในตัว"),
        )
        result = transcript_aligner.align_transcripts(
            "ผมใช้ iPhone ทุกวัน มันมี AI assistant ในตัว", timing
        )

        self.assertTrue(result["cues"])
        joined = " ".join(cue["text"] for cue in result["cues"])
        self.assertIn("iPhone", joined)
        self.assertIn("assistant", joined)
        _assert_monotonic(self, result["cues"])

    def test_mixed_chinese_english(self):
        timing = _segments(
            (0.0, 2.0, "我们使用 GitHub Actions"),
            (2.0, 4.0, "部署这个 Python 项目"),
        )
        result = transcript_aligner.align_transcripts(
            "我们使用 GitHub Actions 部署这个 Python 项目。", timing
        )

        joined = " ".join(cue["text"] for cue in result["cues"])
        self.assertIn("GitHub", joined)
        self.assertIn("Python", joined)
        _assert_monotonic(self, result["cues"])


class TestSpeakerBoundaries(unittest.TestCase):
    def test_cue_never_crosses_speaker_change(self):
        timing = _segments(
            (0.0, 2.0, "how are you doing today", "spk_0"),
            (2.0, 4.0, "i am doing fine thanks", "spk_1"),
            words=True,
        )
        # One canonical sentence spans both speakers.
        result = transcript_aligner.align_transcripts(
            "How are you doing today i am doing fine thanks", timing
        )

        canonical_cues = [cue for cue in result["cues"] if cue["source"] == "canonical"]
        self.assertEqual(2, len(canonical_cues))
        self.assertEqual("spk_0", canonical_cues[0]["speaker"])
        self.assertEqual("spk_1", canonical_cues[1]["speaker"])
        self.assertLessEqual(canonical_cues[0]["end"], 2.0)
        self.assertGreaterEqual(canonical_cues[1]["start"], 2.0)
        _assert_monotonic(self, result["cues"])

    def test_diarized_speakers_are_preserved_on_cues(self):
        timing = _segments(
            (0.0, 2.0, "hello there", "spk_0"),
            (2.0, 4.0, "general kenobi", "spk_1"),
            words=True,
        )
        result = transcript_aligner.align_transcripts(
            "Hello there. General kenobi.", timing
        )

        speakers = [cue["speaker"] for cue in result["cues"]]
        self.assertEqual(["spk_0", "spk_1"], speakers)


class TestPauseAndLimits(unittest.TestCase):
    def test_long_pause_splits_thai_run_without_punctuation(self):
        timing = _segments(
            (0.0, 3.0, "สวัสดีครับทุกคน"),
            (8.0, 11.0, "วันนี้เรามาดูกัน"),
        )
        result = transcript_aligner.align_transcripts(
            "สวัสดีครับทุกคน วันนี้เรามาดูกัน", timing
        )

        self.assertGreaterEqual(len(result["cues"]), 2)
        self.assertLessEqual(result["cues"][0]["end"], 3.0)
        self.assertGreaterEqual(result["cues"][1]["start"], 8.0)

    def test_pause_split_respects_grapheme_boundaries(self):
        # The timing boundary lands on a Thai combining vowel; the text split
        # must move past it so no cue starts mid-grapheme.
        timing = _segments(
            (0.0, 2.0, "สวัสด"),
            (8.0, 10.0, "ีครับทุกคน"),
        )
        result = transcript_aligner.align_transcripts("สวัสดีครับทุกคน", timing)

        self.assertEqual(
            ["สวัสดี", "ครับทุกคน"], [cue["text"] for cue in result["cues"]]
        )

    def test_chunk_boundary_overlap_duplicate_content(self):
        # Simulates semantic chunk overlap already stitched; timing has the
        # phrase once. Alignment must not duplicate cues.
        timing = _segments(
            (0.0, 2.0, "closing remarks and thanks"),
            (2.0, 4.0, "see you next week"),
            words=True,
        )
        result = transcript_aligner.align_transcripts(
            "Closing remarks and thanks. See you next week.", timing
        )

        self.assertEqual(2, len(result["cues"]))
        _assert_monotonic(self, result["cues"])


class TestConfidenceAndFallback(unittest.TestCase):
    def test_unmatched_canonical_is_rescued_onto_uncovered_timing(self):
        # Canonical text keeps priority: with no accepted cue covering the
        # segment, the canonical sentence is rescued onto its trusted times.
        timing = _segments(
            (0.0, 2.0, "real spoken content here"),
            words=True,
        )
        result = transcript_aligner.align_transcripts(
            "Zzz qqq www yyy xxx.", timing
        )

        self.assertEqual(1, len(result["cues"]))
        cue = result["cues"][0]
        self.assertEqual("unresolved_rescue", cue["source"])
        self.assertEqual("Zzz qqq www yyy xxx", cue["text"])
        self.assertEqual(0.0, cue["start"])
        self.assertEqual(2.0, cue["end"])
        self.assertEqual(1, len(result["unresolved_spans"]))
        self.assertTrue(result["unresolved_spans"][0]["rescued"])

    def test_confidence_threshold_controls_rescue(self):
        timing = _segments(
            (0.0, 4.0, "alpha beta gamma delta epsilon zeta"),
            words=True,
        )
        canonical = "Alpha beta unknown1 unknown2 unknown3 unknown4."

        strict = transcript_aligner.align_transcripts(
            canonical, timing, confidence_threshold=0.9
        )
        self.assertEqual("unresolved_rescue", strict["cues"][0]["source"])
        self.assertEqual(0.0, strict["cues"][0]["alignment_confidence"])

        lenient = transcript_aligner.align_transcripts(
            canonical, timing, confidence_threshold=0.2
        )
        self.assertEqual("canonical", lenient["cues"][0]["source"])

    def test_stats_and_ids_are_stable(self):
        timing = _segments((0.0, 2.0, "stable ids here"), words=True)
        result = transcript_aligner.align_transcripts("Stable ids here.", timing)

        self.assertEqual("cue_000001", result["cues"][0]["id"])
        self.assertEqual(["timing_000001"], result["cues"][0]["timing_ids"])
        self.assertIn("mean_confidence", result["stats"])


class TestHallucinationWarnings(unittest.TestCase):
    def test_repeated_fallback_loop_is_kept_and_recorded(self):
        timing = _segments(
            (0.0, 2.0, "alpha bravo charlie delta"),
            (10.0, 10.4, "watermark credit line"),
            (20.0, 20.4, "watermark credit line"),
            (30.0, 30.4, "watermark credit line"),
            words=True,
        )
        result = transcript_aligner.align_transcripts(
            "Alpha bravo charlie delta.", timing
        )

        self.assertEqual(4, len(result["cues"]))
        self.assertEqual("canonical", result["cues"][0]["source"])
        self.assertEqual(0, result["stats"]["dropped_fallback_cues"])
        self.assertEqual(3, result["stats"]["fallback_warning_cues"])
        self.assertEqual([], result["dropped_fallbacks"])
        self.assertEqual(
            {"repeated_fallback_loop"},
            {item["reason"] for item in result["fallback_warnings"]},
        )
        fallbacks = [cue for cue in result["cues"] if cue["source"] == "timing_fallback"]
        self.assertEqual(3, len(fallbacks))
        self.assertTrue(all("repeated_fallback_loop" in cue["flags"] for cue in fallbacks))

    def test_isolated_foreign_word_fallback_is_kept_and_flagged(self):
        timing = _segments(
            (0.0, 2.5, "สวัสดีครับทุกคน"),
            (3.0, 3.5, "ideas"),
            (4.0, 6.5, "วันนี้อากาศดีมาก"),
        )
        result = transcript_aligner.align_transcripts(
            "สวัสดีครับทุกคน วันนี้อากาศดีมาก", timing
        )

        joined = "".join(cue["text"] for cue in result["cues"])
        self.assertIn("ideas", joined)
        self.assertEqual(0, result["stats"]["dropped_fallback_cues"])
        self.assertEqual(
            "isolated_foreign_word", result["fallback_warnings"][0]["reason"]
        )

    def test_matching_script_fallback_is_kept(self):
        # A same-script uncovered segment is real missed speech, not noise.
        timing = _segments(
            (0.0, 2.0, "alpha bravo charlie delta"),
            (10.0, 10.5, "echo"),
            words=True,
        )
        result = transcript_aligner.align_transcripts(
            "Alpha bravo charlie delta.", timing
        )

        fallbacks = [cue for cue in result["cues"] if cue["source"] == "timing_fallback"]
        self.assertEqual(1, len(fallbacks))
        self.assertEqual("echo", fallbacks[0]["text"])
        self.assertEqual(0, result["stats"]["dropped_fallback_cues"])

    def test_upstream_suspect_hallucination_flag_is_preserved_as_warning(self):
        timing = _segments(
            (0.0, 2.0, "alpha bravo charlie delta"),
            (5.0, 8.0, "some noise words"),
            words=True,
        )
        timing[1]["suspect_hallucination"] = True
        result = transcript_aligner.align_transcripts(
            "Alpha bravo charlie delta.", timing
        )

        self.assertEqual(2, len(result["cues"]))
        self.assertEqual(
            "suspect_hallucination", result["fallback_warnings"][0]["reason"]
        )
        self.assertIn("some noise words", [cue["text"] for cue in result["cues"]])


class TestTrustedTimingIntegrity(unittest.TestCase):
    def test_overlapping_evidence_is_not_shifted_or_collapsed(self):
        timing = _segments(
            (0.0, 10.0, "alpha"),
            (5.0, 6.0, "beta"),
        )
        result = transcript_aligner.align_transcripts("Alpha. Beta.", timing)

        self.assertEqual((0.0, 10.0), (
            result["cues"][0]["start"], result["cues"][0]["end"]
        ))
        self.assertEqual((5.0, 6.0), (
            result["cues"][1]["start"], result["cues"][1]["end"]
        ))

    def test_invalid_timing_evidence_is_rejected_instead_of_fabricated(self):
        timing = _segments((5.0, 5.0, "alpha"))
        with self.assertRaises(ValueError):
            transcript_aligner.align_transcripts("Alpha.", timing)


class TestUnicodeScripts(unittest.TestCase):
    def test_supported_unicode_scripts_use_semantic_alignment(self):
        samples = (
            "مرحبا بالعالم",
            "Привет мир",
            "नमस्ते दुनिया",
            "សួស្តីពិភពលោក",
        )
        for text in samples:
            with self.subTest(text=text):
                result = transcript_aligner.align_transcripts(
                    text, _segments((0.0, 2.0, text))
                )
                self.assertGreater(result["stats"]["canonical_tokens"], 0)
                self.assertEqual("canonical", result["cues"][0]["source"])
                self.assertEqual(1.0, result["cues"][0]["alignment_confidence"])


class TestUnresolvedSpanRescue(unittest.TestCase):
    def test_unresolved_sentence_is_rescued_onto_garbled_segments(self):
        # Whisper garbled the second sentence completely; the canonical text
        # must survive on the garbled segments' trusted times.
        timing = _segments(
            (0.0, 2.0, "alpha bravo charlie delta"),
            (2.0, 4.0, "zzz qqq www"),
            (4.0, 6.0, "yyy xxx vvv"),
            words=True,
        )
        result = transcript_aligner.align_transcripts(
            "Alpha bravo charlie delta. "
            "Echo foxtrot golf hotel india juliett kilo lima.",
            timing,
        )

        rescued = [
            cue for cue in result["cues"] if cue["source"] == "unresolved_rescue"
        ]
        self.assertEqual(2, len(rescued))
        self.assertEqual(
            "Echo foxtrot golf hotel india juliett kilo lima",
            " ".join(cue["text"] for cue in rescued),
        )
        self.assertEqual(2.0, rescued[0]["start"])
        self.assertEqual(6.0, rescued[-1]["end"])
        self.assertIn("unresolved_span_rescue", rescued[0]["flags"])
        self.assertEqual(0, result["stats"]["fallback_cues"])
        self.assertEqual(2, result["stats"]["rescued_cues"])
        self.assertTrue(result["unresolved_spans"][0]["rescued"])
        _assert_monotonic(self, result["cues"])

    def test_rescue_skips_text_spoken_during_untimed_silence(self):
        # One lone garbled segment far from the matched region: only the text
        # near its position in the gap lands on it; the rest stays unresolved
        # instead of being crammed in at unreadable density.
        timing = _segments(
            (0.0, 2.0, "alpha bravo charlie delta"),
            (50.0, 51.0, "zzz qqq"),
            words=True,
        )
        result = transcript_aligner.align_transcripts(
            "Alpha bravo charlie delta. "
            "Echo foxtrot golf hotel india juliett kilo lima mike november "
            "oscar papa quebec romeo sierra tango uniform victor whiskey xray.",
            timing,
        )

        rescued = [
            cue for cue in result["cues"] if cue["source"] == "unresolved_rescue"
        ]
        self.assertEqual(1, len(rescued))
        self.assertLessEqual(len(rescued[0]["text"].split()), 3)
        self.assertTrue(rescued[0]["text"].endswith("xray"))
        self.assertEqual(50.0, rescued[0]["start"])
        self.assertEqual(51.0, rescued[0]["end"])
        self.assertEqual(0, result["stats"]["fallback_cues"])

    def test_long_unmatched_tail_moves_to_hosts_instead_of_stretching_cue(self):
        # The resolved sentence's tail (>=10 tokens) has no matched timing;
        # it must ride the garbled segment in the gap, not inflate the
        # matched cue far beyond its timed span.
        matched_head = (
            "alpha bravo charlie delta echo foxtrot golf hotel india "
            "juliett kilo lima"
        )
        timing = _segments(
            (0.0, 3.0, matched_head),
            (3.0, 5.0, "zzz qqq www"),
            words=True,
        )
        result = transcript_aligner.align_transcripts(
            "Alpha bravo charlie delta echo foxtrot golf hotel india "
            "juliett kilo lima one two three four five six seven eight "
            "nine ten.",
            timing,
        )

        canonical = [cue for cue in result["cues"] if cue["source"] == "canonical"]
        rescued = [
            cue for cue in result["cues"] if cue["source"] == "unresolved_rescue"
        ]
        self.assertEqual(1, len(canonical))
        self.assertNotIn("ten", canonical[0]["text"])
        self.assertEqual(3.0, canonical[0]["end"])
        self.assertEqual(1, len(rescued))
        self.assertEqual(
            "one two three four five six seven eight nine ten",
            rescued[0]["text"],
        )
        self.assertEqual(3.0, rescued[0]["start"])
        self.assertEqual(5.0, rescued[0]["end"])
        self.assertEqual(0, result["stats"]["fallback_cues"])

    def test_long_unmatched_tail_without_hosts_stays_attached(self):
        timing = _segments(
            (0.0, 3.0,
             "alpha bravo charlie delta echo foxtrot golf hotel india "
             "juliett kilo lima"),
            words=True,
        )
        sentence = (
            "Alpha bravo charlie delta echo foxtrot golf hotel india "
            "juliett kilo lima one two three four five six seven eight "
            "nine ten."
        )
        result = transcript_aligner.align_transcripts(sentence, timing)

        self.assertEqual(1, len(result["cues"]))
        self.assertEqual(sentence, result["cues"][0]["text"])
        self.assertEqual("canonical", result["cues"][0]["source"])
        # The display layer needs to know this cue carries un-timed trailing
        # speech so it can extend the display window forward.
        self.assertIn("unhosted_trail_run", result["cues"][0]["flags"])

    def test_long_unmatched_lead_without_hosts_is_flagged(self):
        timing = _segments(
            (20.0, 23.0,
             "alpha bravo charlie delta echo foxtrot golf hotel india "
             "juliett kilo lima"),
            words=True,
        )
        sentence = (
            "One two three four five six seven eight nine ten alpha bravo "
            "charlie delta echo foxtrot golf hotel india juliett kilo lima."
        )
        result = transcript_aligner.align_transcripts(sentence, timing)

        self.assertEqual(1, len(result["cues"]))
        self.assertEqual(sentence, result["cues"][0]["text"])
        self.assertIn("unhosted_lead_run", result["cues"][0]["flags"])

    def test_short_unmatched_tail_stays_attached(self):
        # Below the run threshold nothing is stripped; behavior is unchanged.
        timing = _segments(
            (0.0, 2.0, "alpha bravo charlie delta"),
            (2.0, 4.0, "zzz qqq www"),
            words=True,
        )
        result = transcript_aligner.align_transcripts(
            "Alpha bravo charlie delta echo foxtrot.", timing
        )

        canonical = [cue for cue in result["cues"] if cue["source"] == "canonical"]
        self.assertEqual(1, len(canonical))
        self.assertEqual("Alpha bravo charlie delta echo foxtrot.", canonical[0]["text"])
        self.assertEqual(
            0, sum(1 for cue in result["cues"] if cue["source"] == "unresolved_rescue")
        )

    def test_stripped_tail_merges_with_following_unresolved_sentence(self):
        # Tail of resolved sentence A and all of unresolved sentence B form
        # one contiguous unmatched run; they share the gap's hosts in order.
        matched_head = (
            "alpha bravo charlie delta echo foxtrot golf hotel india "
            "juliett kilo lima"
        )
        timing = _segments(
            (0.0, 3.0, matched_head),
            (3.0, 7.0, "zzz qqq www yyy xxx vvv ttt sss"),
            (7.0, 9.0, "mike november oscar papa"),
            words=True,
        )
        result = transcript_aligner.align_transcripts(
            "Alpha bravo charlie delta echo foxtrot golf hotel india "
            "juliett kilo lima one two three four five six seven eight "
            "nine ten. Eleven twelve thirteen fourteen fifteen sixteen "
            "seventeen eighteen nineteen twenty. Mike november oscar papa.",
            timing,
        )

        rescued = [
            cue for cue in result["cues"] if cue["source"] == "unresolved_rescue"
        ]
        self.assertEqual(1, len(rescued))
        self.assertEqual(3.0, rescued[0]["start"])
        self.assertEqual(7.0, rescued[0]["end"])
        words = rescued[0]["text"].split()
        self.assertLess(0, len(words))
        # Slice order follows canonical order across the merged run.
        all_run_words = (
            "one two three four five six seven eight nine ten eleven twelve "
            "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty"
        ).split()
        positions = [all_run_words.index(word.lower()) for word in words]
        self.assertEqual(sorted(positions), positions)
        self.assertTrue(result["unresolved_spans"][0]["rescued"])

    def test_unhosted_unresolved_span_stays_unrescued(self):
        timing = _segments((0.0, 2.0, "actual spoken words"), words=True)
        result = transcript_aligner.align_transcripts(
            "Actual spoken words. Completely hallucinated sentence here.", timing
        )

        self.assertEqual(1, len(result["cues"]))
        self.assertFalse(result["unresolved_spans"][0]["rescued"])
        self.assertEqual(0, result["stats"]["rescued_cues"])


if __name__ == "__main__":
    unittest.main()
