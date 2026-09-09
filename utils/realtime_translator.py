import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import quote

import certifi


REALTIME_TRANSLATE_MODEL = "gpt-realtime-translate"
DEFAULT_WEBSOCKET_URL = "wss://api.openai.com/v1/realtime/translations"
SAMPLE_RATE = 24_000
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH_BYTES
DEFAULT_CHUNK_MS = 200
DEFAULT_PACING_RATE = 1.0
DEFAULT_CONNECT_TIMEOUT_SEC = 20
DEFAULT_CLOSE_TIMEOUT_SEC = 120
DEFAULT_SEGMENT_DURATION_SEC = 10 * 60
DEFAULT_SEGMENT_OVERLAP_SEC = 10
DEFAULT_SEGMENT_MAX_RETRIES = 3
CHECKPOINT_VERSION = 1

_SENTENCE_END_RE = re.compile(r"[.!?。！？…](?:[\"'’”）)】》」』]*)\s*$")
_LANGUAGE_CODE_RE = re.compile(r"^[a-zA-Z]{2,3}(?:-[a-zA-Z]{2,4})?$")
_TARGET_LANGUAGE_CODES = {
    "simplified chinese": "zh",
    "chinese (simplified)": "zh",
    "chinese": "zh",
    "简体中文": "zh",
    "中文": "zh",
    "traditional chinese": "zh",
    "chinese (traditional)": "zh",
    "繁体中文": "zh",
    "english": "en",
    "japanese": "ja",
    "thai": "th",
    "korean": "ko",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "portuguese": "pt",
    "vietnamese": "vi",
    "indonesian": "id",
    "russian": "ru",
    "arabic": "ar",
}


class RealtimeAPIError(RuntimeError):
    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable


def _api_error_is_retryable(error):
    value = str(error or "").lower()
    permanent_markers = (
        "invalid_request_error",
        "invalid_value",
        "authentication_error",
        "permission_error",
        "model_not_found",
    )
    return not any(marker in value for marker in permanent_markers)


@dataclass(frozen=True)
class TimedTextDelta:
    position: float
    text: str


@dataclass(frozen=True)
class TranslationChunk:
    index: int
    start: float
    end: float
    stream_start: float

    @property
    def stream_duration(self):
        return self.end - self.stream_start


@dataclass
class RealtimeTranslationResult:
    source_segments: list
    translated_segments: list
    source_text: str
    translated_text: str
    duration: float
    target_language: str
    model: str = REALTIME_TRANSLATE_MODEL
    transcript_events: list = field(default_factory=list)

    def to_metadata(self):
        return {
            "model": self.model,
            "target_language": self.target_language,
            "duration": self.duration,
            "source_text": self.source_text,
            "translated_text": self.translated_text,
            "source_segments": self.source_segments,
            "translated_segments": self.translated_segments,
            "transcript_events": self.transcript_events,
        }

    @classmethod
    def from_metadata(cls, payload):
        return cls(
            source_segments=list(payload.get("source_segments") or []),
            translated_segments=list(payload.get("translated_segments") or []),
            source_text=str(payload.get("source_text") or ""),
            translated_text=str(payload.get("translated_text") or ""),
            duration=float(payload.get("duration") or 0.0),
            target_language=str(payload.get("target_language") or ""),
            model=str(payload.get("model") or REALTIME_TRANSLATE_MODEL),
            transcript_events=list(payload.get("transcript_events") or []),
        )


def resolve_target_language_code(target_language):
    value = str(target_language or "").strip()
    if not value:
        raise ValueError("Realtime translation requires a target language.")

    mapped = _TARGET_LANGUAGE_CODES.get(value.lower())
    if mapped:
        return mapped
    if _LANGUAGE_CODE_RE.fullmatch(value):
        # Translation sessions currently accept base language codes (for
        # example `zh` and `th`), not regional tags such as `zh-CN`.
        return value.split("-", 1)[0].lower()
    raise ValueError(
        f"Unsupported realtime target language {target_language!r}. "
        "Use a common language name or a base ISO language code such as zh, ja, or th."
    )


