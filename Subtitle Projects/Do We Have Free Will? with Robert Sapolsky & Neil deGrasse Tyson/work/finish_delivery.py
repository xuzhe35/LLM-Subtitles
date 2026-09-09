from pathlib import Path
import json,re,hashlib,subprocess,sys
from codex_subtitles.storage import atomic_write_json,update_manifest
from codex_subtitles.translation_service import translation_status,materialize_translation
from codex_subtitles.export_service import export_job
p=Path(__file__).resolve().parent;project=p.parent;j=Path((p/'job-path.txt').read_text())
source=json.loads((j/'source.json').read_text());byid={c['id']:c for c in source['segments']};timemap=json.loads((p/'source-timing-map.json').read_text());tm={c['source_id']:c for c in timemap}
assert len(source['segments'])==1264
for c in source['segments']:
 t=tm[c['id']];assert round(c['start']*1000)==t['start_ms'] and round(c['end']*1000)==t['normalized_end_ms']
assert all(source['segments'][i]['start']>=source['segments'][i-1]['end'] for i in range(1,len(source['segments'])))
status=translation_status(j);assert status['state']=='complete';atomic_write_json(p/'validation.json',status)
r=subprocess.run([sys.executable,'-m','codex_subtitles','validate',str(j)],capture_output=True,text=True);assert r.returncode==0,r.stdout
result=export_job(j);update_manifest(j,status='complete',final=result)

def ts(ms):
 h,rem=divmod(ms,3600000);m,rem=divmod(rem,60000);s,msec=divmod(rem,1000);return f'{h:02}:{m:02}:{s:02},{msec:03}'
def zhwrap(txt):
 lines=txt.splitlines()
 if len(lines)<=2 and max(map(len,lines))<=32:return txt
 t=''.join(lines)
 if len(t)<=32:return t
 candidates=[]
 for k in range(max(1,len(t)-32),min(32,len(t)-1)+1):
  if t[k] in '，。！？；：、）》”':continue
  if t[k-1].isascii() and t[k].isascii() and t[k-1].isalnum() and t[k].isalnum():continue
  candidates.append((abs(k-len(t)/2)-(8 if t[k-1] in '，。！？；：、' else 0),k))
 assert candidates,(len(t),t)
 k=min(candidates)[1];return t[:k]+'\n'+t[k:]
def enwrap(t):
 if len(t)<=96:return t
 positions=[i for i,x in enumerate(t) if x==' ' and i<=116 and len(t)-i-1<=116]
 assert positions,(len(t),t)
 k=min(positions,key=lambda i:abs(i-len(t)/2));return t[:k]+'\n'+t[k+1:]
rows=materialize_translation(j);assert all(not x.get('dropped') for x in rows)
final=project/'final';final.mkdir(exist_ok=True);zhfile=final/'youtube.zh-Hans.srt';bifile=final/'youtube.zh-Hans-en.srt';enfile=final/'youtube.reviewed.en.srt'
zb=[];bb=[];mapping=[]
for n,r in enumerate(rows,1):
 ids=r['source_ids'];a=tm[ids[0]]['start_ms'];b=tm[ids[-1]]['normalized_end_ms'];assert b>a and b-a<=15000
 zh=zhwrap(r['text']);en=enwrap(' '.join(byid[k]['text'] for k in ids));prefix=f'{n}\n{ts(a)} --> {ts(b)}\n'
 assert len(zh.splitlines())<=2 and len(en.splitlines())<=2
 zb.append(prefix+zh);bb.append(prefix+zh+'\n'+en)
 mapping.append({'output_cue':n,'source_ids':ids,'start_ms':a,'end_ms':b,'start':ts(a),'end':ts(b),'zh_lines':len(zh.splitlines()),'bilingual_lines':len(zh.splitlines())+len(en.splitlines()),'zh_chars_per_second':round(len(re.sub(r'\s','',zh))/((b-a)/1000),3)})
