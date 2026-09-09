from pathlib import Path
import json, hashlib, re
from codex_subtitles.source_service import load_subtitle
from utils.subtitle_formatter import generate_srt

P = Path(__file__).resolve().parent.parent
W = P / 'work'
ROOT = P.parent.parent
def dump(p, d):
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + '\n')

raw = json.loads((W/'youtube.th.normalized.json').read_text())['segments']
# Keys correspond to the chronological speech fragments after the opening acknowledgement.
# These are textual repairs, not a claim of independent audio transcription.
repairs = {
7: ('พิพาทกัน ถ้าเค้าไป[ข้อความไม่ชัด]กันเองนอกศาล', 'OCR 5 establishes settling a dispute outside court; exact Thai verb is unresolved and is visibly marked.'),
9: ('โมโหกัน พอศาลเป็นประธานปุ๊บ ก็มาฆ่าตีกันไม่ได้', 'OCR 6–7 and following Thai ไม่ได้ establish the negation; automatic caption garbled the court clause.'),
10: ('ก็ต้องยอมฟังเหตุผลซึ่งกันและกัน ก็ไกล่', 'OCR 8–9; restore split word ไกล่เกลี่ย.'),
11: ('เกลี่ยลงไปหลายเรื่องเหมือนกัน ทำนองเดียว', 'OCR 9; restore split word ไกล่เกลี่ย.'),
12: ('กัน ต้องมีพระพุทธเจ้ามาเป็นประธาน พระ', 'OCR 10; remove recognition contamination ทาน.'),
18: ('ออกไปทั่วอนันตจักรวาลทุกทิศทุกทางเลย อัน', 'Repeated Thai อนันตจักรวาล and OCR 15.'),
20: ('ตาว่ารองจากนิพพาน เราเป็นเด็กดีที่สน', 'OCR 16 verifies second only to Nibbāna, not trying Nibbāna.'),
30: ('แผ่ความดี แผ่บุญกุศลให้แก่สรรพสัตว์ทั้ง', 'OCR 25 and repeated term สรรพสัตว์.'),
31: ('หลาย ไม่เลือกที่รักมักที่ชัง ทุกทิศทุก', 'OCR 26 and Thai idiom for no preference or aversion.'),
32: ('ทาง ไม่มีที่สุด ไม่มีประมาณ', 'OCR 27 and repeated formula ไม่มีที่สุดไม่มีประมาณ.'),
33: ('ไอ้ความเก็บกด เรื่องราวใดๆ ทั้งหมด เรื่อง', 'OCR 28; suppressed feelings, not rules or secrets.'),
37: ('[ข้อความไม่ชัด] แต่มันออกไปจากใจ ออกไปจากใจ', 'The beginning of this Thai fragment remains unrecoverable from captions alone. OCR 31 supports Chinese meaning without claiming exact Thai wording.'),
40: ('ถึงบอกว่าเมตตารองแค่นิพพาน เพราะว่า', 'OCR 33; รอง, second only to, supported by earlier statement.'),
44: ('เลยเหมือนนิพพานที่จำลองไว้ แต่เมื่อ', 'OCR 37 and Thai จำลอง: comparison, not actual attainment or a band.'),
49: ('เลยไม่กลับมารวมศูนย์ในใจ มันก็เลยไม่มี', 'OCR 41; ใจ, heart/mind, not a place name.'),
66: ('อนันตจักรวาล', 'OCR 54 and repeated Thai term; do not add OCR-only beings in hell to Thai speech.'),
67: ('แผ่ให้ทุกทิศทุกทาง ไม่มีที่สุด ไม่มีประมาณ ไม่', 'OCR 55 and repeated unlimited formula.'),
68: ('เลือกที่รักมักที่ชัง แต่ตอนแผ่ใหม่ๆ เนี่ย', 'OCR 55–56; restore Thai idiom.'),
72: ('เป็นแบบนี้เว้ย มันร้ายกาจขนาดนี้ คนเดียว', 'OCR 58–62 and Thai adjective spelling.'),
93: ('เอ้ย มันเริ่มเหมือนมีอะไรไหลริน เหมือนน้ำ', 'OCR 85; restore ไหลริน.'),
95: ('น้ำอมฤตที่มันเย็นออกไปจากใจ ไหลรินๆ ออกไป', 'OCR 87; น้ำอมฤต = cool nectar, not counting.'),
97: ('ที่เรารักจริงๆ เลย ทุกคน ทุกชีวิต ทุกดวงจิต', 'OCR 88–89 and following วิญญาณ; restore repeated all-beings phrase.'),
100: ('ถึงอนันตจักรวาล ทะลุไปเลยจนไม่มีขอบเขต', 'OCR 90; repeated cosmological term.'),
101: ('เลย ตอนนี้พอได้ทิศหนึ่ง ง่ายแล้ว ตอนนี้ทำ', 'OCR 91; mechanical recognition repairs.'),
105: ('ให้มันไปให้ได้ ข้างหลังน่ะ ไปสู่อนันต', 'OCR 95; split อนันตจักรวาล.'),
106: ('จักรวาลให้ได้ ค่อยๆ ไล่ไปตั้งแต่ตัวเรา', 'OCR 95–96; continuation of อนันตจักรวาล.'),
113: ('ไง เพื่อเป็นสี่ทิศ ตอนนี้พอได้อนันตจักรวาลสี่', 'OCR 101–102; restore four directions and boundless universe.'),
114: ('ทิศ ต่อไปแปดทิศง่ายแล้ว แปดทิศก็เฉียงไปแปด', 'OCR 102–103; preserve four to eight directions.'),
116: ('เบื้องบนไปสู่อนันตจักรวาล เบื้องล่างไปสู่', 'OCR 103–104; above and below.'),
118: ('ทิศไปสู่อนันตจักรวาลนะ พอมันเหมือนน้ำไหล', 'OCR 105–106.'),
122: ('ทั้งวันเป็นคำบริกรรมเลย มันออกจากใจที่', 'OCR 108–109; remove trailing recognition noise.'),
123: ('มันกักไว้ในเขื่อนของใจเนี่ย มันแตก', 'OCR 109–110; dam of the heart.'),
125: ('มันออกจนตัวเนี่ย กายกับใจเนี่ยมันแห้ง', 'OCR 111–112; preserve unusual dry-body-and-heart metaphor.'),
132: ('มองเห็นอย่างแค่แม่ค้า เขาหาบของขาย', 'Thai caption and OCR 119; repair missing letters.'),
133: ('ข้ามถนนเนี่ย ก็กลัวเขาโดนรถชน ก็บอกให้', 'Thai source explicitly says crossing road and fear of vehicle collision, omitted from English OCR.'),
134: ('แคล้วคลาด แคล้วคลาด แคล้วคลาด ขายดี ขายหมด ขาย', 'OCR 120–122 and Thai protective wish; not a nonsensical repeated proper name.'),
136: ('ก็แผ่เมตตาไปอย่างนี้ มันก็แผ่ๆๆๆ', 'OCR 123–127 and repeated Thai แผ่เมตตา; not transforming seeds.'),
146: ('พุทธานุภาเวนะ ธัมมานุภาเวนะ สังฆานุภาเวนะ', 'OCR 136–138 and audible-language caption formula invokes Buddha, Dhamma, Sangha; standardized Thai spelling of Pali phrase.'),
155: ('เมตตา ไม่มีตัวตนของผู้เผยแผ่พระสัทธรรม', 'OCR 146–147; พระสัทธรรม orthography.'),
158: ('ที่สุด ไม่มีประมาณ มันจึงพ้นทุกข์ง่าย แล้วก็เข้า', 'OCR 148–149 and repeated boundless formula.'),
160: ('ของเมตตาอัปปมัญญา การถ่ายทอดพระสัทธรรม', 'OCR 150–151; metta appamaññā and true Dhamma.'),
168: ('ทุกข์เสมอภาคกันทั้งหมดทั่วอนันตจักรวาล ทำให้', 'OCR 157 and Thai context: equally free from suffering, not equal suffering.'),
}
review=[]; fused=[]
for i,c in enumerate(raw):
    t=c['text'].removeprefix('>>').strip()
    if i in repairs:
        revised,reason=repairs[i]
        review.append({'source_id':f'c{i+1:06d}','original_text':c['text'],'revised_text':revised,'evidence_used':['youtube.th.vtt',reason.split(';')[0]],'reason':reason,'audio_verified':False})
        t=revised
    fused.append({'start':c['start'],'end':c['end'],'text':t})
