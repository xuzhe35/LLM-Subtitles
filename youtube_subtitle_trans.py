import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from openai import OpenAI

from utils import (
    audio_enhancer,
    downloader,
    realtime_translator,
    subtitle_formatter,
    subtitle_polisher,
    transcriber,
    translator,
)
from utils.segments import normalize_segments, transcript_segments


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
DEFAULT_TRANSLATION_MODEL = "gpt-realtime-translate"
DEFAULT_POLISH_MODEL = subtitle_polisher.DEFAULT_POLISH_MODEL

# Processing pipelines. The high-quality Transcribe + LLM route is opt-in;
# with no explicit selection the pipeline is inferred from the translation
# model exactly as before, so existing users see no behavior change.
PIPELINE_REALTIME = "realtime"
PIPELINE_TRANSCRIBE_LLM = "transcribe_llm"
PIPELINE_LEGACY = "legacy"
VALID_PIPELINES = (PIPELINE_REALTIME, PIPELINE_TRANSCRIBE_LLM, PIPELINE_LEGACY)

# Characters that are unsafe in filenames on common filesystems.
_FS_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_FILENAME_LEN = 100

_API_KEY_PLACEHOLDER_MARKERS = (
    "YOUR_OPENAI_API_KEY",
    "YOUR_API_KEY",
    "YOUR-KEY",
    "REPLACE_ME",
    "<YOUR_KEY>",
    "EXAMPLE",
)

CHINESE_SUBTITLE_CODES = {"zh-Hans", "zh-CN", "zh-SG", "zh-Hans-CN", "zh"}
FALLBACK_MANUAL_SUBTITLE_PRIORITY = ("en", "en-US", "ja", "ko")

OPENAI_REQUEST_TIMEOUT_SEC = 30 * 60
OPENAI_MAX_RETRIES = 3

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class ProcessingRequest:
    url: str
    target_lang: str
    translation_model: str
    force_audio: bool
    source_lang: Optional[str]
    use_vad: bool
    whisper_prompt: Optional[str]
    max_segment_sec: Optional[int]
    engine: str
    output_dir: str
    enhance_audio: bool
    enhance_mode: str
    polish_realtime: bool
    polish_model: str
    pipeline: str = PIPELINE_LEGACY
    # Settings mapping for the opt-in Transcribe + LLM route; consumed by
    # utils.high_quality_pipeline.HighQualitySettings.from_mapping.
    high_quality: Optional[Dict] = None


@dataclass(frozen=True)
class OutputDirs:
    root: str
    original: str
    translated: str


@dataclass(frozen=True)
class VideoSource:
    info: dict
    title: str
    safe_title: str
    video_id: Optional[str] = None

    @property
    def artifact_stem(self):
        """Filesystem stem that cannot collide solely because titles match."""
        if not self.video_id:
            # Compatibility for callers/tests that construct VideoSource by hand.
            return self.safe_title
        raw_id = str(self.video_id)
        safe_id = sanitize_filename(raw_id, fallback="video-id")
        if len(safe_id) > 40:
            safe_id = f"{safe_id[:31].rstrip(' ._')}-{hashlib.sha256(raw_id.encode('utf-8')).hexdigest()[:8]}"
        title_budget = max(1, _MAX_FILENAME_LEN - len(safe_id) - 1)
        safe_title = self.safe_title[:title_budget].rstrip(" ._") or "v"
        return f"{safe_title}.{safe_id}"


@dataclass(frozen=True)
class SourceMaterial:
    segments: list
    already_translated: bool = False


@dataclass(frozen=True)
class OutputArtifacts:
    translated_srt: str
    bilingual_srt: str
    metadata_json: Optional[str] = None


def validate_openai_api_key(api_key):
    """
    Validate an OpenAI API key value pulled from config or env.

    Returns (is_valid, normalized_key_or_None, reason).
    """
    if api_key is None:
        return False, None, (
            "Missing OpenAI API key. Set OPENAI_API_KEY or add 'openai_api_key' to config.json."
        )

    key = str(api_key).strip().strip("'\"")
    if not key:
        return False, None, "OpenAI API key is empty after trimming whitespace/quotes."

    upper = key.upper()
    for marker in _API_KEY_PLACEHOLDER_MARKERS:
        if marker in upper:
            return False, None, (
                f"OpenAI API key looks like an unfilled placeholder ({marker!r}). "
                "Replace it with a real key in config.json or OPENAI_API_KEY."
            )

    return True, key, None