zhfile.write_text('\n\n'.join(zb)+'\n\n');bifile.write_text('\n\n'.join(bb)+'\n\n')
enfile.write_text('\n\n'.join(f'{n}\n{ts(tm[c["id"]]["start_ms"])} --> {ts(tm[c["id"]]["normalized_end_ms"])}\n{c["text"]}' for n,c in enumerate(source['segments'],1))+'\n\n')
atomic_write_json(p/'delivery-cue-map.json',mapping)
for f in [zhfile,bifile]:
 blocks=f.read_text().strip().split('\n\n');assert [int(x.splitlines()[0]) for x in blocks]==list(range(1,len(rows)+1))
 times=re.findall(r'(?m)^(\d\d:\d\d:\d\d,\d{3}) --> (\d\d:\d\d:\d\d,\d{3})$',f.read_text());assert times==[(x['start'],x['end']) for x in mapping]
assert [k for x in mapping for k in x['source_ids']]==[x['id'] for x in source['segments']]
initial=json.loads((p/'initial-inventory.json').read_text());assert hashlib.sha256((project/'URL.md').read_bytes()).hexdigest()==initial['files'][0]['sha256']
checks={str(f.relative_to(project)):hashlib.sha256(f.read_bytes()).hexdigest() for f in [project/'URL.md',p/'evidence/youtube.auto.en-orig.json3',p/'evidence/youtube.auto.en-orig.vtt',p/'source.youtube-normalized.json',p/'source.reviewed.json',zhfile,bifile,enfile]};atomic_write_json(p/'checksums.sha256.json',checks)
review=json.loads((p/'evidence-review.json').read_text())
q={'status':'passed_with_source_limitations','source_cues':1264,'translation_cues':len(rows),'windows_complete':13,'dropped_source_cues':0,'source_text_corrections':len(review['source_corrections']),'source_timing_changes_after_normalization':0,'timeline':'youtube-source','offset_ms':0,'all_delivery_bounds_match_normalized_source_ms':True,'normalization':'Nonempty JSON3 new-text events retained once. Rolling-display ends capped at next new-text start; final event capped to metadata media duration 3288s. Original event times and word offsets preserved in raw JSON3.','start':mapping[0]['start'],'end':mapping[-1]['end'],'max_duration_sec':max((x['end_ms']-x['start_ms'])/1000 for x in mapping),'max_zh_lines':max(x['zh_lines'] for x in mapping),'max_bilingual_lines':max(x['bilingual_lines'] for x in mapping),'max_zh_chars_per_second':max(x['zh_chars_per_second'] for x in mapping),'source_kind':'YouTube original English automatic captions; kind=asr; not a verified manual track','ocr_run':False,'local_asr_run':False,'paid_api_used':False,'source_media_independent_listening':False,'local_video_path':None,'local_video_sha256':None,'speaker_review':'Known dialogue transitions kept separate at source-event boundaries; mixed-speaker source events marked with Chinese dialogue dashes where possible. No independent acoustic diarization.','limitations':'Opening/closing music-adjacent ASR word placement and a few garbled English fragments remain uncertain; see evidence-review.json.'};atomic_write_json(p/'delivery-quality.json',q)
# Share only applicable earlier evidence. No prior timing is imported.
prev=project.parent/'You have no free will at all | Stanford professor Robert Sapolsky';pj=Path((prev/'work/job-path.txt').read_text());ps={c['id']:c for c in json.loads((pj/'source.json').read_text())['segments']}
shared=[]
for theme,old,new in [('行为的多重前因',(139,157),(93,104)),('饥饿法官和假释',(340,350),(113,121)),('银行贷款与饭点',(382,384),(139,141)),('意图不等于自由意志',(66,87),(439,451)),('无法控制的生物与环境',(78,83),(580,583)),('践行观点的频率不同，不复制数字',(1014,1017),(725,728)),('飞行员用药与最低必要限制',(1092,1108),(919,923)),('养育方式和文化',(849,855),(968,975)),('功绩、奖惩与归责',(1010,1013),(717,724))]:
 shared.append({'theme':theme,'prior_cues':[f'c{x:06d}' for x in range(old[0],old[1]+1)],'current_cues':[f'c{x:06d}' for x in range(new[0],new[1]+1)],'prior_excerpt':' '.join(ps[f'c{x:06d}']['text'] for x in range(old[0],old[1]+1)),'use':'概念互参和译法一致；不是本片措辞或时间的独立证明'})
