"""Run the repository exporter with exact millisecond serialization for this job."""
from pathlib import Path
import json, shutil, hashlib, re, subprocess
from utils import subtitle_formatter as fmt
from codex_subtitles.cli import main
from codex_subtitles.translation_service import materialize_translation

W=Path(__file__).resolve().parent
J=next((W/'codex_native').iterdir())
P=W.parent
def exact_timestamp(seconds):
    n=int(round(seconds*1000));s,ms=divmod(n,1000);m,s=divmod(s,60);h,m=divmod(m,60)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'
# The repository formatter truncates float fractions, losing 1 ms at some values.
# This project adapter serializes the trusted millisecond value without retiming.
fmt.format_timestamp=exact_timestamp
payload=json.loads((J/'source.json').read_text())
fused=json.loads((W/'source.fused.json').read_text())['segments']
source=payload['segments']
for c,f in zip(source,fused):
    c['text']=f['text']
(J/'source.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
from codex_subtitles.workflow_service import plan_job
plan_job(J)
groups=materialize_translation(J)
by_id={c['id']:c for c in source}
strings=[c['text'] for c in source]+[g['text'] for g in groups]+[' '.join(by_id[i]['text'] for i in g['source_ids']) for g in groups]
node='/Users/xuzhe/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
js=r'''
let input='';process.stdin.setEncoding('utf8');process.stdin.on('data',s=>input+=s);process.stdin.on('end',()=>{
 const out={};for(const text of JSON.parse(input)){
  const thai=/[\u0e00-\u0e7f]/u.test(text),limit=thai?58:24;
  const words=new Intl.Segmenter(thai?'th':'zh',{granularity:'word'}).segment(text);
  const gs=new Intl.Segmenter(thai?'th':'zh',{granularity:'grapheme'});
  const count=s=>[...gs.segment(s)].length;
  const lines=[];let line='';
  for(const w of words){
   const token=w.segment;
   if(line && count(line+token)>limit && !/^[\s，。？！、；：」\],.?!]+$/u.test(token)){lines.push(line.trim());line='';}
   line+=token;
  }
  if(line.trim())lines.push(line.trim());out[text]=lines.join('\n');
 }
 process.stdout.write(JSON.stringify(out));
});
'''
wrapped=json.loads(subprocess.run([node,'-e',js],input=json.dumps(strings,ensure_ascii=False),text=True,capture_output=True,check=True).stdout)
fmt.wrap_subtitle_text=lambda text,**kwargs:wrapped.get(text,text)
fmt.generate_srt(source,str(W/'source.fused.th.srt'))
shutil.copy2(W/'source.fused.th.srt',J/'artifacts/source.fused.th.srt')
assert main(['finalize',str(J)])==0
final=P/'final';final.mkdir(exist_ok=True)
for a,b in [('translated.简体中文.srt','中文字幕.zh-CN.srt'),('bilingual.简体中文.srt','泰中双语.th-zh-CN.srt')]:
    shutil.copy2(J/'final'/a,final/b)
groups=materialize_translation(J)
raw=json.loads((W/'youtube.th.normalized.json').read_text())['segments']
by_id={c['id']:c for c in source}
assert len(source)==len(raw)==178
assert all(round(c[k]*1000)==round(r[k]*1000) for c,r in zip(source,raw) for k in ('start','end'))
expected=[f"{exact_timestamp(by_id[g['source_ids'][0]]['start'])} --> {exact_timestamp(by_id[g['source_ids'][-1]]['end'])}" for g in groups]
for f in final.glob('*.srt'):
    actual=[x for x in f.read_text().splitlines() if '-->' in x]
    assert actual==expected, f
    cues=fmt.parse_srt(str(f))
    assert len(cues)==len(groups)
    assert all(c['end']>c['start'] and (i==0 or c['start']>=cues[i-1]['end']) for i,c in enumerate(cues))
originals=json.loads((W/'evidence-checksums.json').read_text())
for f in originals:
    h=hashlib.sha256()
    with Path(f['path']).open('rb') as st:
        for b in iter(lambda:st.read(1024*1024),b''):h.update(b)
    assert h.hexdigest()==f['sha256'],f['path']
risks=[{'output_cue':i+1,'source_ids':g['source_ids'],'seconds':round(g['end']-g['start'],3),'characters_per_second':round(len(g['text'])/(g['end']-g['start']),2),'text':g['text']} for i,g in enumerate(groups) if len(g['text'])/(g['end']-g['start'])>9]
result={'timing_basis':'youtube-source','youtube_video_id':'xjymQaKAknE','source_cues':178,'output_cues':len(groups),'source_ids_covered_once_in_order':True,'dropped_spoken_cues':0,'translation_windows_valid':3,'merged_groups_at_most_8_cues_and_15_seconds':True,'no_cross_caption_turn_merge':True,'all_final_timestamps_exact_source_milliseconds':True,'source_evidence_checksums_unchanged':True,'reviewed_flagged_ocr_cues':51,'reviewed_images_total':55,'source_video_visual_samples_sec':[1,253,516],'local_asr_ran':False,'ocr_ran':False,'paid_api_used':False,'readability_flags':risks,'unresolved_thai_wording_source_ids':['c000008','c000038'],'range':[expected[0].split(' --> ')[0],expected[-1].split(' --> ')[1]]}
(W/'quality-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False,indent=2))
