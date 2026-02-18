import subprocess
import re
import os

def detect_speech_segments(audio_path, min_silence_len=1000, silence_thresh=-40, padding=200):
    """
    Detects non-silent segments in an audio file using ffmpeg's silencedetect filter.
    
    Args:
        audio_path (str): Path to audio file
        min_silence_len (int): Minimum length of silence to be considered a split (ms).
                               Converted to seconds for ffmpeg (e.g. 1000 -> 1).
        silence_thresh (int): Silence threshold in dB (e.g. -40).
        padding (int): Padding to add to speech segments in ms (to avoid clipping words).
        
    Returns:
        list of tuples: [(start_ms, end_ms), ...]
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
    print(f"Detecting speech using ffmpeg: {audio_path}")
    
    # ffmpeg arguments
    # noise: threshold (e.g. -40dB)
    # d: duration in seconds (min_silence_len / 1000)
    duration_sec = min_silence_len / 1000.0
    
    cmd = [
        'ffmpeg',
        '-i', audio_path,
        '-af', f'silencedetect=noise={silence_thresh}dB:d={duration_sec}',
        '-f', 'null',
        '-' 
    ]
    
    try:
        # stderr contains the silencedetect output
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except Exception as e:
        print(f"Error running ffmpeg for vad: {e}")
        return []
        
    output = result.stderr
    
    # Parse output
    # [silencedetect @ ...] silence_start: 24.532
    # [silencedetect @ ...] silence_end: 28.102 | silence_duration: 3.57
    
    silence_starts = []
    silence_ends = []
    
    for line in output.splitlines():
        if "silence_start:" in line:
            match = re.search(r"silence_start:\s*([0-9\.]+)", line)
            if match:
                silence_starts.append(float(match.group(1)))
        elif "silence_end:" in line:
            match = re.search(r"silence_end:\s*([0-9\.]+)", line)
            if match:
                silence_ends.append(float(match.group(1)))
                
    # Get total duration to find the last segment
    # ffmpeg output usually contains "Duration: 00:00:00.00"
    duration = 0.0
    dur_match = re.search(r"Duration:\s*(\d{2}):(\d{2}):(\d{2}\.\d+)", output)
    if dur_match:
        h, m, s = dur_match.groups()
        duration = float(h)*3600 + float(m)*60 + float(s)
    
    # Construct speech segments (intervals between silences)
    speech_segments = []
    
    # Time pointers
    current_time = 0.0
    
    # 3 cases for silence/speech interleaving:
    # 1. Starts with speech (silence_start[0] > 0)
    # 2. Starts with silence (silence_start[0] approx 0)
    
    # We'll iterate through silences and fill gaps.
    # Note: mismatched len(starts) and len(ends) shouldn't happen usually, 
    # but if silence continues until end of file, we might miss silence_end?
    # ffmpeg documentation says silence_end is printed when silence ends OR end of stream.
    
    combined_silences = sorted(list(zip(silence_starts, silence_ends)), key=lambda x: x[0])
    
    # If file starts with silence (start ~ 0), we skip it.
    if combined_silences and combined_silences[0][0] < 0.1:
         current_time = combined_silences[0][1] # Start output after first silence
         combined_silences.pop(0)
         
    for s_start, s_end in combined_silences:
        if s_start > current_time:
            # Found a speech segment
            speech_segments.append((current_time, s_start))
        current_time = s_end
        
    # Check for tail speech
    if duration > current_time:
         speech_segments.append((current_time, duration))
         
    # Convert to ms and integer
    final_segments = []
    for start, end in speech_segments:
        s_ms = int(start * 1000)
        e_ms = int(end * 1000)
        
        # Apply padding if requested, but clamp to boundaries?
        # Since we are just extracting, adding padding is good for VAD softness
        # But we must be careful not to overlap? 
        # For simplicity, let's just use exact or small padding
        s_ms = max(0, s_ms - padding)
        e_ms = min(int(duration*1000), e_ms + padding)
        
        # Ensure valid
        if e_ms > s_ms:
            final_segments.append((s_ms, e_ms))
            
    print(f"Found {len(final_segments)} speech segments.")
    return final_segments
