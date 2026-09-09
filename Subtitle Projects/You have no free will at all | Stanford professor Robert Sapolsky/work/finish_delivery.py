from pathlib import Path
import json,re,hashlib,textwrap,runpy
from codex_subtitles.storage import atomic_write_json,update_manifest
from codex_subtitles.translation_service import translation_status,materialize_translation
from codex_subtitles.source_service import clean_caption_text
from codex_subtitles.export_service import export_job
p=Path(__file__).resolve().parent; project=p.parent
j=Path((p/'job-path.txt').read_text())
runpy.run_path(str(p/'translation/build_targets.py'))
s=json.loads((j/'source.json').read_text()); byid={c['id']:c for c in s['segments']}
terms=[
('Robert Sapolsky','罗伯特·萨波尔斯基','c000013','受访者'),
('Alex O’Connor','亚历克斯·奥康纳','c000115','播客对谈者'),
('Phineas Gage','菲尼亚斯·盖奇','c000197','脑损伤案例'),
('free will','自由意志','c000015','全片'),
('determinism','决定论','c001145','全片'),
('distributed causality','分布式因果','c000118','多种时间尺度的共同致因'),
('causal agent','致因者','c000101','因果作用不等于自由意志'),
('frontal cortex','额叶皮层','c000001','全片；不擅自改成前额叶皮层'),
('amygdala','杏仁核','c000298','情绪与威胁判断'),
('testosterone','睾酮','c000295','激素'),
('plasticity','可塑性','c000150','大脑变化'),
('PTSD / post-traumatic stress disorder','创伤后应激障碍','c000306','创伤'),
('stress hormones','应激激素','c000315','胎儿环境'),
('emergence / emergent property','涌现／涌现属性','c000580','整体层次属性；不据此推出自由意志'),
('recursive loop','递归循环','c000532','反思、强化与改变'),
('large language models','大语言模型','c000541','人工智能'),
('epigenetics','表观遗传','c000714','历史因素'),
('genetic determinism','遗传决定论','c000008','开场和正片保持同译'),
('environmental determinism','环境决定论','c000010','与遗传决定论成对使用'),
('collectivist / individualist cultures','集体主义／个人主义文化','c000810','保留平均意义、可能性与比较语气'),
('conformity','从众','c000677','人与群体关系'),
('culture of honor','荣誉文化','c000888','牧业文化案例'),
('Doctors Without Borders','无国界医生','c000484','组织'),
('meritocracy','功绩制','c001117','依功绩分配地位与奖赏的制度'),
('quarantine','隔离','c001075','借用公共卫生概念，指最低必要约束'),
('antihistamines','抗组胺药','c001094','飞行员例子'),
('agency / steerability','能动性／可引导性','c000973','不等同于自由意志'),
]
ctx=json.loads((j/'context.json').read_text())
ctx.update(summary='Big Think 访谈：萨波尔斯基认为行为由无法自主选择的生物因素、环境和历史共同形成，因而不存在自由意志。讨论分布式因果、额叶皮层、涌现、文化与养育、可改变性，以及责备奖惩和公共卫生式隔离。字幕忠实转述观点，不充当独立科学核验。',speakers=[{'name':'罗伯特·萨波尔斯基','role':'受访者','provenance':'官方英语字幕 c000013、c000032及回答段落'},{'name':'未具名主持人','role':'采访提问及节目导语','provenance':'官方英语字幕；不推测姓名'}],terminology=[{'source_term':a,'target':b,'note':d,'provenance':{'artifact':'work/evidence/youtube.manual.en.vtt','cue_id':c},'scope':d,'confidence':'high for caption identity; Chinese rendering chosen by Codex'} for a,b,c,d in terms],timeline={'target':'youtube-source','authority':'YouTube publisher-provided manual en caption','offset_seconds':0},evidence={'existing_project_files':['URL.md'],'youtube_manual_caption':'used: English/en','local_video':'absent','local_asr':'absent; no installed local backend; not run','ocr':'absent; not run','ocr_quality_alignment_reports':'absent','reference_documents':'absent; references directory absent'},style={'target_language':'Simplified Chinese','register':'自然、准确、适合阅读的访谈字幕','preserve_names_numbers':True,'no_translator_notes_inside_subtitles':True,'preserve_speaker_boundaries':True,'book_titles':{'Behave':'《行为》；首处扩展直译副标题','Determined':'《注定》','Why Zebras Don’t Get Ulcers':'《为什么斑马不得胃溃疡》','The Trouble with Testosterone':'《睾酮的麻烦》'},'book_title_note':'中文为本项目采用的译名，未据出版社版本核验；报告保留英文书名。'})
atomic_write_json(j/'context.json',ctx)
status=translation_status(j);assert status['state']=='complete';atomic_write_json(p/'validation.json',status)
result=export_job(j);update_manifest(j,status='complete',final=result)
raw=(p/'evidence/youtube.manual.en.vtt').read_text()
rx=re.compile(r'(?m)^(\d\d:\d\d:\d\d\.\d{3}) --> (\d\d:\d\d:\d\d\.\d{3})[^\n]*\n(.*?)(?=\n\n|\Z)',re.S)
rawc=rx.findall(raw)
assert len(rawc)==len(s['segments'])==1154