def sanitize_filename(title, fallback="video"):
    """
    Produce a filesystem-safe filename from a possibly Unicode title.
    """
    if not title:
        return fallback

    cleaned = _FS_UNSAFE_CHARS.sub("_", str(title))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.strip(" ._")

    if not cleaned:
        return fallback
    if not any(c.isalnum() for c in cleaned):
        return fallback

    if len(cleaned) > _MAX_FILENAME_LEN:
        cleaned = cleaned[:_MAX_FILENAME_LEN].rstrip(" ._")
        if not cleaned or not any(c.isalnum() for c in cleaned):
            return fallback

    return cleaned


def load_local_env(path=None):
    """Load an ignored local env file without overriding the process environment."""
    env_path = path or os.path.join(PROJECT_ROOT, ".env.local")
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            key, value = line.split("=", 1)
            key = key.strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                continue
            os.environ.setdefault(key, value.strip().strip("'\""))


def load_config():
    load_local_env()
    config_path = os.path.join(PROJECT_ROOT, "config.json")
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_config_value(config, env_keys, config_keys=None, default=None):
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


def get_boolean_config_value(config, env_keys, config_keys=None, explicit=None, default=False):
    if explicit is not None:
        return bool(explicit)

    for env_key in env_keys:
        value = os.getenv(env_key)
        if value is not None:
            return str(value).strip().lower() in {"1", "true", "yes", "on"}

    if config_keys:
        for key in config_keys:
            if key in config:
                value = config[key]
                if isinstance(value, bool):
                    return value
                return str(value).strip().lower() in {"1", "true", "yes", "on"}
    return bool(default)


def resolve_output_dir(config, explicit=None):
    """
    Resolve the output directory. Priority: explicit arg > env > config > default.
    Relative paths are resolved against PROJECT_ROOT.
    """
    candidate = explicit or get_config_value(
        config,
        env_keys=["LLM_SUBTITLES_OUTPUT_DIR"],
        config_keys=["output_dir"],
        default=DEFAULT_OUTPUT_DIR,
    )
    if not os.path.isabs(candidate):
        candidate = os.path.join(PROJECT_ROOT, candidate)
    return os.path.abspath(candidate)


def _build_openai_client(api_key):
    return OpenAI(
        api_key=api_key,
        timeout=float(OPENAI_REQUEST_TIMEOUT_SEC),
        max_retries=OPENAI_MAX_RETRIES,
    )


def ensure_dirs(base_path):
    dirs = {
        "original": os.path.join(base_path, "original"),
        "translated": os.path.join(base_path, "translated"),
    }
    for directory in dirs.values():
        os.makedirs(directory, exist_ok=True)
    return dirs


def _ensure_output_dirs(base_path):
    dirs = ensure_dirs(base_path)
    return OutputDirs(
        root=os.path.abspath(base_path),
        original=dirs["original"],
        translated=dirs["translated"],
    )


def normalize_pipeline_name(value):
    """Map CLI/config spellings (e.g. 'transcribe-llm') to canonical names."""
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized and normalized not in VALID_PIPELINES:
        raise ValueError(
            f"Unsupported pipeline: {value!r}. Expected one of: {', '.join(VALID_PIPELINES)}"
        )
    return normalized or None


def resolve_pipeline(explicit, config, translation_model):
    """Resolution priority: explicit CLI/GUI choice > config > inference.

    Backward-compatible inference keeps existing behavior exactly:
    gpt-realtime-translate selects the Realtime route, anything else keeps
    the legacy manual-subtitle/ASR/text-translation path.
    """
    selected = normalize_pipeline_name(explicit)
    if selected:
        return selected
    configured = normalize_pipeline_name(config.get("pipeline"))
    if configured:
        return configured
    if str(translation_model or "").strip().lower() == realtime_translator.REALTIME_TRANSLATE_MODEL:
        return PIPELINE_REALTIME
    return PIPELINE_LEGACY


def load_keywords_file(path):
    """Load transcription keywords from a JSON array or newline-separated file."""
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    stripped = content.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        keywords = json.loads(stripped)
        if not isinstance(keywords, list):
            raise ValueError(f"Keywords file must contain a JSON array: {path}")
        return [str(keyword) for keyword in keywords]
    return [line.strip() for line in stripped.splitlines() if line.strip()]


