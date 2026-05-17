import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import audio_splitter

# Default max duration per Whisper API call (in ms).
# 10 min works well for English. For challenging languages (Thai etc.), use 60-90s.
DEFAULT_MAX_SEGMENT_MS = 10 * 60 * 1000  # 10 minutes

# Default max duration per gpt-4o-transcribe-diarize API call (in ms).
# The diarize endpoint is meaningfully slower than plain transcribe (it does
# speaker labeling on top of ASR), and a single long upload + server-side
# auto-chunking response can blow past the OpenAI SDK's request timeout on
# 20+ minute audio. We force short local chunks so workers can parallelize
# and each individual HTTPS request stays well under the timeout ceiling
# (see OPENAI_REQUEST_TIMEOUT_SEC in youtube_subtitle_trans.py).
DEFAULT_DIARIZE_MAX_SEGMENT_MS = 5 * 60 * 1000  # 5 minutes

# Google API Key limit: Must be < 60 seconds for direct upload (no GCS)
GOOGLE_API_MAX_SEGMENT_MS = 59 * 1000  # 59 seconds

# Overlap between adjacent segments (ms). Compensates for Whisper dropping
# content near segment boundaries — a known issue with non-English audio.
SEGMENT_OVERLAP_MS = 10 * 1000  # 10 seconds

TRANSCRIPTION_MODEL_AUTO = "auto"
TRANSCRIPTION_MODEL_OPENAI = "openai-whisper-1"
TRANSCRIPTION_MODEL_OPENAI_GPT4O_DIARIZE = "gpt-4o-transcribe-diarize"
TRANSCRIPTION_MODEL_GOOGLE = "google-speech"
TRANSCRIPTION_MODEL_TYPHOON = "typhoon-whisper-large-v3"
TYPHOON_HF_MODEL_ID = "typhoon-ai/typhoon-whisper-large-v3"

_VALID_TRANSCRIPTION_MODELS = {
    TRANSCRIPTION_MODEL_AUTO,
    TRANSCRIPTION_MODEL_OPENAI,
    TRANSCRIPTION_MODEL_OPENAI_GPT4O_DIARIZE,
    TRANSCRIPTION_MODEL_GOOGLE,
    TRANSCRIPTION_MODEL_TYPHOON,
}
_TYPHOON_PIPELINE = None


class TranscriptResult:
    def __init__(self, segments):
        self.segments = segments


def _report(progress_callback, message):
    if progress_callback:
        progress_callback(message)
    else:
        print(message)


def _is_thai_source_language(source_lang):
    if not source_lang:
        return False
    normalized = str(source_lang).strip().lower().replace("_", "-")
    return normalized in {"th", "thai", "th-th"} or normalized.startswith("th-")


def resolve_transcription_model(engine, source_lang, requested=TRANSCRIPTION_MODEL_AUTO):
    """
    Resolve the concrete transcription model from the current engine and source language.
    Explicit engine/model choices win. Thai auto-routing only applies to engine='auto'.
    """
    requested = requested or TRANSCRIPTION_MODEL_AUTO
    if requested not in _VALID_TRANSCRIPTION_MODELS:
        raise ValueError(f"Unsupported transcription model: {requested}")

    if requested != TRANSCRIPTION_MODEL_AUTO:
        return requested

    normalized_engine = (engine or "whisper").strip().lower()
    if normalized_engine in {"gpt-4o-transcribe-diarize", "openai-gpt-4o-transcribe-diarize", "diarize"}:
        return TRANSCRIPTION_MODEL_OPENAI_GPT4O_DIARIZE
    if normalized_engine in {"typhoon", "typhoon-whisper", "typhoon-whisper-large-v3"}:
        return TRANSCRIPTION_MODEL_TYPHOON
    if normalized_engine == "google":
        return TRANSCRIPTION_MODEL_GOOGLE
    if normalized_engine == "whisper":
        return TRANSCRIPTION_MODEL_OPENAI

    if _is_thai_source_language(source_lang):
        return TRANSCRIPTION_MODEL_TYPHOON

    return TRANSCRIPTION_MODEL_OPENAI


