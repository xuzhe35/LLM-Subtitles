import re
import os

DEFAULT_MAX_LINE_CHARS = 32
_BREAK_AFTER_CHARS = set("，。！？；：、,.!?;:")

# Display-time extension: cue end times from the timing backbone mark where
# the *matched* speech evidence ends, which can be well before the speech
# itself ends (garbled ASR tails carry no timestamps). Extending the display
# window into the following gap keeps subtitles readable without ever
# overlapping the next cue. Reading rates are conservative chars-per-second
# guidelines; dense scripts (CJK) read fewer characters per second.
CJK_READ_CHARS_PER_SEC = 9.0
DEFAULT_READ_CHARS_PER_SEC = 15.0
MIN_SUBTITLE_DURATION_SEC = 1.0
MAX_EXTENDED_DURATION_SEC = 12.0

_DENSE_SCRIPT_RE = re.compile(
    "[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]"
)


def _reading_time_needed_sec(text):
    """Seconds a viewer needs for this cue, by script-aware reading rate."""
    visible = "".join(str(text or "").split())
    if not visible:
        return 0.0
    dense = len(_DENSE_SCRIPT_RE.findall(visible))
    rate = (CJK_READ_CHARS_PER_SEC if dense >= len(visible) / 3
            else DEFAULT_READ_CHARS_PER_SEC)
    needed = max(MIN_SUBTITLE_DURATION_SEC, len(visible) / rate)
    return min(needed, MAX_EXTENDED_DURATION_SEC)


def extend_display_times(segments, media_duration=None):
    """Give each cue its reading time without moving evidence it contradicts.

    Forward pass: every cue's end may extend into the following gap up to its
    reading need; it never overlaps the next cue and never shrinks. Backward
    pass: only cues marked ``extend_lead`` (speech with no timing evidence
    before the matched span) may start earlier, into whatever gap the
    previous cue left — appearance times otherwise stay exactly on evidence.
    The final cue is capped by ``media_duration``. Without a known duration,
    its evidence end is preserved instead of guessing beyond the media. The
    ``extend_lead`` marker is consumed. Returns new segment dicts.
    """
    result = [dict(segment) for segment in segments]
    for index, segment in enumerate(result):
        needed = _reading_time_needed_sec(segment.get("text"))
        if needed <= 0.0:
            continue
        start = float(segment["start"])
        end = float(segment["end"])
        if index + 1 < len(result):
            limit = float(result[index + 1]["start"])
        elif media_duration is not None:
            limit = max(end, float(media_duration))
        else:
            limit = end
        segment["end"] = max(end, min(start + needed, limit))
    for index, segment in enumerate(result):
        wants_lead = bool(segment.pop("extend_lead", False))
        if not wants_lead:
            continue
        needed = _reading_time_needed_sec(segment.get("text"))
        start = float(segment["start"])
        end = float(segment["end"])
        if end - start >= needed:
            continue
        floor = float(result[index - 1]["end"]) if index > 0 else 0.0
        segment["start"] = min(start, max(floor, end - needed))
    return result

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

def wrap_subtitle_text(text, max_line_chars=DEFAULT_MAX_LINE_CHARS):
    """
    Inserts line breaks inside a subtitle cue without changing cue timing or count.
    """
    if text is None:
        return ""

    if not max_line_chars or max_line_chars <= 0:
        return str(text).strip()

    normalized = str(text).replace('\r\n', '\n').replace('\r', '\n')
    wrapped_lines = []

    for raw_line in normalized.split('\n'):
        line = raw_line.strip()
        if not line:
            wrapped_lines.append("")
            continue

        wrapped_lines.extend(_wrap_single_subtitle_line(line, max_line_chars))

    return "\n".join(wrapped_lines).strip()

def _wrap_single_subtitle_line(line, max_line_chars):
    lines = []
    remaining = line

    while len(remaining) > max_line_chars:
        break_at = _choose_subtitle_break(remaining, max_line_chars)
        chunk = remaining[:break_at].strip()
        if chunk:
            lines.append(chunk)
        remaining = remaining[break_at:].lstrip()

    if remaining.strip():
        lines.append(remaining.strip())

    return lines