def _parse_source_languages(value):
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _resolve_high_quality_settings(config, overrides=None):
    """Merge config's high_quality block with explicit CLI/GUI overrides."""
    settings = dict(config.get("high_quality") or {})
    for key, value in (overrides or {}).items():
        if value is not None:
            settings[key] = value
    if "source_languages" in settings:
        settings["source_languages"] = _parse_source_languages(settings["source_languages"])
    return settings


def _resolve_request(config, url, lang, model, force_audio, source_lang, use_vad,
                     whisper_prompt, max_segment_sec, engine, output_dir,
                     enhance_audio=False, enhance_mode="mild",
                     polish_realtime=None, polish_model=None,
                     pipeline=None, high_quality_overrides=None):
    target_lang = lang or get_config_value(
        config,
        env_keys=["DEFAULT_TARGET_LANGUAGE"],
        config_keys=["default_target_language"],
        default="Simplified Chinese",
    )
    translation_model = model or get_config_value(
        config,
        env_keys=["OPENAI_MODEL"],
        config_keys=["model"],
        default=DEFAULT_TRANSLATION_MODEL,
    )
    resolved_polish_model = polish_model or get_config_value(
        config,
        env_keys=["OPENAI_POLISH_MODEL"],
        config_keys=["polish_model"],
        default=DEFAULT_POLISH_MODEL,
    )
    return ProcessingRequest(
        url=url,
        target_lang=target_lang,
        translation_model=translation_model,
        force_audio=force_audio,
        source_lang=source_lang,
        use_vad=use_vad,
        whisper_prompt=whisper_prompt,
        max_segment_sec=max_segment_sec,
        engine=(engine or "whisper").strip().lower(),
        output_dir=resolve_output_dir(config, explicit=output_dir),
        enhance_audio=bool(enhance_audio),
        enhance_mode=audio_enhancer.normalize_mode(enhance_mode),
        polish_realtime=get_boolean_config_value(
            config,
            env_keys=["POLISH_REALTIME_SUBTITLES"],
            config_keys=["polish_realtime_subtitles"],
            explicit=polish_realtime,
            default=True,
        ),
        polish_model=str(resolved_polish_model).strip() or DEFAULT_POLISH_MODEL,
        pipeline=resolve_pipeline(pipeline, config, translation_model),
        high_quality=_resolve_high_quality_settings(config, high_quality_overrides),
    )


def _resolve_openai_api_key(config, progress_callback):
    raw_api_key = get_config_value(
        config,
        env_keys=["OPENAI_API_KEY"],
        config_keys=["openai_api_key"],
    )
    is_valid, api_key, reason = validate_openai_api_key(raw_api_key)
    if not is_valid:
        progress_callback(f"Error: {reason}")
        return None

    if not api_key.startswith("sk-"):
        progress_callback(
            f"Warning: OpenAI API key does not start with 'sk-' (got prefix {api_key[:4]!r}). "
            "Proceeding anyway - this is OK for self-hosted proxies / Azure endpoints."
        )

    return api_key


def _resolve_openai_client(config, progress_callback):
    api_key = _resolve_openai_api_key(config, progress_callback)
    if api_key is None:
        return None

    return _build_openai_client(api_key)


def _fetch_video_source(request, progress_callback):
    progress_callback("Fetching video info...")
    info = downloader.get_video_info(request.url)
    if not info:
        progress_callback("Error: Failed to get video info.")
        return None

    title = info.get("title", "video")
    raw_video_id = str(info.get("id") or "").strip()
    if not raw_video_id:
        # Some extractors omit an ID. A stable URL digest still isolates the
        # local cache without exposing the URL in a filename.
        raw_video_id = hashlib.sha256(request.url.encode("utf-8")).hexdigest()[:12]
    fallback_name = raw_video_id or "video"
    safe_title = sanitize_filename(title, fallback=fallback_name)
    if safe_title != title:
        progress_callback(f"Sanitized title for filenames: {safe_title!r} (from {title!r})")

    return VideoSource(
        info=info,
        title=title,
        safe_title=safe_title,
        video_id=raw_video_id,
    )


def _target_language_codes(target_lang):
    if "Chinese" in target_lang:
        return CHINESE_SUBTITLE_CODES
    return set()


def _subtitle_matches_target(lang_code, target_lang):
    if "Chinese" in target_lang:
        return lang_code in CHINESE_SUBTITLE_CODES
    return target_lang.lower() in lang_code.lower()