def _select_torch_device(torch_module):
    if torch_module.cuda.is_available():
        return "cuda:0", torch_module.bfloat16

    mps_backend = getattr(getattr(torch_module, "backends", None), "mps", None)
    if mps_backend and mps_backend.is_available():
        return "mps", torch_module.float16

    return "cpu", torch_module.float32


def _get_typhoon_pipeline(progress_callback=print):
    global _TYPHOON_PIPELINE
    if _TYPHOON_PIPELINE is not None:
        return _TYPHOON_PIPELINE

    try:
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
    except ImportError as e:
        raise RuntimeError(
            "Missing optional dependencies for Typhoon ASR. "
            "Install with: pip install transformers accelerate huggingface_hub"
        ) from e

    device, torch_dtype = _select_torch_device(torch)
    _report(progress_callback, f"Loading Typhoon ASR model: {TYPHOON_HF_MODEL_ID} ({device})")
    started = time.perf_counter()

    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        TYPHOON_HF_MODEL_ID,
        torch_dtype=torch_dtype,
        low_cpu_mem_usage=True,
        use_safetensors=True,
    )
    model.to(device)
    processor = AutoProcessor.from_pretrained(TYPHOON_HF_MODEL_ID)

    _TYPHOON_PIPELINE = pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        max_new_tokens=448,
        chunk_length_s=30,
        batch_size=16,
        return_timestamps=True,
        torch_dtype=torch_dtype,
        device=device,
    )
    _report(progress_callback, f"Typhoon ASR model loaded in {time.perf_counter() - started:.1f}s.")
    return _TYPHOON_PIPELINE


def _coerce_timestamp_pair(timestamp):
    if not isinstance(timestamp, (list, tuple)) or len(timestamp) != 2:
        return None
    start, end = timestamp
    if start is None or end is None:
        return None
    return float(start), float(end)


def _normalize_typhoon_segments(result, audio_file_path):
    segments = []
    chunks = result.get("chunks", []) if isinstance(result, dict) else []

    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        text = str(chunk.get("text", "")).strip()
        timestamp = _coerce_timestamp_pair(chunk.get("timestamp"))
        if not text or not timestamp:
            continue
        start, end = timestamp
        if end <= start:
            continue
        segments.append({
            "start": start,
            "end": end,
            "text": text,
        })

    if segments:
        return segments

    text = result.get("text", "").strip() if isinstance(result, dict) else ""
    if not text:
        return []

    duration = audio_splitter.get_audio_duration(audio_file_path)
    return [{
        "start": 0.0,
        "end": float(duration) if duration else 0.0,
        "text": text,
    }]


def _transcribe_audio_typhoon(audio_file_path, progress_callback=print):
    _report(progress_callback, "Transcribing with Typhoon Whisper Large v3 (Thai)...")
    started = time.perf_counter()
    pipe = _get_typhoon_pipeline(progress_callback=progress_callback)
    result = pipe(
        audio_file_path,
        generate_kwargs={"language": "thai"},
    )
    segments = _normalize_typhoon_segments(result, audio_file_path)
    _report(progress_callback, f"Typhoon transcription finished in {time.perf_counter() - started:.1f}s ({len(segments)} segments).")
    return TranscriptResult(segments)