refs=[{'title':'Max Tegmark · MIT Physics','url':'https://physics.mit.edu/faculty/max-tegmark/','used_for':'确认Max Tegmark的姓名拼写与MIT身份；不用于证明23个数量级的原话或结论。'}, {'title':'Extraneous factors in judicial decisions · PNAS','url':'https://doi.org/10.1073/pnas.1018033108','used_for':'确认假释研究的主题及Proceedings of the National Academy of Sciences期刊名称；不将它视作对所有复现或争议断言的核验。'}, {'title':'Starry Messenger · Neil deGrasse Tyson','url':'https://neildegrassetyson.com/books/2022-09-starry-messenger/','used_for':'确认书名。'}, {'title':'A Primate’s Memoir · Simon & Schuster','url':'https://www.simonandschuster.com/books/A-Primates-Memoir/Robert-M-Sapolsky/9781416590361','used_for':'确认书名与作者；未取得或通读书稿。'}, {'title':'Brian Greene · Columbia Physics','url':'https://www.physics.columbia.edu/content/brian-greene','used_for':'确认人名拼写。'}]
sharedfiles=[prev/'work/evidence/youtube.manual.en.vtt',prev/'work/evidence-review.json',pj/'context.json']
atomic_write_json(p/'shared-evidence.json',{'prior_project':str(prev),'sharing_authorized':True,'artifacts':[{'path':str(f),'sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'type':'原始人工字幕' if f.suffix=='.vtt' else '上一任务生成的术语/审核记录，不是作者文档'} for f in sharedfiles],'comparisons':shared,'copied_prior_timestamps':False})
atomic_write_json(p/'reference-evidence.json',{'project_reference_documents':[],'external_primary_sources':refs,'scope':'专名、书名、术语核对；不提供字幕时间戳或增加未说出的内容。'})
ctx=json.loads((j/'context.json').read_text());ctx['additional_terms']=[{'source':'Max Tegmark','target':'马克斯·泰格马克','provenance':refs[0]['url']},{'source':'Brian Greene','target':'布赖恩·格林','provenance':refs[4]['url']},{'source':'post hoc rationalization','target':'事后合理化','provenance':'本片c000995-c001000解释'},{'source':'dorsolateral prefrontal cortex','target':'背外侧前额叶皮层','provenance':'本片c000376-c000377'},{'source':'nucleus accumbens','target':'伏隔核','provenance':'本片c000378'},{'source':'dyslexia','target':'阅读障碍','provenance':'本片c000750及后文'},{'source':'CV','target':'履历','provenance':'本片c001049-c001061上下文推断；ASR原为TV'}];ctx['reviewed_source']='work/source.reviewed.json';atomic_write_json(j/'context.json',ctx)
ledger={'timeline_target':'youtube-source','requested_manual_english':True,'available_track':'en-orig / English (Original) in automatic_captions, URL kind=asr','manual_track_found':False,'manual_check_limit':'Default extraction exposed no manual tracks; web client secondary check hit subtitle PO-token requirements, so absence is limited to accessible evidence, not a universal claim.','initial_project_files':['URL.md'],'used':['URL.md','work/evidence/youtube.auto.en-orig.json3','work/evidence/youtube.auto.en-orig.vtt','work/youtube-metadata.json','work/shared-evidence.json','work/reference-evidence.json'],'absent':['local video/audio','existing ASR transcripts','OCR/ORC subtitles','OCR quality/alignment sidecars','references directory and author notes'],'local_asr_backend_available':False,'ocr_run':False,'paid_api_used':False};atomic_write_json(p/'evidence-ledger.json',ledger)
report=f'''# 字幕证据使用与质量报告

项目：Do We Have Free Will? with Robert Sapolsky & Neil deGrasse Tyson  
来源：[StarTalk · 本片 YouTube 视频](https://www.youtube.com/watch?v=pFg1ysJ1oUs)  
目标：简体中文；YouTube 源视频时间轴。处理日期：2026-09-08。

## 当前交付

- [中文字幕](youtube.zh-Hans.srt)
- [中英双语字幕](youtube.zh-Hans-en.srt)，中文在上、英文在下。
- [审核后的英语原文](youtube.reviewed.en.srt)，以英语自动字幕为基础，非官方人工校订版。

**本次完成翻译与结构校验，但来源有局限：实际取得的是 YouTube 原始英语自动字幕，不是已验证的人工英语轨。** 所有交付采用本片 YouTube 时间轴，没有借用上一片时间或应用9秒偏移。

## 人工英语优先的核查结果

用户提示默认官方英语字幕，应优先采用。此次默认抓取返回的 `subtitles`（人工轨）为空，英语原始轨位于 `automatic_captions`，其来源参数明确标为 `kind=asr`，名称为 `English (Original)`、语言为 `en`。另一次 web 客户端检查受到字幕令牌要求限制，未能取得额外人工轨。

因此，本报告只结论为“**没有取得可用的人工英语轨**”，不将默认可显示的英语轨误称成人工字幕，也不声称排除了所有登录或客户端条件下的其他轨道。采用原始英语自动轨完成工作；自动中文字幕未作为语义来源。

## 全部现有证据的清点与使用

| 证据 | 状态与使用 |
| --- | --- |
| 本项目 `URL.md` | 开始处理时项目内唯一文件。提取视频ID `pFg1ysJ1oUs`；保留原文件及哈希。 |
| 本片英语字幕 | 本次获取原始JSON3及VTT。JSON3用于词句和源时间，VTT保留供复核；两者是同一自动轨的不同格式，不算两份独立听写。 |
| 本片元数据及节目说明 | 确认频道、标题、时长、参与者、书名和主题；不作为逐字发言证据。 |
| 本地视频、音频、已有听写 | 未发现。没有本地视频路径或校验值，也没有运行本地ASR。环境检查未发现已安装的本地ASR后端。 |
| OCR/ORC字幕、质量报告、对齐侧车、帧图 | 均未发现。未运行硬字幕OCR，没有声称看过任何OCR证据帧。 |
| 本项目 `references/` 与作者笔记 | 目录及文档均未发现。没有作者手稿或完整书籍可供通读。 |
| 上一项目材料 | 按用户授权使用英语人工字幕、术语表、翻译/质量记录，辅助概念和译法。详见下节。 |
| 外部一手资料 | 对专名、书名、期刊名做有限核查，页面和用途见文末；未添加原视频没有说出的内容。 |

完整清单：[evidence-ledger.json](../work/evidence-ledger.json)。原始证据：[英语JSON3](../work/evidence/youtube.auto.en-orig.json3)、[英语VTT](../work/evidence/youtube.auto.en-orig.vtt)。

## 与上一项目共享的证据

上一项目为 *You have no free will at all | Stanford professor Robert Sapolsky*。其中的人工英语字幕可供比较，但其术语表、译文和质量报告是上一任务生成的工作材料，**不是作者笔记**。

共记录9组主题对照：行为的多重前因、饥饿法官与假释、银行贷款和饭点、意图与自由意志的区别、生物与环境、实践观点的频率、飞行员用药、养育文化、奖惩与功绩。

沿用“自由意志、决定论、额叶皮层、杏仁核、睾酮、表观遗传、功绩制、隔离”等译法，并保持《注定》的项目译名。本片新增量子物理、可证伪性、混沌、阅读障碍、事后合理化等语境，逐段独立翻译。

两片的数值没有混用。例如上一片说“每个月约三分半钟”，本片说“每隔一个月约三分钟”；本片还有“每次两秒”的另一次自述，分别保留。没有整段复制上一片的时间或以其台词覆盖本片。

具体cue对照、摘录与来源哈希：[shared-evidence.json](../work/shared-evidence.json)。

## 时间处理与覆盖校验

- 元数据时长54分48秒。原始链接的 `t=9s` 是播放起点，应用偏移为 **0毫秒**。
- JSON3含新文本事件、纯换行事件和显示窗口事件。只取非空新文本事件一次，得到 **1,264个源cue**，没有把滚动重显的上一行翻译两遍。
- 自动轨会让旧行继续停留，造成事件显示时长互相覆盖。规范化时，结束时间取“原始事件结束”和“下一个新文本开始”的较早者；末条截到视频元数据时长。没有移动开始时间、拉伸时间轴或按上一片推算偏移。
- 原始事件起止、词级偏移留在JSON3；每个事件的原始结束与规范化结束列在 [source-timing-map.json](../work/source-timing-map.json)。规范化完成后，翻译过程没有再改源时间。
- 所有1,264个cue ID恰好覆盖一次，顺序一致，**无丢弃**，包括音乐、笑声、掌声、宣传段落和结尾。
- 中文和双语各 **{len(rows)}条**，13个窗口全部通过验证。每次合并只包含相邻源cue，最多8个、最长15秒以内；明确的说话人转换保持分开。源事件本身包含多人的地方，以中文对话短横区分，并未声称完成声纹识别。
- 交付开始 **{q['start']}**，结束 **{q['end']}**；最长显示 **{q['max_duration_sec']:.3f}秒**。中文字幕最多2行，双语最多4行。
- 所有交付SRT序号和时间边界均重新读取核对，逐毫秒匹配规范化源事件的边界。此项是字幕轨一致性校验，不等于对音视频逐帧同步的独立证明。

质量数据：[delivery-quality.json](../work/delivery-quality.json)；交付到源cue映射：[delivery-cue-map.json](../work/delivery-cue-map.json)。

## 原文审核与译文处理

记录了 **{len(review['source_corrections'])}条英语文本修订**，均保留原文、修订文、理由，时间不变。修订例子包括 Neil deGrasse Tyson、Robert Sapolsky、Brian Greene、Max Tegmark、Starry Messenger、Proceedings of the National Academy of Sciences、post hoc、paroled，以及明确的拼写/词形。

`TV → CV`、`different → indifferent`、被截断的 `hypothal → hypothalamus` 等依据局部语义修订，报告标注为上下文推断，**不冒充音频确认**。修订保存在独立的 [source.reviewed.json](../work/source.reviewed.json)，原始JSON3、VTT及规范化原始文字保留不动。

中文重视完整论证、否定、数字、单位和幽默；精简口头停顿，保留“错选子宫”“圣诞老人”“奥普拉发礼物”等笑话。没有把“随机”译成“自主控制”，也没有把“没有自由意志”译成“无法学习或改变”。

## 尚存的不确定性与复核点

1. **开场约00:12–00:23，结尾约54:30–54:48**：自动轨将 `shift` 分到片头音乐之后，把 `listening` 放到片尾音乐之后。未独立听校，没有凭猜测改它们的时间；因此开场和结尾句子会被音乐提示隔开。需要原始音视频核对后，才能宣称这两处精确同步。
2. **约03:02、03:28、05:40、20:19、34:26附近**：`epipal`、`ING`、重叠插话、`appen for you`、`vault` 等识别破碎。中文采用可由句意确定的谨慎表达；例如 `vault` 未擅自指定酒精或某种药。英文保留无法可靠复原的片段，不捏造逐字原话。
3. “99%”在种族偏差相关的幽默插话里出现，保持笑话语境；23个数量级、70%囚犯贫困经历、癫痫研究年代、假释研究复现等，均作为节目原有陈述保留，未用有限书目核对冒充全面科学事实核验。
4. 多人抢话及简短接话的识别受自动轨分段限制；本次未获得独立声轨或本地听写，不能宣称每次换人均经听觉确认。

逐cue说明：[evidence-review.json](../work/evidence-review.json)。这些限制不影响源cue完整性和文件结构通过，但影响对逐字原话及实际发声边界的确信程度。

## 外部资料的有限用途

'''
for ref in refs:report+=f'- [{ref["title"]}]({ref["url"]})：{ref["used_for"]}\n'
report+='''
以上均为本次查询的一手页面，查询日期2026-09-08。只用于所列核查，不把网页背景当成额外台词。详细记录：[reference-evidence.json](../work/reference-evidence.json)。

## 文件保留与成本

原始URL、JSON3、VTT、规范化原文和上一项目材料均保留。当前首选交付为本项目 `final/` 中的中文、双语和报告；工作窗口及服务原始导出留在 `work/` 供续改。

未运行硬字幕OCR，未运行本地ASR，未下载媒体或安装转写模型，未读取或使用OpenAI/Google API密钥，未调用收费API。网络访问仅用于字幕、元数据及上述参考页面。

校验值：[checksums.sha256.json](../work/checksums.sha256.json)。
'''
(final/'证据使用报告.md').write_text(report)
update_manifest(j,preferred_delivery={'timeline':'youtube-source','translated_srt':str(zhfile),'bilingual_srt':str(bifile),'evidence_report':str(final/'证据使用报告.md')})
print(json.dumps(q,ensure_ascii=False,indent=2))