def _select_manual_subtitle(info, request, progress_callback):
    manual_subs = info.get("subtitles", {})
    if request.force_audio:
        progress_callback("Force Audio Source enabled: Skipping manual subtitles check.")
        return None

    progress_callback("Checking for manual subtitles...")
    target_codes = _target_language_codes(request.target_lang)
    for code in manual_subs:
        if code in target_codes or request.target_lang.lower() in code.lower():
            progress_callback(f"Found manual subtitle for target language: {code}")
            return code

    if not manual_subs:
        return None

    progress_callback("No manual subtitle in target language found, using fallback manual subtitle.")
    for code in FALLBACK_MANUAL_SUBTITLE_PRIORITY:
        if code in manual_subs:
            return code
    return next(iter(manual_subs))


def _parse_subtitle_file(path):
    if path.endswith(".srt"):
        return subtitle_formatter.parse_srt(path)
    return subtitle_formatter.parse_vtt(path)


def _download_manual_source(request, video, output_dirs, lang_code,
                            progress_callback, download_progress_callback):
    progress_callback(f"Downloading manual subtitle: {lang_code}")
    output_base = os.path.join(output_dirs.original, video.artifact_stem)
    subtitle_path = downloader.download_manual_subtitle(
        request.url,
        lang_code,
        output_base,
        progress_hook=download_progress_callback,
    )

    if not subtitle_path or not os.path.exists(subtitle_path):
        fallback_path = f"{output_base}.{lang_code}.vtt"
        if os.path.exists(fallback_path):
            subtitle_path = fallback_path
        else:
            progress_callback("Error: Could not find downloaded subtitle file.")
            return None

    progress_callback(f"Original subtitle saved to: {subtitle_path}")
    segments = normalize_segments(_parse_subtitle_file(subtitle_path))
    return SourceMaterial(
        segments=segments,
        already_translated=_subtitle_matches_target(lang_code, request.target_lang),
    )


def _find_existing_audio(output_base):
    for ext in (".mp3", ".m4a", ".wav", ".opus", ".webm"):
        candidate = output_base + ext
        if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
            return candidate
    return None


def _load_or_download_audio(request, video, output_dirs, progress_callback, download_progress_callback):
    output_base = os.path.join(output_dirs.original, f"{video.artifact_stem}_audio")
    audio_path = _find_existing_audio(output_base)
    if audio_path:
        progress_callback(f"Audio already exists: {os.path.basename(audio_path)}, skipping download.")
        return audio_path

    progress_callback("Downloading audio...")
    audio_path = downloader.download_audio(
        request.url,
        output_base,
        progress_hook=download_progress_callback,
    )
    if not audio_path:
        progress_callback("Error: Audio download failed.")
        return None
    return audio_path


def _prepare_audio_for_transcription(request, video, output_dirs, audio_path, progress_callback):
    if not request.enhance_audio or request.enhance_mode == audio_enhancer.MODE_OFF:
        return audio_path

    enhanced_path = os.path.join(
        output_dirs.original,
        f"{video.artifact_stem}_audio.enhanced.{request.enhance_mode}.wav",
    )
    try:
        return audio_enhancer.enhance_audio(
            audio_path,
            output_path=enhanced_path,
            mode=request.enhance_mode,
            progress_callback=progress_callback,
        )
    except Exception as e:
        progress_callback(f"Warning: audio enhancement failed, using original audio instead: {e}")
        return audio_path


def _google_api_key_for_model(config, transcription_model, progress_callback):
    if transcription_model != transcriber.TRANSCRIPTION_MODEL_GOOGLE:
        return None

    google_api_key = get_config_value(
        config,
        env_keys=["GOOGLE_API_KEY"],
        config_keys=["google_api_key", "Google API Key"],
    )
    if not google_api_key:
        progress_callback("Error: Google engine requires GOOGLE_API_KEY or config.json google_api_key.")
        return None
    return google_api_key


