"""Documented MLX Whisper word-timestamp adapter; local files only."""
import pathlib,json,time,os
# All model/tokenizer caches belong to this workspace, no credentials are read.
root=pathlib.Path.cwd();os.environ.setdefault('TIKTOKEN_CACHE_DIR',str(root/'.cache/subtitle_models/tokenizers'))
import mlx_whisper
p=pathlib.Path(__file__).resolve().parent
model=root/'.cache/subtitle_models/whisper-large-v3-turbo'
manifest=json.loads((p/'audio-probes.json').read_text())
for clip in manifest['clips']:
 f=pathlib.Path(clip['path']);out=f.with_suffix('.asr.json')
 if out.exists():print('REUSE',out.name,flush=True);continue
 print('START',f.name,flush=True);t=time.monotonic()
 result=mlx_whisper.transcribe(str(f),path_or_hf_repo=str(model),language='en',task='transcribe',word_timestamps=True,temperature=0.0,condition_on_previous_text=False,verbose=None)
 result['provenance']={'audio_path':str(f),'audio_sha256':clip['sha256'],'source_start_sec':clip['source_start_sec'],'source_end_sec':clip['source_end_sec'],'model_path':str(model),'runtime':'mlx-whisper','word_timestamps':True,'time_basis':'clip-relative; add source_start_sec for YouTube timeline','wall_seconds':time.monotonic()-t}
 out.write_text(json.dumps(result,ensure_ascii=False,indent=2));print('DONE',f.name,round(time.monotonic()-t,1),'seconds',len(result['segments']),'segments',flush=True)
