import unittest
from unittest.mock import MagicMock
from utils import translator, subtitle_formatter
import json
import os
import tempfile

import youtube_subtitle_trans

class TestCore(unittest.TestCase):
    def test_format_timestamp(self):
        # 1 hour, 1 minute, 1 second, 500ms
        seconds = 3661.500
        formatted = subtitle_formatter.format_timestamp(seconds)
        self.assertEqual(formatted, "01:01:01,500")

    def test_generate_srt(self):
        segments = [
            {'start': 0, 'end': 2, 'text': "Hello"},
            {'start': 2.5, 'end': 4, 'text': "World"}
        ]
        output_path = "test_output.srt"
        subtitle_formatter.generate_srt(segments, output_path)
        
        with open(output_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        expected = "1\n00:00:00,000 --> 00:00:02,000\nHello\n\n2\n00:00:02,500 --> 00:00:04,000\nWorld\n\n"
        # Normalize newlines
        self.assertEqual(content.replace('\r\n', '\n'), expected)
        
        os.remove(output_path)

    def test_wrap_subtitle_text_keeps_single_segment_with_internal_line_breaks(self):
        text = "明显白没有故意侵犯的意图，但是我们自己担心是否做错。"

        wrapped = subtitle_formatter.wrap_subtitle_text(text, max_line_chars=12)

        lines = wrapped.split("\n")
        self.assertGreater(len(lines), 1)
        self.assertEqual("".join(lines), text)
        self.assertTrue(all(len(line) <= 13 for line in lines))
        self.assertTrue(all(not line.startswith(("，", "。")) for line in lines))

    def test_generate_srt_wraps_long_translation_without_splitting_cues(self):
        segments = [
            {
                'start': 0,
                'end': 2,
                'text': "明显白没有故意侵犯的意图，但是我们自己担心是否做错。"
            }
        ]
        output_path = "test_wrapped_output.srt"
        subtitle_formatter.generate_srt(segments, output_path, max_line_chars=12)

        try:
            with open(output_path, 'r', encoding='utf-8') as f:
                content = f.read().replace('\r\n', '\n')
        finally:
            os.remove(output_path)

        self.assertEqual(content.count("00:00:00,000 --> 00:00:02,000"), 1)
        self.assertIn("明显白没有故意侵犯的意图，\n但是我们自己担心是否做错。", content)

    def test_translator_mock(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        
        # Mocking the response for _translate_segments_wrapper
        # It expects a JSON object with "segments"
        mock_content = json.dumps({
            "segments": [
                {"id": 0, "start": 0, "end": 2, "text": "你好"},
                {"id": 1, "start": 2, "end": 4, "text": "世界"}
            ]
        })
        
        mock_response.choices = [MagicMock(message=MagicMock(content=mock_content))]
        mock_client.chat.completions.create.return_value = mock_response
        
        segments = [
            {'start': 0, 'end': 2, 'text': "Hello"},
            {'start': 2, 'end': 4, 'text': "World"}
        ]
        
        result = translator.translate_segments(mock_client, segments, "Simplified Chinese")
        
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['text'], "你好")
        self.assertEqual(result[1]['text'], "世界")

    def test_parse_vtt(self):
        vtt_content = """WEBVTT

00:00:00.000 --> 00:00:02.000
Line 1
Line 2

00:00:02.500 --> 00:00:04.000 align:start position:0%
Line 3
"""
        with open("test.vtt", "w", encoding="utf-8") as f:
            f.write(vtt_content)
            
        segments = subtitle_formatter.parse_vtt("test.vtt")
        
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]['start'], 0.0)
        self.assertEqual(segments[0]['end'], 2.0)
        self.assertEqual(segments[0]['text'], "Line 1\nLine 2")
        self.assertEqual(segments[1]['start'], 2.5)
        self.assertEqual(segments[1]['end'], 4.0)
        self.assertEqual(segments[1]['text'], "Line 3")
        
        os.remove("test.vtt")

    def test_parse_srt(self):
        srt_content = """1
00:00:00,000 --> 00:00:02,000
Line 1

2
00:00:02,500 --> 00:00:04,000
Line 2
"""
        with open("test.srt", "w", encoding="utf-8") as f:
            f.write(srt_content)
            
        segments = subtitle_formatter.parse_srt("test.srt")
        
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]['start'], 0.0)
        self.assertEqual(segments[0]['end'], 2.0)
        self.assertEqual(segments[0]['text'], "Line 1")
        
        os.remove("test.srt")

