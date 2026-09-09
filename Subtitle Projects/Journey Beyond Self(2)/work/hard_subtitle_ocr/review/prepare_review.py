from pathlib import Path
import json,re
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parent.parent
JOB=next(p.parent for p in ROOT.glob('*/ocr-job.json') if json.loads(p.read_text())['status']=='complete')
quality=json.loads(next(JOB.glob('*.quality.json')).read_text())
blocks=re.split(r'\n\s*\n',Path(quality['srt']).read_text().strip())
texts={int(b.splitlines()[0]):' '.join(b.splitlines()[2:]) for b in blocks}
obs=json.loads((JOB/'artifacts/ocr.observations.json').read_text())
observations={o['observation_id']:o for r in obs['records'].values() for o in r['observations']}
frames=json.loads((JOB/'artifacts/frames.index.json').read_text())
images={f['frame_id']:f['image'] for f in frames['frames']}
selected=[]
for c in quality['cues']:
 if c['needs_visual_check'] or c['srt_index'] in [1,2,3,quality['cue_count']//2,quality['cue_count']-1,quality['cue_count']] or c['srt_index'] % 10 == 0 or len(texts[c['srt_index']]) < 10:
  rows=[observations[x] for x in c['observation_ids']]
  mid=rows[len(rows)//2]
  chosen=[mid]
  variants={mid['text']}
  for o in sorted(rows,key=lambda o:o['confidence']):
   if o['text'] not in variants:
    chosen.append(o);variants.add(o['text'])
   if len(chosen)>=3:break
  for o in chosen:
   selected.append({'srt_index':c['srt_index'],'start':c['start'],'end':c['end'],'issues':c['issues'],'text':texts[c['srt_index']],'observation_text':o['text'],'timestamp':o['timestamp'],'image':images[o['frame_id']]})
font=ImageFont.truetype('/System/Library/Fonts/Monaco.ttf',16)
for page in range((len(selected)+7)//8):
 rows=selected[page*8:(page+1)*8]
 sheet=Image.new('RGB',(1600,185*len(rows)),'white');d=ImageDraw.Draw(sheet)
 for i,r in enumerate(rows):
  y=i*185
  d.text((8,y+3),f"Cue {r['srt_index']} | {r['start']:.3f}-{r['end']:.3f} | frame {r['timestamp']:.3f} | {','.join(r['issues'])}",font=font,fill='black')
  d.text((8,y+27),'SRT: '+r['text'],font=font,fill='black')
  d.text((8,y+51),'OCR: '+r['observation_text'].replace('\n',' / '),font=font,fill='black')
  im=Image.open(JOB/'artifacts'/r['image']).convert('RGB');im.thumbnail((1600,102));sheet.paste(im,(0,y+78))
 sheet.save(ROOT/'review'/f'flagged-review-{page+1:02d}.jpg')
(ROOT/'review'/'review-index.json').write_text(json.dumps(selected,ensure_ascii=False,indent=2))
print(json.dumps({'cue_count':quality['cue_count'],'flagged_cues':quality['cues_needing_visual_check'],'review_images':len(selected),'pages':(len(selected)+7)//8}))
