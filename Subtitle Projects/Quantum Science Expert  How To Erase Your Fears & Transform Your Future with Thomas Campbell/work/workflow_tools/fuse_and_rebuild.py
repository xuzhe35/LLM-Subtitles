import json,re,shutil,hashlib
from pathlib import Path
from codex_subtitles import SCHEMA_VERSION
from codex_subtitles.storage import create_job_dir,new_manifest,atomic_write_json,update_manifest
from codex_subtitles.workflow_service import plan_job
from codex_subtitles.translation_service import translation_status

w=Path(__file__).resolve().parents[1];project=w.parent
oldjob=Path((w/'active-job.txt').read_text().strip())
if (w/'initial-job.txt').exists():oldjob=Path((w/'initial-job.txt').read_text().strip())
else:(w/'initial-job.txt').write_text(str(oldjob))
source=json.loads((w/'source.youtube.segmented.json').read_text())['segments']
assert len(source)==1314
raw=json.loads((w/'youtube/gFka_huRM38.en-orig.json3').read_text())
provs=json.loads((w/'source-word-provenance.json').read_text())
for i,(s,v) in enumerate(zip(source,provs)):
 s.update(id=f'c{i+1:06d}',index=i)
 words=[raw['events'][e]['segs'][q]['utf8'] for e,q in v['event_segments']]
 assert re.sub(r'\s','',s['text'])==re.sub(r'\s','',''.join(words)),s['id']
 assert s['id']==v['id']
rows=json.loads((w/'audio/alignment.proposed.json').read_text())
large=json.loads((w/'audio/large-adjustment-review.json').read_text());largeby={r['id']:r for r in large}
rejected=[]
for r in rows:
 if abs(r['advance_sec'])>.6:
  review=largeby[r['id']]
  if review['repeat_residual_sec'] is None or not review['repeat_onset']['valid'] or abs(review['repeat_residual_sec'])>.2:
   rejected.append({'id':r['id'],'proposed_advance':r['advance_sec'],'reason':'Large first-word change not reproduced in separate short window; retain nearby-word estimate'})
   ev=r['evidence'];allm=json.loads((w/'audio/word-matches.json').read_text())
   near=sorted([m for m in allm if m['probability']>=.8 and .04<=m['duration']<=.5 and abs(m['lag'])<=.5 and abs(m['start']-r['old_start'])<8],key=lambda m:abs(m['start']-r['old_start']))[:12]
   import statistics
   shift=round(statistics.median(m['lag'] for m in near),3) if near else 0
   r.update(start=round(r['old_start']-shift,3),advance_sec=shift,method='nearby_word_estimate_after_rejected_large_shift',rejected_first_word_evidence=ev,evidence=near)