class TestSanitizeFilename(unittest.TestCase):
    def test_ascii_title_preserved(self):
        self.assertEqual(youtube_subtitle_trans.sanitize_filename("Hello World 123"), "Hello World 123")

    def test_cjk_title_preserved(self):
        self.assertEqual(youtube_subtitle_trans.sanitize_filename("中文标题"), "中文标题")

    def test_filesystem_unsafe_chars_replaced(self):
        result = youtube_subtitle_trans.sanitize_filename('My/File:Name?')
        self.assertNotIn('/', result)
        self.assertNotIn(':', result)
        self.assertNotIn('?', result)
        self.assertIn('_', result)
        self.assertTrue(result.startswith('My'))

    def test_emoji_only_falls_back(self):
        # Emoji are not alpha, leaving the cleaned string empty after stripping.
        self.assertEqual(youtube_subtitle_trans.sanitize_filename("😀🎉", fallback="abc123"), "abc123")

    def test_punctuation_only_falls_back(self):
        self.assertEqual(youtube_subtitle_trans.sanitize_filename("!?!?", fallback="vid1"), "vid1")

    def test_empty_input_falls_back(self):
        self.assertEqual(youtube_subtitle_trans.sanitize_filename("", fallback="abc"), "abc")
        self.assertEqual(youtube_subtitle_trans.sanitize_filename(None, fallback="abc"), "abc")

    def test_trailing_dots_and_spaces_stripped(self):
        # Windows rejects trailing dots/spaces — make sure we strip them.
        self.assertEqual(youtube_subtitle_trans.sanitize_filename("video..."), "video")
        self.assertEqual(youtube_subtitle_trans.sanitize_filename("video   "), "video")

    def test_length_capped(self):
        result = youtube_subtitle_trans.sanitize_filename("a" * 500)
        self.assertLessEqual(len(result), 100)

    def test_control_chars_stripped(self):
        result = youtube_subtitle_trans.sanitize_filename("hello\x00\x01world")
        self.assertNotIn('\x00', result)
        self.assertNotIn('\x01', result)
        self.assertIn('hello', result)
        self.assertIn('world', result)