def _event_position(event, fallback):
    for key in ("audio_end_ms", "end_ms", "timestamp_ms", "offset_ms"):
        value = event.get(key)
        if isinstance(value, (int, float)):
            return max(0.0, float(value) / 1000.0)
    for key in ("end", "timestamp", "offset"):
        value = event.get(key)
        if isinstance(value, (int, float)):
            return max(0.0, float(value))
    return max(0.0, float(fallback))


def _safe_event_for_metadata(event, observed_position):
    event_type = str(event.get("type", ""))
    if event_type == "session.output_audio.delta":
        return None

    safe = dict(event)
    safe["observed_audio_position"] = round(float(observed_position), 3)
    for key in ("audio", "data"):
        if key in safe:
            safe[key] = "<omitted>"
    return safe


class RealtimeTranscriptCollector:
    def __init__(self):
        self.source_deltas = []
        self.output_deltas = []
        self.transcript_events = []
        self.errors = []
        self.closed = threading.Event()

    def handle_event(self, event, observed_position):
        event_type = str(event.get("type", ""))
        position = _event_position(event, observed_position)

        if event_type == "session.input_transcript.delta":
            delta = str(event.get("delta", ""))
            if delta:
                self.source_deltas.append(TimedTextDelta(position, delta))
        elif event_type == "session.output_transcript.delta":
            delta = str(event.get("delta", ""))
            if delta:
                self.output_deltas.append(TimedTextDelta(position, delta))
        elif event_type == "error":
            error = event.get("error")
            self.errors.append(error if isinstance(error, str) else json.dumps(error or event, ensure_ascii=False))
            self.closed.set()
        elif event_type == "session.closed":
            self.closed.set()

        safe_event = _safe_event_for_metadata(event, observed_position)
        if safe_event is not None and (
            "transcript" in event_type
            or event_type in {"error", "session.created", "session.updated", "session.closed"}
        ):
            self.transcript_events.append(safe_event)

    def build_result(self, duration, target_language, model=REALTIME_TRANSLATE_MODEL):
        source_text = "".join(delta.text for delta in self.source_deltas).strip()
        translated_text = "".join(delta.text for delta in self.output_deltas).strip()
        if not translated_text:
            raise RuntimeError("Realtime translation completed without a translated transcript.")

        lag = _estimate_translation_lag(self.source_deltas, self.output_deltas)
        translated_segments = build_timed_segments(
            self.output_deltas,
            duration=duration,
            position_offset=-lag,
        )
        source_segments = align_source_to_intervals(
            self.source_deltas,
            source_text,
            translated_segments,
        )
        return RealtimeTranslationResult(
            source_segments=source_segments,
            translated_segments=translated_segments,
            source_text=source_text,
            translated_text=translated_text,
            duration=float(duration),
            target_language=target_language,
            model=model,
            transcript_events=list(self.transcript_events),
        )


def _estimate_translation_lag(source_deltas, output_deltas):
    source = next((item for item in source_deltas if item.text.strip()), None)
    output = next((item for item in output_deltas if item.text.strip()), None)
    if source is None or output is None:
        return 0.0
    return min(6.0, max(0.0, output.position - source.position))


def _split_text(text, max_chars=42):
    value = str(text or "").strip()
    if not value:
        return []

    sentence_pattern = r".+?(?:[.!?。！？…]+(?:[\"'’”）)】》」』]*)|$)"
    sentences = [
        part.strip()
        for part in re.findall(sentence_pattern, value, re.S)
        if part.strip()
    ]
    pieces = []
    for sentence in sentences:
        remaining = sentence
        while len(remaining) > max_chars:
            break_at = max(
                remaining.rfind(" ", 0, max_chars + 1),
                remaining.rfind("，", 0, max_chars + 1),
                remaining.rfind(",", 0, max_chars + 1),
            )
            if break_at < max_chars // 2:
                break_at = max_chars
            pieces.append(remaining[:break_at].strip())
            remaining = remaining[break_at:].strip(" ,，")
        if remaining:
            pieces.append(remaining)
    return pieces


