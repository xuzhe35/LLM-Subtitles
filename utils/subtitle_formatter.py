import re
import os

def format_timestamp(seconds):
    """
    Converts seconds to SRT timestamp format (00:00:00,000).
    """
    millis = int((seconds - int(seconds)) * 1000)
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"

def parse_vtt(vtt_file_path):
    """
    Parses a WEBVTT file and returns a list of segments.
    Each segment is a dict: {'start': float, 'end': float, 'text': str}
    """
    segments = []
    with open(vtt_file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Simple VTT parser. 
    # VTT format:
    # 00:00:00.000 --> 00:00:02.000
    # Text line 1
    # Text line 2
    
    # Regex to find timestamps: (\d{2}:)?\d{2}:\d{2}\.\d{3} --> (\d{2}:)?\d{2}:\d{2}\.\d{3}
    # But often it's simpler to iterate blocks.
    
    blocks = content.strip().split('\n\n')
    
    for block in blocks:
        lines = block.split('\n')
        # Filter out header 'WEBVTT' or notes
        if lines[0].strip() == "WEBVTT" or lines[0].startswith("NOTE"):
            continue
            
        # Find the timestamp line
        time_line_index = -1
        for i, line in enumerate(lines):
            if '-->' in line:
                time_line_index = i
                break
        
        if time_line_index == -1:
            continue
            
        time_line = lines[time_line_index]
        text_lines = lines[time_line_index+1:]
        text = "\n".join(text_lines).strip()
        
        # Parse start and end times
        try:
            start_str, end_str = time_line.split(' --> ')
            start = _vtt_time_to_seconds(start_str.strip())
            end = _vtt_time_to_seconds(end_str.split(' ')[0].strip()) # clean potential settings
            
            segments.append({
                'start': start,
                'end': end,
                'text': text
            })
        except ValueError:
            continue
            
    return segments

def _vtt_time_to_seconds(time_str):
    # format: MM:SS.mmm or HH:MM:SS.mmm
    parts = time_str.split(':')
    seconds = 0
    if len(parts) == 3:
        seconds += int(parts[0]) * 3600
        seconds += int(parts[1]) * 60
        seconds += float(parts[2])
    elif len(parts) == 2:
        seconds += int(parts[0]) * 60
        seconds += float(parts[1])
    return seconds

def generate_srt(segments, output_path):
    """
    Generates an SRT file from a list of segments.
    Segments should have 'start', 'end', and 'text'.
    """
    with open(output_path, 'w', encoding='utf-8') as f:
        for i, segment in enumerate(segments):
            start = format_timestamp(segment['start'])
            end = format_timestamp(segment['end'])
            text = segment.get('text', '').strip()
            
            f.write(f"{i+1}\n")
            f.write(f"{start} --> {end}\n")
            f.write(f"{text}\n\n")
    
    return output_path

def generate_bilingual_srt(original_segments, translated_segments, output_path):
    """
    Generates a bilingual SRT file.
    Assumes segments align roughly by index or timestamp.
    If lengths differ, it tries to align by index (simplest for 1:1 translation).
    Effect:
    Translated Text
    Original Text
    """
    with open(output_path, 'w', encoding='utf-8') as f:
        # Use translated segments as the base for timing if available, or original.
        # Usually original has better timing if translated was just text list.
        # But here our translator preserves dict structure.
        
        count = max(len(original_segments), len(translated_segments))
        
        for i in range(count):
            # Get translated text
            trans_text = ""
            if i < len(translated_segments):
                trans_text = translated_segments[i]['text']
                # Use translated timing? Or original?
                start = format_timestamp(translated_segments[i]['start'])
                end = format_timestamp(translated_segments[i]['end'])
            else:
                 # Fallback timing
                 start = "00:00:00,000" 
                 end = "00:00:00,000"

            # Get original text
            orig_text = ""
            if i < len(original_segments):
                orig_text = original_segments[i]['text']
                # If we rely on original timing (often safer)
                start = format_timestamp(original_segments[i]['start'])
                end = format_timestamp(original_segments[i]['end'])
            
            # Combine
            # Style: Translated on top (Target), Original below.
            texts_to_combine = []
            if trans_text.strip():
                texts_to_combine.append(trans_text.strip())
            if orig_text.strip():
                texts_to_combine.append(orig_text.strip())
                
            combined_text = "\n".join(texts_to_combine)
            
            f.write(f"{i+1}\n")
            f.write(f"{start} --> {end}\n")
            f.write(f"{combined_text}\n\n")
    
    return output_path

def parse_srt(srt_file_path):
    """
    Parses an SRT file into segments list.
    """
    segments = []
    if not os.path.exists(srt_file_path):
        return []
        
    with open(srt_file_path, 'r', encoding='utf-8') as f:
        content = f.read().strip()
        
    blocks = re.split(r'\n\n+', content)
    
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
            
        # Line 0: ID
        # Line 1: Time
        # Line 2+: Text
        
        time_line = lines[1]
        text = "\n".join(lines[2:])
        
        if '-->' in time_line:
            start_str, end_str = time_line.split(' --> ')
            start = _srt_time_to_seconds(start_str.strip())
            end = _srt_time_to_seconds(end_str.strip())
            
            segments.append({
                'start': start,
                'end': end,
                'text': text
            })
            
    return segments

def _srt_time_to_seconds(time_str):
    # 00:00:00,000
    time_str = time_str.replace(',', '.')
    return _vtt_time_to_seconds(time_str)