class TestGenerateBilingualSrt(unittest.TestCase):
    def _read(self, path):
        with open(path, 'r', encoding='utf-8') as f:
            return f.read().replace('\r\n', '\n')

    def test_aligned_segments_use_original_timing(self):
        original = [
            {'start': 0.0, 'end': 2.0, 'text': "Hello"},
            {'start': 2.5, 'end': 4.0, 'text': "World"},
        ]
        translated = [
            {'start': 0.0, 'end': 2.0, 'text': "你好"},
            {'start': 2.5, 'end': 4.0, 'text': "世界"},
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            out = os.path.join(tmp_dir, "out.srt")
            subtitle_formatter.generate_bilingual_srt(original, translated, out)
            content = self._read(out)

        self.assertIn("00:00:00,000 --> 00:00:02,000", content)
        self.assertIn("00:00:02,500 --> 00:00:04,000", content)
        # Translated on top, original below.
        self.assertIn("你好\nHello", content)
        self.assertIn("世界\nWorld", content)
        # Never emits the bogus zero-duration entry.
        self.assertEqual(content.count("00:00:00,000 --> 00:00:00,000"), 0)

    def test_translated_shorter_uses_original_timing(self):
        original = [
            {'start': 0.0, 'end': 1.0, 'text': "A"},
            {'start': 1.0, 'end': 2.0, 'text': "B"},
            {'start': 2.0, 'end': 3.0, 'text': "C"},
        ]
        translated = [
            {'start': 0.0, 'end': 1.0, 'text': "甲"},
        ]
        logs = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            out = os.path.join(tmp_dir, "out.srt")
            subtitle_formatter.generate_bilingual_srt(original, translated, out, progress_callback=logs.append)
            content = self._read(out)

        # All three originals appear with their own timing.
        self.assertIn("00:00:00,000 --> 00:00:01,000", content)
        self.assertIn("00:00:01,000 --> 00:00:02,000", content)
        self.assertIn("00:00:02,000 --> 00:00:03,000", content)
        # Never a 00:00:00,000 --> 00:00:00,000 entry.
        self.assertEqual(content.count("00:00:00,000 --> 00:00:00,000"), 0)
        # A mismatch warning was logged.
        self.assertTrue(any("mismatch" in m for m in logs))

    def test_original_shorter_uses_translated_timing_no_zero_entries(self):
        original = [
            {'start': 0.0, 'end': 1.0, 'text': "A"},
        ]
        translated = [
            {'start': 0.0, 'end': 1.0, 'text': "甲"},
            {'start': 1.5, 'end': 2.5, 'text': "乙"},
            {'start': 3.0, 'end': 4.0, 'text': "丙"},
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            out = os.path.join(tmp_dir, "out.srt")
            subtitle_formatter.generate_bilingual_srt(original, translated, out)
            content = self._read(out)

        # Translated entries beyond the originals keep their own timing,
        # not the dreaded 00:00:00,000 placeholder.
        self.assertIn("00:00:01,500 --> 00:00:02,500", content)
        self.assertIn("00:00:03,000 --> 00:00:04,000", content)
        self.assertEqual(content.count("00:00:00,000 --> 00:00:00,000"), 0)

    def test_segments_renumbered_sequentially(self):
        original = [
            {'start': 0.0, 'end': 1.0, 'text': "A"},
            {'start': 1.0, 'end': 2.0, 'text': "B"},
        ]
        translated = [
            {'start': 0.0, 'end': 1.0, 'text': "甲"},
            {'start': 1.0, 'end': 2.0, 'text': "乙"},
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            out = os.path.join(tmp_dir, "out.srt")
            subtitle_formatter.generate_bilingual_srt(original, translated, out)
            content = self._read(out)

        # Entries are numbered 1, 2 (not 0-indexed, no gaps).
        self.assertTrue(content.startswith("1\n"))
        self.assertIn("\n2\n", content)

    def test_missing_timing_entries_skipped_not_zeroed(self):
        original = [
            {'start': 0.0, 'end': 1.0, 'text': "A"},
            {'text': "Lost"},  # No start/end — should be skipped.
            {'start': 2.0, 'end': 3.0, 'text': "C"},
        ]
        translated = [
            {'start': 0.0, 'end': 1.0, 'text': "甲"},
            {'text': "丢失"},
            {'start': 2.0, 'end': 3.0, 'text': "丙"},
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            out = os.path.join(tmp_dir, "out.srt")
            subtitle_formatter.generate_bilingual_srt(original, translated, out)
            content = self._read(out)

        # The 'Lost' entry has no timing on either side — skipped, not zeroed.
        self.assertNotIn("Lost", content)
        self.assertNotIn("丢失", content)
        self.assertEqual(content.count("00:00:00,000 --> 00:00:00,000"), 0)
        # The two valid entries are renumbered 1 and 2.
        self.assertTrue(content.startswith("1\n"))
        self.assertIn("\n2\n", content)


class TestValidateOpenAIApiKey(unittest.TestCase):
    def test_real_looking_key_accepted(self):
        ok, key, reason = youtube_subtitle_trans.validate_openai_api_key("sk-proj-abcdefghijklmnop")
        self.assertTrue(ok)
        self.assertEqual(key, "sk-proj-abcdefghijklmnop")
        self.assertIsNone(reason)

    def test_whitespace_and_quotes_trimmed(self):
        ok, key, _ = youtube_subtitle_trans.validate_openai_api_key('  "sk-abc123"  \n')
        self.assertTrue(ok)
        self.assertEqual(key, "sk-abc123")

    def test_none_rejected_with_actionable_reason(self):
        ok, key, reason = youtube_subtitle_trans.validate_openai_api_key(None)
        self.assertFalse(ok)
        self.assertIsNone(key)
        self.assertIn("OPENAI_API_KEY", reason)

    def test_empty_string_rejected(self):
        ok, _, reason = youtube_subtitle_trans.validate_openai_api_key("")
        self.assertFalse(ok)
        self.assertIn("empty", reason.lower())

    def test_whitespace_only_rejected(self):
        ok, _, reason = youtube_subtitle_trans.validate_openai_api_key("   \t\n")
        self.assertFalse(ok)
        self.assertIn("empty", reason.lower())

    def test_placeholder_template_rejected(self):
        for placeholder in ["YOUR_OPENAI_API_KEY", "your_openai_api_key", "sk-YOUR_API_KEY-xxx", "REPLACE_ME_PLEASE"]:
            with self.subTest(placeholder=placeholder):
                ok, _, reason = youtube_subtitle_trans.validate_openai_api_key(placeholder)
                self.assertFalse(ok, f"Expected rejection for {placeholder!r}")
                self.assertIn("placeholder", reason.lower())

    def test_old_check_substring_no_longer_collides(self):
        # The old code used `"YOUR_OPENAI_API_KEY" in api_key` which is also
        # what the new check does — but the new check is case-insensitive and
        # covers a wider set of placeholders, so real keys that incidentally
        # contained the substring would have been rejected. Make sure the
        # check still works on a literal pasted-template value.
        ok, _, _ = youtube_subtitle_trans.validate_openai_api_key("sk-real-key-1234567890")
        self.assertTrue(ok)

    def test_custom_proxy_key_accepted(self):
        # Azure / self-hosted proxies often use non-'sk-' prefixes — we
        # explicitly do NOT block them.
        ok, key, _ = youtube_subtitle_trans.validate_openai_api_key("azure-proxy-key-abcd1234")
        self.assertTrue(ok)
        self.assertEqual(key, "azure-proxy-key-abcd1234")


if __name__ == '__main__':
    unittest.main()
