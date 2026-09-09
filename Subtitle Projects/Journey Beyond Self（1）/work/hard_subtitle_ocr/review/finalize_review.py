from pathlib import Path
import json,re,hashlib,datetime,copy,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[5]))
from codex_subtitles.recorded_video_ocr import srt_timestamp,recorded_ocr_status
from codex_subtitles.video_frame_service import file_checksum
from codex_subtitles.hard_subtitle_ocr_service import validate_ocr_artifact
ROOT=Path(__file__).resolve().parent.parent;PROJECT=ROOT.parent.parent
JOB=ROOT/'Journey Beyond Self.5554caa86d0b.recovered';EVIDENCE=JOB/'artifacts'
q=json.loads((JOB/'Journey Beyond Self.ocr.en.quality.json').read_text())
blocks=re.split(r'\n\s*\n',Path(q['srt']).read_text().strip());texts={int(b.splitlines()[0]):'\n'.join(b.splitlines()[2:]) for b in blocks}
review=json.loads((ROOT/'review/review-index.json').read_text())
reviewed_ids=sorted({r['srt_index'] for r in review})
flagged_ids=[c['srt_index'] for c in q['cues'] if c['needs_visual_check']]
assert set(flagged_ids)<=set(reviewed_ids)
fixes={9:"There is no 'me' to take it personally and get involved.",10:"There is no 'me' to take it personally and get involved.",22:"with no owner, no self, no 'me' or 'mine' in them.",27:'that all mental fabrications in the present are like a mirage,',60:'True Dhamma is the reality of life—aging, sickness, death,',72:'All things are subject to change and are not self (anattā)',84:'Nibbāna is nothing but Right View.',90:'one cannot realize nibbāna.',133:'manifest "anicca" (impermanent), "dukkha" (suffering), and "anattā" (not self) at all times.'}
merges={9:10,29:30};skip=set(merges.values());out=[];changes=[]
for c0 in q['cues']:
 i=c0['srt_index']
 if i in skip:continue
 c=copy.deepcopy(c0);c['raw_srt_indices']=[i];c['text']=fixes.get(i,texts[i]);c['raw_machine_issues']=c.pop('issues');c['review_status']='visually_checked' if i in reviewed_ids or i in fixes else 'not_individually_viewed'
 c['source_review_images']=[r['image'] for r in review if r['srt_index']==i]
 c['needs_visual_check']=False
 if i in fixes:
  changes.append({'kind':'text_correction_from_visible_frames','raw_srt_index':i,'before':texts[i],'after':fixes[i],'evidence':c['source_review_images'] or ['review/supplemental-1.jpg','review/supplemental-2.jpg']})
 if i in merges:
  nxt=q['cues'][merges[i]-1];c['raw_srt_indices'].append(merges[i]);c['end']=nxt['end'];c['recording_end']=nxt['recording_end'];c['observation_ids']=list(dict.fromkeys(c['observation_ids']+nxt['observation_ids']));c['evidence_images']=list(dict.fromkeys(c['evidence_images']+nxt['evidence_images']));c['raw_machine_issues']=list(dict.fromkeys(c['raw_machine_issues']+nxt['issues']));c['review_status']='visually_checked_and_joined';c['continuous_visibility_evidence']=['review/supplemental-1.jpg','review/supplemental-2.jpg']
  changes.append({'kind':'join_false_ocr_fragmentation','raw_srt_indices':c['raw_srt_indices'],'start':c['start'],'end':c['end'],'reason':'Same subtitle visibly persists through the OCR gap; verified on crops at 25.766667/25.866667 and 108.333333/108.433333/108.533333 seconds.','evidence':['review/supplemental-1.jpg','review/supplemental-2.jpg']})
 c['srt_index']=len(out)+1;out.append(c)
video=json.loads((JOB/'video.json').read_text());duration=video['duration'];last_end=0
for i,c in enumerate(out,1):
 assert c['text'].strip() and c['srt_index']==i
 assert c['start']>=last_end and c['end']>c['start']>=0 and c['end']<=duration
 last_end=c['end']
