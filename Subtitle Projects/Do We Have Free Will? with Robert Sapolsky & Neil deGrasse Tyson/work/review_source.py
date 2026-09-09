from pathlib import Path
import json,re
from codex_subtitles.storage import atomic_write_json,update_manifest
p=Path(__file__).resolve().parent;j=Path((p/'job-path.txt').read_text())
raw=json.loads((p/'source.youtube-normalized.json').read_text())['segments']
source=json.loads((j/'source.json').read_text());changes=[]
fixes={
7:('Neil degrass','Neil deGrasse','节目标题和视频说明确认主持人拼写'),
18:('Brian Green','Brian Greene','哥伦比亚大学物理系官方简介确认人名'),
22:('Robert sapolsky','Robert Sapolsky','节目标题、说明及上一项目人工英语字幕'),
32:('determined a science','Determined: The Science','视频说明确认书名；与后一cue衔接'),
45:('primates Memoir',"Primate’s Memoir",'出版社正式书名；前一cue已有冠词a'),
66:('free fre will','free will','机械重复'),
71:('big empty and','big empty','与下一cue重组被误拆的indifferent；上下文推断'),
72:('different Universe','indifferent Universe','empty indifferent Universe句意，非独立听校；高置信上下文推断'),
110:('senten','sentence','被截断词形'),
112:('preceding National Academy of Sciences','Proceedings of the National Academy of Sciences','PNAS原论文页面确认期刊名称'),
118:('back from more jail','back for more jail','语法与后续含义明确'),
170:('craby','crabby','下文反复使用crabby，明显拼写错误'),
172:('completely crab','completely crabby','下文crabby及广告情境'),
199:('pared','paroled','假释例子及上一项目人工字幕'),
201:('set him back','sent him back','监禁上下文'),
258:('fruit','root','deterministic root，因果来源上下文；推断'),
270:('determin from','determined from','词形修复'),
377:('lateral prefrontal cortex','lateral prefrontal cortex','与前cue dorsal相连，保持原词'),

401:('pepperon','pepperoni','前cue已正确识别pepperoni'),
411:('physiochemical','physicochemical','物理化学术语规范词形'),
413:('in all','vanilla','前后反复对举vanilla/strawberry；上下文明确'),
417:('lullab','lullabies','sing lullabies语法及养育情境'),
429:('reigning','reining','reining in固定用法'),
451:('person who would want vanilla over','person who would want vanilla over','无改动，保留'),
526:('Max tag Mark','Max Tegmark','MIT官方简介确认人名'),
528:("he’s car",'he has calculated','placeholder'),
581:('and it’s','and its','placeholder'),
612:('compliment','compliment','无改动'),
641:('drunker','drunkard','指代醉汉；词形修复'),
650:('brink of suicide','brink of suicide','无改动'),
733:('witchcraft','witchcraft','无改动'),
750:('dyslexia','dyslexia','无改动'),
767:('neurog genetic','neurogenetic','与后cue developmental disorder构成术语'),
798:('quazars','quasars','类星体拼写'),
813:('song for Broadway musicals','songwriter for Broadway musicals','职业问答上下文；补全可确定词形'),
818:('Star Messenger','Starry Messenger','作者官网确认书名'),
856:('bad Boon','baboon','狒狒语境；明显语音误识别'),
886:('pedantry','pedantry','无改动'),
887:('culture and bi','culture and biology','后文明确文化与生物学二分'),
973:('feel s','feel safe','坚强/安全感对比；上一片养育主题与句意支持，未独立听校'),
989:('post Haw','post hoc','下文对事后解释的明确定义'),
995:('post Hawk','post hoc','同一术语'),
1000:('post Haw','post hoc','同一术语'),
1023:('himself','themselves','指奖惩本身的复数反身代词'),
1034:('prestigious is college','prestigious college','误插语音碎片'),
1049:('amazing Sal','amazing salary','与高分、办公室并列的待遇；词形补全'),
1058:('my TV','my CV','履历、SAT、地位、薪资的连续论证；上下文推断，并非独立听校'),
1070:("you’re brain",'your brain','placeholder'),
1147:('they hypothal','their hypothalamus','饱腹激素与下丘脑语境；被截断词形'),
1184:('free myp','free will','全片主题与圣诞老人笑话语境'),
1220:("you're brain",'your brain','代词词形'),
1255:('Robert spolski','Robert Sapolsky','标题、视频说明与同片其他字幕'),
1258:('Neil theg grass Tyson','Neil deGrasse Tyson','标题与说明'),
}
# Literal ASCII spellings actually returned by ASR.
fixes[528]=("he's car",'he has calculated','后面23 orders of magnitude，承接前句calculated；上下文推断')
fixes[581]=("and it's",'and its','代词词形')
fixes[1070]=("you're brain",'your brain','代词词形')
for n,(old,new,reason) in fixes.items():
 if old==new:continue
 c=source['segments'][n-1];before=raw[n-1]['text'];assert old in before,(n,old,before)
 after=before.replace(old,new)
 c['text']=after
 changes.append({'cue_id':c['id'],'original_text':before,'revised_text':after,'evidence_used':reason,'timing_changed':False})
source['source_kind']='youtube_automatic_caption_reviewed'
atomic_write_json(p/'source.reviewed.json',source);atomic_write_json(j/'source.json',source)
update_manifest(j,source_kind='youtube_automatic_caption_reviewed')
uncertain=[
 {'cue_ids':['c000005','c000007','c001260','c001264'],'issue':'ASR在开头音乐附近把shift分到片头后，在结尾把listening放到片尾音乐后。无原始音频独立核验，保留源轨分段，不冒充精确听校；报告标出复核点。'},
 {'cue_ids':['c000067'],'issue':'epipal为不完整识别；中文按上下文译“豁然开朗”，英文原词保留，未猜测具体英语词形。'},
 {'cue_ids':['c000077'],'issue':'ING with your head包含缺失词；中文采用上下文确定的“把脑子搅乱”，不补写具体粗口。'},
 {'cue_ids':['c000126','c000127'],'issue':'统计玩笑与插话重叠，局部ASR破碎；中文保留讨论剩余差异的含义。'},
 {'cue_ids':['c000470'],'issue':'appen for you为ASR破碎片段；根据父母相同/相反的完整论证译“同样没有自由意志”；英文不强造逐字原话。'},
 {'cue_ids':['c000791'],'issue':'vault所指物质不明；中文仅译“用过量”，不擅自指定酒精或某种药。'},
 {'cue_ids':['c001140'],'issue':'ASR说50 years ago，按五十年前保留；未当作已核实医学史。'},
 {'cue_ids':['c000125','c000528','c000662'],'issue':'99%的种族偏差玩笑、23个数量级和70%囚犯贫困经历，保留本片原有数字与语气；不据此断言科学上已独立验证。'}]
atomic_write_json(p/'evidence-review.json',{'timeline_target':'youtube-source','selected_raw_source':'work/evidence/youtube.auto.en-orig.json3','reviewed_source':'work/source.reviewed.json','source_corrections':changes,'source_insertions':[],'uncertainties':uncertain,'new_media_asr_run':False,'ocr_run':False,'paid_api_used':False,'speaker_note':'多人插话依据字幕语义保守分段；不宣称完成声纹识别或逐声轨听校。源事件自身已有混合说话人的，保留并在中文用对话短横区分。'})
print('Source corrections:',len(changes))
