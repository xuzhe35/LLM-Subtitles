import argparse
import json
import os
from openai import OpenAI
from utils import downloader, transcriber, translator, subtitle_formatter

def load_config():
    config_path = "config.json"
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r") as f:
        return json.load(f)

def get_config_value(config, env_keys, config_keys=None, default=None):
    """
    Resolve config from environment variables first, then config.json keys.
    """
    for env_key in env_keys:
        value = os.getenv(env_key)
        if value:
            return value

    if config_keys:
        for key in config_keys:
            value = config.get(key)
            if value:
                return value

    return default

def ensure_dirs(base_path):
    dirs = {
        'original': os.path.join(base_path, 'original'),
        'translated': os.path.join(base_path, 'translated')
    }
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    return dirs

def process_video(url, lang=None, model=None, force_audio=False, source_lang=None, use_vad=False, whisper_prompt=None, max_segment_sec=None, engine='whisper', progress_callback=print, download_progress_callback=None):
    """
    Main processing logic, callable by UI.
    progress_callback: function to receive log strings.
    download_progress_callback: function to receive yt-dlp percent string (e.g. "45.0%").
    """
    config = load_config()

    api_key = get_config_value(
        config,
        env_keys=["OPENAI_API_KEY"],
        config_keys=["openai_api_key"]
    )
    if not api_key or "YOUR_OPENAI_API_KEY" in api_key:
        progress_callback("Error: Missing OpenAI API key. Set OPENAI_API_KEY or config.json openai_api_key.")
        return

    client = OpenAI(api_key=api_key, max_retries=0)
    target_lang = lang if lang else get_config_value(
        config,
        env_keys=["DEFAULT_TARGET_LANGUAGE"],
        config_keys=["default_target_language"],
        default="Simplified Chinese"
    )
    Model = model if model else get_config_value(
        config,
        env_keys=["OPENAI_MODEL"],
        config_keys=["model"],
        default="gpt-4o"
    )

    progress_callback(f"Processing URL: {url} | Target: {target_lang}")
    
    # Setup Output Directories
    output_root = "output"
    dirs = ensure_dirs(output_root)
    
    # 1. Get Video Info
    progress_callback("Fetching video info...")
    info = downloader.get_video_info(url)
    if not info:
        progress_callback("Error: Failed to get video info.")
        return

    video_title = info.get('title', 'video')
    safe_title = "".join([c for c in video_title if c.isalpha() or c.isdigit() or c==' ']).rstrip()
    
    # Variables to track
    original_segments = []
    translated_segments = []
    
    # 2. Check for Manual Subtitles (unless forced audio)
    manual_subs = info.get('subtitles', {})
    found_manual_code = None
    
    if force_audio:
        progress_callback("Force Audio Source enabled: Skipping manual subtitles check.")
    else:
        progress_callback("Checking for manual subtitles...")
        
        target_lang_codes = ['zh-Hans', 'zh-CN', 'zh-SG', 'zh-Hans-CN'] if 'Chinese' in target_lang else []
        
        for code in manual_subs:
            if code in target_lang_codes or (target_lang.lower() in code.lower()):
                found_manual_code = code
                progress_callback(f"Found manual subtitle for target language: {code}")
                break
                
        if not found_manual_code and manual_subs:
            progress_callback("No manual subtitle in target language found, using fallback manual subtitle.")
            priority_langs = ['en', 'en-US', 'ja', 'ko']
            for l in priority_langs:
                if l in manual_subs:
                    found_manual_code = l
                    break
            if not found_manual_code:
                found_manual_code = list(manual_subs.keys())[0]

    if found_manual_code:
        progress_callback(f"Downloading manual subtitle: {found_manual_code}")
        original_sub_path_base = os.path.join(dirs['original'], safe_title)
        expected_filename = downloader.download_manual_subtitle(url, found_manual_code, original_sub_path_base, progress_hook=download_progress_callback)
        
        if not expected_filename or not os.path.exists(expected_filename):
             potential = f"{original_sub_path_base}.{found_manual_code}.vtt"
             if os.path.exists(potential):
                 expected_filename = potential
             else:
                 progress_callback(f"Error: Could not find downloaded subtitle file.")
                 return
                 
        progress_callback(f"Original subtitle saved to: {expected_filename}")

        # Parse original
        if expected_filename.endswith('.vtt'):
            original_segments = subtitle_formatter.parse_vtt(expected_filename)
        else:
             # Assume SRT or try parsing VTT logic?
             # For now assume VTT as yt-dlp default
             original_segments = subtitle_formatter.parse_vtt(expected_filename)

        # Logic for fallback translation vs direct use
        # If we found EXACT match manual sub, we might want to just output it.
        # But user might still want "translation" if the manual sub is not in target lang.
        
        # If found_manual_code is in target_lang_codes, then it is already translated.
        is_target = False
        if 'Chinese' in target_lang:
             if found_manual_code in ['zh-Hans', 'zh-CN', 'zh-SG', 'zh-Hans-CN', 'zh']:
                 is_target = True
        elif target_lang.lower() in found_manual_code.lower():
             is_target = True

        if is_target:
            # Matches target language. 
            progress_callback("Manual subtitle matches target language. Generating SRTs...")
            translated_segments = original_segments # It IS the translated one
        else:
            # Fallback manual. Needs translation.
            progress_callback(f"Translating {len(original_segments)} segments to {target_lang}...")
            translated_segments = translator.translate_segments(client, original_segments, target_lang, Model, progress_callback=progress_callback)

    else:
        # 3. Audio Extraction & AI Flow
        if not force_audio:
            progress_callback("No manual subtitles found. Proceeding with AI extraction...")
        else:
             progress_callback("Proceeding with AI extraction (Forced)...")
        
        audio_file_path = os.path.join(dirs['original'], f"{safe_title}_audio")
        
        # Check if audio already exists (try common extensions)
        audio_path = None
        for ext in ['.mp3', '.m4a', '.wav', '.opus', '.webm']:
            candidate = audio_file_path + ext
            if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
                audio_path = candidate
                break
        
        if audio_path:
            progress_callback(f"Audio already exists: {os.path.basename(audio_path)}, skipping download.")
        else:
            progress_callback("Downloading audio...")
            audio_path = downloader.download_audio(url, audio_file_path, progress_hook=download_progress_callback)
            if not audio_path:
                 progress_callback("Error: Audio download failed.")
                 return

        progress_callback(f"Transcribing audio ({engine})...")
        google_api_key = get_config_value(
            config,
            env_keys=["GOOGLE_API_KEY"],
            config_keys=["google_api_key", "Google API Key"]
        )
        if engine == 'google' and not google_api_key:
             progress_callback("Error: Google engine requires GOOGLE_API_KEY or config.json google_api_key.")
             return

        transcript = transcriber.transcribe_audio(
            client, audio_path, source_lang=source_lang, use_vad=use_vad, 
            whisper_prompt=whisper_prompt, max_segment_sec=max_segment_sec,
            engine=engine, google_api_key=google_api_key
        )
        if not transcript:
             progress_callback("Error: Transcription failed.")
             return

        original_segments_raw = transcript.segments if hasattr(transcript, 'segments') else []
        # Standardize structure from whisper object to dict
        for s in original_segments_raw:
             if isinstance(s, dict):
                 start = s['start']
                 end = s['end']
                 text = s['text']
             else:
                 start = s.start
                 end = s.end
                 text = s.text

             original_segments.append({
                 'start': start,
                 'end': end,
                 'text': text
             })

        progress_callback("Translating segments (LLM)...")
        translated_segments = translator.translate_segments(client, original_segments, target_lang, Model, progress_callback=progress_callback)
    
    # Generate Outputs
    progress_callback("Generating final files...")
    
    # 1. Translated SRT
    srt_path = os.path.join(dirs['translated'], f"{safe_title}.{target_lang}.srt")
    subtitle_formatter.generate_srt(translated_segments, srt_path)
    progress_callback(f"Translated SRT saved: {srt_path}")
    
    # 2. Bilingual SRT
    bilingual_path = os.path.join(dirs['translated'], f"{safe_title}.{target_lang}.bilingual.srt")
    subtitle_formatter.generate_bilingual_srt(original_segments, translated_segments, bilingual_path)
    progress_callback(f"Bilingual SRT saved: {bilingual_path}")
    
    progress_callback("Done!")

def main():
    parser = argparse.ArgumentParser(description="YouTube Subtitle Generator")
    parser.add_argument("url", help="YouTube Video URL")
    parser.add_argument("--lang", help="Target Language (overrides config)")
    parser.add_argument("--model", help="OpenAI Model (overrides config)")
    parser.add_argument("--force-audio", action="store_true", help="Force audio extraction even if manual subtitles exist")
    parser.add_argument("--source-lang", help="Source audio language ISO code (e.g. en, ja, th)", default=None)
    parser.add_argument("--use-vad", action="store_true", help="Enable Voice Activity Detection to filter silence/noise")
    parser.add_argument("--whisper-prompt", help="Prompt to guide Whisper transcription", default=None)
    parser.add_argument("--max-segment-sec", type=int, help="Max segment duration in seconds (default: 600)", default=None)
    parser.add_argument("--engine", help="Transcription engine: 'whisper' or 'google'", default='whisper')
    args = parser.parse_args()
    
    process_video(
        args.url, args.lang, args.model, args.force_audio, 
        source_lang=args.source_lang, use_vad=args.use_vad, 
        whisper_prompt=args.whisper_prompt, max_segment_sec=args.max_segment_sec,
        engine=args.engine
    )

if __name__ == "__main__":
    main()
