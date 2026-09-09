from pathlib import Path
import json, re, hashlib
from codex_subtitles.translation_service import materialize_translation, translation_status
from codex_subtitles.storage import atomic_write_json
from utils.subtitle_formatter import wrap_subtitle_text, parse_srt
p=Path("Subtitle Projects/Anthropic's Chloe Lubinski explains how AI works (in 14 minutes)").resolve()
j=next((p/'work/codex_native').glob('Chloe Lubinski*/job.json')).parent
s=json.loads((j/'source.json').read_text())['segments'];by={c['id']:c for c in s};ts=materialize_translation(j); d=p/'final';d.mkdir(exist_ok=True)
def stamp(x):
 ms=round(x*1000);h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);ss,ms=divmod(ms,1000);return f'{h:02}:{m:02}:{ss:02},{ms:03}'
def zhwrap(t):
 if len(t)<=32:return t
 # Prefer a clause boundary and avoid a one-character final line.
 lo=max(1,len(t)-32); hi=32
 punct=[i+1 for i,ch in enumerate(t) if ch in '，。！？；：' and lo<=i+1<=hi and 8<=i+1<=len(t)-8]
 spaces=[m.start() for m in re.finditer(' ',t) if lo<=m.start()<=hi and 8<=m.start()<=len(t)-8]
 choices=punct or spaces
 if choices:
  k=min(choices,key=lambda x:abs(x-len(t)/2));return t[:k].rstrip()+'\n'+t[k:].lstrip()
 return wrap_subtitle_text(t,32)
def enwrap(t):
 if len(t)<=76:return t
 # Two balanced lines, splitting only at existing word boundaries.
 candidates=[m.start() for m in re.finditer(' ',t) if max(1,len(t)-76)<=m.start()<=76]
 if candidates:
  mid=min(candidates,key=lambda k:abs(k-len(t)/2));return t[:mid]+'\n'+t[mid+1:]
 return wrap_subtitle_text(t,max_line_chars=76)
def save(path,segs,textfn):
 path.write_text(''.join(f"{i+1}\n{stamp(c['start'])} --> {stamp(c['end'])}\n{textfn(c)}\n\n" for i,c in enumerate(segs)),encoding='utf-8')
zh=d/'Chloe_Lubinski.youtube.zh-Hans.srt';bi=d/'Chloe_Lubinski.youtube.zh-Hans-en.srt';en=d/'Chloe_Lubinski.youtube.en.reviewed.srt'
save(zh,ts,lambda c:zhwrap(c['text']))
save(bi,ts,lambda c:zhwrap(c['text'])+'\n'+enwrap(' '.join(by[x]['text'] for x in c['source_ids'])))
save(en,s,lambda c:enwrap(c['text']))
# Also expose the selected working source with exact integer-millisecond serialization.
save(p/'work/source.fused.youtube.en.srt',s,lambda c:c['text'])
source_ids=[x for c in ts for x in c['source_ids']]
assert source_ids==[c['id'] for c in s]
assert len(source_ids)==len(set(source_ids))==387
orig=json.loads((p/'work/source.youtube.en.json').read_text())['segments']
assert all(a['start']==b['start'] and a['end']==b['end'] for a,b in zip(orig,s))
raw=p/'work/evidence/youtube.auto.en-orig.vtt';dr=json.loads((p/'work/evidence/deroll-review.json').read_text());assert hashlib.sha256(raw.read_bytes()).hexdigest()==dr['raw_sha256']
assert (p/'URL.md').read_text()=='https://www.youtube.com/watch?v=aBUniZHgCnE'
qa=[]
for f in (zh,bi):
 parsed=parse_srt(str(f));assert len(parsed)==len(ts)
 for a,b in zip(parsed,ts):
  assert round(a['start']*1000)==round(b['start']*1000)
  assert round(a['end']*1000)==round(b['end']*1000)
  assert a['end']>a['start']
 assert all(a['end']<=b['start'] for a,b in zip(parsed,parsed[1:]))
 qa.append({'file':f.name,'cue_count':len(parsed),'max_lines':max(len(c['text'].splitlines()) for c in parsed),'sha256':hashlib.sha256(f.read_bytes()).hexdigest()})