def _transcribe_audio_source(client, config, request, video, output_dirs,
                             progress_callback, download_progress_callback):
    progress_callback("Proceeding with AI extraction (Forced)." if request.force_audio else
                      "No manual subtitles found. Proceeding with AI extraction...")

    audio_path = _load_or_download_audio(
        request,
        video,
        output_dirs,
        progress_callback,
        download_progress_callback,
    )
    if not audio_path:
        return None
    audio_path = _prepare_audio_for_transcription(
        request,
        video,
        output_dirs,
        audio_path,
        progress_callback,
    )

    transcription_model = transcriber.resolve_transcription_model(request.engine, request.source_lang)
    progress_callback(f"Resolved transcription model: {transcription_model}")
    progress_callback(f"Transcribing audio ({transcription_model})...")

    google_api_key = _google_api_key_for_model(config, transcription_model, progress_callback)
    if transcription_model == transcriber.TRANSCRIPTION_MODEL_GOOGLE and not google_api_key:
        return None

    transcript = transcriber.transcribe_audio(
        client,
        audio_path,
        source_lang=request.source_lang,
        use_vad=request.use_vad,
        whisper_prompt=request.whisper_prompt,
        max_segment_sec=request.max_segment_sec,
        engine=request.engine,
        google_api_key=google_api_key,
        transcription_model=transcription_model,
        progress_callback=progress_callback,
    )
    if not transcript:
        progress_callback("Error: Transcription failed.")
        return None

    return SourceMaterial(segments=transcript_segments(transcript))


def _resolve_source_material(client, config, request, video, output_dirs,
                             progress_callback, download_progress_callback):
    manual_code = _select_manual_subtitle(video.info, request, progress_callback)
    if manual_code:
        return _download_manual_source(
            request,
            video,
            output_dirs,
            manual_code,
            progress_callback,
            download_progress_callback,
        )
    return _transcribe_audio_source(
        client,
        config,
        request,
        video,
        output_dirs,
        progress_callback,
        download_progress_callback,
    )


def _translate_source_material(client, request, source_material, progress_callback):
    if source_material.already_translated:
        progress_callback("Manual subtitle matches target language. Generating SRTs...")
        return source_material.segments

    progress_callback(f"Translating {len(source_material.segments)} segments to {request.target_lang}...")
    return translator.translate_segments(
        client,
        source_material.segments,
        request.target_lang,
        request.translation_model,
        progress_callback=progress_callback,
    )


def _write_outputs(request, video, output_dirs, original_segments, translated_segments,
                   progress_callback, filename_tag=None, metadata_json=None):
    progress_callback("Generating final files...")

    stem_parts = [video.artifact_stem]
    if filename_tag:
        stem_parts.append(filename_tag)
    stem_parts.append(request.target_lang)
    output_stem = ".".join(stem_parts)

    translated_path = os.path.join(
        output_dirs.translated,
        f"{output_stem}.srt",
    )
    subtitle_formatter.generate_srt(translated_segments, translated_path)
    progress_callback(f"Translated SRT saved: {translated_path}")

    bilingual_path = os.path.join(
        output_dirs.translated,
        f"{output_stem}.bilingual.srt",
    )
    subtitle_formatter.generate_bilingual_srt(
        original_segments,
        translated_segments,
        bilingual_path,
        progress_callback=progress_callback,
    )
    progress_callback(f"Bilingual SRT saved: {bilingual_path}")

    return OutputArtifacts(
        translated_srt=translated_path,
        bilingual_srt=bilingual_path,
        metadata_json=metadata_json,
    )