def ms(st):
 h,m,sec=st.split(':');return int(h)*3600000+int(m)*60000+round(float(sec)*1000)
for (a,b,txt),c in zip(rawc,s['segments']):
 assert ms(a)==round(c['start']*1000) and ms(b)==round(c['end']*1000)
 assert clean_caption_text(txt)==c['text']
assert all(ms(a)<ms(b) for a,b,_ in rawc)
assert all(ms(rawc[i][0])>=ms(rawc[i-1][1]) for i in range(1,len(rawc)))
trusted={c['id']:(a.replace('.',','),b.replace('.',',')) for c,(a,b,_) in zip(s['segments'],rawc)}

# Subtitle delivery layout: two Chinese lines and up to two English lines.
# Split only whitespace for English; keep all official wording intact.
def zhwrap(txt):
 lines=txt.splitlines()
 if len(lines)<=2 and max(map(len,lines))<=32:return txt
 t=''.join(lines)
 if len(t)<=32:return t
 candidates=[]
 for k in range(max(1,len(t)-32),min(32,len(t)-1)+1):
  if t[k] in '，。！？；：、）》”':continue
  if t[k-1].isascii() and t[k].isascii() and t[k-1].isalnum() and t[k].isalnum():continue
  bonus=8 if t[k-1] in '，。！？；：、' else 0
  candidates.append((abs(k-len(t)/2)-bonus,k))
 assert candidates,(len(t),t)
 k=min(candidates)[1];return t[:k]+'\n'+t[k:]

def enwrap(t):
 if len(t)<=96:return t
 positions=[i for i,x in enumerate(t) if x==' ' and i<=96 and len(t)-i-1<=96]
 assert positions,(len(t),t)
 k=min(positions,key=lambda i:abs(i-len(t)/2))
 return t[:k]+'\n'+t[k+1:]

rows=materialize_translation(j);assert all(not r.get('dropped') for r in rows)
delivery=project/'final';delivery.mkdir(exist_ok=True)
zhfile=delivery/'youtube.zh-Hans.srt';bifile=delivery/'youtube.zh-Hans-en.srt';enfile=delivery/'youtube.official.en.srt'
zhblocks=[];biblocks=[];mapping=[]
for n,r in enumerate(rows,1):
 ids=r['source_ids'];a=trusted[ids[0]][0];b=trusted[ids[-1]][1]
 zh=zhwrap(r['text']);en=enwrap(' '.join(byid[i]['text'] for i in ids))
 assert len(zh.splitlines())<=2 and len(en.splitlines())<=2
 assert not any(byid[i]['text'].startswith('- ') for i in ids[1:])
 rate=len(re.sub(r'\s','',zh))/(r['end']-r['start']);assert rate<=9,(ids,rate)
 assert '\n'.join(zh.splitlines()).replace('\n','')==r['text'].replace('\n','')
 prefix=f'{n}\n{a} --> {b}\n';zhblocks.append(prefix+zh);biblocks.append(prefix+zh+'\n'+en)
 mapping.append({'output_cue':n,'source_ids':ids,'start':a,'end':b,'zh_chars_per_second':round(rate,3),'zh_lines':len(zh.splitlines()),'bilingual_lines':len(zh.splitlines())+len(en.splitlines())})
