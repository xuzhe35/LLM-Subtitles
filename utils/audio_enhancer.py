import os
import subprocess


MODE_OFF = "off"
MODE_MILD = "mild"
MODE_STRONG_FFMPEG = "strong_ffmpeg"

SUPPORTED_MODES = {
    MODE_OFF,
    MODE_MILD,
    MODE_STRONG_FFMPEG,
}

FFMPEG_FILTERS = {
    MODE_MILD: "highpass=f=80,lowpass=f=7600,afftdn=nf=-25,loudnorm=I=-23:LRA=7:TP=-2",
    MODE_STRONG_FFMPEG: "highpass=f=100,lowpass=f=6800,anlmdn=s=0.00003,afftdn=nf=-30,loudnorm=I=-23:LRA=7:TP=-2",
}


def normalize_mode(mode):
    normalized = (mode or MODE_OFF).strip().lower().replace("-", "_")
    if normalized == "strong":
        normalized = MODE_STRONG_FFMPEG
    if normalized not in SUPPORTED_MODES:
        raise ValueError(
            f"Unsupported audio enhancement mode: {mode!r}. "
            f"Supported modes: {', '.join(sorted(SUPPORTED_MODES))}."
        )
    return normalized


def is_enabled(mode):
    return normalize_mode(mode) != MODE_OFF


def enhanced_audio_path(input_path, mode):
    normalized_mode = normalize_mode(mode)
    if normalized_mode == MODE_OFF:
        return input_path

    base, _ext = os.path.splitext(input_path)
    return f"{base}.enhanced.{normalized_mode}.wav"


def build_ffmpeg_command(input_path, output_path, mode):
    normalized_mode = normalize_mode(mode)
    if normalized_mode == MODE_OFF:
        return None

    return [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-ac",
        "1",
        "-ar",
        "16000",
        "-af",
        FFMPEG_FILTERS[normalized_mode],
        output_path,
    ]


def _remove_partial_output(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def enhance_audio(input_path, output_path=None, mode=MODE_MILD, progress_callback=print,
                  overwrite=False, runner=subprocess.run):
    """
    Prepare audio for ASR with optional FFmpeg speech enhancement.

    Returns the path that should be sent to ASR. `mode="off"` returns the
    original input path without touching FFmpeg.
    """
    normalized_mode = normalize_mode(mode)

    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Audio file not found: {input_path}")

    if normalized_mode == MODE_OFF:
        return input_path

    output_path = output_path or enhanced_audio_path(input_path, normalized_mode)
    if os.path.abspath(output_path) == os.path.abspath(input_path):
        raise ValueError("Enhanced audio output_path must not overwrite the input audio.")

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0 and not overwrite:
        if progress_callback:
            progress_callback(f"Audio enhancement cache hit: {output_path}")
        return output_path

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    cmd = build_ffmpeg_command(input_path, output_path, normalized_mode)
    if progress_callback:
        progress_callback(f"Enhancing audio ({normalized_mode}) -> {output_path}")

    try:
        runner(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    except subprocess.CalledProcessError as e:
        _remove_partial_output(output_path)
        stderr = (e.stderr or "").strip()
        detail = f": {stderr[:500]}" if stderr else ""
        raise RuntimeError(f"FFmpeg audio enhancement failed{detail}") from e

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(f"FFmpeg audio enhancement produced no output: {output_path}")

    return output_path