def _proportional_segments(text, duration, max_chars=42):
    pieces = _split_text(text, max_chars=max_chars)
    if not pieces:
        return []

    duration = max(float(duration), len(pieces) * 0.6)
    weights = [max(1, len(piece)) for piece in pieces]
    total_weight = sum(weights)
    cursor = 0.0
    segments = []
    for index, (piece, weight) in enumerate(zip(pieces, weights)):
        end = duration if index == len(pieces) - 1 else cursor + duration * weight / total_weight
        segments.append({"start": cursor, "end": max(cursor + 0.05, end), "text": piece})
        cursor = end
    return segments


def build_timed_segments(deltas, duration, position_offset=0.0, max_chars=42, max_duration=7.0):
    deltas = [delta for delta in deltas if str(delta.text).strip()]
    duration = max(0.0, float(duration or 0.0))
    if not deltas:
        return []

    positions = [min(duration, max(0.0, delta.position + position_offset)) for delta in deltas]
    if duration >= 20.0 and max(positions) - min(positions) < duration * 0.25:
        return _proportional_segments("".join(delta.text for delta in deltas), duration, max_chars=max_chars)

    segments = []
    cursor = 0.0
    current = ""
    last_position = 0.0
    for delta, position in zip(deltas, positions):
        current += delta.text
        last_position = max(last_position, position)
        visible = current.strip()
        should_flush = bool(visible) and (
            _SENTENCE_END_RE.search(visible) is not None
            or len(visible) >= max_chars
            or last_position - cursor >= max_duration
        )
        if not should_flush:
            continue

        end = min(duration, max(cursor + 0.6, last_position)) if duration else max(cursor + 0.6, last_position)
        segments.append({"start": cursor, "end": end, "text": visible})
        cursor = end
        current = ""

    visible = current.strip()
    if visible:
        final_end = duration if duration > cursor else max(cursor + 0.6, last_position)
        segments.append({"start": cursor, "end": final_end, "text": visible})

    if not segments:
        return _proportional_segments("".join(delta.text for delta in deltas), duration, max_chars=max_chars)
    return segments


def _distribute_text_to_intervals(text, intervals):
    pieces = _split_text(text, max_chars=60)
    count = len(intervals)
    if count == 0:
        return []
    if not pieces:
        return [{"start": item["start"], "end": item["end"], "text": ""} for item in intervals]

    result = []
    for index, interval in enumerate(intervals):
        start_index = round(index * len(pieces) / count)
        end_index = round((index + 1) * len(pieces) / count)
        result.append({
            "start": interval["start"],
            "end": interval["end"],
            "text": " ".join(pieces[start_index:end_index]).strip(),
        })
    return result


def align_source_to_intervals(source_deltas, source_text, translated_segments):
    if not translated_segments:
        return []
    if not source_deltas:
        return _distribute_text_to_intervals(source_text, translated_segments)

    result = []
    delta_index = 0
    for segment_index, interval in enumerate(translated_segments):
        parts = []
        is_last = segment_index == len(translated_segments) - 1
        while delta_index < len(source_deltas):
            delta = source_deltas[delta_index]
            if not is_last and delta.position > interval["end"] + 0.75:
                break
            parts.append(delta.text)
            delta_index += 1
        result.append({
            "start": interval["start"],
            "end": interval["end"],
            "text": "".join(parts).strip(),
        })

    blank_count = sum(not item["text"] for item in result)
    if blank_count > len(result) * 0.4:
        return _distribute_text_to_intervals(source_text, translated_segments)
    return result


def _receive_events(ws, websocket_module, collector, current_position, stop_event):
    while not stop_event.is_set():
        try:
            payload = ws.recv()
        except websocket_module.WebSocketTimeoutException:
            continue
        except websocket_module.WebSocketConnectionClosedException:
            break
        except Exception as exc:
            if not collector.closed.is_set():
                collector.errors.append(str(exc))
            break

        if not payload:
            continue
        try:
            event = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            continue
        collector.handle_event(event, current_position())
        if collector.closed.is_set():
            break