zhfile.write_text('\n\n'.join(zhblocks)+'\n\n');bifile.write_text('\n\n'.join(biblocks)+'\n\n')
enfile.write_text('\n\n'.join(f'{i}\n{a.replace(".",",")} --> {b.replace(".",",")}\n{txt.strip()}' for i,(a,b,txt) in enumerate(rawc,1))+'\n\n')
atomic_write_json(p/'delivery-cue-map.json',mapping)
# Verify serialized delivery times rather than trusting the writer.
for f in [zhfile,bifile]:
 times=re.findall(r'(?m)^(\d\d:\d\d:\d\d,\d{3}) --> (\d\d:\d\d:\d\d,\d{3})$',f.read_text())
 assert times==[(x['start'],x['end']) for x in mapping]
assert hashlib.sha256((project/'URL.md').read_bytes()).hexdigest()==hashlib.sha256(b'https://www.youtube.com/watch?v=ke8oFS8-fBk').hexdigest()
assert (p/'evidence/youtube.manual.en.vtt').read_bytes()==(j/'artifacts/youtube.manual.en.vtt').read_bytes()
checks={str(f.relative_to(project)):hashlib.sha256(f.read_bytes()).hexdigest() for f in [project/'URL.md',p/'evidence/youtube.manual.en.vtt',j/'artifacts/youtube.manual.en.vtt',j/'source.json',zhfile,bifile,enfile]}
atomic_write_json(p/'checksums.sha256.json',checks)
qa={'status':'passed','source_cue_count':1154,'delivered_cue_count':len(rows),'windows_complete':12,'dropped_cues':0,'source_text_changes':0,'source_timestamp_changes':0,'timeline':'youtube-source','timeline_offset_ms':0,'all_delivery_timestamps_match_raw_vtt_ms':True,'speaker_boundaries_preserved':True,'max_zh_lines':max(x['zh_lines'] for x in mapping),'max_bilingual_lines':max(x['bilingual_lines'] for x in mapping),'max_zh_chars_per_second':max(x['zh_chars_per_second'] for x in mapping),'max_duration_sec':max(round(r['end']-r['start'],3) for r in rows),'first_start':mapping[0]['start'],'last_end':mapping[-1]['end'],'local_media_path':None,'local_media_sha256':None,'local_media_reason':'No local media present; explicitly requested YouTube source timeline','paid_api_used':False,'local_asr_run':False,'ocr_run':False,'delivery_files':[str(x) for x in [zhfile,bifile,enfile]],'qa_scope':'Caption text, structure and exact raw-caption timestamps verified; no independent audio listening or source-video visual playback QA.'}
atomic_write_json(p/'delivery-quality.json',qa)
review={'timeline_target':'youtube-source','selected_source':'work/evidence/youtube.manual.en.vtt','source_corrections':[],'source_insertions':[],'changed_or_inserted_source_cues':0,'evidence_fusion':'Only one usable transcript source exists; no multi-source corroboration claimed. Manual English was explicitly selected over available auto and machine-translated caption tracks.','uncertainties':[{'cues':['c000209','c000242'],'issue':'Caption says TNT in the Gage account. Retained as the speaker’s claim; not silently fact-corrected.'},{'cues':['c000335','c000339'],'issue':'Claims about parole-study challenges and replications are the speaker’s statements, not independently verified findings.'},{'cues':['c000358','c000587','c000588'],'issue':'Brain mass/energy percentages and the fruit-fly neuron ratio are retained exactly in meaning and quantity; no independent scientific fact-check.'},{'cues':['c000818','c000902','c000921'],'issue':'Speaker moves between Southeast Asia and Southeast China. Geographical names follow each caption passage; not homogenized.'},{'cues':['c000832'],'issue':'Awkward caption phrase “dependent descendants” translated as descendants from context; raw English retained unchanged.'},{'cues':['c001124'],'issue':'“take out your brain” rendered as brain surgery in the immediately preceding brain-tumor context; official English remains unchanged.'}], 'book_titles':ctx['style']['book_titles'],'book_title_note':ctx['style']['book_title_note']}
atomic_write_json(p/'evidence-review.json',review)
print(json.dumps(qa,ensure_ascii=False,indent=2))
