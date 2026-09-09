from pathlib import Path
import json,copy,sys,hashlib,re
sys.path.insert(0,str(Path(__file__).resolve().parents[5]))
from codex_subtitles.recorded_video_ocr import export_ocr_srt,recorded_ocr_status
from codex_subtitles.storage import stable_id
from codex_subtitles.video_frame_service import file_checksum
ROOT=Path(__file__).resolve().parent.parent
PROJECT=ROOT.parent.parent
JOB=ROOT/'Journey Beyond Self(2).1984193ab268.recovered'
EV=JOB/'artifacts'
raw=json.loads((EV/'ocr.cues.json').read_text())
timeline=copy.deepcopy(raw)
obs=json.loads((EV/'ocr.observations.json').read_text())
frames=json.loads((EV/'frames.index.json').read_text())
anchors=json.loads((ROOT/'alignment.verified-anchors.json').read_text())
review=json.loads((ROOT/'review/review-index.json').read_text())
visual_ids={r['srt_index'] for r in review}|{122,134,144,78,138}
changes=[]
def change(n,text=None,start=None):
 c=timeline['cues'][n-1];before=copy.deepcopy(c)
 if text is not None:c['text']=text
 if start is not None:
  c['start']=start
  c['observation_ids']=list(dict.fromkeys(c['observation_ids']+[o['observation_id'] for r in obs['records'].values() for o in r['observations'] if start<=o['timestamp']<before['start'] and o['text']]))
 changes.append({'raw_srt_indices':[n],'before':{'text':before['text'],'start':before['start'],'end':before['end']},'after':{'text':c['text'],'start':c['start'],'end':c['end']},'basis':'Visual inspection of preserved recording frame crops; no translation or ASR.'})
change(37,text='Even what we think is "us,"')
change(50,start=136.633333)
change(122,text='There must be death,')
change(134,text='empty of "me" and "mine."')
change(144,text='It is directly known for oneself.')
c15,c16=timeline['cues'][14:16]
changes.append({'raw_srt_indices':[15,16],'before_texts':[c15['text'],c16['text']],'after_text':c15['text'],'recording_start':c15['start'],'recording_end':c16['end'],'basis':'Same subtitle continuously visible at 43.200, 43.333, 43.433, 43.533 and 43.767 seconds. Correct denyia to deny a and merge the false split.'})
c15['end']=c16['end'];c15['observation_ids']+=c16['observation_ids']
for n,c in enumerate(timeline['cues'],1):
 c['raw_srt_indices']=[15,16] if n==15 else [n]
 if n in visual_ids:c['review_status']='visually_verified'
timeline['cues'].pop(15)
timeline['review_changes']=changes
timeline['fingerprint']=stable_id(json.dumps(timeline,sort_keys=True),32)
reviewed_timeline=ROOT/'review/ocr.cues.visually-reviewed.json'
if reviewed_timeline.exists():assert json.loads(reviewed_timeline.read_text())==timeline
else:reviewed_timeline.write_text(json.dumps(timeline,ensure_ascii=False,indent=2))
output=PROJECT/'Journey Beyond Self(2).ocr.en.youtube.srt';assert not output.exists()
export_timeline=copy.deepcopy(timeline)
obs_times={o['observation_id']:o['timestamp'] for r in obs['records'].values() for o in r['observations']}
for c in export_timeline['cues']:
 c.pop('raw_srt_indices',None)
 c['observation_ids'].sort(key=lambda x:obs_times[x])
 if c['review_status']=='visually_verified':c['review_status']='accepted'
result=export_ocr_srt(export_timeline,frames,obs,EV,output,language='en',time_offset=anchors['offset_seconds'])
qp=Path(result['quality_report']);q=json.loads(qp.read_text())
for c,t in zip(q['cues'],timeline['cues']):
 c['raw_srt_indices']=t['raw_srt_indices'];c['automated_needs_visual_check']=c['needs_visual_check'];c['visual_review_status']=t['review_status']
 if t['review_status']=='visually_verified':c['needs_visual_check']=False
q.update(timing_basis='youtube-source-via-verified-offset',kind='visually_reviewed_original_language_ocr',machine_generated=True,visual_corrections_applied=True,cues_needing_visual_check=sum(c['needs_visual_check'] for c in q['cues']),automated_raw_flagged_cues=32,raw_cue_count=146,visually_reviewed_raw_cue_count=len(visual_ids),review_changes=changes,raw_recording_srt=str(next(JOB.glob('*.srt'))),raw_quality_report=str(next(JOB.glob('*.quality.json'))),ocr_job_dir=str(JOB),review_index=str(ROOT/'review/review-index.json'),frame_count=len(frames['frames']),failed_frames_before_recovery=19,failed_frames_after_recovery=0,alignment_report=str(PROJECT/'Journey Beyond Self(2).ocr.alignment.json'),limitations=['Visual OCR, not an independently transcribed reference; unflagged cues were sampled, not all visually checked.','SRT boundaries come from 3 fps sampling with 10 fps refinement. Three-anchor residual measures global offset consistency, not every individual cue boundary.','Location labels and centered end credits are outside the English spoken-subtitle region.','Source video could not be downloaded (HTTP 403); alignment used direct browser playback of the original YouTube video.'])
blocks=re.split(r'\n\s*\n',output.read_text().strip());last=0
for n,b in enumerate(blocks,1):
 lines=b.splitlines();assert int(lines[0])==n and lines[2:]
 times=[]
 for ts in lines[1].split(' --> '):
  h,m,s,ms=map(int,re.split('[:,]',ts));times.append(h*3600+m*60+s+ms/1000)
 assert 0<=times[0]<times[1]<=anchors['source_duration_seconds'] and times[0]>=last;last=times[1]
