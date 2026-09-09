import json,pathlib,re,hashlib
from codex_subtitles.workflow_service import import_source_job,plan_job
p=pathlib.Path('Subtitle Projects/Quantum Science Expert  How To Erase Your Fears & Transform Your Future with Thomas Campbell'); w=p/'work'
events=json.loads((w/'youtube/gFka_huRM38.en-orig.json3').read_text())['events']; words=[]
for ei,e in enumerate(events):
 for si,s in enumerate(e.get('segs',[])):
  if s['utf8'].strip(): words.append({'text':s['utf8'],'ms':e['tStartMs']+s.get('tOffsetMs',0),'event':ei,'seg':si,'raw_end':e['tStartMs']+e.get('dDurationMs',0)})
assert all(a['ms']<=b['ms'] for a,b in zip(words,words[1:]))
segments=[]; mappings=[]; group=[]
def flush(next_ms):
 if not group:return
 text=''.join((' ' if j and x['event']!=group[j-1]['event'] else '')+x['text'] for j,x in enumerate(group)).strip(); start=group[0]['ms'];end=min(next_ms,group[-1]['raw_end'])
 if end<=start:end=start+1
 segments.append({'start':start/1000,'end':end/1000,'text':text});mappings.append({'id':f'c{len(segments):06}','event_segments':[[x['event'],x['seg']] for x in group]});group.clear()
for i,x in enumerate(words):
 if group and ('>>' in x['text'] or x['ms']-group[0]['ms']>=8000 or len(group)>=27):flush(x['ms'])
 group.append(x)
 nxt=words[i+1]['ms'] if i+1<len(words) else 5825480
 if re.search(r'[.!?]["\x27]?\s*$',x['text']) and len(group)>=5:flush(nxt)
flush(5825480)
(w/'source.youtube.segmented.json').write_text(json.dumps({'segments':segments},ensure_ascii=False,indent=2))
(w/'source-word-provenance.json').write_text(json.dumps(mappings,indent=2))
m=import_source_job(w/'source.youtube.segmented.json',source_language='en',target_language='Simplified Chinese',output_root=w/'translation',title='Erase Your Fears');job=pathlib.Path(m['job_dir']);plan_job(job,max_cues=100,max_duration_sec=1200)
(w/'active-job.txt').write_text(str(job.resolve()))
# Persist a full source digest for review; no language-generation service is called.
(w/'transcript.review.txt').write_text('\n'.join(f'{i+1}|{s["start"]:.3f}|{s["text"]}' for i,s in enumerate(segments)))
print('segments',len(segments),'words',len(words),'job',job)