def _filter_hallucinations(segments, max_repeat=5):
    """
    Post-processing filter to remove likely hallucinated segments.
    
    Strategies:
    1. Remove any text that appears more than `max_repeat` times total.
    2. Remove consecutive segments with identical text (keep first occurrence).
    3. Remove very short meaningless segments (1-2 chars with very short duration).
    """
    if not segments:
        return segments
    
    def get_text(seg):
        if isinstance(seg, dict):
            return seg.get('text', '').strip()
        return getattr(seg, 'text', '').strip()
    
    def get_no_speech_prob(seg):
        if isinstance(seg, dict):
            return seg.get('no_speech_prob', 0.0)
        return getattr(seg, 'no_speech_prob', 0.0)
    
    def get_duration(seg):
        if isinstance(seg, dict):
            return seg.get('end', 0) - seg.get('start', 0)
        return getattr(seg, 'end', 0) - getattr(seg, 'start', 0)
    
    from collections import Counter
    text_counts = Counter(get_text(s) for s in segments)
    total_segments = len(segments)
    
    # Flag as hallucination if:
    # - Appears > max_repeat times AND is > 15% of total segments
    hallucinated_texts = set()
    for text, count in text_counts.items():
        if not text:
            continue
        if count > max_repeat and count > total_segments * 0.15:
            hallucinated_texts.add(text)
    
    if hallucinated_texts:
        print(f"Hallucination detector: found {len(hallucinated_texts)} repeated text(s):")
        for ht in list(hallucinated_texts)[:5]:
            print(f'  - "{ht[:80]}..." (appeared {text_counts[ht]} times)')
    
    filtered = []
    prev_text = None
    removed_count = 0
    
    for seg in segments:
        text = get_text(seg)
        no_speech = get_no_speech_prob(seg)
        duration = get_duration(seg)
        
        # Skip empty text
        if not text:
            removed_count += 1
            continue
        # Skip high no_speech_prob
        if no_speech > 0.9:
            removed_count += 1
            continue
        # Skip hallucinated repetitive text
        if text in hallucinated_texts:
            removed_count += 1
            continue
        # Skip consecutive exact duplicates
        if text == prev_text:
            removed_count += 1
            continue
        
        filtered.append(seg)
        prev_text = text
    
    return filtered