q['validation']={'srt_cues':len(blocks),'nonempty':True,'positive_duration':True,'monotonic':True,'no_overlap':True,'within_source_duration':True,'checksum_matches':file_checksum(output)==q['srt_checksum']}
video=json.loads((JOB/'video.json').read_text());assert file_checksum(video['path'])==video['checksum']
q['validation']['input_video_checksum_unchanged']=True
qp.write_text(json.dumps(q,ensure_ascii=False,indent=2)+'\n')
a=copy.deepcopy(anchors);a.update(schema_version=1,timing_basis='youtube-source-via-verified-offset',source_alignment='verified_constant_offset',status='aligned',equation='youtube_seconds = recording_elapsed_seconds - 1.476107',selected_ocr_media=video,source_acquisition='Video format 137 and audio format 139 download attempts returned HTTP 403; partial downloads retained. Browser source playback available for visual anchors.',raw_recording_srt=q['raw_recording_srt'],aligned_srt=str(output),supersedes=str(ROOT/'alignment.initial.json'),recording_anchor_montage=str(ROOT/'review/alignment-recording-anchors.jpg'),recording_mapped_source_interval_seconds=[0,video['duration']+a['offset_seconds']],coverage_note='Recording reaches approximately source 444.657 seconds. Source lasts 451.581 seconds; the remaining tail is not OCR-covered. A source frame at 446.404 seconds was visually checked and shows a centered location/date credit card, with no bottom-band spoken English subtitle. No cues were fabricated for unrecorded tail.',uncertainty_note='Three global anchors agree within 0.013 seconds. Transition brackets are 0.033–0.100 seconds on the recording and 0.040–0.080 seconds on source; this is not a per-cue frame-accuracy guarantee.')
(PROJECT/'Journey Beyond Self(2).ocr.alignment.json').write_text(json.dumps(a,ensure_ascii=False,indent=2)+'\n')
report=PROJECT/'Journey Beyond Self(2).ocr.report.md';assert not report.exists()
report.write_text(f'''# Journey Beyond Self (2) 英文硬字幕 OCR 报告

交付字幕：`{output.name}`，共 145 条。时间轴为 **YouTube 源视频，经三处锚点验证的固定偏移**。

## 来源与证据

- 源视频：https://www.youtube.com/watch?v=-UlYcKRfX1I
- 优先下载源视频及用于对齐的音轨，均遇到 HTTP 403；部分下载、元数据和失败记录已保留。
- 使用项目录屏 MP4 执行本地 Apple Vision OCR，未使用云 OCR、语音转写或翻译。
- 扫描 5,287 帧，19 个失败帧通过上下黑色补边后本地重试成功；原始失败记录、补边图和原始识别结果均保留。
- 原始录屏时间轴 OCR 共 146 条，保存在 `{JOB.relative_to(PROJECT)}`；未覆盖。

## 质量复核

已检查全部 32 条自动标记字幕，并抽查开头、中段、结尾及其他字幕，共复核 {len(visual_ids)} 个原始字幕条目。14 张复核图、附加复核图与锚点图保存在 `work/hard_subtitle_ocr/review/`。

交付版对照画面修正：合并原第 15、16 条的错误分段并修复 `denyia`；补全第 37 条引号、第 134 条句号；补第 122、144 条空格；根据连续可见画面将第 50 条录屏起点从 137.200 秒修正为 136.633 秒。完整更改及原条目映射见质量 JSON。原始 OCR 文本和时间轴未被改写。

结构检查通过：145 条均非空、时长为正、顺序正确、没有重叠、未超过源视频时长；输入视频校验和未变。自动标记已复核，剩余未解决标记为 {q['cues_needing_visual_check']}。这不是逐字人工校对全片的准确率保证。

## 时间轴对齐

采用浏览器直接播放 URL.md 指定的 YouTube 原视频，以暂停后加载稳定的帧核对字幕切换点，读取播放器真实时间。开头、中段、末段三个锚点的偏移中位数为 **−1.476107 秒**，最大残差 **{a['max_absolute_residual_seconds']:.6f} 秒**，通过 0.35 秒阈值。

| 锚点 | 录屏切换中点（秒） | 源视频切换中点（秒） | 偏移（秒） |
|---|---:|---:|---:|
'''+''.join(f"| {x['text']} | {x['recording_time']:.6f} | {x['source_time']:.6f} | {x['offset_seconds']:.6f} |\n" for x in a['anchors'])+f'''
时间公式：`YouTube 时间 = 录屏时间 − 1.476107 秒`。详细边界区间和残差见 `{PROJECT.name}.ocr.alignment.json`。

录屏对应源视频约至 444.657 秒，源片长 451.581 秒；未录到的最后约 6.924 秒没有补造字幕。源视频 446.404 秒抽查为居中地点和日期片尾卡。地点标签、日期及泰文片尾不是本次底部英语讲解字幕的提取范围。整体偏移验证不等同于每条字幕都达到逐帧精度。

## 文件

- 英文交付 SRT：`{output.name}`
- 质量与逐条证据：`{qp.name}`
- 对齐报告：`Journey Beyond Self(2).ocr.alignment.json`
- 原始 OCR 工作目录：`{JOB.relative_to(PROJECT)}`
''',encoding='utf-8')
status=recorded_ocr_status(JOB);assert status['status']=='complete',status
(ROOT/'review/final-validation.json').write_text(json.dumps({'job_status':status,'output_validation':q['validation'],'input_checksum':video['checksum'],'outputs':[str(output),str(qp),str(PROJECT/'Journey Beyond Self(2).ocr.alignment.json'),str(report)]},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'outputs':[str(output),str(qp),str(report)],'cue_count':len(blocks),'remaining_review_flags':q['cues_needing_visual_check'],'status':status['status']},ensure_ascii=False,indent=2))
