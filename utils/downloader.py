import yt_dlp
import os

def get_video_info(url):
    """
    Retrieves video metadata and available subtitles.
    """
    ydl_opts = {
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            return info
        except Exception as e:
            print(f"Error extracting video info: {e}")
            return None

def download_manual_subtitle(url, lang_code, output_path):
    """
    Downloads the manual subtitle for the given language code.
    """
    ydl_opts = {
        'skip_download': True,
        'writesubtitles': True,
        'subtitleslangs': [lang_code],
        'outtmpl': output_path,
        'quiet': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            ydl.download([url])
            # yt-dlp appends the lang code to the filename, e.g., filename.en.vtt
            # We might need to rename it or return the actual filename
            expected_filename = f"{output_path}.{lang_code}.vtt" # Default usually vtt
            return expected_filename
        except Exception as e:
            print(f"Error downloading subtitle: {e}")
            return None

def download_audio(url, output_path):
    """
    Downloads audio from the video, converting to MP3.
    """
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': output_path, # .mp3 will be appended by postprocessor
        'quiet': True,
    }
    
    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            ydl.download([url])
            return f"{output_path}.mp3"
        except Exception as e:
            print(f"Error downloading audio: {e}")
            return None