def _start_ffmpeg_pcm(audio_file_path, start_sec=0.0, duration_sec=None):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required for gpt-realtime-translate audio streaming.")
    command = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    if start_sec > 0:
        command.extend(["-ss", f"{float(start_sec):.3f}"])
    command.extend(["-i", audio_file_path])
    if duration_sec is not None:
        command.extend(["-t", f"{float(duration_sec):.3f}"])
    command.extend([
        "-vn",
        "-ac", str(CHANNELS),
        "-ar", str(SAMPLE_RATE),
        "-f", "s16le",
        "pipe:1",
    ])
    return subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _probe_audio_duration(audio_file_path):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is required for resumable realtime translation.")
    completed = subprocess.run(
        [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_file_path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or completed.returncode
        raise RuntimeError(f"ffprobe could not determine audio duration: {detail}")
    try:
        duration = float(completed.stdout.strip())
    except ValueError as exc:
        raise RuntimeError("ffprobe returned an invalid audio duration.") from exc
    if duration <= 0:
        raise RuntimeError("Audio duration must be greater than zero.")
    return duration


def _audio_fingerprint(audio_file_path, sample_bytes=1024 * 1024):
    stat = os.stat(audio_file_path)
    digest = hashlib.sha256()
    digest.update(str(stat.st_size).encode("ascii"))
    with open(audio_file_path, "rb") as audio_file:
        digest.update(audio_file.read(sample_bytes))
        if stat.st_size > sample_bytes:
            audio_file.seek(max(0, stat.st_size - sample_bytes))
            digest.update(audio_file.read(sample_bytes))
    return {"size": stat.st_size, "sample_sha256": digest.hexdigest()}


def _atomic_write_json(path, payload):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=f".{os.path.basename(path)}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = temp_file.name
            json.dump(payload, temp_file, ensure_ascii=False, indent=2)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _build_chunks(duration, segment_duration, overlap):
    duration = float(duration)
    segment_duration = float(segment_duration)
    overlap = float(overlap)
    if segment_duration <= 0:
        raise ValueError("segment_duration_sec must be greater than zero.")
    if overlap < 0 or overlap >= segment_duration:
        raise ValueError("segment_overlap_sec must be non-negative and smaller than segment duration.")

    chunks = []
    index = 0
    start = 0.0
    while start < duration:
        end = min(duration, start + segment_duration)
        chunks.append(TranslationChunk(
            index=index,
            start=start,
            end=end,
            stream_start=max(0.0, start - overlap),
        ))
        index += 1
        start = end
    return chunks


def _translate_audio_session(
    api_key,
    audio_file_path,
    target_language,
    progress_callback=print,
    model=REALTIME_TRANSLATE_MODEL,
    websocket_url=None,
    pacing_rate=DEFAULT_PACING_RATE,
    chunk_ms=DEFAULT_CHUNK_MS,
    connect_timeout=DEFAULT_CONNECT_TIMEOUT_SEC,
    close_timeout=DEFAULT_CLOSE_TIMEOUT_SEC,
    start_sec=0.0,
    duration_sec=None,
):
    if not os.path.exists(audio_file_path):
        raise FileNotFoundError(f"Audio file not found: {audio_file_path}")
    if not api_key:
        raise ValueError("An OpenAI API key is required for realtime translation.")
    if pacing_rate <= 0:
        raise ValueError("pacing_rate must be greater than zero.")

    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError(
            "gpt-realtime-translate requires websocket-client. "
            "Install project dependencies with: pip install -r requirements.txt"
        ) from exc

    target_code = resolve_target_language_code(target_language)
    base_url = websocket_url or os.getenv("OPENAI_REALTIME_TRANSLATION_URL") or DEFAULT_WEBSOCKET_URL
    separator = "&" if "?" in base_url else "?"
    url = f"{base_url}{separator}model={quote(model)}"
    progress_callback(f"Connecting to {model} (target={target_code})...")

    try:
        ws = websocket.create_connection(
            url,
            header=[f"Authorization: Bearer {api_key}"],
            timeout=connect_timeout,
            sslopt={
                # python.org macOS installs can have no default OpenSSL CA file.
                # Keep TLS verification enabled by using the bundled Mozilla CA
                # store, while still allowing an explicit enterprise CA override.
                "ca_certs": os.getenv("SSL_CERT_FILE") or certifi.where(),
            },
        )
    except Exception as exc:
        raise RuntimeError(
            "Could not connect to the OpenAI Realtime Translation endpoint. "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    ws.settimeout(1.0)
    collector = RealtimeTranscriptCollector()
    position = [0.0]
    stop_event = threading.Event()
    receiver = threading.Thread(
        target=_receive_events,
        args=(ws, websocket, collector, lambda: position[0], stop_event),
        daemon=True,
    )
    receiver.start()

    process = None
    bytes_sent = 0
    started = time.monotonic()
    last_reported_second = -30
    frame_bytes = max(1, int(BYTES_PER_SECOND * chunk_ms / 1000))
    try:
        ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "audio": {
                    "input": {
                        "transcription": {
                            "model": "gpt-realtime-whisper",
                        },
                    },
                    "output": {
                        "language": target_code,
                    },
                },
            },
        }))

        process = _start_ffmpeg_pcm(
            audio_file_path,
            start_sec=start_sec,
            duration_sec=duration_sec,
        )
        while True:
            chunk = process.stdout.read(frame_bytes)
            if not chunk:
                break
            bytes_sent += len(chunk)
            position[0] = bytes_sent / BYTES_PER_SECOND
            ws.send(json.dumps({
                "type": "session.input_audio_buffer.append",
                "audio": base64.b64encode(chunk).decode("ascii"),
            }))
            if collector.errors:
                error = collector.errors[0]
                raise RealtimeAPIError(
                    f"Realtime translation API error: {error}",
                    retryable=_api_error_is_retryable(error),
                )

            target_elapsed = position[0] / pacing_rate
            sleep_for = started + target_elapsed - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)

            whole_second = int(position[0])
            if whole_second - last_reported_second >= 30:
                progress_callback(f"Realtime translation streamed {whole_second}s of audio...")
                last_reported_second = whole_second

        stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(f"ffmpeg failed while preparing realtime audio: {stderr or return_code}")

        ws.send(json.dumps({"type": "session.close"}))
        if not collector.closed.wait(timeout=close_timeout):
            raise TimeoutError(
                f"Realtime translation did not emit session.closed within {close_timeout}s."
            )
        if collector.errors:
            error = collector.errors[0]
            raise RealtimeAPIError(
                f"Realtime translation API error: {error}",
                retryable=_api_error_is_retryable(error),
            )

        result = collector.build_result(
            duration=position[0],
            target_language=target_code,
            model=model,
        )
        progress_callback(
            f"Realtime translation complete: {len(result.translated_segments)} subtitle segments."
        )
        return result
    finally:
        stop_event.set()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        try:
            ws.close()
        except Exception:
            pass
        receiver.join(timeout=2)


