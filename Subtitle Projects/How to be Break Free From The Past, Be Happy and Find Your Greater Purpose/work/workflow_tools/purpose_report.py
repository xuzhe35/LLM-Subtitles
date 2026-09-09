import json,pathlib,hashlib,shutil
p=pathlib.Path('Subtitle Projects/How to be Break Free From The Past, Be Happy and Find Your Greater Purpose').resolve();w=p/'work';out=p/'final';j=pathlib.Path((w/'active-job.txt').read_text());q=json.loads((out/'quality.youtube.json').read_text());r=json.loads((w/'evidence-review.json').read_text());s=json.loads((j/'source.json').read_text())['segments'];by={int(x['id'][1:]):x for x in s}
def ts(sec):
 n=int(sec);return f'{n//3600:02}:{n//60%60:02}:{n%60:02}'
unc=[(36,'“signs of extra sensory abilities”可能是“science of…”的自动识别错误。缺少音频复核，保留“超感官能力的迹象”，不擅自改成“科学”。'),(126,'自动字幕为“more than communities of shared belief”，未补入可能缺失的“no”。中文按现有文本译为“不只是……”，该处比较关系待音频复核。'),(536,'关于光反弹后失去信息的说法，按原话保留。字幕校订不将说话者的科学论断改写成另一种说法。'),(857,'“著名犹太哲学家”的引语归属可能有问题。未补入字幕未出现的人名，也未改写说话者的归属。'),(893,'“Peter / letter to the Corinthians / God is love”这一串人物、书信与引文的关联可疑。无法判断是口误还是自动识别错误，按原字幕保留，不替换为其他人名或经文章节。'),(932,'“decaction”不是清晰词形。根据前后“输入—处理—记忆—输出”的语义，保守解读为“reaction / 反应”；这是上下文修复，未经音频确认。'),(1044,'Michael J. Lincoln 与书名已由作者/出版方网站确认；别名 Narayan Singh 的拼写另参考 Spirit Rising Yoga 的书目页，不视为项目内作者笔记。'),(1166,'字幕数字为“95 … thousand years”，译为“九万五千年”。与前面二十万年的叙述有张力，但不据此猜改为十九万五千年或其他数值。'),(1234,'“光速在小数点后第八或第九位变化四五次”按说话者的原主张保留，没有把它当作已经独立核实的事实。'),(1342,'片尾主题曲中的“or two fiction”被理解为对“一个或两个博士”的玩笑补充，译为“后者是虚构的”。具体歌词和插话归属未经音频复核。')]
r['unresolved_or_context_only']=[{'source_id':by[n]['id'],'time':ts(by[n]['start']),'text':note} for n,note in unc];(w/'evidence-review.json').write_text(json.dumps(r,ensure_ascii=False,indent=2))
ledger={'timeline_target':'youtube-source','preferred_delivery_folder':str(out),'original_project_inventory':[{'path':'URL.md','language':'URL','source_kind':'user-supplied link','time_basis':'YouTube; t=360s is playback context only','sha256':q['sha256']['URL.md']}],'absent_evidence':['local video/audio','local ASR transcript','OCR/ORC subtitle','OCR quality or alignment report','references/ author notes and reference documents'],'used_evidence':[{'path':'work/youtube/9on5PnWPlk4.en-orig.json3','language':'en','source_kind':'YouTube English (Original) automatic captions','machine_generated':True,'time_basis':'youtube-source','use':'primary wording and word-level timing'},{'path':'work/youtube/9on5PnWPlk4.info.json','source_kind':'YouTube metadata; publisher-authored description plus machine-provided fields','machine_generated':'mixed','time_basis':'YouTube duration/chapters, not subtitle replacement','use':'track inventory, title, names, book title and conceptual context'}],'external_reference_urls':r['external_references'],'missing_evidence_recomputed':False,'local_asr_backends':[],'ocr_started':False,'paid_api_used':False}
(w/'evidence-ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2))
report=f'''# 证据使用与字幕质量报告

项目：How to be Break Free From The Past, Be Happy and Find Your Greater Purpose  
交付日期：2026-09-08  
源视频：[Quantum Mechanics Expert: How to be Break Free From The Past, Be Happy & Find Your Greater Purpose](https://www.youtube.com/watch?v=9on5PnWPlk4)  
发布者：Dr. Mayim Bialik；嘉宾：Thomas Campbell。

## 当前交付

本目录 `final/` 中以下文件为当前首选交付，均采用 **YouTube 源视频时间轴**：

- [简体中文字幕](<Break Free From The Past.youtube.zh-Hans.srt>)
- [中英双语字幕](<Break Free From The Past.youtube.zh-Hans-en.srt>)：中文在上，英文在下；英文为经校订的自动字幕。
- [校订后英文源字幕](<Break Free From The Past.youtube.en.reviewed.srt>)
- [机器可读质量与校验和报告](quality.youtube.json)

中文字幕与双语字幕各 **{q['translated_cue_count']} 条**，覆盖 `00:00:00,160–01:46:10,920`。视频元数据显示总时长约 `01:46:11`。完整保留开场摘要、访谈、赞助口播、下期预告与片尾，没有按链接中的 6 分钟位置截断。

## 实际发现和使用的证据

开始处理时，项目目录**只有 `URL.md` 一个文件**。并不存在本地音视频、听写文本、OCR/ORC 字幕、质量/对齐报告或 `references/` 目录中的作者资料，因此无法声称使用了这些未提供的证据。

| 证据 | 是否存在/使用 | 性质与用途 | 时间依据 |
|---|---|---|---|
| 原有 `URL.md` | 存在，已使用 | 提取并验证唯一视频 ID `9on5PnWPlk4` | `t=360s` 仅表示播放入口 |
| YouTube 英语原始自动字幕 | 本次获取，主要证据 | `en-orig / English (Original)`；原始 JSON3 保留于 `work/youtube/` | 原视频词级时间 |
| YouTube 人工字幕 | 未发现 | 元数据 `subtitles` 为空 | 无 |
| YouTube 视频说明与元数据 | 本次获取，已使用 | 发布者说明用于人名、书名、访谈主题；轨道列表与时长用于来源核验 | 只以原字幕计时，不用章节覆盖字幕时间 |
| 本地音频听写 | 未提供，未运行 | 环境检查未发现本地 ASR 后端；已有英语字幕足以继续 | 无 |
| 现成 OCR/ORC 字幕及质量报告 | 未提供，未使用 | **未启动任何硬字幕 OCR、检测或抽帧** | 无 |
| `references/` 作者笔记、文档 | 未提供 | 未虚构或借用其他项目的参考文档 | 无 |
| 外部名称/书名核对页面 | 按需查阅，辅助使用 | 仅校订名称、品牌及术语，不补写视频未说的内容 | 不提供时间 |

获取过程：仓库默认 `prepare` 一度优先选择了自动翻译的 `zh-Hans` 轨道，并遇到 HTTP 429；没有使用该轨道翻译。随后明确请求原始英语 `en-orig`，成功取得完整文件。另一个英语轨道 `en` 的重复下载遇到 429，但不影响已经成功取得的 `en-orig`。最终主证据始终是原始英语自动字幕，不是 YouTube 自动汉译。

原始文件保留：`work/youtube/9on5PnWPlk4.en-orig.json3`、`work/youtube/9on5PnWPlk4.info.json`。早期失败的准备任务仅作为过程记录保留；当前翻译任务由 `work/active-job.txt` 指向。

## 时间轴与来源保护

1. 原始 JSON3 的显示事件存在滚动字幕重叠，直接按事件持续时间导出会产生重叠。提取每个词的 `tStartMs + tOffsetMs`，依句末和阅读长度整理成 **1344 个源片段**。片段结束以随后词的原始起点及原事件结束界限确定。该步骤使用 YouTube 原时间，不做录屏偏移或伸缩。
2. 每个源片段与原 JSON3 事件/词片段的对应关系存于 `work/source-word-provenance.json`。已验证规范化前后所有非空字幕文本均有对应，没有因滚动字幕整理而丢失内容。
3. 在翻译中，目标窗口只保存 `source_ids` 和译文，没有手写时间。校订英文时也未改变任何规范化源片段的起止时间。
4. 为避免过短闪现，合并 **13 组**经文本上下文确认属于同一说话人的相邻片段，合计减少 18 条显示字幕；每组不超过 8 个源片段、15 秒。没有跨说话人合并。
5. 首选交付按原数据的整数毫秒重新格式化时间，修正通用导出器浮点截断可能产生的 1 毫秒误差。中文字幕、双语字幕的每一条起止时间完全一致。
6. 没有本地视频，所以本地视频路径与 SHA-256 为 `null`，不伪称对某个本地媒体做了验证。源字幕、元数据、原始链接及最终字幕的 SHA-256 已写入 `quality.youtube.json`。原有 `URL.md` 的字节内容保持不变。

## 英文校订、中文风格及术语

对 **55 个英文源片段**进行了有记录的识别/拼写校订，例如 Bohr、Schrödinger、Dennis Mennerich、Bob Monroe、Elizabeth Krohn、C. W. Leadbeater、Reiki、savants、Kabbalah、Descartes、UnKibble，以及节目名、品牌名与错拼网址。逐条原文、修订文、依据和原因见 [evidence-review.json](../work/evidence-review.json)。未对英文全文进行凭空润写；口语重复和部分不完整句仍能在双语英文行中看到。

中文以自然口语和完整语义为优先，处理填充词与机械重复，保持人名、数值、否定、条件和原话的不确定性。访谈中的个人理论、超常现象及健康相关陈述按说话者原意呈现；没有把翻译做成另一个立场的改写。广告折扣按中文习惯表达：40% off→六折、20% off→八折、50% off→五折、25% off→七五折；片内优惠未验证当前是否有效。

| 英文 | 本片统一译法 |
|---|---|
| consciousness / awareness | 意识 / 觉知 |
| larger consciousness system | 更大的意识系统 |
| Theory of Everything / My Big TOE | 万物理论 / 《我的大万物理论》 |
| virtual reality / data stream | 虚拟现实 / 数据流 |
| remote viewing / intuition | 遥视 / 直觉 |
| intent / intellect | 意图 / 理性思维（按句意调整） |
| entropy / lower entropy | 熵 / 降低熵、减熵 |
| individuated unit of consciousness | 个体化意识单元 |
| avatar / belief trap | 化身 / 信念陷阱 |
| Reiki / Kabbalah | 灵气疗法 / 卡巴拉 |

中文书名为本次便于理解的译名，不声称采用了作者授权的中文版名称。“TOE”与“大脚趾”的文字游戏在相关笑话处保留。

辅助核对的公开资料及范围：

- [Thomas Campbell 本人官网简介](https://www.my-big-toe.com/about/tom-campbell/)：核对 Bob Monroe、Dennis Mennerich 及 My Big TOE 相关身份与术语。
- [节目发布者关于 Elizabeth Krohn 的文章](https://bialikbreakdown.substack.com/p/mayims-monday-motivation-41c)：核对受访者姓名拼写。
- [Theosophical Publishing House 书目](https://www.ts-adyar.org/book/man-visible-and-invisible-hc)：核对 C. W. Leadbeater 与 Man Visible and Invisible。
- [Michael J. Lincoln 作者/出版方书页](https://talkinghearts.net/products/messages-from-the-body)：核对作者和 Messages from the Body；[补充书目](https://spiritrisingyoga.org/blog/books-by-michael-jlincoln)用于别名拼写。
- [Spot & Tango 官方产品页](https://www.spotandtango.com/unkibble?pc=1)：核对 UnKibble、品牌和官方域名，不据此增加广告内容。
- [《创世记》1:2 原文资料](https://www.sefaria.org/Genesis.1.2?lang=bi)：辅助识别 Tohu va-vohu，译为“空虚混沌”。

以上是本次补充的名称与术语参考，不是项目原有作者笔记，也不构成对访谈所有论断的事实认证。

## 保留的疑点

下表采用稳定的**源片段 ID**，合并后与最终 SRT 序号不同，时间可直接用于定位。

| 源片段与时间 | 处理与限制 |
|---|---|
'''
for n,note in unc:report+=f'| `{by[n]["id"]}` · {ts(by[n]["start"])} | {note} |\n'
report+='''
未下载或收听音频进行逐词复核，因此这些疑点没有被包装成已确认的修正；自动字幕原生计时也不等同于经过听音或画面逐帧校准。

## 最终检查

- 14/14 翻译窗口有效，待完成与无效窗口均为 0。
- 1344 个源 ID 全部按顺序、恰好覆盖一次，零漏译、零故意删除。
- 两套交付字幕均 1326 条，起止时间逐条一致；所有持续时间为正，时间单调且无重叠。
- 中文显示每条最多 2 行；英文按词边界换行，网址和英文标识符不拆开。
- 汉字阅读速度最高约 8.92 字/秒。该指标只统计汉字，不代表包含网址与英语的总阅读负担；双语同时阅读会更密集。
- 保留 1 条约 0.961 秒的简短应答，因为合并会混淆说话者衔接。没有为了拉长显示而改动原视频时间。
- 完成全片逐段翻译及重点语义复查，检查开头、中段、结尾、分段衔接、数字、否定和名称争议。没有宣称进行逐句音频校听或逐帧视觉验收。
- 未运行硬字幕 OCR；未运行本地 ASR；未调用付费 OpenAI/Google API，也未读取相应密钥。

可追溯文件：[证据清单](../work/evidence-ledger.json)、[逐条校订记录](../work/evidence-review.json)、[词级来源对应](../work/source-word-provenance.json)、[质量及校验和](quality.youtube.json)。
'''
(out/'证据使用报告.md').write_text(report)
(p/'DELIVERY.md').write_text('# 当前字幕交付\n\n本项目当前首选字幕采用 **YouTube 源视频时间轴**，适用于视频 `9on5PnWPlk4`，完整覆盖开头至片尾。\n\n- [简体中文字幕](<final/Break Free From The Past.youtube.zh-Hans.srt>)\n- [中英双语字幕](<final/Break Free From The Past.youtube.zh-Hans-en.srt>)\n- [校订后英文源字幕](<final/Break Free From The Past.youtube.en.reviewed.srt>)\n- [证据使用报告](final/证据使用报告.md)\n\n项目最初只有 URL.md；未提供本地视频、ASR、OCR 或 references 文档。本次使用原始英语自动字幕、发布者元数据及少量公开名称核对资料。详情与疑点见报告。\n')
# Keep deterministic task-local processing recipes and authored batches with resumable windows.
tools=w/'workflow_tools';tools.mkdir(exist_ok=True)
for name in ['build_purpose_source.py','purpose_batch.py','purpose_context.py','purpose_polish.py','purpose_deliver.py','purpose_report.py']:
 shutil.copy2(pathlib.Path('/tmp')/name,tools/name)
print('report',out/'证据使用报告.md');print('deliveries',[(f.name,f.stat().st_size) for f in out.iterdir()])