def _process_realtime_translation(api_key, request, video, output_dirs,
                                  progress_callback, download_progress_callback):
    progress_callback(
        "gpt-realtime-translate selected: forcing the downloaded audio path and "
        "skipping manual subtitles, ASR, and text-model translation."
    )
    audio_path = _load_or_download_audio(
        request,
        video,
        output_dirs,
        progress_callback,
        download_progress_callback,
    )
    if not audio_path:
        return None
    audio_path = _prepare_audio_for_transcription(
        request,
        video,
        output_dirs,
        audio_path,
        progress_callback,
    )

    target_tag = sanitize_filename(request.target_lang, fallback="target")
    artifact_stem = (
        f"{video.artifact_stem}.{realtime_translator.REALTIME_TRANSLATE_MODEL}.{target_tag}"
    )
    checkpoint_path = os.path.join(
        output_dirs.translated,
        f"{artifact_stem}.resume.json",
    )
    segment_duration = (
        request.max_segment_sec
        or realtime_translator.DEFAULT_SEGMENT_DURATION_SEC
    )
    segment_overlap = min(
        realtime_translator.DEFAULT_SEGMENT_OVERLAP_SEC,
        max(0.1, float(segment_duration) * 0.1),
    )
    progress_callback(
        f"Realtime recovery plan: {segment_duration}s sessions with "
        f"{segment_overlap:g}s overlap; checkpoint={checkpoint_path}"
    )

    result = realtime_translator.translate_audio(
        api_key=api_key,
        audio_file_path=audio_path,
        target_language=request.target_lang,
        model=realtime_translator.REALTIME_TRANSLATE_MODEL,
        progress_callback=progress_callback,
        checkpoint_path=checkpoint_path,
        segment_duration_sec=segment_duration,
        segment_overlap_sec=segment_overlap,
    )
    metadata_path = os.path.join(
        output_dirs.translated,
        f"{artifact_stem}.json",
    )
    with open(metadata_path, "w", encoding="utf-8") as metadata_file:
        json.dump(result.to_metadata(), metadata_file, ensure_ascii=False, indent=2)
    progress_callback(f"Realtime transcript metadata saved: {metadata_path}")

    raw_artifacts = _write_outputs(
        request,
        video,
        output_dirs,
        result.source_segments,
        result.translated_segments,
        progress_callback,
        filename_tag=realtime_translator.REALTIME_TRANSLATE_MODEL,
        metadata_json=metadata_path,
    )
    if not request.polish_realtime:
        progress_callback("Global subtitle polishing disabled; keeping raw Realtime output.")
        return raw_artifacts

    polish_checkpoint_path = os.path.join(
        output_dirs.translated,
        f"{artifact_stem}.polish.resume.json",
    )
    polished_metadata_path = os.path.join(
        output_dirs.translated,
        f"{artifact_stem}.polished.json",
    )
    progress_callback(
        f"Starting context-aware global subtitle polish with {request.polish_model}; "
        f"checkpoint={polish_checkpoint_path}"
    )
    try:
        polish_client = _build_openai_client(api_key)
        polish_result, polished_metadata = subtitle_polisher.polish_realtime_metadata(
            polish_client,
            result.to_metadata(),
            target_language=request.target_lang,
            model=request.polish_model,
            checkpoint_path=polish_checkpoint_path,
            progress_callback=progress_callback,
        )
        with open(polished_metadata_path, "w", encoding="utf-8") as metadata_file:
            json.dump(polished_metadata, metadata_file, ensure_ascii=False, indent=2)
        progress_callback(f"Polished subtitle metadata saved: {polished_metadata_path}")
    except Exception:
        progress_callback(
            "Global polishing stopped. Raw Realtime JSON/SRT files are safe, and the "
            "polish checkpoint can resume completed windows on the next run."
        )
        raise

    return _write_outputs(
        request,
        video,
        output_dirs,
        result.source_segments,
        polish_result.translated_segments,
        progress_callback,
        filename_tag=f"{realtime_translator.REALTIME_TRANSLATE_MODEL}.polished",
        metadata_json=polished_metadata_path,
    )


def _process_transcribe_llm(api_key, request, video, output_dirs,
                            progress_callback, download_progress_callback):
    """Run the opt-in Transcribe + LLM high-quality route.

    The orchestrator is imported lazily so the Realtime and legacy routes
    never touch it.
    """
    from utils import high_quality_pipeline

    progress_callback(
        "transcribe_llm pipeline selected: dual ASR (semantic + timing), "
        "deterministic alignment, and context-aware LLM translation. "
        "Intermediate artifacts are reusable across target languages."
    )
    audio_path = _load_or_download_audio(
        request,
        video,
        output_dirs,
        progress_callback,
        download_progress_callback,
    )
    if not audio_path:
        return None
    audio_path = _prepare_audio_for_transcription(
        request,
        video,
        output_dirs,
        audio_path,
        progress_callback,
    )

    client = _build_openai_client(api_key)
    target_tag = sanitize_filename(request.target_lang, fallback="target")
    artifacts = high_quality_pipeline.run_pipeline(
        client,
        audio_path=audio_path,
        original_dir=output_dirs.original,
        translated_dir=output_dirs.translated,
        stem=video.artifact_stem,
        target_language=request.target_lang,
        settings=request.high_quality,
        target_tag=target_tag,
        progress_callback=progress_callback,
    )
    return OutputArtifacts(
        translated_srt=artifacts.translated_srt,
        bilingual_srt=artifacts.bilingual_srt,
        metadata_json=artifacts.translated_json,
    )