def _checkpoint_identity(audio_file_path, target_code, model, duration,
                         segment_duration, overlap):
    return {
        "audio": _audio_fingerprint(audio_file_path),
        "target_language": target_code,
        "model": model,
        "duration": round(float(duration), 3),
        "segment_duration": float(segment_duration),
        "segment_overlap": float(overlap),
    }


def _new_checkpoint(identity, chunks):
    return {
        "version": CHECKPOINT_VERSION,
        "identity": identity,
        "complete": False,
        "chunks": [
            {
                "index": chunk.index,
                "start": chunk.start,
                "end": chunk.end,
                "stream_start": chunk.stream_start,
                "status": "pending",
            }
            for chunk in chunks
        ],
    }


def _load_checkpoint(path, identity, chunks, progress_callback):
    if not os.path.exists(path):
        return _new_checkpoint(identity, chunks)
    try:
        with open(path, "r", encoding="utf-8") as checkpoint_file:
            checkpoint = json.load(checkpoint_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Realtime resume checkpoint is unreadable: {path}: {exc}") from exc

    identity_mismatch = checkpoint.get("identity") != identity
    completed = sum(
        item.get("status") == "complete"
        for item in checkpoint.get("chunks") or []
    )
    if identity_mismatch and completed == 0:
        progress_callback(
            "Realtime checkpoint settings changed before any chunk completed; "
            "rebuilding the checkpoint."
        )
        return _new_checkpoint(identity, chunks)
    if checkpoint.get("version") != CHECKPOINT_VERSION or identity_mismatch:
        raise RuntimeError(
            "Realtime resume checkpoint does not match the current audio/model/settings. "
            f"Move or delete it before starting a different run: {path}"
        )
    if len(checkpoint.get("chunks") or []) != len(chunks):
        raise RuntimeError(f"Realtime resume checkpoint has an invalid chunk plan: {path}")

    completed = sum(item.get("status") == "complete" for item in checkpoint["chunks"])
    if completed:
        progress_callback(
            f"Realtime resume checkpoint found: {completed}/{len(chunks)} chunk(s) already complete."
        )
    return checkpoint


def _absolute_segments(segments, chunk, is_last):
    absolute = []
    for segment in segments:
        start = float(segment.get("start") or 0.0) + chunk.stream_start
        end = float(segment.get("end") or start) + chunk.stream_start
        midpoint = (start + end) / 2.0
        owns_midpoint = midpoint >= chunk.start - 1e-6 and (
            midpoint < chunk.end - 1e-6 or is_last
        )
        if not owns_midpoint:
            continue
        clipped_start = max(chunk.start, start)
        clipped_end = min(chunk.end, end)
        if clipped_end <= clipped_start:
            clipped_end = min(chunk.end, clipped_start + 0.05)
        if clipped_end <= clipped_start:
            continue
        absolute.append({
            "start": clipped_start,
            "end": clipped_end,
            "text": str(segment.get("text") or "").strip(),
        })
    return absolute


def _combine_checkpoint_results(checkpoint, chunks, duration, target_code, model):
    source_segments = []
    translated_segments = []
    transcript_events = []
    for chunk, item in zip(chunks, checkpoint["chunks"]):
        if item.get("status") != "complete" or not item.get("result"):
            raise RuntimeError(f"Realtime translation chunk {chunk.index + 1} is incomplete.")
        result = RealtimeTranslationResult.from_metadata(item["result"])
        is_last = chunk.index == len(chunks) - 1
        source_segments.extend(_absolute_segments(result.source_segments, chunk, is_last))
        translated_segments.extend(_absolute_segments(result.translated_segments, chunk, is_last))
        for event in result.transcript_events:
            absolute_event = dict(event)
            if isinstance(absolute_event.get("observed_audio_position"), (int, float)):
                absolute_event["observed_audio_position"] = round(
                    float(absolute_event["observed_audio_position"]) + chunk.stream_start,
                    3,
                )
            absolute_event["chunk_index"] = chunk.index
            transcript_events.append(absolute_event)

    source_segments.sort(key=lambda item: (item["start"], item["end"]))
    translated_segments.sort(key=lambda item: (item["start"], item["end"]))
    return RealtimeTranslationResult(
        source_segments=source_segments,
        translated_segments=translated_segments,
        source_text=" ".join(item["text"] for item in source_segments if item["text"]).strip(),
        translated_text=" ".join(
            item["text"] for item in translated_segments if item["text"]
        ).strip(),
        duration=float(duration),
        target_language=target_code,
        model=model,
        transcript_events=transcript_events,
    )


def translate_audio(
    api_key,
    audio_file_path,
    target_language,
    progress_callback=print,
    model=REALTIME_TRANSLATE_MODEL,
    websocket_url=None,
    pacing_rate=DEFAULT_PACING_RATE,
    chunk_ms=DEFAULT_CHUNK_MS,
    connect_timeout=DEFAULT_CONNECT_TIMEOUT_SEC,
    close_timeout=DEFAULT_CLOSE_TIMEOUT_SEC,
    checkpoint_path=None,
    segment_duration_sec=DEFAULT_SEGMENT_DURATION_SEC,
    segment_overlap_sec=DEFAULT_SEGMENT_OVERLAP_SEC,
    max_retries=DEFAULT_SEGMENT_MAX_RETRIES,
    retry_backoff_sec=2.0,
):
    """Translate audio, optionally using durable per-session checkpoints."""
    if checkpoint_path is None:
        return _translate_audio_session(
            api_key=api_key,
            audio_file_path=audio_file_path,
            target_language=target_language,
            progress_callback=progress_callback,
            model=model,
            websocket_url=websocket_url,
            pacing_rate=pacing_rate,
            chunk_ms=chunk_ms,
            connect_timeout=connect_timeout,
            close_timeout=close_timeout,
        )
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative.")

    target_code = resolve_target_language_code(target_language)
    duration = _probe_audio_duration(audio_file_path)
    chunks = _build_chunks(duration, segment_duration_sec, segment_overlap_sec)
    identity = _checkpoint_identity(
        audio_file_path,
        target_code,
        model,
        duration,
        segment_duration_sec,
        segment_overlap_sec,
    )
    checkpoint = _load_checkpoint(checkpoint_path, identity, chunks, progress_callback)
    _atomic_write_json(checkpoint_path, checkpoint)

    for chunk, item in zip(chunks, checkpoint["chunks"]):
        if item.get("status") == "complete" and item.get("result"):
            progress_callback(
                f"Realtime chunk {chunk.index + 1}/{len(chunks)} already complete; skipping."
            )
            continue

        total_attempts = max_retries + 1
        for attempt in range(1, total_attempts + 1):
            progress_callback(
                f"Realtime chunk {chunk.index + 1}/{len(chunks)}: "
                f"{chunk.stream_start:.1f}s-{chunk.end:.1f}s "
                f"(attempt {attempt}/{total_attempts})."
            )
            try:
                result = _translate_audio_session(
                    api_key=api_key,
                    audio_file_path=audio_file_path,
                    target_language=target_code,
                    progress_callback=progress_callback,
                    model=model,
                    websocket_url=websocket_url,
                    pacing_rate=pacing_rate,
                    chunk_ms=chunk_ms,
                    connect_timeout=connect_timeout,
                    close_timeout=close_timeout,
                    start_sec=chunk.stream_start,
                    duration_sec=chunk.stream_duration,
                )
                item["status"] = "complete"
                item["result"] = result.to_metadata()
                item.pop("last_error", None)
                _atomic_write_json(checkpoint_path, checkpoint)
                progress_callback(
                    f"Realtime chunk {chunk.index + 1}/{len(chunks)} checkpoint saved."
                )
                break
            except Exception as exc:
                item["status"] = "pending"
                item["last_error"] = f"{type(exc).__name__}: {exc}"
                _atomic_write_json(checkpoint_path, checkpoint)
                if isinstance(exc, RealtimeAPIError) and not exc.retryable:
                    raise RuntimeError(
                        f"Realtime chunk {chunk.index + 1}/{len(chunks)} failed with a "
                        "non-retryable API error. Resume will restart this chunk after "
                        "the configuration is corrected."
                    ) from exc
                if attempt >= total_attempts:
                    raise RuntimeError(
                        f"Realtime chunk {chunk.index + 1}/{len(chunks)} failed after "
                        f"{total_attempts} attempt(s). Resume will restart this chunk."
                    ) from exc
                delay = retry_backoff_sec * (2 ** (attempt - 1))
                progress_callback(
                    f"Realtime chunk failed: {exc}. Retrying in {delay:.1f}s..."
                )
                if delay > 0:
                    time.sleep(delay)

    checkpoint["complete"] = True
    _atomic_write_json(checkpoint_path, checkpoint)
    progress_callback(f"Realtime checkpoint complete: {checkpoint_path}")
    return _combine_checkpoint_results(checkpoint, chunks, duration, target_code, model)
