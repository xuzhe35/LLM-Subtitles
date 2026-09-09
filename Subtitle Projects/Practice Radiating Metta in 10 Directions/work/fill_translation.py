from pathlib import Path
import json
from codex_subtitles.translation_service import validate_window

W=Path(__file__).resolve().parent
J=next((W/'codex_native').iterdir())
def dump(p,d): p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
context=json.loads((J/'context.json').read_text())
terms=[('เมตตา / mettā','慈心','全文'),('แผ่เมตตา','散发慈心','全文'),('เมตตาอัปปมัญญา','慈无量心','结尾总结'),('นิพพาน / Nibbāna','涅槃','保留暂时离心与断尽执著的区别'),('ฌาน / jhāna','禅那','入定语境'),('น้ำอมฤต','甘露','修行体验中的清凉水流比喻'),('พระพุทธ พระธรรม พระสงฆ์','佛、法、僧','三宝'),('หลวงตา / Luangta','隆达','尊称，不等同于祖父'),('กิเลสตัณหา','烦恼、渴爱','内心烦恼语境'),('อนันตจักรวาล','无边宇宙','范围无量，不解释为天文学事实'),('พระสัทธรรม','正法','弘扬佛法'),('จิตบริสุทธิ์','清净心','优先泰语บริสุทธิ์，英文平静心仅作辅助')]
context.update(summary='讲述由忆念三宝开始，将慈心从亲爱的人扩展到一切众生，再从前后左右、八方到上下十方。持续练习使慈心自然流露，逐渐消融自私和我执。保留讲者对暂时离心和究竟涅槃的区分。',speakers=[{'role':'讲法者，以隆达自称或被称呼','channel':'Luangta Narongsak Kheenalayo','identity_confidence':'频道名已核对；各声音身份未逐一确认'},{'role':'简短应答者','identity':'未确认'}],terminology=[{'source':a,'target':b,'note':c,'provenance':['YouTube 泰语原始自动字幕','本地英文 OCR 与已存截图'],'scope':c,'confidence':'high for terminology; transliteration is editorial choice'} for a,b,c in terms],style={'target_language':'简体中文','register':'自然、平实、适合佛法讲解的口语字幕','preserve_names_numbers':True,'bilingual_languages':['th','zh-CN'],'uncertainty':'两处无法可靠恢复的泰语原词保留 [ข้อความไม่ชัด] 标记。中文据可见英文字幕保守翻译。'},evidence_policy={'author_reference_documents':'未找到项目 references/reference/notes/evidence 目录及作者参考文档，未虚构作者偏好','local_asr':'无现成听写；doctor 显示无支持的本地后端；未运行','youtube_zh':'自动机器翻译，已全篇对照，不能当作独立人工证据','timing':'YouTube 原始泰语字幕，不使用录屏时间或 URL t=5s 偏移','speaker_boundaries':'保守采用 >> 说话轮次标记，未将标签当作已验证身份'})
dump(J/'context.json',context)
groups=[]
for line in (W/'translation.zh-CN.txt').read_text().splitlines():
    r,t=line.split('|',1);v=list(map(int,r.split('-')));a,b=v[0],v[-1]
    groups.append({'source_ids':[f'c{i+1:06d}' for i in range(a,b+1)],'text':t})
assert [x for g in groups for x in g['source_ids']]==[f'c{i+1:06d}' for i in range(178)]
index=json.loads((J/'windows/index.json').read_text())
for entry in index['windows']:
    ids=set(entry['core_ids']);target=json.loads((J/'windows'/entry['target']).read_text());target['cues']=[]
    for g in groups:
        if ids.intersection(g['source_ids']):
            assert set(g['source_ids'])<=ids, g
            target['cues'].append(g)
    dump(J/'windows'/entry['target'],target)
    validate_window(J,entry)
    print(entry['window_id'],'valid',len(target['cues']))
