from pathlib import Path
import json
W=Path(__file__).resolve().parent
P=W.parent
J=Path((W/'active-job.txt').read_text())
from codex_subtitles.translation_service import materialize_translation
from utils.subtitle_formatter import parse_srt
import hashlib,re,math,shutil
D=P/'final';D.mkdir(exist_ok=True)
s=json.loads((J/'source.json').read_text())['segments'];byid={x['id']:x for x in s};allz=materialize_translation(J);z=[x for x in allz if not x.get('dropped')]
def ts(v):
 ms=round(v*1000);h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);ss,ms=divmod(ms,1000);return f'{h:02}:{m:02}:{ss:02},{ms:03}'
def wrapzh(t):
 if len(t)<=30:return t
 opts=[i for i in range(1,len(t)) if not(t[i-1].isascii() and t[i].isascii() and t[i-1].isalnum() and t[i].isalnum()) and t[i] not in '，。！？；：、）”》' and t[i-1] not in '（“《']
 i=min(opts,key=lambda i:abs(i-len(t)/2)-(4 if t[i-1] in '，。！？；：、' else 0))
 return t[:i].rstrip()+'\n'+t[i:].lstrip()
def wrapen(t):
 t=re.sub(r'(^|\s)>>\s*',r'\1',t).strip()
 if len(t)<=88:return t
 inds=[m.start() for m in re.finditer(' ',t)];i=min(inds,key=lambda i:abs(i-len(t)/2));return t[:i]+'\n'+t[i+1:]
zh=[];bi=[]
for i,x in enumerate(z,1):
 head=f'{i}\n{ts(x["start"])} --> {ts(x["end"])}\n';target=wrapzh(x['text']);en=wrapen(' '.join(byid[n]['text'] for n in x['source_ids']))
 zh.append(head+target+'\n\n');bi.append(head+target+'\n'+en+'\n\n')
zhpath=D/'translated.youtube.zh-Hans.srt';bipath=D/'bilingual.youtube.zh-Hans-en.srt';zhpath.write_text(''.join(zh));bipath.write_text(''.join(bi))
shutil.copy2(W/'source.fused.youtube.en.srt',D/'source.reviewed.youtube.en.srt')
# Keep canonical finalized paths identical to delivered review-ready files, with exact millisecond rendering.
shutil.copy2(zhpath,J/'final/translated.Simplified Chinese.srt');shutil.copy2(bipath,J/'final/bilingual.Simplified Chinese.srt')
a,b=parse_srt(str(zhpath)),parse_srt(str(bipath));assert len(a)==len(b)==678
allids=[n for x in allz for n in x['source_ids']];assert allids==[x['id'] for x in s]
for x,y,o in zip(a,b,z):
 assert abs(x['start']-o['start'])<1e-7 and abs(x['end']-o['end'])<1e-7
 assert (x['start'],x['end'])==(y['start'],y['end']);assert x['end']>x['start'];assert re.sub(r'\s+','',x['text'])==re.sub(r'\s+','',o['text'])
 assert len(x['text'].splitlines())<=2 and len(y['text'].splitlines())<=4
