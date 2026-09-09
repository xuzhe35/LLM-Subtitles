import unittest

from utils import subtitle_formatter


def _seg(start, end, text, **extra):
    segment = {"start": start, "end": end, "text": text}
    segment.update(extra)
    return segment


class TestExtendDisplayTimes(unittest.TestCase):
    def test_short_cjk_cue_extends_into_following_gap(self):
        text = "这是一段需要更多阅读时间的中文字幕文本内容测试一二三四五六"
        segments = [_seg(0.0, 1.0, text), _seg(10.0, 12.0, "下一条")]
        result = subtitle_formatter.extend_display_times(segments)

        # Dense chars read at 9 chars/sec.
        self.assertAlmostEqual(len(text) / 9.0, result[0]["end"], places=3)
        self.assertEqual(0.0, result[0]["start"])

    def test_extension_never_overlaps_next_cue(self):
        segments = [
            _seg(0.0, 1.0, "这是一段需要更多阅读时间的中文字幕文本内容测试一二三四五六"),
            _seg(2.0, 4.0, "下一条"),
        ]
        result = subtitle_formatter.extend_display_times(segments)

        self.assertEqual(2.0, result[0]["end"])

    def test_already_long_cue_never_shrinks(self):
        segments = [_seg(0.0, 10.0, "短句"), _seg(20.0, 22.0, "下一条")]
        result = subtitle_formatter.extend_display_times(segments)

        self.assertEqual(10.0, result[0]["end"])

    def test_latin_text_uses_faster_rate(self):
        segments = [_seg(0.0, 1.0, "a" * 30), _seg(10.0, 12.0, "next")]
        result = subtitle_formatter.extend_display_times(segments)

        # 30 latin chars at 15 chars/sec need 2s.
        self.assertAlmostEqual(2.0, result[0]["end"], places=3)

    def test_minimum_duration_applies_to_tiny_cues(self):
        segments = [_seg(0.0, 0.3, "嗯"), _seg(5.0, 6.0, "下一条")]
        result = subtitle_formatter.extend_display_times(segments)

        self.assertAlmostEqual(1.0, result[0]["end"], places=3)

    def test_last_cue_does_not_extend_without_media_duration(self):
        segments = [_seg(0.0, 0.5, "这是最后一条比较长的中文字幕内容测试")]
        result = subtitle_formatter.extend_display_times(segments)

        self.assertEqual(0.5, result[0]["end"])

    def test_last_cue_extends_only_to_media_duration(self):
        segments = [_seg(99.0, 100.0, "很长" * 100)]
        result = subtitle_formatter.extend_display_times(
            segments, media_duration=100.0
        )

        self.assertEqual(100.0, result[0]["end"])

    def test_last_cue_uses_available_media_tail(self):
        segments = [_seg(90.0, 91.0, "这是最后一条比较长的中文字幕内容测试")]
        result = subtitle_formatter.extend_display_times(
            segments, media_duration=100.0
        )

        self.assertGreater(result[0]["end"], 91.0)
        self.assertLessEqual(result[0]["end"], 100.0)

    def test_extend_lead_pulls_start_into_remaining_gap(self):
        text = "这一条字幕对应的语音其实早就开始了需要提前出现的很长的中文内容"
        segments = [
            _seg(0.0, 5.0, "前一条"),
            _seg(20.0, 21.0, text, extend_lead=True),
            _seg(21.5, 23.0, "下一条"),
        ]
        result = subtitle_formatter.extend_display_times(segments)

        # Forward extension is blocked at 21.5, so the start moves back to
        # end - needed reading time.
        self.assertEqual(21.5, result[1]["end"])
        self.assertAlmostEqual(21.5 - len(text) / 9.0, result[1]["start"], places=3)
        self.assertGreaterEqual(result[1]["start"], result[0]["end"])

    def test_unflagged_cue_start_never_moves(self):
        segments = [
            _seg(0.0, 5.0, "前一条"),
            _seg(20.0, 21.0, "没有标记的字幕开始时间必须保持在证据时间上不能提前出现"),
            _seg(21.5, 23.0, "下一条"),
        ]
        result = subtitle_formatter.extend_display_times(segments)

        self.assertEqual(20.0, result[1]["start"])

    def test_extend_lead_respects_previous_extended_end(self):
        # The previous cue extends forward first; the flagged cue may only
        # take what is left of the shared gap.
        segments = [
            _seg(0.0, 1.0, "前一条也很长需要向后延展的中文字幕内容测试一二三四五六七八"),
            _seg(
                4.0, 4.5,
                "这一条有标记需要向前延展的中文字幕内容测试一二三四五六七八",
                extend_lead=True,
            ),
            _seg(4.6, 6.0, "下一条堵住向后延展"),
        ]
        result = subtitle_formatter.extend_display_times(segments)

        self.assertEqual(result[0]["end"], result[1]["start"])
        self.assertLess(result[1]["start"], 4.0)

    def test_marker_is_consumed_and_originals_untouched(self):
        segments = [
            _seg(0.0, 5.0, "前一条"),
            _seg(20.0, 21.0, "内容", extend_lead=True),
        ]
        result = subtitle_formatter.extend_display_times(segments)

        for segment in result:
            self.assertNotIn("extend_lead", segment)
        self.assertEqual(21.0, segments[1]["end"])  # inputs not mutated

    def test_empty_text_untouched(self):
        segments = [_seg(0.0, 0.2, ""), _seg(5.0, 6.0, "下一条")]
        result = subtitle_formatter.extend_display_times(segments)

        self.assertEqual(0.2, result[0]["end"])


if __name__ == "__main__":
    unittest.main()
