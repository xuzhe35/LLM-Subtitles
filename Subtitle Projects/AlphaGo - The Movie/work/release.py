"""Export reviewed targets and preserve the downloaded VTT's exact milliseconds."""
from pathlib import Path
import json,re,hashlib,shutil
from datetime import datetime,timezone
from codex_subtitles.export_service import export_job
from codex_subtitles.translation_service import translation_status
from codex_subtitles.storage import update_manifest
from utils.subtitle_formatter import generate_srt,parse_srt,parse_vtt,wrap_subtitle_text
w=Path(__file__).resolve().parent; project=w.parent
job=next((w/'codex_native').glob('*'))
raw=w/'evidence/youtube.manual.en-GB.vtt'
sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
raw_hash=sha(raw);url_hash=sha(project/'URL.md')
assert (project/'URL.md').read_text()=='https://www.youtube.com/watch?v=WXuK6gekU1Y&t=468s'
assert raw.read_bytes()==(job/'artifacts/youtube.manual.en-GB.vtt').read_bytes()
status=translation_status(job);assert status['state']=='complete',status
source=json.loads((job/'source.json').read_text())['segments']
raw_cues=parse_vtt(raw)
target=[c for f in sorted((job/'windows').glob('*.target.json'))for c in json.loads(f.read_text())['cues']]
assert len(source)==len(raw_cues)==len(target)==1785
assert all(c['source_ids']==[s['id']] and c['text'].strip() for s,c in zip(source,target))
times=re.findall(r'^(\d\d:\d\d:\d\d\.\d{3}) --> (\d\d:\d\d:\d\d\.\d{3})',raw.read_text(),re.M)
assert len(times)==len(source)
exact=[a.replace('.',',')+' --> '+b.replace('.',',')for a,b in times]
result=export_job(job)
# Reflow bilingual English to 42 columns, Chinese to 32; retain dialogue line breaks.
bilingual_path=Path(result['bilingual_srt'])
blocks=bilingual_path.read_text().strip().split('\n\n')
for i,block in enumerate(blocks):
 lines=block.splitlines()[:2]
 lines += [wrap_subtitle_text(target[i]['text'],max_line_chars=32),wrap_subtitle_text(source[i]['text'],max_line_chars=42)]
 blocks[i]='\n'.join(lines)
bilingual_path.write_text('\n\n'.join(blocks)+'\n\n')
precision={}
def restore(path):
 lines=path.read_text().splitlines();j=0;changed=0
 for i,line in enumerate(lines):
  if '-->' in line:
   changed+=int(line!=exact[j]);lines[i]=exact[j];j+=1
 assert j==len(exact)
 path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
 precision[path.name]=changed
for key in ['translated_srt','bilingual_srt']:restore(Path(result[key]))
out=project/'final';out.mkdir(exist_ok=True)
zh=out/'AlphaGo.youtube.zh-Hans.srt';bi=out/'AlphaGo.youtube.zh-Hans.en.srt';en=out/'AlphaGo.youtube.en-GB.srt'
shutil.copyfile(result['translated_srt'],zh);shutil.copyfile(result['bilingual_srt'],bi)
generate_srt(source,en,max_line_chars=42);restore(en)
checks={}
for name,file in [('chinese',zh),('bilingual',bi),('english',en)]:
 cues=parse_srt(file);assert len(cues)==len(source)
 timing_lines=re.findall(r'^\d\d:\d\d:\d\d,\d{3} --> \d\d:\d\d:\d\d,\d{3}',file.read_text(),re.M)
 assert timing_lines==exact
 for i,(a,b) in enumerate(zip(raw_cues,cues)):
  assert a['start']==b['start'] and a['end']==b['end'] and b['end']>b['start']
  if i:assert b['start']>=cues[i-1]['end']
 clean=lambda x:re.sub(r'\s+','',x)
 if name=='chinese':assert all(clean(a['text'])==clean(b['text'])for a,b in zip(target,cues))
 if name=='english':assert all(clean(a['text'])==clean(b['text'])for a,b in zip(source,cues))
 if name=='bilingual':assert all(clean(c['text'])==clean(t['text'])+clean(s['text'])for s,t,c in zip(source,target,cues))
 checks[name]={'path':str(file),'cue_count':len(cues),'exact_raw_timestamps':True,'positive_durations':True,'no_overlaps':True,'text_roundtrip':True,'sha256':sha(file),'max_display_lines':max(len(c['text'].splitlines()) for c in cues)}
assert raw_hash==sha(raw) and url_hash==sha(project/'URL.md')
review_entries=[]
def note(n,reason,evidence,confidence='high'):
 s=source[n-1];review_entries.append({'source_id':s['id'],'original_text':s['text'],'translated_text':target[n-1]['text'],'start':s['start'],'end':s['end'],'reason':reason,'evidence_used':evidence,'confidence':confidence,'english_source_changed':False,'timestamps_changed':False})
