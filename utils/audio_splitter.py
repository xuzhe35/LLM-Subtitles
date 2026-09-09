import os
import subprocess
import math
import re
import shutil
import time


_SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.]+)")

def get_audio_duration(file_path):
    """
    Get the duration of the audio file in seconds using ffprobe.
    """
    cmd = [
        'ffprobe', 
        '-v', 'error', 
        '-show_entries', 'format=duration', 
        '-of', 'default=noprint_wrappers=1:nokey=1', 
        file_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"Error getting duration: {e}")
        return None


def find_silence_boundaries(file_path, *, noise_db=-35, min_silence_sec=0.35):
    """Return midpoints of detected silence gaps for safer chunk cuts.

    Detection is best-effort. If ffmpeg is unavailable or cannot inspect the
    input, callers receive an empty list and can retain overlap-based boundary
    recovery instead of failing the transcription.
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        file_path,
        "-vn",
        "-af",
        f"silencedetect=noise={float(noise_db):g}dB:d={float(min_silence_sec):g}",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except (OSError, ValueError):
        return []

    boundaries = []
    silence_start = None
    for line in (result.stderr or "").splitlines():
        start_match = _SILENCE_START_RE.search(line)
        if start_match:
            silence_start = float(start_match.group(1))
        end_match = _SILENCE_END_RE.search(line)
        if end_match and silence_start is not None:
            silence_end = float(end_match.group(1))
            if silence_end > silence_start:
                boundaries.append(round((silence_start + silence_end) / 2.0, 3))
            silence_start = None
    return boundaries

def split_audio(file_path, chunk_size_mb=24):
    """
    Splits the audio file into chunks smaller than chunk_size_mb.
    Returns a list of file paths for the chunks.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    split_started = time.perf_counter()
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    
    if file_size_mb <= chunk_size_mb:
        print(f"Audio split skipped: {file_path} is {file_size_mb:.2f}MB (<= {chunk_size_mb}MB).")
        return [file_path]

    duration = get_audio_duration(file_path)
    if not duration:
        raise Exception("Could not determine audio duration.")

    # Calculate number of chunks
    num_chunks = math.ceil(file_size_mb / chunk_size_mb)
    chunk_duration = duration / num_chunks
    
    # Safety margin: slightly reduce chunk duration to ensure file size is within limits
    # VBR (Variable Bit Rate) encoded files might vary, so being conservative is good.
    # However, strict specific size splitting is hard with ffmpeg without re-encoding to CBR.
    # We will try to rely on duration.
    
    # A safer approach for Whisper is just to split by time.
    # If the file is 50MB and 10 mins, split into 2 x 5 mins.
    # To be safe, let's aim for a target size of 20MB.
    
    target_size_mb = 20
    safe_num_chunks = math.ceil(file_size_mb / target_size_mb)
    safe_chunk_duration = duration / safe_num_chunks

    base_name, ext = os.path.splitext(file_path)
    chunk_files = []

    print(f"Splitting {file_path} ({file_size_mb:.2f}MB, {duration:.2f}s) into {safe_num_chunks} chunks of ~{safe_chunk_duration:.2f}s each.")

    for i in range(safe_num_chunks):
        chunk_started = time.perf_counter()
        start_time = i * safe_chunk_duration
        output_file = f"{base_name}_part{i}.m4a"
        
        # ffmpeg -i input.mp3 -ss 00:00:30 -t 00:00:10 -c copy output.m4a
        # allow re-encoding if copy fails or to ensure clean cuts? 
        # Whisper supports mp3, mp4, mpeg, mpga, m4a, wav, and webm.
        # -c copy is fast but might result in slightly inaccurate cuts or timestamp issues at boundaries.
        # Re-encoding is safer for timestamp accuracy.
        
        cmd = [
            'ffmpeg',
            '-y', # Overwrite
            '-i', file_path,
            '-ss', str(start_time),
            '-t', str(safe_chunk_duration),
            '-c', 'copy', # Try copy first
            output_file
        ]
        
        # If copy causes issues (e.g. starting with empty frames), we might need re-encoding
        # specific for whisper. But let's try copy first as it's fast. 
        # Actually, copy can cause issues with "start time" metadata not being 0.
        # Using -c:a libmp3lame -q:a 2 (if mp3) might be better but slower.
        
        # Let's stick to -c copy for speed, but verify.
        # Actually, for Whisper, we want the file to start at 0 timestamp internally, 
        # otherwise it might mess up "relative" timestamps.
        # ffmpeg -ss before -i ... is faster and resets timestamp.
        
        cmd = [
            'ffmpeg',
            '-y',
            '-ss', str(start_time),
            '-t', str(safe_chunk_duration),
            '-i', file_path,
            '-acodec', 'aac',
            output_file
        ]
        
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            chunk_files.append(output_file)
            print(f"  Split chunk {i+1}/{safe_num_chunks} finished in {time.perf_counter() - chunk_started:.1f}s: {output_file}")
        except subprocess.CalledProcessError as e:
            print(f"Error splitting chunk {i}: {e}")
            # If copy fails, maybe try re-encoding (implementation detail omitted for now)
            raise e

    print(f"Audio splitting finished in {time.perf_counter() - split_started:.1f}s.")
    return chunk_files
