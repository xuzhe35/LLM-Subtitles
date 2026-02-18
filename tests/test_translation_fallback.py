import unittest
from unittest.mock import MagicMock
import sys
import os
import json

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils import translator

class TestTranslationFallback(unittest.TestCase):
    def test_fallback_logic(self):
        print("Starting test_fallback_logic...")
        # Mock client
        mock_client = MagicMock()
        
        # Scenario: 
        # Batch of 2 segments.
        # Batch translation returns only 1 segment (id 0).
        # Segment 1 should trigger fallback.
        
        # Batch response mock
        # It needs to return a valid object that translator expects
        batch_response = MagicMock()
        # The code does: content = response.choices[0].message.content
        batch_response.choices[0].message.content = json.dumps({
            "segments": [
                {"id": 0, "start": 0, "end": 5, "text": "Translated 0"}
            ]
        })
        
        # Fallback response mock
        fallback_response = MagicMock()
        fallback_response.choices[0].message.content = "Translated 1 Fallback"
        
        # Configure side_effect
        def side_effect(*args, **kwargs):
            messages = kwargs.get('messages')
            system_content = messages[0]['content']
            
            if "Output a JSON object" in system_content:
                # This is the batch translation
                # print("Mock: Batch translation called")
                return batch_response
            else:
                # This is the single fallback
                # print("Mock: Fallback translation called")
                return fallback_response

        mock_client.chat.completions.create.side_effect = side_effect
        
        segments = [
            {'start': 0, 'end': 5, 'text': 'Original 0'},
            {'start': 5, 'end': 10, 'text': 'Original 1'}
        ]
        
        # Run translation
        print("Calling translate_segments...")
        results = translator.translate_segments(mock_client, segments, "Spanish", batch_size=2)
        
        self.assertEqual(len(results), 2)
        print(f"Result 0: {results[0]['text']}")
        self.assertEqual(results[0]['text'], "Translated 0")
        
        print(f"Result 1: {results[1]['text']}")
        self.assertEqual(results[1]['text'], "Translated 1 Fallback")
        
        print("Test passed!")

if __name__ == '__main__':
    unittest.main()
