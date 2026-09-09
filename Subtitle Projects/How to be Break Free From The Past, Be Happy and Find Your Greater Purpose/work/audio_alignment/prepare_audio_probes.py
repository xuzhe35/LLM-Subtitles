"""Prepare source-time audio probes; no ASR installation or API use."""
import pathlib,subprocess,json,hashlib
p=pathlib.Path(__file__).resolve().parent
source=p/'source.youtube.webm'
if not source.exists(): raise SystemExit('Wait for source.youtube.webm download to complete.')
probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(source)]))
plan=json.loads((p/'alignment-plan.json').read_text());clips=[]
for start,end in plan['pilot_ranges']:
 clip=p/f'probe.{start:04d}-{end:04d}.wav'
 if not clip.exists():
  subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-ss',str(start),'-i',str(source),'-t',str(end-start),'-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(clip)],check=True)
 clips.append({'path':str(clip),'source_start_sec':start,'source_end_sec':end,'sha256':hashlib.sha256(clip.read_bytes()).hexdigest()})
(p/'audio-probes.json').write_text(json.dumps({'source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'ffprobe':probe,'clips':clips,'timestamp_policy':'Add source_start_sec to local clip ASR times; retain container start/codec-delay metadata; verify sound alignment with controls.'},ensure_ascii=False,indent=2))
print(json.dumps({'source_size':source.stat().st_size,'duration':probe['format'].get('duration'),'probes':len(clips)},indent=2))
