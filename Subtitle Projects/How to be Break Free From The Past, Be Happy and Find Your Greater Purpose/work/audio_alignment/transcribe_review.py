"""Fresh short windows verify the five largest proposed onset changes."""
import pathlib, json, subprocess, os, time, hashlib
root=pathlib.Path.cwd()
os.environ.setdefault('TIKTOKEN_CACHE_DIR',str(root/'.cache/subtitle_models/tokenizers'))
import mlx_whisper
p=pathlib.Path(__file__).resolve().parent
for start,end in [(1906,1942),(2068,2098),(2848,2878),(2964,2995)]:
    f=p/f'review.{start:04d}-{end:04d}.wav'; out=f.with_suffix('.asr.json')
    if out.exists(): continue
    if not f.exists():
        subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-ss',str(start),'-i',str(p/'source.youtube.webm'),'-t',str(end-start),'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(f)],check=True)
    t=time.monotonic()
    d=mlx_whisper.transcribe(str(f),path_or_hf_repo=str(root/'.cache/subtitle_models/whisper-large-v3-turbo'),language='en',task='transcribe',word_timestamps=True,temperature=0.0,condition_on_previous_text=False,verbose=None)
    d['provenance']={'audio_path':str(f),'audio_sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'source_start_sec':start,'source_end_sec':end,'time_basis':'clip-relative','runtime':'mlx-whisper 0.4.3','word_timestamps':True,'wall_seconds':time.monotonic()-t}
    out.write_text(json.dumps(d,ensure_ascii=False,indent=2))
    print('DONE',out.name,round(time.monotonic()-t,1),'seconds',flush=True)