def process_video(url, lang=None, model=None, force_audio=False, source_lang=None,
                  use_vad=False, whisper_prompt=None, max_segment_sec=None,
                  engine="whisper", progress_callback=print,
                  download_progress_callback=None, output_dir=None,
                  enhance_audio=False, enhance_mode="mild",
                  polish_realtime=None, polish_model=None,
                  pipeline=None, high_quality_overrides=None):
    """
    Main processing logic, callable by CLI and GUI.
    """
    config = load_config()
    request = _resolve_request(
        config,
        url=url,
        lang=lang,
        model=model,
        force_audio=force_audio,
        source_lang=source_lang,
        use_vad=use_vad,
        whisper_prompt=whisper_prompt,
        max_segment_sec=max_segment_sec,
        engine=engine,
        output_dir=output_dir,
        enhance_audio=enhance_audio,
        enhance_mode=enhance_mode,
        polish_realtime=polish_realtime,
        polish_model=polish_model,
        pipeline=pipeline,
        high_quality_overrides=high_quality_overrides,
    )
    api_key = _resolve_openai_api_key(config, progress_callback)
    if api_key is None:
        return None

    progress_callback(f"Processing URL: {request.url} | Target: {request.target_lang}")
    progress_callback(f"Output directory: {request.output_dir}")
    progress_callback(f"Pipeline: {request.pipeline}")
    output_dirs = _ensure_output_dirs(request.output_dir)

    video = _fetch_video_source(request, progress_callback)
    if video is None:
        return None

    if request.pipeline == PIPELINE_REALTIME:
        artifacts = _process_realtime_translation(
            api_key,
            request,
            video,
            output_dirs,
            progress_callback,
            download_progress_callback,
        )
        if artifacts is not None:
            progress_callback("Done!")
        return artifacts

    if request.pipeline == PIPELINE_TRANSCRIBE_LLM:
        artifacts = _process_transcribe_llm(
            api_key,
            request,
            video,
            output_dirs,
            progress_callback,
            download_progress_callback,
        )
        if artifacts is not None:
            progress_callback("Done!")
        return artifacts

    client = _build_openai_client(api_key)

    source_material = _resolve_source_material(
        client,
        config,
        request,
        video,
        output_dirs,
        progress_callback,
        download_progress_callback,
    )
    if source_material is None:
        return None

    translated_segments = _translate_source_material(
        client,
        request,
        source_material,
        progress_callback,
    )

    artifacts = _write_outputs(
        request,
        video,
        output_dirs,
        source_material.segments,
        translated_segments,
        progress_callback,
    )
    progress_callback("Done!")
    return artifacts


