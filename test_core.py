import unittest
from unittest.mock import MagicMock
from utils import translator, subtitle_formatter
import json
import os

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

if __name__ == '__main__':
    unittest.main()