def _split_long_segments(speech_segments, max_segment_ms):
    """
    Split speech segments that are longer than max_segment_ms into
    smaller overlapping sub-segments.
    """
    result = []
    stride_ms = max(max_segment_ms - SEGMENT_OVERLAP_MS, max_segment_ms // 2)
    
    for start_ms, end_ms in speech_segments:
        duration = end_ms - start_ms
        if duration <= max_segment_ms:
            result.append((start_ms, end_ms))
        else:
            sub_count = 0
            current = start_ms
            while current < end_ms:
                sub_end = min(current + max_segment_ms, end_ms)
                # Skip tiny leftover segments (< 5s)
                if sub_end - current >= 5000 or current == start_ms:
                    result.append((current, sub_end))
                    sub_count += 1
                current += stride_ms
            print(f"  Split long segment ({duration/1000:.0f}s) into {sub_count} sub-segments "
                  f"(~{max_segment_ms/1000:.0f}s each, {SEGMENT_OVERLAP_MS/1000:.0f}s overlap)")
    return result


def _deduplicate_segments(segments, threshold_sec=1.0):
    """
    Remove duplicate segments that arise from overlapping audio chunks.
    Two segments are considered duplicates if their start times are within
    threshold_sec of each other.
    """
    if not segments:
        return segments
    
    # Sort by start time
    def get_start(seg):
        return seg.get('start', 0) if isinstance(seg, dict) else getattr(seg, 'start', 0)
    def get_text(seg):
        return (seg.get('text', '').strip() if isinstance(seg, dict) else getattr(seg, 'text', '').strip())
    
    sorted_segs = sorted(segments, key=get_start)
    deduped = [sorted_segs[0]]
    
    for seg in sorted_segs[1:]:
        prev_start = get_start(deduped[-1])
        curr_start = get_start(seg)
        # If starts are very close, keep the one with longer text (more complete)
        if abs(curr_start - prev_start) < threshold_sec:
            prev_text = get_text(deduped[-1])
            curr_text = get_text(seg)
            if len(curr_text) > len(prev_text):
                deduped[-1] = seg  # Replace with better version
            # else keep existing
        else:
            deduped.append(seg)
    
    return deduped


def _extract_segment(audio_file_path, start_ms, end_ms, output_path):
    """Extract a single segment from audio using ffmpeg with re-encoding for precision."""
    start_sec = start_ms / 1000.0
    duration_sec = (end_ms - start_ms) / 1000.0
    started = time.perf_counter()
    
    cmd = [
        'ffmpeg', '-y',
        '-ss', str(start_sec),
        '-t', str(duration_sec),
        '-i', audio_file_path,
        '-acodec', 'aac',
        output_path
    ]
    
    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        print(f"  Extracted segment {start_sec:.1f}-{start_sec+duration_sec:.1f}s in {time.perf_counter() - started:.1f}s: {output_path}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error extracting segment {start_sec:.1f}-{start_sec+duration_sec:.1f}s: {e.stderr.decode()[:200]}")
        return False


def _transcribe_single_segment_google(api_key, audio_file_path, seg_index, start_ms, end_ms, lang_code='th-TH'):
    """
    Transcribe a single <60s segment using Google Speech API Key (REST).
    Uses word-level timestamps to split into subtitle-sized segments.
    """
    import base64
    import requests
    
    start_sec = start_ms / 1000.0
    duration_sec = (end_ms - start_ms) / 1000.0
    chunk_path_flac = f"{audio_file_path}_seg_{seg_index}.flac"
    started = time.perf_counter()
    
    # Max words per subtitle line (Thai words are short, so allow more)
    MAX_WORDS_PER_SUB = 12
    
    try:
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(start_sec),
            '-t', str(duration_sec),
            '-i', audio_file_path,
            '-acodec', 'flac',
            '-ar', '16000',
            '-ac', '1',
            chunk_path_flac
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        
        with open(chunk_path_flac, "rb") as audio_file:
            content = base64.b64encode(audio_file.read()).decode('utf-8')
            
        url = f"https://speech.googleapis.com/v1/speech:recognize?key={api_key}"
        data = {
            "config": {
                "encoding": "FLAC",
                "sampleRateHertz": 16000,
                "languageCode": lang_code,
                "enableAutomaticPunctuation": True,
                "enableWordTimeOffsets": True,
                "model": "default"
            },
            "audio": {
                "content": content
            }
        }
        
        print(f"  Uploading segment {seg_index+1} to Google Speech API; waiting for response...")
        response = requests.post(url, json=data)
        if response.status_code != 200:
            print(f"  Google API Error {response.status_code}: {response.text}")
            return (seg_index, [])
            
        result_json = response.json()
        transcript_segments = []
        
        # Collect all words with their timestamps
        all_words = []
        if 'results' in result_json:
            for res in result_json['results']:
                if 'alternatives' in res and res['alternatives']:
                    alt = res['alternatives'][0]
                    if 'words' in alt:
                        for w in alt['words']:
                            # Google returns times like "1.500s" or "0s"
                            w_start = float(w['startTime'].rstrip('s')) if w.get('startTime') else 0
                            w_end = float(w['endTime'].rstrip('s')) if w.get('endTime') else 0
                            all_words.append({
                                'word': w['word'],
                                'start': w_start,
                                'end': w_end
                            })
        
        if all_words:
            # Group words into subtitle-sized chunks
            chunk_words = []
            for word_info in all_words:
                chunk_words.append(word_info)
                
                # Split at MAX_WORDS_PER_SUB, or at punctuation boundaries
                is_punct_end = word_info['word'].endswith(('。', '？', '！', '，', '.', '?', '!', ',', 'ครับ', 'ค่ะ'))
                should_split = (len(chunk_words) >= MAX_WORDS_PER_SUB or 
                               (len(chunk_words) >= 5 and is_punct_end))
                
                if should_split:
                    text = ' '.join(w['word'] for w in chunk_words)
                    seg_start = chunk_words[0]['start'] + start_sec
                    seg_end = chunk_words[-1]['end'] + start_sec
                    if seg_end <= seg_start:
                        seg_end = seg_start + 2.0
                    transcript_segments.append({
                        'start': seg_start,
                        'end': seg_end,
                        'text': text
                    })
                    chunk_words = []
            
            # Remaining words
            if chunk_words:
                text = ' '.join(w['word'] for w in chunk_words)
                seg_start = chunk_words[0]['start'] + start_sec
                seg_end = chunk_words[-1]['end'] + start_sec
                if seg_end <= seg_start:
                    seg_end = seg_start + 2.0
                transcript_segments.append({
                    'start': seg_start,
                    'end': seg_end,
                    'text': text
                })
        else:
            # Fallback: no word timestamps, use full transcript
            full_text = ""
            if 'results' in result_json:
                for res in result_json['results']:
                    if 'alternatives' in res and res['alternatives']:
                        full_text += res['alternatives'][0].get('transcript', '') + " "
            full_text = full_text.strip()
            if full_text:
                transcript_segments.append({
                    'start': start_sec,
                    'end': start_sec + duration_sec,
                    'text': full_text
                })
            
        print(f"  Segment {seg_index+1} (Google): {len(transcript_segments)} text segments "
              f"({start_sec:.0f}s-{start_sec+duration_sec:.0f}s)")
        return (seg_index, transcript_segments)

    except Exception as e:
        print(f"  Error Google transcribing segment {seg_index+1}: {e}")
        return (seg_index, [])
    finally:
        if os.path.exists(chunk_path_flac):
            os.remove(chunk_path_flac)


def _transcribe_single_segment(client, audio_file_path, seg_index, start_ms, end_ms, source_lang=None, whisper_prompt=None):
    """
    Extract and transcribe a single audio segment.
    Returns (seg_index, list_of_segments_with_absolute_timestamps) or (seg_index, []) on error.
    """
    start_sec = start_ms / 1000.0
    duration_sec = (end_ms - start_ms) / 1000.0
    chunk_path = f"{audio_file_path}_seg_{seg_index}.m4a"
    started = time.perf_counter()
    
    # Extract segment
    if not _extract_segment(audio_file_path, start_ms, end_ms, chunk_path):
        return (seg_index, [])
    
    try:
        # Transcribe
        with open(chunk_path, "rb") as af:
            whisper_kwargs = {
                'model': 'whisper-1',
                'file': af,
                'response_format': 'verbose_json',
                'timestamp_granularities': ['segment']
            }
            if source_lang:
                whisper_kwargs['language'] = source_lang
            if whisper_prompt:
                whisper_kwargs['prompt'] = whisper_prompt
            print(f"  Uploading segment {seg_index+1} to OpenAI whisper-1; waiting for response...")
            transcript = client.audio.transcriptions.create(**whisper_kwargs)
        
        # Parse and remap timestamps
        raw_segments = transcript.segments if hasattr(transcript, 'segments') else (
            transcript.get('segments', []) if isinstance(transcript, dict) else []
        )
        
        result_segments = []
        for seg in raw_segments:
            if isinstance(seg, dict):
                s_start, s_end, s_text = seg['start'], seg['end'], seg['text']
            else:
                s_start, s_end, s_text = seg.start, seg.end, seg.text
            
            # Simple offset: add the segment's original start time
            result_segments.append({
                'start': s_start + start_sec,
                'end': s_end + start_sec,
                'text': s_text
            })
        
        print(f"  Segment {seg_index+1}: {len(result_segments)} text segments "
              f"({start_sec:.0f}s-{start_sec+duration_sec:.0f}s, lang={source_lang or 'auto'}, {time.perf_counter() - started:.1f}s)")
        return (seg_index, result_segments)
        
    except Exception as e:
        print(f"  Error transcribing segment {seg_index+1}: {e}")
        return (seg_index, [])
    finally:
        if os.path.exists(chunk_path):
            os.remove(chunk_path)


def _get_transcript_segments(transcript):
    if isinstance(transcript, dict):
        return transcript.get('segments', [])
    return getattr(transcript, 'segments', [])


def _get_transcript_text(transcript):
    if isinstance(transcript, dict):
        return transcript.get('text', '')
    return getattr(transcript, 'text', '')


def _normalize_diarized_segments(transcript, time_offset=0.0, fallback_duration=None):
    result_segments = []
    for seg in _get_transcript_segments(transcript):
        if isinstance(seg, dict):
            s_start = seg.get('start')
            s_end = seg.get('end')
            s_text = seg.get('text', '')
        else:
            s_start = getattr(seg, 'start', None)
            s_end = getattr(seg, 'end', None)
            s_text = getattr(seg, 'text', '')

        if s_start is None or s_end is None:
            continue
        text = str(s_text).strip()
        if not text:
            continue
        result_segments.append({
            'start': float(s_start) + time_offset,
            'end': float(s_end) + time_offset,
            'text': text
        })

    if result_segments:
        return result_segments

    text = str(_get_transcript_text(transcript)).strip()
    if not text:
        return []

    return [{
        'start': time_offset,
        'end': time_offset + float(fallback_duration or 0.0),
        'text': text
    }]


def _transcribe_diarize_file(client, audio_file_path, time_offset=0.0, fallback_duration=None):
    started = time.perf_counter()
    with open(audio_file_path, "rb") as audio_file:
        print(f"  Uploading {audio_file_path} to OpenAI gpt-4o-transcribe-diarize; waiting for response...")
        transcript = client.audio.transcriptions.create(
            model=TRANSCRIPTION_MODEL_OPENAI_GPT4O_DIARIZE,
            file=audio_file,
            response_format="diarized_json",
            chunking_strategy="auto"
        )

    segments = _normalize_diarized_segments(
        transcript,
        time_offset=time_offset,
        fallback_duration=fallback_duration
    )
    print(f"  gpt-4o diarize API finished in {time.perf_counter() - started:.1f}s: {audio_file_path} ({len(segments)} segments)")
    return segments


def _transcribe_single_segment_diarize(client, audio_file_path, seg_index, start_ms, end_ms):
    """
    Extract and transcribe a segment with GPT-4o diarized transcription.
    Speaker labels are ignored; start/end/text are normalized for SRT generation.
    """
    start_sec = start_ms / 1000.0
    duration_sec = (end_ms - start_ms) / 1000.0
    chunk_path = f"{audio_file_path}_seg_{seg_index}.m4a"
    started = time.perf_counter()

    if not _extract_segment(audio_file_path, start_ms, end_ms, chunk_path):
        return (seg_index, [])

    try:
        result_segments = _transcribe_diarize_file(
            client,
            chunk_path,
            time_offset=start_sec,
            fallback_duration=duration_sec
        )
        print(f"  Segment {seg_index+1} (gpt-4o diarize): {len(result_segments)} text segments "
              f"({start_sec:.0f}s-{start_sec+duration_sec:.0f}s, {time.perf_counter() - started:.1f}s)")
        return (seg_index, result_segments)
    except Exception as e:
        print(f"  Error transcribing segment {seg_index+1} with gpt-4o diarize: {e}")
        return (seg_index, [])
    finally:
        if os.path.exists(chunk_path):
            os.remove(chunk_path)


def transcribe_audio(
    client,
    audio_file_path,
    source_lang=None,
    use_vad=False,
    whisper_prompt=None,
    max_segment_sec=None,
    engine='whisper',
    google_api_key=None,
    transcription_model=TRANSCRIPTION_MODEL_AUTO,
    progress_callback=print,
):
    """
    Transcribes audio using OpenAI Whisper or Google Speech.
    
    Args:
        client: OpenAI client instance (used if engine='whisper').
        audio_file_path: Path to the audio file.
        source_lang: ISO language code (e.g. 'en', 'ja', 'th').
        use_vad: If True, use Voice Activity Detection.
        whisper_prompt: Optional text prompt (Whisper only).
        max_segment_sec: Max duration per chunk. For Google, FORCE < 60s.
        engine: 'whisper' or 'google'.
        google_api_key: API Key for Google Speech.
        transcription_model: Concrete ASR model id, or 'auto' to resolve from source language/engine.
        progress_callback: Optional logger for user-visible progress and errors.
    """
    if not os.path.exists(audio_file_path):
        raise FileNotFoundError(f"Audio file not found: {audio_file_path}")

    try:
        transcription_started = time.perf_counter()
        resolved_model = resolve_transcription_model(engine, source_lang, transcription_model)
        if resolved_model == TRANSCRIPTION_MODEL_TYPHOON:
            return _transcribe_audio_typhoon(audio_file_path, progress_callback=progress_callback)

        use_openai_diarize = resolved_model == TRANSCRIPTION_MODEL_OPENAI_GPT4O_DIARIZE
        if use_openai_diarize and whisper_prompt:
            _report(progress_callback, "Whisper Prompt ignored for gpt-4o-transcribe-diarize.")

        engine = "google" if resolved_model == TRANSCRIPTION_MODEL_GOOGLE else "whisper"

        # Resolve max segment duration
        if engine == 'google':
            max_segment_ms = GOOGLE_API_MAX_SEGMENT_MS
            print(f"Engine: Google. Forcing max_segment_sec to {max_segment_ms/1000}s (API limitation).")
            # Google API mapping for common codes
            # Whisper uses 'th', Google uses 'th-TH'
            lang_map = {'th': 'th-TH', 'en': 'en-US', 'ja': 'ja-JP', 'zh': 'zh-CN'}
            google_lang = lang_map.get(source_lang, 'en-US') if source_lang else 'en-US'
        elif use_openai_diarize:
            # Diarize MUST chunk locally - without this, the fallback path
            # streamed the entire file to _transcribe_diarize_file in one
            # request, which routinely exceeded the OpenAI SDK request timeout
            # on >15 min audio. Honor the user's max_segment_sec if they set
            # one; otherwise default to DEFAULT_DIARIZE_MAX_SEGMENT_MS.
            max_segment_ms = (
                int(max_segment_sec * 1000) if max_segment_sec
                else DEFAULT_DIARIZE_MAX_SEGMENT_MS
            )
            print(
                f"Engine: gpt-4o-transcribe-diarize. Forcing local chunking at "
                f"{max_segment_ms/1000:.0f}s per chunk to keep each API request "
                "bounded."
            )
        else:
            max_segment_ms = int(max_segment_sec * 1000) if max_segment_sec else DEFAULT_MAX_SEGMENT_MS

        # Local chunking is required for:
        #   - explicit user choice (max_segment_sec)
        #   - Google (API limit < 60s)
        #   - diarize (request-timeout protection; see comment above)
        use_custom_chunking = (
            (max_segment_sec is not None)
            or (engine == 'google')
            or use_openai_diarize
        )
        
        speech_segments = []
        
        if use_vad:
            from . import vad
            speech_segments = vad.detect_speech_segments(audio_file_path)
            
            if speech_segments:
                original_count = len(speech_segments)
                speech_segments = _split_long_segments(speech_segments, max_segment_ms)
                print(f"VAD: {original_count} speech segments → {len(speech_segments)} after splitting (max {max_segment_ms/1000:.0f}s). "
                      f"Processing in parallel (max 5 workers, lang={source_lang or 'auto'})...")
            else:
                print("No speech detected by VAD. Falling back to standard chunking.")
        
        elif use_custom_chunking:
            # No VAD, but user chose a specific chunk size → fixed-interval splitting
            total_duration = audio_splitter.get_audio_duration(audio_file_path)
            if total_duration:
                total_ms = int(total_duration * 1000)
                stride_ms = max(max_segment_ms - SEGMENT_OVERLAP_MS, max_segment_ms // 2)
                current = 0
                while current < total_ms:
                    seg_end = min(current + max_segment_ms, total_ms)
                    if seg_end - current >= 5000 or current == 0:
                        speech_segments.append((current, seg_end))
                    current += stride_ms
                print(f"Fixed chunking: {len(speech_segments)} segments of ~{max_segment_ms/1000:.0f}s each "
                      f"({SEGMENT_OVERLAP_MS/1000:.0f}s overlap, total {total_duration:.0f}s). "
                      f"Processing in parallel (max 5 workers, lang={source_lang or 'auto'})...")
            else:
                print("Could not determine audio duration. Falling back to standard chunking.")
        
        else:
            print(f"Using standard chunking. (lang={source_lang or 'auto'})")
        
        all_segments = []
        
        # ===== VAD Path: process each segment individually, in parallel =====
        if speech_segments:
            MAX_WORKERS = 5  # Handle more sub-segments efficiently
            
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {}
                for i, (start_ms, end_ms) in enumerate(speech_segments):
                    duration_sec = (end_ms - start_ms) / 1000.0
                    print(f"  Queuing segment {i+1}/{len(speech_segments)}: "
                          f"{start_ms/1000.0:.1f}s - {end_ms/1000.0:.1f}s "
                          f"(duration: {duration_sec:.1f}s)")
                    
                    if engine == 'google':
                        future = executor.submit(
                            _transcribe_single_segment_google,
                            google_api_key, audio_file_path, i, start_ms, end_ms, google_lang
                        )
                    elif use_openai_diarize:
                        future = executor.submit(
                            _transcribe_single_segment_diarize,
                            client, audio_file_path, i, start_ms, end_ms
                        )
                    else:
                        future = executor.submit(
                            _transcribe_single_segment,
                            client, audio_file_path, i, start_ms, end_ms, source_lang, whisper_prompt
                        )
                    futures[future] = i
                
                results = {}
                for future in as_completed(futures):
                    seg_index, segments = future.result()
                    results[seg_index] = segments
            
            # Reassemble in original order
            for i in range(len(speech_segments)):
                if i in results:
                    all_segments.extend(results[i])
            
            # Deduplicate overlapping regions
            before_dedup = len(all_segments)
            all_segments = _deduplicate_segments(all_segments)
            if before_dedup != len(all_segments):
                print(f"Deduplication: {before_dedup} → {len(all_segments)} segments ({before_dedup - len(all_segments)} duplicates removed)")
            
            print(f"Total transcribed segments (before filter): {len(all_segments)}")
        
        # ===== Fallback Path: standard chunking (no VAD) =====
        else:
            split_started = time.perf_counter()
            chunks = audio_splitter.split_audio(audio_file_path)
            print(f"Audio split step returned {len(chunks)} chunk(s) in {time.perf_counter() - split_started:.1f}s.")
            time_offset = 0.0
            
            for i, chunk_path in enumerate(chunks):
                chunk_started = time.perf_counter()
                before_chunk_segments = len(all_segments)
                print(f"Transcribing chunk {i+1}/{len(chunks)}: {chunk_path}")

                duration_started = time.perf_counter()
                duration = audio_splitter.get_audio_duration(chunk_path)
                print(f"  Duration probe finished in {time.perf_counter() - duration_started:.1f}s for chunk {i+1}/{len(chunks)}.")
                if use_openai_diarize:
                    all_segments.extend(_transcribe_diarize_file(
                        client,
                        chunk_path,
                        time_offset=time_offset,
                        fallback_duration=duration
                    ))
                else:
                    with open(chunk_path, "rb") as audio_file:
                        whisper_kwargs = {
                            'model': 'whisper-1',
                            'file': audio_file,
                            'response_format': 'verbose_json',
                            'timestamp_granularities': ['segment']
                        }
                        if source_lang:
                            whisper_kwargs['language'] = source_lang
                        if whisper_prompt:
                            whisper_kwargs['prompt'] = whisper_prompt
                        print(f"  Uploading chunk {i+1}/{len(chunks)} to OpenAI whisper-1; waiting for response...")
                        transcript = client.audio.transcriptions.create(**whisper_kwargs)
                    
                    chunk_segments = transcript.segments if hasattr(transcript, 'segments') else (
                        transcript.get('segments', []) if isinstance(transcript, dict) else []
                    )

                    for segment in chunk_segments:
                        if hasattr(segment, 'start'):
                            s_start, s_end, s_text = segment.start, segment.end, segment.text
                        elif isinstance(segment, dict):
                            s_start, s_end, s_text = segment['start'], segment['end'], segment['text']
                        
                        all_segments.append({
                            'start': s_start + time_offset,
                            'end': s_end + time_offset,
                            'text': s_text
                        })

                if duration is None:
                    raise RuntimeError(f"Could not determine duration for chunk: {chunk_path}")
                time_offset += duration
                print(f"Finished transcribing chunk {i+1}/{len(chunks)} in {time.perf_counter() - chunk_started:.1f}s "
                      f"({len(all_segments) - before_chunk_segments} new segments).")
                
                if chunk_path != audio_file_path:
                    try:
                        os.remove(chunk_path)
                    except:
                        pass

        # Filter hallucinations
        filtered_segments = _filter_hallucinations(all_segments)
        if len(filtered_segments) < len(all_segments):
            print(f"Hallucination filter: removed {len(all_segments) - len(filtered_segments)} "
                  f"suspicious segments ({len(all_segments)} → {len(filtered_segments)}).")
        all_segments = filtered_segments

        print(f"Transcription finished in {time.perf_counter() - transcription_started:.1f}s ({len(all_segments)} segments).")
        return TranscriptResult(all_segments)

    except Exception as e:
        _report(progress_callback, f"Error during transcription: {e}")
        if not isinstance(e, RuntimeError):
            import traceback
            traceback.print_exc()
        return None