note(408,'five-all 与随后的 all games 矛盾；在五局全胜语境中译为5比0。',['manual c000408-c000410'])
note(472,'原句省略年龄组限定；中文明确同龄棋手，避免误解为成年世界第二。',['manual c000465-c000472','https://achievement.org/achiever/demis-hassabis-ph-d/'])
note(433,'heavy node 的项目内部确切含义未证实，保留原词，不擅自解释为权重或神经元。',['manual c000432-c000438'],'medium')
note(367,'保留说话者列举的音乐、诗歌、绘画；没有按常识改写成原句未说的书法。',['manual c000364-c000369'])
note(1367,'胜率曲线下降 eight percent 在此理解为八个百分点。',['manual c001364-c001368'])
note(1544,'保留精确值0.007%，没有强行改成0.01%；随后“万分之一”保留为现场近似说法。',['manual c001544-c001551'])
note(1676,'White lights up in places 措辞不清；根据数目语境采用保守表述，不据此增补具体棋形。',['manual c001674-c001677'],'medium')
note(1374,'现场交叠残句保留未完句语气；没有音频证据，不补足技术含义。',['manual c001373-c001375'],'medium')
for n in [364,797,1191,1286,1624]:note(n,'终审核对姓名、否定语义或围棋术语，详见 context.json 和 polish-log.json。',['manual source','context.json','polish-log.json'])
review={'timeline_target':'youtube-source','source_wording_changes':0,'inserted_cues':0,'dropped_cues':0,'merged_cues':0,'translation_interpretations':review_entries,'unverified_name_labels':['Yuan','Hojung','Jimyung','Soyong','Yeowon'],'raw_evidence_unchanged':True}
(w/'evidence-review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n')
ledger={'project':str(project),'created_utc':datetime.now(timezone.utc).isoformat(),'timeline_target':'youtube-source','youtube_video_id':'WXuK6gekU1Y','youtube_url':'https://www.youtube.com/watch?v=WXuK6gekU1Y','offset_seconds':0,'inventory_before_work':[{'path':'URL.md','kind':'user supplied source locator','sha256':url_hash}],'evidence':[{'path':str(raw),'language':'en-GB','time_basis':'youtube-source','source_kind':'YouTube standard/manual caption track','machine_generated':False,'authorship':'Uploader-provided non-automatic track; individual author identity not independently verified','used_for':['source wording','all final timestamps','segmentation'],'sha256':raw_hash},{'path':str(w/'youtube-metadata.json'),'source_kind':'yt-dlp metadata','used_for':['video identity','duration','manual/automatic track classification'],'sha256':sha(w/'youtube-metadata.json')}],'absent':['local video','local audio transcription','OCR/ORC subtitle','OCR quality/alignment sidecars','references/author notes'],'automatic_captions':{'available':True,'downloaded':False,'used_as_translation_source':False,'reason':'User explicitly requested standard English; sufficient manual track available.'},'external_references':json.loads((job/'context.json').read_text())['verified_reference_notes'],'local_asr':{'ran':False,'installed_backends':[],'reason':'No local media/evidence, standard English caption sufficient.'},'hard_subtitle_ocr_ran':False,'paid_api_calls':False}
(w/'evidence-ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n')
quality={'status':'passed','translation_windows':status,'coverage':{'source':1785,'translated':1785,'dropped':0,'inserted':0,'merged':0},'alignment':{'target':'youtube-source','method':'one-to-one exact timestamp transfer from downloaded manual VTT','offset_seconds':0,'max_start_error_ms':0,'max_end_error_ms':0,'video_duration_seconds':5428,'first_timestamp':times[0][0],'last_timestamp':times[-1][1],'tail_without_source_cues_seconds':round(5428-source[-1]['end'],3),'local_video_path':None,'local_video_checksum':None,'audio_or_visual_spotcheck':False,'limitation':'Validated directly against source caption timings, not independently re-aligned by listening.'},'precision_restoration':{'reason':'Existing exporter truncates floating-point fractions; replaced timing lines with raw VTT millisecond strings. No repository code modified.','changed_timing_lines_by_export':precision},'outputs':checks,'source_sha256':raw_hash,'original_url_sha256':url_hash,'review':{'semantic_pass':'all chronological batches translated with context; second-pass window boundaries, numerical expressions, core terms, selected negation/uncertainty, source ambiguity clusters','unresolved':['heavy node internal meaning','c001374 fragment','c001676 unusual wording','five retained original-language speaker labels']}}
(out/'quality-report.json').write_text(json.dumps(quality,ensure_ascii=False,indent=2)+'\n')
update_manifest(job,status='complete',source_kind='youtube_manual_caption',youtube_source_url=ledger['youtube_url'],youtube_source_video_id='WXuK6gekU1Y',timeline_target='youtube-source',final=result,preferred_delivery={'chinese':str(zh),'bilingual':str(bi),'english':str(en),'quality_report':str(out/'quality-report.json')})
print(json.dumps({'status':'passed','outputs':checks,'precision_restoration':precision,'source_sha256':raw_hash},ensure_ascii=False,indent=2))
