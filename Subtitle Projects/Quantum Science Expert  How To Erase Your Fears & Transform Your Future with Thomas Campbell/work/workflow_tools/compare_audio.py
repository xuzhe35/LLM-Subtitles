import json,re,difflib,statistics,sys
from pathlib import Path
p=Path(__file__).resolve().parents[1];raw=json.loads((p/'youtube/gFka_huRM38.en-orig.json3').read_text());yt=[]
norm=lambda t:re.findall(r"[a-z]+(?:'[a-z]+)?|[0-9]+",t.lower().replace('’',"'"))
provs=json.loads((p/'source-word-provenance.json').read_text());owner={f'{e}:{s}':x['id'] for x in provs for e,s in x['event_segments']}
for ei,e in enumerate(raw['events']):
 for si,s in enumerate(e.get('segs',[])):
  for ti,t in enumerate(norm(s['utf8'])):yt.append(dict(word=t,start=(e['tStartMs']+s.get('tOffsetMs',0))/1000,key=f'{ei}:{si}:{ti}',source_id=owner.get(f'{ei}:{si}')))
matches=[];diffs=[];stats=[]
filler=set("uh um ah oh you know i and so well the a it that is to of but yeah yes okay right in then this no be it's there just or not as an for my we at".split())
review_mode='--review' in sys.argv
for f in sorted((p/'audio').glob(('review' if review_mode else 'focus')+'.*.asr.json')):
 d=json.loads(f.read_text());v=d['provenance'];base,end=v['source_start_sec'],v['source_end_sec'];v.setdefault('core_start_sec',base);v.setdefault('core_end_sec',end);a=[m for m in yt if base-5<=m['start']<end+5];b=[]
 for s in d['segments']:
  for w in s.get('words',[]):
   for t in norm(w['word']):b.append(dict(word=t,start=base+w['start'],end=base+w['end'],probability=w['probability']))
 sm=difflib.SequenceMatcher(None,[x['word'] for x in a],[x['word'] for x in b],autojunk=False)
 local=[]
 for block in sm.get_matching_blocks():
  if block.size<5:continue
  for k in range(block.size):
   x,y=a[block.a+k],b[block.b+k]
   if not v['core_start_sec']<=x['start']<v['core_end_sec']:continue
   if abs(x['start']-y['start'])>5:continue
   m=dict(**x,audio_start=y['start'],audio_end=y['end'],lag=round(x['start']-y['start'],3),duration=round(y['end']-y['start'],3),probability=y['probability'],block_length=block.size,file=f.name)
   matches.append(m)
   if m['probability']>=.65 and .03<=m['duration']<=.6:local.append(m['lag'])
 for tag,ai,aj,bi,bj in sm.get_opcodes():
  if tag=='equal' or ai>=len(a):continue
  t=a[ai]['start']
  if not v['core_start_sec']<=t<v['core_end_sec']:continue
  aa=' '.join(x['word'] for x in a[ai:aj]);bb=' '.join(x['word'] for x in b[bi:bj])
  if set(aa.split()+bb.split())<=filler:continue
  diffs.append(dict(source_id=a[ai]['source_id'],time=t,youtube=aa,asr=bb,file=f.name,context=' '.join(x['word'] for x in a[max(0,ai-4):min(len(a),aj+4)])))
 stats.append(dict(range_sec=[v['core_start_sec'],v['core_end_sec']],anchors=len(local),median_lag_sec=round(statistics.median(local),3) if local else None))
suffix='.review' if review_mode else ''
(p/f'audio/word-matches{suffix}.json').write_text(json.dumps(matches,ensure_ascii=False,indent=2))
(p/f'audio/text-disagreements{suffix}.json').write_text(json.dumps(diffs,ensure_ascii=False,indent=2))
(p/f'audio/timing-comparison{suffix}.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2))
for x in diffs:print(f"{x['source_id']}|{x['time']:.2f}|YT:{x['youtube']}|ASR:{x['asr']}|{x['context']}")
print('MATCHES',len(matches),'DIFFERENCES',len(diffs),'TIMING',stats)