assert file_checksum(video['path'])==video['checksum']
assert recorded_ocr_status(JOB)['status']=='complete'
index=json.loads((EVIDENCE/'frames.index.json').read_text());ocr=json.loads((EVIDENCE/'ocr.observations.json').read_text());validate_ocr_artifact(ocr,index,EVIDENCE)
assert len(ocr['records'])==len(index['frames']) and all(r['status']=='complete' for r in ocr['records'].values())
srt=PROJECT/'Journey Beyond Self.ocr.en.srt';quality=srt.with_suffix('.quality.json');alignment=srt.with_suffix('.alignment.json')
content='\n'.join(f"{c['srt_index']}\n{srt_timestamp(c['start'])} --> {srt_timestamp(c['end'])}\n{c['text']}\n" for c in out)
with srt.open('x',encoding='utf-8') as f:f.write(content)
quality_data={'schema_version':1,'kind':'visually_reviewed_original_language_ocr_evidence','language':'en','srt':str(srt),'srt_checksum':file_checksum(srt),'timing_basis':'recording-elapsed','target_timing_basis':'youtube-source','source_alignment':'nonlinear_or_unresolved','time_offset':0,'evidence_root':str(EVIDENCE),'ocr_job_dir':str(JOB),'machine_generated':True,'visual_review_by':'Codex image inspection','raw_srt':q['srt'],'raw_quality_report':str(JOB/'Journey Beyond Self.ocr.en.quality.json'),'raw_cue_count':q['cue_count'],'cue_count':len(out),'raw_cues_flagged':flagged_ids,'raw_flagged_cues_visually_checked':len(flagged_ids),'raw_cues_visually_sampled':reviewed_ids,'cues_needing_visual_check':0,'review_scope':'All 18 automatically flagged cues inspected, plus unflagged samples and supplemental frames. This is not a frame-by-frame human transcript audit; machine flags and original observations are preserved.','cues':out,'edits':changes,'frame_count':len(index['frames']),'recovered_frame_count':7,'remaining_failed_frames':0,'validation':{'srt_nonempty':True,'positive_duration_cues':True,'monotonic_nonoverlapping':True,'within_recording_duration':True,'frame_integrity':True,'ocr_coverage_complete':True,'input_video_checksum_unchanged':True,'ocr_video_status':'complete','youtube_alignment_verified':False},'source_typography_preserved':[{'raw_srt_index':51,'text':'One cannot alway get what one wishes for','note':'Visible source uses alway; not silently corrected.'},{'raw_srt_index':94,'text':'both just create moreclouds.','note':'Visible source has no clear space between more and clouds; preserved.'}],'limitations':['Source video and source audio downloads failed with HTTP 403; output uses recording elapsed time.','No three verified source anchors; no offset, speed correction or fabricated source timing applied.','Cue boundaries are sampled estimates (3 fps, 10 fps around detected changes), not verified frame-accurate boundaries.','Original wording and apparent source typos preserved; untranslated.']}
with quality.open('x',encoding='utf-8') as f:json.dump(quality_data,f,ensure_ascii=False,indent=2)
a=json.loads((ROOT/'alignment.json').read_text());a.update(ocr_job_dir=str(JOB),raw_ocr_job_dir=str(ROOT/'Journey Beyond Self.5554caa86d0b'),srt=str(srt),srt_sha256=file_checksum(srt),cue_count=len(out),created_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
with alignment.open('x',encoding='utf-8') as f:json.dump(a,f,ensure_ascii=False,indent=2)
report=PROJECT/'Journey Beyond Self.ocr.en.quality.md'
report_text=f'''# Journey Beyond Self — 英文硬字幕 OCR 质量与时间轴报告

已导出 **{len(out)} 条审校版英文 OCR 字幕**。原始机器合并版为 137 条，完整保留在 OCR 任务目录内。

## 时间轴结论

- 用户目标：YouTube 源视频 `06h3fndh2wA`，元数据时长 634 秒。
- 实际使用：项目录屏 `Journey Beyond Self.mp4`，时长 {duration} 秒。
- 实际时间基准：**recording-elapsed（录屏起点计时）**。
- 源视频及源音频下载均返回 HTTP 403；尝试记录与不完整下载均已保留。
- 源视频对齐状态：**nonlinear_or_unresolved**，具体为缺少可用源媒体及经过验证的锚点。未验证任何时间偏移或播放速度映射，未输出伪造的源时间字幕。时长差异本身不能证明偏移或倍速。

## 处理与校验

- 使用本地 FFmpeg 和 macOS Apple Vision；未调用付费或云端 OCR、ASR、翻译 API。
- 英文区域：x=0、y=0.855、width=1、height=0.075。采样 3 fps，变化区间细化 10 fps。
- 共保存 {len(index['frames'])} 张帧证据。7 张异常帧通过上下添加黑色边距后重识别，其中 1 张确认无字幕；全部帧现均有完整识别结果。
- 原始失败任务、恢复前观察、恢复图像与逐行识别结果均保留。重复启动产生的独立目录也未删除。
- 检查了全部 18 条机器标记字幕；另检查未标记字幕、短句及开头/中段/结尾，核对图共 9 页，另有 2 页补充图。
- 根据画面修正引号、句末逗号、破折号和巴利词长音符；合并两处被误拆的连续字幕（原序号 9/10 与 29/30）。逐项修改及证据见 JSON sidecar。
- 画面自身的 `alway`、`moreclouds` 原样保留，未按语法润色或替换成转写内容。
- 非空、正时长、单调、无重叠、未超出录屏时长、原视频校验值和证据完整性检查均通过；OCR 任务状态检查为 complete。
- **局限**：未逐帧人工核对全部字幕；起止时间是采样估计，非逐帧精确对齐。上述检查不代表已对齐 YouTube。

## 文件

- 审校版字幕：`{srt.name}`
- 逐条质量与修改依据：`{quality.name}`
- 时间轴对齐报告：`{alignment.name}`
- OCR 任务目录：`work/hard_subtitle_ocr/{JOB.name}/`
- 原始机器字幕：`work/hard_subtitle_ocr/{JOB.name}/Journey Beyond Self.ocr.en.srt`
- 核对图：`work/hard_subtitle_ocr/review/`
- 获取源媒体的记录：`work/hard_subtitle_ocr/acquisition-report.json`

原始录屏 SHA-256：`{video['checksum']}`。
'''
with report.open('x',encoding='utf-8') as f:f.write(report_text)
print(json.dumps({'srt':str(srt),'quality':str(quality),'alignment':str(alignment),'readable_report':str(report),'cue_count':len(out),'reviewed_raw_cues':len(reviewed_ids),'frame_count':len(index['frames']),'edits':len(changes),'validation':'passed'},ensure_ascii=False,indent=2))
