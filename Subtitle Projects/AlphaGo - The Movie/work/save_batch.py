import json,sys
from pathlib import Path
from codex_subtitles.translation_service import validate_window
p=next((Path(__file__).parent/'codex_native').glob('*'))
rows={}
for line in sys.stdin.read().splitlines():
 if not line.strip(): continue
 n,t=line.split('|',1); rows[f'c{int(n):06d}']=t.replace('\\n','\n')
entries=json.loads((p/'windows/index.json').read_text())['windows']
for e in entries:
 ids=e['core_ids']
 if not any(i in rows for i in ids):continue
 f=p/'windows'/e['target']; t=json.loads(f.read_text()); old={x['source_ids'][0]:x['text'] for x in t['cues']}; old.update({i:rows[i] for i in ids if i in rows}); t['cues']=[{'source_ids':[i],'text':old[i]} for i in ids if i in old]; f.write_text(json.dumps(t,ensure_ascii=False,indent=2)+'\n')
 if all(i in old for i in ids): validate_window(p,e); print('validated',e['window_id'],len(ids))
 else: print('saved partial',e['window_id'],len(old),'/',len(ids))
