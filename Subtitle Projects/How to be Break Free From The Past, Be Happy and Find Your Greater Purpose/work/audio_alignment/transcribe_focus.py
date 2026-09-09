"""Local word-timestamp ASR for the reported interval, with overlap context."""
import pathlib,json,subprocess,os,time
root=pathlib.Path.cwd();os.environ.setdefault('TIKTOKEN_CACHE_DIR',str(root/'.cache/subtitle_models/tokenizers'))
import mlx_whisper
p=pathlib.Path(__file__).resolve().parent;model=root/'.cache/subtitle_models/whisper-large-v3-turbo'
for core_start in range(1140,3060,240):
 start=max(0,core_start-20);end=core_start+260;f=p/f'focus.{start:04d}-{end:04d}.wav';out=f.with_suffix('.asr.json')
 if out.exists():print('REUSE',out.name,flush=True);continue
 if not f.exists():subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-ss',str(start),'-i',str(p/'source.youtube.webm'),'-t',str(end-start),'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(f)],check=True)
 print('START',f.name,flush=True);t=time.monotonic()
 d=mlx_whisper.transcribe(str(f),path_or_hf_repo=str(model),language='en',task='transcribe',word_timestamps=True,temperature=0.0,condition_on_previous_text=False,verbose=None)
 d['provenance']={'audio_path':str(f),'source_start_sec':start,'source_end_sec':end,'core_start_sec':core_start,'core_end_sec':core_start+240,'time_basis':'clip-relative','model_path':str(model),'runtime':'mlx-whisper 0.4.3','word_timestamps':True,'wall_seconds':time.monotonic()-t}
 out.write_text(json.dumps(d,ensure_ascii=False,indent=2));print('DONE',out.name,round(time.monotonic()-t,1),'seconds',flush=True)