for prev,nxt in zip(a,a[1:]):assert nxt['start']>=prev['end']-1e-7
norm=json.loads((W/'caption-normalization.json').read_text());rawsha=hashlib.sha256((W/'youtube.auto.en.vtt').read_bytes()).hexdigest();assert rawsha==norm['raw_sha256']
assert (P/'URL.md').read_text().strip()=='https://www.youtube.com/watch?v=xH7U7w9Qzlo'
qa={'status':'PASS','video_id':'xH7U7w9Qzlo','timeline':'youtube-source','video_duration_sec':3224,'first_cue':ts(a[0]['start']),'last_cue':ts(a[-1]['end']),'source_cues':731,'output_cues':678,'translation_windows':13,'completed_valid_windows':13,'source_ids_in_order_exactly_once':True,'same_timing_zh_bilingual':True,'max_timing_error_vs_selected_source_ms':0,'overlap_count':0,'positive_durations':True,'original_raw_caption_sha256':rawsha,'url_md_sha256':hashlib.sha256((P/'URL.md').read_bytes()).hexdigest(),'raw_caption_unchanged':True,'url_md_unchanged':True,'max_zh_lines':max(len(x['text'].splitlines()) for x in a),'max_bilingual_lines':max(len(x['text'].splitlines()) for x in b),'dropped_source_ids':[x['source_ids'][0] for x in allz if x.get('dropped')],'speech_drops':0,'local_asr_ran':False,'hard_subtitle_ocr_ran':False,'paid_api_used':False,'local_target_video':None,'audio_listening_review':False,'short_cues_under_one_second':[{'source_ids':o['source_ids'],'duration':round(o['end']-o['start'],3),'text':o['text']} for o in z if o['end']-o['start']<1], 'files':{q.name:{'sha256':hashlib.sha256(q.read_bytes()).hexdigest(),'bytes':q.stat().st_size} for q in [zhpath,bipath,D/'source.reviewed.youtube.en.srt']}}
(D/'quality-report.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2));(W/'final-validation.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2))
ledger={'timeline_target':'youtube-source','selection':'English original automatic captions, with bounded corrections corroborated by publisher transcript','items':[{'path':'URL.md','kind':'user supplied source pointer','language':None,'time_basis':'YouTube source','used':True},{'path':'work/youtube.auto.en.vtt','kind':'YouTube automatic captions','language':'en','time_basis':'YouTube source','machine_generated':True,'used':True,'role':'Primary wording and sole timing authority','sha256':rawsha},{'path':'work/youtube.metadata.json','kind':'YouTube metadata and publisher description','language':'en','time_basis':'YouTube chapters only','used':True,'role':'Title, guest names, topic context, video duration, original-language track inspection'},{'path':'work/sequoia.official.txt','kind':'Publisher episode transcript fetched during this task','language':'en','time_basis':'Untimed; no timestamp authority','machine_generated':'unknown; editorial status not asserted','used':True,'url':'https://sequoiacap.com/podcast/rich-sutton-and-khurram-javed-why-ai-models-stop-learning-and-how-to-start-it-again','role':'Bounded correction of auto-caption names, homophones and incomplete phrases'},{'url':'https://www.nature.com/articles/s41586-024-07711-7','kind':'Author coauthored research paper','language':'en','time_basis':'None','used':True,'role':'Terminology check only: continual backpropagation and loss of plasticity; no speech added'},{'url':'https://khurramjaved.com/papers/the_big_world_hypothesis.pdf','kind':'Author hosted paper search result','used':'Title and author corroboration only; full paper not read'}],'absent_at_inventory':['Local MP4/audio','Local audio transcription','OCR SRT (including .orc aliases)','OCR quality report/alignment sidecar','references/ or other author notes'],'manual_youtube_tracks':[],'not_used':['YouTube auto-translated Chinese: prepare attempted it and returned HTTP 429; no usable artifact and no translation copied','Third party translations from search results']}
(W/'evidence-ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2));shutil.copy2(W/'evidence-review.json',D/'evidence-review.json')
report=f'''# 字幕证据使用与质量报告

- 节目：Rich Sutton and Khurram Javed: Why AI Models Stop Learning, and How to Start It Again
- 原视频：[YouTube](https://www.youtube.com/watch?v=xH7U7w9Qzlo)，视频 ID：`xH7U7w9Qzlo`。
- 目标语言：简体中文。最终时间轴：**YouTube 源视频时间轴**。
- 当前交付：`translated.youtube.zh-Hans.srt`、`bilingual.youtube.zh-Hans-en.srt`。双语为中文在上、英文在下。
- 校订后的英语源字幕：`source.reviewed.youtube.en.srt`。

## 证据盘点和实际使用

开始时指定项目只有 `URL.md`。没有本地视频、音频听写、OCR 字幕（包括 `.orc.` 别名）、OCR 质量报告、对齐报告或 `references` 目录。因此没有声称使用这些不存在的证据，也没有借用其他项目的资料。

| 证据 | 来源与作用 | 限制 |
| --- | --- | --- |
| YouTube 自动英语字幕 | 新下载并完整保留于 `../work/youtube.auto.en.vtt`；原声英语轨，无翻译目标参数；全文翻译和时间轴的主依据 | 机器生成，存在误识别和残句 |
| YouTube 元数据、说明及章节 | `../work/youtube.metadata.json`；核对视频标题、嘉宾、节目主题、时长 | 说明不证明逐字对白 |
| 节目方文字稿 | [Sequoia 官方节目页](https://sequoiacap.com/podcast/rich-sutton-and-khurram-javed-why-ai-models-stop-learning-and-how-to-start-it-again)；保存在 `../work/sequoia.official.txt` 及 HTML，辅助校对姓名、同音误识别、少数残句 | 本次补充获取，不是项目原有作者笔记；无时间戳，未假定其人工校订程度 |
| 作者参与的研究论文 | [Loss of plasticity in deep continual learning](https://www.nature.com/articles/s41586-024-07711-7)；仅核对持续反向传播及可塑性相关术语 | 不把论文结论改写成视频对白；论文区分可塑性损失与灾难性遗忘，字幕忠实保留嘉宾的原话 |
| 大世界假说作者页面 | [作者托管的论文](https://khurramjaved.com/papers/the_big_world_hypothesis.pdf)搜索结果只用于标题和作者核对 | 未将其算作已全文阅读的参考文档 |

没有人工英语字幕轨。准备命令首次自动选中了中文翻译轨并遇到 HTTP 429；随后成功下载指定英语自动字幕，未采用机器翻译中文。没有下载媒体，没有运行本地 ASR（环境未安装后端），没有运行硬字幕 OCR，没有调用付费 OpenAI/Google API。

## 文本处理与修订

英语 VTT 是滚动显示字幕。去除了重复显示的旧行和约 10 毫秒的显示过渡块，仅保留新出现的文字。对所有被省略的旧行核查后，**未发现被遗漏的独有文字行**。相邻残片在原始时间边界上整理为 731 条源字幕；标准化映射保存于 `../work/caption-normalization.json`。

翻译由当前 Codex 任务逐段完成，结合全片上下文统一术语、清理口吃重复、保持否定、数字、推测语气和幽默；没有使用第三方现成中文译稿。`../work/translation/` 保留 13 个翻译窗口及 `context.json`。

有据修订包括 Khurram Javed、X post、OpenAI/Anthropic、Cursor Tab autocomplete、Moravec's paradox、Lean，以及 “modern tome”“friction and wear”“I love that”“All our money”等自动识别问题。具体原文、修改后文字、时间与依据见 `evidence-review.json`。原始下载的 VTT 不变。

## 时间轴与排版

所有字幕时间取自 YouTube 原始 VTT 的毫秒边界；没有本地录屏偏移、时间拉伸或录屏时间轴。合并字幕仅取首条起点和末条终点，不跨明确换人标记，不跨翻译窗口，均不超过 8 条源字幕及 15 秒。

发现通用 SRT 格式化函数存在浮点截断导致的 1 毫秒误差，因此交付序列化直接使用原始整数毫秒，并检查中英两份成品逐条一致。相对最终源字幕的时间误差为 **0 毫秒**。这表示精确保留自动字幕时间，并不表示已通过听音验证声学对齐精度。

每条中文字幕最多 2 行，双语最多 4 行。原视频约 53 分 44 秒，字幕覆盖 **{qa['first_cue']}—{qa['last_cue']}**；末尾音乐保留。个别快速插话短于 1 秒，为遵守源时间轴保留原时间，详见 `quality-report.json`。

## 校验结果

- 13/13 翻译窗口完整且有效，731 个源 ID 按顺序各覆盖一次。
- 输出 678 条中文字幕、678 条双语字幕；两者时间逐条一致。
- 无倒序、负时长、零时长或时间重叠；目标窗口中没有自行填写时间戳。
- 相邻同一说话人的句子残片经过合并。删除 3 条极短的独立笑声显示片段（源 ID `c000075`、`c000356`、`c000588`），避免闪烁；**没有删除语音字幕条目**。
- 检查了开头、中段、结尾、姓名和术语、重点数字、全部标记疑点及窗口衔接。
- 原始 `URL.md` 和下载 VTT 校验保持一致。最终文件校验值详见 `quality-report.json`。

## 保留的疑点与范围

1. 约 48:33 的 “similar performance at a higher energy scale” 在自动字幕和官方文字稿中一致，但与周围能效讨论的指代关系不够清楚。保留“更高能耗”的字面含义，没有擅改为“更低能耗”。
2. 约 21:55 关于 “prior knowledge ... should be dismissed” 的表述，按直接复制既有智能体的上下文，译作无需特别操心先验知识，而非删除已有知识。
3. 约 52:34 的 “a handful or two or three” 是口语化的小团队数量，译为几个人、十来个人，不给出精确招聘人数。
4. “bitter lesson pill” 可能是 “bitter-lesson-pilled” 的识别形式；保留吃下这颗药丸的比喻。
5. 没有本地音视频、ASR 或 OCR 可交叉验证，也未进行逐段听音审校。官方文字稿与自动字幕可能共享误识别，故剩余歧义未冒充已证实。嘉宾的事实性判断、年份与预测按其原话保留。

## 原始证据校验

- `URL.md` SHA-256：`{qa['url_md_sha256']}`
- `youtube.auto.en.vtt` SHA-256：`{rawsha}`
- 本地目标视频路径与校验值：不适用；本次目标明确为 YouTube 源视频，项目没有本地视频。

完整清单：`../work/evidence-ledger.json`；修订记录：`evidence-review.json`；可机读质量结果：`quality-report.json`。
'''
(D/'证据使用报告.md').write_text(report,encoding='utf-8')
# Preserve exact output reproduction helper in project working files.
# This file is the persistent renderer.
m=json.loads((J/'job.json').read_text());m.setdefault('import_source_id',m['video_id']);m['video_id']='xH7U7w9Qzlo';m['preferred_delivery_dir']=str(D.resolve());(J/'job.json').write_text(json.dumps(m,ensure_ascii=False,indent=2))
print(json.dumps({k:qa[k] for k in ['status','source_cues','output_cues','first_cue','last_cue','max_timing_error_vs_selected_source_ms','max_zh_lines','max_bilingual_lines','short_cues_under_one_second']},ensure_ascii=False,indent=2))
print(str(D.resolve()))
