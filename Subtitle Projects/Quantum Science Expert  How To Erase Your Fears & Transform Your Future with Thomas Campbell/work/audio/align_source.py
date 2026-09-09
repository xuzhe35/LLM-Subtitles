import json,statistics
from pathlib import Path
from acoustic_helpers import acoustic_start
p=Path(__file__).resolve().parent;w=p.parent
matches=json.loads((p/'word-matches.json').read_text())
for m in matches:
 m['acoustic']=acoustic_start(m);m['refined_lag']=round(m['start']-m['acoustic']['onset'],3)
good=[m for m in matches if m['probability']>=.8 and .04<=m['duration'] and m['acoustic']['valid'] and abs(m['refined_lag'])<=.6]
bykey={m['key']:m for m in matches};source=json.loads((w/'source.youtube.segmented.json').read_text())['segments'];provs=json.loads((w/'source-word-provenance.json').read_text());rows=[]
for i,s in enumerate(source):s['id']=f'c{i+1:06d}'
for s,v in zip(source,provs):
 key=':'.join(map(str,v['event_segments'][0]))+':0';m=bykey.get(key)
 near=sorted([m for m in good if abs(m['start']-s['start'])<8],key=lambda m:abs(m['start']-s['start']))[:12]
 local=round(statistics.median(m['refined_lag'] for m in near),3) if len(near)>=3 else 0
 if m and m['probability']>=.8 and m['acoustic']['valid'] and -.35<=m['refined_lag']<=3:
  start=m['acoustic']['onset'];method='matched_first_word_acoustic_pause_trim';evidence=m
 elif len(near)>=3:
  start=max(0,round(s['start']-local,3));method='nearby_word_median_estimate';evidence=near
 else:start=s['start'];method='original_timestamp_insufficient_audio_anchor';evidence=[]
 rows.append(dict(id=s['id'],old_start=s['start'],old_end=s['end'],start=start,advance_sec=round(s['start']-start,3),method=method,evidence=evidence,text=s['text']))
for i,r in enumerate(rows):
 r['end']=rows[i+1]['start'] if i+1<len(rows) else 5825.48
 assert r['start']<r['end'],r['id']
(p/'alignment.proposed.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
print('methods',{x:sum(r['method']==x for r in rows) for x in set(r['method'] for r in rows)})
for r in rows:
 if abs(r['advance_sec'])>.6:print(r['id'],r['old_start'],r['start'],r['advance_sec'],r['text'][:100])
print('Median advance',statistics.median(r['advance_sec'] for r in rows))