def _choose_subtitle_break(text, max_line_chars):
    if len(text) <= max_line_chars:
        return len(text)

    # Keep punctuation with the previous line when it sits just after the limit.
    if len(text) > max_line_chars and text[max_line_chars] in _BREAK_AFTER_CHARS:
        return max_line_chars + 1

    min_preferred = max(1, int(max_line_chars * 0.55))
    search_end = min(len(text), max_line_chars + 1)

    for i in range(search_end - 1, min_preferred - 1, -1):
        if text[i] in _BREAK_AFTER_CHARS:
            return i + 1

    for i in range(search_end - 1, min_preferred - 1, -1):
        if text[i].isspace():
            return i

    return max_line_chars

def generate_srt(segments, output_path, max_line_chars=DEFAULT_MAX_LINE_CHARS):
    """
    Generates an SRT file from a list of segments.
    Segments should have 'start', 'end', and 'text'.
    """
    with open(output_path, 'w', encoding='utf-8') as f:
        for i, segment in enumerate(segments):
            start = format_timestamp(segment['start'])
            end = format_timestamp(segment['end'])
            text = segment.get('text', '').strip()
            text = wrap_subtitle_text(text, max_line_chars=max_line_chars)
            
            f.write(f"{i+1}\n")
            f.write(f"{start} --> {end}\n")
            f.write(f"{text}\n\n")
    
    return output_path

def _segment_timing(segment):
    """Return (start, end) from a segment dict, or None if either is missing/None."""
    if not segment:
        return None
    start = segment.get('start')
    end = segment.get('end')
    if start is None or end is None:
        return None
    return start, end


def generate_bilingual_srt(original_segments, translated_segments, output_path, max_line_chars=DEFAULT_MAX_LINE_CHARS, progress_callback=None):
    """
    Generates a bilingual SRT file.
    Aligns by index (the translator preserves 1:1 ordering with the originals).

    Timing precedence: original segment timing wins when present (it comes from
    the audio/source subtitles and is the most reliable); otherwise translated
    timing is used. Entries that have no valid timing on either side are
    skipped with a warning rather than emitted with a placeholder 00:00:00,000
    timestamp (which previously collapsed orphan entries to the start of the
    video and silently corrupted the output).

    Style: Translated on top (target language), Original below.
    """
    orig_len = len(original_segments)
    trans_len = len(translated_segments)
    count = max(orig_len, trans_len)

    if orig_len != trans_len and progress_callback is not None:
        progress_callback(
            f"Warning: bilingual SRT segment count mismatch "
            f"(original={orig_len}, translated={trans_len}). "
            "Entries without matching timing will be skipped."
        )

    skipped = 0
    written = 0

    with open(output_path, 'w', encoding='utf-8') as f:
        for i in range(count):
            orig_seg = original_segments[i] if i < orig_len else None
            trans_seg = translated_segments[i] if i < trans_len else None

            # Pick timing: prefer original, fall back to translated.
            timing = _segment_timing(orig_seg) or _segment_timing(trans_seg)
            if timing is None:
                skipped += 1
                continue

            start, end = timing

            orig_text = (orig_seg.get('text') if orig_seg else '') or ''
            trans_text = (trans_seg.get('text') if trans_seg else '') or ''

            texts_to_combine = []
            if trans_text.strip():
                texts_to_combine.append(wrap_subtitle_text(trans_text.strip(), max_line_chars=max_line_chars))
            if orig_text.strip():
                texts_to_combine.append(wrap_subtitle_text(orig_text.strip(), max_line_chars=max_line_chars))

            if not texts_to_combine:
                # Timing exists but both texts are empty — nothing meaningful to show.
                skipped += 1
                continue

            combined_text = "\n".join(texts_to_combine)
            written += 1

            f.write(f"{written}\n")
            f.write(f"{format_timestamp(start)} --> {format_timestamp(end)}\n")
            f.write(f"{combined_text}\n\n")

    if skipped and progress_callback is not None:
        progress_callback(f"Bilingual SRT: skipped {skipped} entry/entries with no valid timing or text.")

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