def build_arg_parser():
    parser = argparse.ArgumentParser(description="YouTube Subtitle Generator")
    parser.add_argument("url", help="YouTube Video URL")
    parser.add_argument("--lang", help="Target Language (overrides config)")
    parser.add_argument(
        "--model",
        help="Translation model (for example gpt-4o or gpt-realtime-translate; overrides config)",
    )
    parser.add_argument("--force-audio", action="store_true", help="Force audio extraction even if manual subtitles exist")
    parser.add_argument("--source-lang", help="Source audio language ISO code (e.g. en, ja, th)", default=None)
    parser.add_argument("--use-vad", action="store_true", help="Enable Voice Activity Detection to filter silence/noise")
    parser.add_argument("--whisper-prompt", help="Prompt to guide Whisper transcription", default=None)
    parser.add_argument(
        "--max-segment-sec",
        type=int,
        help=(
            "Max ASR segment duration, or resumable realtime session duration, "
            "in seconds (default: 600)"
        ),
        default=None,
    )
    parser.add_argument(
        "--engine",
        help=(
            "Transcription engine/model: 'whisper', 'google', "
            "'gpt-4o-transcribe-diarize', or 'auto'"
        ),
        default="whisper",
    )
    parser.add_argument("--output-dir", help="Output directory (overrides config). Relative paths resolve against the project root.", default=None)
    parser.add_argument("--enhance-audio", action="store_true", help="Enhance/denoise audio before transcription")
    parser.add_argument(
        "--enhance-mode",
        choices=["mild", "strong_ffmpeg", "off"],
        default="mild",
        help="Audio enhancement mode used when --enhance-audio is set",
    )
    parser.set_defaults(polish_realtime=None)
    parser.add_argument(
        "--polish-realtime",
        dest="polish_realtime",
        action="store_true",
        help="Run resumable whole-video context polishing after Realtime Translation (default)",
    )
    parser.add_argument(
        "--no-polish-realtime",
        dest="polish_realtime",
        action="store_false",
        help="Keep raw Realtime subtitles without the global polishing pass",
    )
    parser.add_argument(
        "--polish-model",
        default=None,
        help=f"Text model for global subtitle polishing (default: {DEFAULT_POLISH_MODEL})",
    )

    # --- Transcribe + LLM high-quality route (opt-in) ---------------------
    parser.add_argument(
        "--pipeline",
        choices=["realtime", "transcribe-llm", "transcribe_llm", "legacy"],
        default=None,
        help=(
            "Processing pipeline: 'realtime' (fast streamed translation), "
            "'transcribe-llm' (high-quality dual-ASR + LLM translation), or "
            "'legacy'. Default: inferred from --model as before."
        ),
    )
    parser.add_argument(
        "--semantic-model",
        default=None,
        help="Semantic ASR model for transcribe-llm (default: gpt-transcribe)",
    )
    parser.add_argument(
        "--timing-model",
        choices=["auto", "whisper-1", "gpt-4o-transcribe-diarize"],
        default=None,
        help="Timing backbone for transcribe-llm (default: auto)",
    )
    parser.add_argument(
        "--source-languages",
        default=None,
        help="Expected source language ISO codes for transcribe-llm, e.g. 'th,en'",
    )
    parser.add_argument(
        "--transcription-prompt",
        default=None,
        help="Context prompt for gpt-transcribe (title, subject, setting)",
    )
    parser.add_argument(
        "--transcription-keyword",
        action="append",
        dest="transcription_keywords",
        default=None,
        help="Literal keyword for gpt-transcribe (repeatable)",
    )
    parser.add_argument(
        "--transcription-keywords-file",
        default=None,
        help="File with keywords: JSON array or one keyword per line",
    )
    parser.add_argument(
        "--context-model",
        default=None,
        help="Model for whole-program context analysis (default: gpt-5.6-terra)",
    )
    parser.add_argument(
        "--llm-translation-model",
        default=None,
        help="Text model for transcribe-llm window translation (default: gpt-5.6-terra)",
    )
    parser.add_argument(
        "--translation-escalation-model",
        default=None,
        help="Escalation model for difficult windows (default: gpt-5.6-sol)",
    )
    parser.set_defaults(translation_escalation=None)
    parser.add_argument(
        "--no-translation-escalation",
        dest="translation_escalation",
        action="store_false",
        help="Disable selective escalation to the stronger model",
    )
    parser.set_defaults(strict_high_quality=None)
    parser.add_argument(
        "--strict-high-quality",
        dest="strict_high_quality",
        action="store_true",
        help=(
            "Stop with a resumable error instead of degrading when the "
            "semantic transcript fails (default)"
        ),
    )
    return parser


def build_high_quality_overrides(args):
    """Collect CLI overrides for the transcribe-llm route from parsed args."""
    keywords = list(args.transcription_keywords or [])
    if args.transcription_keywords_file:
        keywords.extend(load_keywords_file(args.transcription_keywords_file))
    return {
        "semantic_model": args.semantic_model,
        "timing_model": args.timing_model,
        "source_languages": args.source_languages,
        "prompt": args.transcription_prompt,
        "keywords": keywords or None,
        "context_model": args.context_model,
        "translation_model": args.llm_translation_model,
        "translation_escalation_model": args.translation_escalation_model,
        "enable_selective_escalation": args.translation_escalation,
        "strict": args.strict_high_quality,
    }


def main():
    parser = build_arg_parser()
    args = parser.parse_args()

    process_video(
        args.url,
        args.lang,
        args.model,
        args.force_audio,
        source_lang=args.source_lang,
        use_vad=args.use_vad,
        whisper_prompt=args.whisper_prompt,
        max_segment_sec=args.max_segment_sec,
        engine=args.engine,
        output_dir=args.output_dir,
        enhance_audio=args.enhance_audio,
        enhance_mode=args.enhance_mode,
        polish_realtime=args.polish_realtime,
        polish_model=args.polish_model,
        pipeline=args.pipeline,
        high_quality_overrides=build_high_quality_overrides(args),
    )


if __name__ == "__main__":
    main()