dump(W/'source.fused.json',{'segments':fused,'language':'th','timing_basis':'youtube-source'})
generate_srt(fused,str(W/'source.fused.th.srt'))
qpath=next((ROOT/'output/recorded_subtitles').glob('Practice*/*.quality.json'))
q=json.loads(qpath.read_text());ocrpath=next(P.glob('*.ocr.*.srt'))
assert hashlib.sha256(ocrpath.read_bytes()).hexdigest()==q['srt_checksum']
ocr=load_subtitle(ocrpath)
for i,c in enumerate(ocr):
    assert c['end']>c['start'] and (i==0 or c['start']>=ocr[i-1]['end'])
dump(W/'evidence-review.json',{'timing_basis':'youtube-source','source_text_repairs':review,'original_speech_fragments':len(raw),'source_timestamps_changed':False,'ocr_quality_checksum_matches':True,'visual_review_count':55,'flagged_ocr_reviewed':51,'ocr_policy':'Visible English is a translation. Align semantically in chronological windows, never transfer recording timestamps. No recording-to-source offset fitted.','unresolved_thai_source_ids':['c000008','c000038'],'speech_policy':'Translate all 178 unique Thai speech fragments. Strip rolling display repetitions, not spoken repetitions. Do not add English-only editorial elaborations.'})
files=[P/'URL.md',next(P.glob('*.mp4')),ocrpath,qpath,qpath.parent/'OCR_TEST_REPORT.zh-CN.md',ROOT/'output/hard_subtitle_evaluation/xjymQaKAknE/job.json',ROOT/'output/hard_subtitle_evaluation/xjymQaKAknE/source.json',ROOT/'output/hard_subtitle_evaluation/xjymQaKAknE/artifacts/caption.zh-Hans.vtt',ROOT/'output/hard_subtitle_evaluation/xjymQaKAknE/artifacts/video/metadata.json',ROOT/'output/hard_subtitle_evaluation/xjymQaKAknE/reports/evaluation.json',W/'youtube.th.vtt']
inventory=[]
for f in files:
    h=hashlib.sha256()
    with f.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): h.update(block)
    inventory.append({'path':str(f),'bytes':f.stat().st_size,'sha256':h.hexdigest()})
dump(W/'evidence-checksums.json',inventory)
print('Fused source:',len(fused),'text repairs:',len(review))
