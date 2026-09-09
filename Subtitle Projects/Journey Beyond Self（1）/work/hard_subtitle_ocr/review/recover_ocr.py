import json,os,shutil,sys
from pathlib import Path
from dataclasses import asdict
sys.path.insert(0,str(Path(__file__).resolve().parents[5]))
from codex_subtitles.ocr_backend import create_backend
from codex_subtitles.hard_subtitle_models import Observation,Region
from codex_subtitles.hard_subtitle_ocr_service import validate_ocr_artifact
from codex_subtitles.recorded_video_ocr import export_ocr_srt
from codex_subtitles.subtitle_timeline_service import reconstruct_timeline
from codex_subtitles.storage import atomic_write_json,stable_id,utc_now
import subprocess,struct
ROOT=Path(__file__).resolve().parent.parent
SOURCE=ROOT/'Journey Beyond Self.5554caa86d0b'
DEST=ROOT/'Journey Beyond Self.5554caa86d0b.recovered'
assert not DEST.exists(),'Recovery destination already exists; preserve it.'
manifest=json.loads((SOURCE/'ocr-job.json').read_text())
assert manifest['status'] in ('failed','complete'),manifest['status']
def copy_file(src,dst):
 if str(src).endswith('.png'):os.link(src,dst)
 else:shutil.copy2(src,dst)
 return dst
shutil.copytree(SOURCE,DEST,copy_function=copy_file)
evidence=DEST/'artifacts'
ocr=json.loads((evidence/'ocr.observations.json').read_text())
shutil.copy2(evidence/'ocr.observations.json',evidence/'ocr.observations.before-recovery.json')
index=json.loads((evidence/'frames.index.json').read_text())
frames={f['frame_id']:f for f in index['frames']}
backend=create_backend();recovered=[]
for frame_id,record in ocr['records'].items():
 if record['status']=='complete':continue
 frame=frames[frame_id];original=evidence/frame['image']
 width,height=struct.unpack('>II',original.read_bytes()[16:24]);pad=120
 image=evidence/'recovery'/f'{frame_id}.pad-vertical-120.png';image.parent.mkdir(exist_ok=True)
 subprocess.run(['ffmpeg','-v','error','-i',str(original),'-vf',f'pad=iw:ih+{pad*2}:0:{pad}:black','-frames:v','1','-n',str(image)],check=True)
 lines=backend.recognize(image,language='en')
 raw=[asdict(line) for line in lines]
 atomic_write_json(image.with_suffix('.vision.json'),{'source_frame':frame['image'],'preprocessing':'pad-vertical-120','padding_pixels':pad,'original_dimensions':[width,height],'padded_dimensions':[width,height+2*pad],'raw_vision_lines':raw,'original_failed_record':record})
 boxes=[];texts=[];weights=[]
 for line in lines:
  y1=max(0,line.box.y*(height+2*pad)-pad);y2=min(height,(line.box.y+line.box.height)*(height+2*pad)-pad)
  if y2<=y1:continue
  boxes.append(asdict(Region(line.box.x,y1/height,line.box.width,(y2-y1)/height)))
  texts.append(line.text);weights.append((line.confidence,len(line.text)))
 text='\n'.join(texts);confidence=sum(c*w for c,w in weights)/max(1,sum(w for c,w in weights))
 obs=Observation(frame_id+':pad-vertical-120',frame_id,frame['timestamp'],'en',text,confidence,backend.identity,'pad-vertical-120',boxes)
 ocr['records'][frame_id]={'status':'complete','observations':[obs.to_dict()]}
 recovered.append({'frame_id':frame_id,'timestamp':frame['timestamp'],'text':text,'padded_image':str(image.relative_to(evidence)),'raw_vision_json':str(image.with_suffix('.vision.json').relative_to(evidence))})
 print('Recovered',frame_id,repr(text),flush=True)
ocr['recovery']={'method':'Local Apple Vision on original crop with 120 black pixels added above and below; boxes mapped back to the original crop. No text supplied by ASR or a language model.','original_job_dir':str(SOURCE),'recovered_frames':recovered}
ocr['fingerprint']=stable_id(json.dumps({'original_fingerprint':ocr['fingerprint'],'recovery':ocr['recovery']},sort_keys=True),32)
ocr['status']='valid'
validate_ocr_artifact(ocr,index,evidence)
atomic_write_json(evidence/'ocr.observations.json',ocr)
timeline=reconstruct_timeline(index,ocr,evidence)
result=export_ocr_srt(timeline,index,ocr,evidence,DEST/'Journey Beyond Self.ocr.en.srt',language='en',time_offset=0)
manifest.update(result);manifest.update(job_dir=str(DEST),status='complete',updated_at=utc_now(),last_successful_artifact=result['srt'],recovery=ocr['recovery']);manifest.pop('error',None)
manifest['history'].append({'state':'complete_after_local_padding_recovery','time':utc_now(),'artifact':result['srt']})
atomic_write_json(DEST/'ocr-job.json',manifest)
print(json.dumps(result,ensure_ascii=False,indent=2))
