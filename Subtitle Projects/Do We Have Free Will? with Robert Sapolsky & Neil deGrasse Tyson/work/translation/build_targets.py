from pathlib import Path
import json
from codex_subtitles.translation_service import translation_status
p=Path(__file__).resolve().parent
j=Path((p.parent/'job-path.txt').read_text())
outputs={}; owners={}
for f in sorted((j/'windows').glob('*.source.json')):
 d=json.loads(f.read_text()); w=d['window_id']
 outputs[w]=[]
 for cid in d['core_ids']: owners[cid]=w
for f in sorted(p.glob('*.txt')):
 for line in f.read_text().splitlines():
  if not line.strip(): continue
  ids,text=line.split('|',1); bounds=list(map(int,ids.split('-')))
  a,b=bounds[0],bounds[-1]; ids=[f'c{i:06d}' for i in range(a,b+1)]
  w=owners[ids[0]]
  assert all(owners[cid]==w for cid in ids),(f.name,a,b,'crosses window')
  outputs[w].append({'source_ids':ids,'text':text.replace('\\n','\n')})
for w,cues in outputs.items():
 if not cues: continue
 t=j/'windows'/f'{w}.target.json';d=json.loads(t.read_text());d['cues']=cues
 t.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(translation_status(j),ensure_ascii=False,indent=2))