for i,r in enumerate(rows):r['end']=rows[i+1]['start'] if i+1<len(rows) else 5825.041
(w/'audio/alignment.accepted.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
changes=[]
def fix(n,old,new,reason='Local ASR plus unambiguous context; English spelling/wording repair'):
 s=source[n-1];before=s['text'];assert old in before,(n,old,before)
 s['text']=before.replace(old,new).replace('T.O.E..','T.O.E.')
 changes.append(dict(source_id=s['id'],start=s['start'],original_text=before,revised_text=s['text'],reason=reason,evidence=['work/youtube/gFka_huRM38.en-orig.json3','work/audio/focus.*.asr.json']))
for n,old,new in [
 (9,'extrensory','extrasensory'),(16,'meet you too','meet you two'),(20,'My Big toe','My Big T.O.E.'),
 (35,'Liry','Leary'),(96,'shaped','shaken'),(170,'crosswives','crosswise'),
 (173,'My Alex breakdown','Mayim Bialik’s Breakdown'),(178,'gamecher','game changer'),(186,'distraction-f free','distraction-free'),
 (215,'Fi find','Find'),(226,'What your UB you call it?','Your OOBE, you call it?'),(227,'Ubby','OOBE'),
 (235,'say here','say, hear'),(274,'bass frequencies','base frequencies'),(283,'oral','aural'),
 (284,'colossum','callosum'),(295,'44','4/4'),(296,'colossum','callosum'),(308,'ent train','entrain'),
 (335,"there's data","there's delta"),(342,'beads','beats'),(399,'toad stool','toadstool'),
 (443,"mime's","Mayim’s"),(445,'whereas the tangible reality','what is the tangible reality'),(458,"they're don't","they don't"),
 (466,'conscious consciousness connection','consciousness-to-consciousness connection'),(470,'conscious person','consciousness'),
 (479,'extrensory','extrasensory'),(507,"don't comes","don't come"),(511,'mindto mind','mind-to-mind'),
 (568,'mamm','Mayim'),(585,'period','pier'),(590,'or to evolve','or devolve'),(625,'meet you too','meet you two'),
 (660,'Allen on','Al-Anon'),(680,'perverative','perseverative'),(731,'micro doing','microdosing'),(784,'balloon','doubloon'),
 (828,'shut down','shot down'),(861,'have interact','have interacted'),(862,'E could','It could'),(889,"you're came",'you came'),
 (967,'your conscious,','your consciousness,'),(1032,'turned into','tuned into'),(1064,'What was me','Woe is me'),
 (1167,'button down','buttoned-down'),(1174,'By Big toe','My Big T.O.E.'),(1178,'www.mmy- bigigentoe and','www.my-big-toe'),
 (1184,'binaral','binaural'),(1186,'daytoday','day-to-day'),(1193,'fiveweek','five-week'),(1209,'my book, too','my book two'),
 (1237,'leftrainers','left-brainers'),(1280,'in language','and languish'),(1283,'Turn on','Turn things off'),
 (1312,"It's my breakdown","It's Mayim Bialik’s Breakdown")]:fix(n,old,new)
n=1141;s=source[n-1];before=s['text'];after=re.sub(r'(?:If have )+', '',before)
after=after.replace('if you heard that song','Have you heard that song?').replace('song? and I','song?" And I')
assert after!=before;s['text']=after;changes.append(dict(source_id=s['id'],original_text=before,revised_text=after,reason='Repeated ASR loop absent from source-audio transcription; preserve the actual question',evidence=['work/audio/focus.5020-5300.asr.json']))
# Canonical term spelling only; this does not change the speaker's claims.
for s in source:
 before=s['text'];after=before.replace('larger conscious system','larger consciousness system')
 if before!=after:
  s['text']=after;changes.append(dict(source_id=s['id'],original_text=before,revised_text=after,reason='Consistent author term; reuse part-one glossary',evidence=['work/context.reused.json']))
for s,r in zip(source,rows):
 assert s['id']==r['id'];s['start']=r['start'];s['end']=r['end'];assert s['start']<s['end']
payload={'schema_version':SCHEMA_VERSION,'language':'en','source_kind':'youtube_automatic_caption_audio_reviewed','segments':source}
atomic_write_json(w/'source.fused.json',payload)

# The standard importer merges repeated adjacent speech. Preserve every raw-owned
# cue explicitly via storage, then use the ordinary planner/validator/exporter.
j=create_job_dir(w/'translation','Erase Your Fears reviewed','gFka_huRM38','Simplified Chinese')
j.mkdir(parents=True,exist_ok=True);(j/'artifacts').mkdir(exist_ok=True)
manifest=new_manifest(job_dir=j,url='https://www.youtube.com/watch?v=gFka_huRM38',title='Erase Your Fears',video_id='gFka_huRM38',target_language='Simplified Chinese',source_language='en')
manifest.update(source_kind=payload['source_kind'],status='source_ready',timeline_target='youtube-source',paths={'source':str(j/'source.json'),'source_artifact':str(w/'source.fused.json')},import_adapter='stable raw IDs retained; bypass adjacent identical-text coalescing')
atomic_write_json(j/'job.json',manifest);atomic_write_json(j/'source.json',payload)
c=json.loads((w/'context.reused.json').read_text());c['job_id']=manifest['job_id'];c['timeline']='youtube-source; current-video audio-supported word onsets, shared adjacent boundaries; original JSON3 retained';c['local_audio_evidence']='full source audio, 25 overlapping ASR chunks and 12 repeat-check clips';atomic_write_json(j/'context.json',c)
if (j/'windows').exists():shutil.rmtree(j/'windows')
plan_job(j,max_cues=120,max_duration_sec=1200)
translations={}
for f in sorted((w/'drafts').glob('*.txt')):
 for line in f.read_text().splitlines():
  if line.strip():n,t=line.split('|',1);assert int(n) not in translations;translations[int(n)]=t
assert set(translations)==set(range(1,1315))
polish={
 28:'去理解自己的生活。你经历过和可能经历的人生，有无数种可能，而你能体验它们的数据。',
 46:'你知道自己获得了什么，也属于你所连接的那个整体。',
 62:'“好，我要表现得更好。看见那位老太太了吗？我去扶她过马路。”',
 193:'Notion Mail 由 Notion 打造，超过半数《财富》500强企业都在使用 Notion。',
 277:'这些年来，我一直在做。',278:'什么是双耳节拍？',
 300:'抱歉，我这个神经科学家想确认一下：你说的节拍，是指电信号信息的整合，对吧？',
 558:'我只是想让你知道，我每天面对什么。',589:'这是你来这里的原因。',
 597:'你可以分析、思考、感受，但最终必须由你作选择。',
 633:'现在不同了，我依靠直觉生活。',643:'我在前面那个房间生过孩子，不知道你能不能感受到。',
 660:'这是 Al-Anon 互助会的常用语。',671:'你有什么感觉？闻到了什么？',
 674:'如果这种感受对应一个动作，就做出那个动作，诸如此类。',801:'它随时间流逝，成了过去。',817:'你就会得到另一种结果。',840:'系统让他传递信息。',
 843:'他们会把一切记下来，再去核实。',852:'别人问他：“是什么飞机？”答案直接就冒出来了。',
 873:'有限。我不记得，他的语言发展到什么程度时，这种情况才停止。那时，大概每周',
 1093:'牛也不爱乡村乐。',1112:'但也不是都喜欢。',
 1178:'可以去我的网站：www.my-big-toe.com',1179:'对，.com。所有内容都在那里。',
 1283:'也能关闭……——我们肯定还有很多可聊的。',
 1310:'从我们的《Breakdown》，到你不愿经历的崩溃。'}
target_changes=[dict(source_id=f'c{n:06d}',original_text=translations[n],revised_text=t,reason='Semantic/readability review against original raw-ID transcript') for n,t in polish.items()]
translations.update(polish)
groups=[[28,29],[46,47],[62,63],[300,301],[597,598,599,600],[633,634],[643,644],[671,672],[674,675],[843,844],[852,853]]
groupby={g[0]:g for g in groups};covered=set()
for f in sorted((j/'windows').glob('*.target.json')):
 d=json.loads(f.read_text());owned=json.loads(f.with_name(f.name.replace('.target.','.source.')).read_text())['core_ids'];out=[]
 for sid in owned:
  n=int(sid[1:])
  if n in covered:continue
  ns=groupby.get(n,[n]);ids=[f'c{v:06d}' for v in ns];assert all(v in owned for v in ids),(n,ids,owned)
  assert source[ns[-1]-1]['end']-source[n-1]['start']<=15
  out.append({'source_ids':ids,'text':translations[n]});covered.update(ns)
 d['cues']=out;atomic_write_json(f,d)
assert covered==set(range(1,1315))
(w/'active-job.txt').write_text(str(j))
review={'source_changes':changes,'target_changes':target_changes,'merged_groups':[{'ids':g,'reason':'Adjacent same-speaker short phrase or sentence, reviewed against context'} for g in groups],
 'rejected_timing_changes':rejected,'mapping_repair':{'raw_cues':1314,'initial_import_cues':1313,'coalesced_raw_ids':['c000475','c000476'],'resolution':'Retain both repeated utterances and all original raw IDs; rebuild every target mapping from original numbered drafts','final_source_count':len(source)},
 'uncertainties':[{'cue':298,'issue':'Full-window ASR inserted “No, no, it’s a set of fibers”; short recheck and primary YouTube captions omit it. Not inserted into subtitles.'},
 {'cue':590,'issue':'“evolve or to evolve” interpreted as “evolve or devolve” from contrast and recurring author concept; local ASR also unclear.'},
 {'cue':873,'issue':'Awkward language-development chronology preserved conservatively; both caption and short audio transcription agree.'},
 {'cue':915,'issue':'irrational/a rational differs across recognizers; retain primary-caption sense and reject unstable large onset change.'},
 {'cue':1313,'issue':'Theme-song “or two / fiction” interjection rendered consistently with part one; exact interjection remains uncertain.'}]}
atomic_write_json(w/'evidence-review.json',review)
print(json.dumps({'job':str(j),'validation':translation_status(j),'source_cues':len(source),'text_corrections':len(changes),'merged_groups':len(groups),'rejected_large_timing_changes':rejected},ensure_ascii=False,indent=2))