report={'timeline_target':'youtube-source','source_url':'https://www.youtube.com/watch?v=aBUniZHgCnE','youtube_video_duration_seconds':874,'target_video_file':None,'target_video_sha256':None,'reason_no_video_checksum':'Project contains only URL.md; no local media, and source video was not downloaded. Native YouTube caption timestamps are the timing authority.','source_cue_count':len(s),'output_cue_count':len(ts),'dropped_source_cues':0,'dropped_source_groups':0,'merged_max_source_cues':max(len(c['source_ids']) for c in ts),'merged_max_duration_seconds':round(max(c['end']-c['start'] for c in ts),3),'max_target_characters_per_second':round(max(len(re.sub(r'\s','',c['text']))/(c['end']-c['start']) for c in ts),2),'coverage_exactly_once':True,'order_valid':True,'all_timings_youtube_source_exact_millisecond':True,'overlap_count':0,'source_timings_unchanged':True,'original_url_unchanged':True,'raw_caption_checksum_verified':True,'raw_caption_sha256':dr['raw_sha256'],'translation_validation':translation_status(j),'files':qa,'local_asr_ran':False,'hard_subtitle_ocr_ran':False,'paid_api_used':False,'source_text_corrections':len(json.loads((p/'work/evidence-review.json').read_text())['changed_cues']),'range_seconds':[ts[0]['start'],ts[-1]['end']],'reviewed_sections':['Whole English/Chinese text comparison','beginning 0:00–1:08','recursive self-improvement 2:10–2:15','numbers 2:23–2:42 and 6:34–7:03','functional emotions and non-human qualifications 6:12–6:31 and 10:16–10:29','reward hacking and hypothesis qualification 7:16–9:35','faith account 9:43–10:16','Chris Olah/Vatican 10:53–11:42','chart colors and occupations 11:47–12:49','Joanna Macy/Great Turning and ending 13:07–14:16'],'review_scope':'Text and source caption timecode verification; no independent audio listening or frame alignment performed.'}
atomic_write_json(d/'quality-report.json',report)
ledger={'initial_project_files':[{'path':'URL.md','kind':'user_supplied_source_locator','language':'URL','time_basis':'none','sha256':hashlib.sha256((p/'URL.md').read_bytes()).hexdigest(),'used':True}],'missing_evidence':['local_video','local_audio','existing_local_asr','ocr_subtitles','ocr_quality_or_alignment_reports','references_directory','author_notes_or_reference_documents'],'acquired_evidence':[{'path':'work/evidence/youtube.auto.en-orig.vtt','kind':'youtube_automatic_caption','language':'en','time_basis':'youtube-source','machine_generated':True,'used':True,'sha256':dr['raw_sha256']},{'path':'work/evidence/youtube-metadata.json','kind':'publisher_video_metadata','language':'en','time_basis':'description chapter timestamps are context only','machine_generated':False,'used':True}],'external_references':[{'url':'https://www.anthropic.com/news/chris-olah-pope-leo-encyclical','kind':'official_primary_statement','used_for':['Chris Olah name','Pope Leo XIV identity','quoted incentive-and-constraint context'],'time_basis':'none','project_supplied':False},{'url':'https://www.ecoliteracy.org/article/great-turning','kind':'Joanna Macy authored essay','used_for':['Great Turning meaning and attribution'],'time_basis':'none','project_supplied':False}],'timeline_target':'youtube-source','offset_applied_seconds':0,'asr':'not run; no existing local evidence and doctor reports no installed backend','ocr':'not run, per explicit user instruction','paid_api':'not used','acquisition_notes':['prepare attempted auto-translated zh-Hans track and received HTTP 429; no Chinese track content acquired or used.','Acquired original English automatic en-orig track explicitly using repository download_caption service.'],'delivery_files':[str(zh),str(bi),str(en)]}
atomic_write_json(p/'work/evidence-ledger.json',ledger)
print(json.dumps(report,ensure_ascii=False,indent=2))
