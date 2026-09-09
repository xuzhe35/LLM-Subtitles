import json,pathlib,hashlib,re,textwrap,shutil
from codex_subtitles.translation_service import materialize_translation,translation_status
from utils.subtitle_formatter import parse_srt
p=pathlib.Path('Subtitle Projects/How to be Break Free From The Past, Be Happy and Find Your Greater Purpose').resolve();w=p/'work';j=pathlib.Path((w/'active-job.txt').read_text());out=p/'final';out.mkdir(exist_ok=True)
s=json.loads((j/'source.json').read_text())['segments'];by={x['id']:x for x in s};tr=materialize_translation(j)
def stamp(v):
 m=round(v*1000);secs,ms=divmod(m,1000);mins,ss=divmod(secs,60);hh,mm=divmod(mins,60);return f'{hh:02}:{mm:02}:{ss:02},{ms:03}'
def wrap_zh(t):
 # Balance up to two Chinese lines; keep punctuation, words and URLs intact.
 tokens=re.findall(r"[A-Za-z0-9][A-Za-z0-9’'&.:/\-]*|.",t)
 def width(v):return sum(.5 if ord(c)<128 else 1 for c in v)
 if width(t)<=34:return t
 options=[]
 for k in range(1,len(tokens)):
  left=''.join(tokens[:k]).strip();right=''.join(tokens[k:]).strip()
  if not left or not right or right[0] in '，。；：？！、）】》”’' or left[-1] in '（【《“‘':continue
  a,b=width(left),width(right)
  if max(a,b)>36:continue
  bonus=4 if left[-1] in '，。；：？！、' else 0
  options.append((max(a,b)+abs(a-b)*.15-bonus,left+'\n'+right))
 if options:return min(options)[1]
 return t
def wrap_en(t):return '\n'.join(textwrap.wrap(t,width=68,break_long_words=False,break_on_hyphens=False))
def write(items,path,kind):
 blocks=[]
 for n,x in enumerate(items,1):
  timing=stamp(x['start'])+' --> '+stamp(x['end'])
  if kind=='source':body=wrap_en(x['text'])
  else:
   body=wrap_zh(x['text'])
   if kind=='bi':body+='\n'+wrap_en(' '.join(by[k]['text'] for k in x['source_ids']))
  blocks.append(f'{n}\n{timing}\n{body}')
 path.write_text('\n\n'.join(blocks)+'\n\n')
zh=out/'Break Free From The Past.youtube.zh-Hans.srt';bi=out/'Break Free From The Past.youtube.zh-Hans-en.srt';en=out/'Break Free From The Past.youtube.en.reviewed.srt'
write(tr,zh,'zh');write(tr,bi,'bi');write(s,en,'source')
# Confirm timestamp fidelity after SRT round-trip, exact total ownership, original token preservation.
a=parse_srt(str(zh));b=parse_srt(str(bi));assert len(a)==len(b)==len(tr)
for x,y,z in zip(a,b,tr):
 assert round(x['start']*1000)==round(y['start']*1000)==round(z['start']*1000)
 assert round(x['end']*1000)==round(y['end']*1000)==round(z['end']*1000)
 assert x['start']<x['end']
assert all(x['end']<=y['start'] for x,y in zip(a,a[1:]));assert [k for x in tr for k in x['source_ids']]==[x['id'] for x in s]
orig=json.loads((w/'source.youtube.normalized.original.json').read_text())['segments'];assert [(x['start'],x['end']) for x in s]==[(x['start'],x['end']) for x in orig]
raw=json.loads((w/'youtube/9on5PnWPlk4.en-orig.json3').read_text());norm=lambda t: re.sub(r'\s+','',t)
rawtext=''.join(v['utf8'] for e in raw['events'] for v in e.get('segs',[]) if v['utf8'].strip());assert norm(rawtext)==norm(''.join(x['text'] for x in orig))
assert (p/'URL.md').read_bytes()==b'https://www.youtube.com/watch?v=9on5PnWPlk4&t=360s'
# Save actual input and output fingerprints for future evidence reuse.
files=[p/'URL.md',w/'youtube/9on5PnWPlk4.en-orig.json3',w/'youtube/9on5PnWPlk4.info.json',w/'source.youtube.normalized.original.json',w/'source.youtube.reviewed.json',zh,bi,en]
hashes={str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
cnstats=[]
for x in a:
 t=x['text'];dense=len(re.findall(r'[\u3400-\u9fff]',t));cnstats.append({'index':len(cnstats)+1,'start':x['start'],'end':x['end'],'lines':len(t.splitlines()),'cjk_cps':round(dense/(x['end']-x['start']),2)})
quality={'timeline_target':'youtube-source','source_url':'https://www.youtube.com/watch?v=9on5PnWPlk4','source_duration_sec':6371,'first_start_sec':a[0]['start'],'last_end_sec':a[-1]['end'],'source_cue_count':len(s),'translated_cue_count':len(a),'bilingual_cue_count':len(b),'dropped_source_cues':0,'window_validation':translation_status(j),'positive_durations':True,'monotonic_nonoverlapping':True,'exact_bilingual_timing_match':True,'all_raw_caption_text_accounted_for_before_review':True,'normalized_source_timestamps_unchanged':True,'output_millisecond_rounding':'nearest integer millisecond; corrects native formatter float truncation in delivery copies','youtube_offset_sec':0,'audio_listening_verified':False,'local_video':None,'local_video_sha256':None,'ocr_run':False,'local_asr_run':False,'paid_api_used':False,'sha256':hashes,'max_chinese_lines':max(x['lines'] for x in cnstats),'max_cjk_characters_per_sec':max(x['cjk_cps'] for x in cnstats),'cues_under_1_sec':[x for x in cnstats if x['end']-x['start']<1],'cues_over_9_cjk_cps':[x for x in cnstats if x['cjk_cps']>9]}
(out/'quality.youtube.json').write_text(json.dumps(quality,ensure_ascii=False,indent=2));(w/'delivery_readability.json').write_text(json.dumps(cnstats,indent=2))
manifest=json.loads((j/'job.json').read_text());manifest.update(original_source_url=quality['source_url'],original_video_id='9on5PnWPlk4',timeline_target='youtube-source',source_kind='youtube_automatic_caption_reviewed',status='complete',preferred_delivery={'translated':str(zh),'bilingual':str(bi),'source':str(en)});(j/'job.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
ctx=json.loads((j/'context.json').read_text());ctx['supplementary_name_checks']=json.loads((w/'evidence-review.json').read_text())['external_references'];(j/'context.json').write_text(json.dumps(ctx,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in quality.items() if k not in ['sha256']},ensure_ascii=False,indent=2))
print('MULTILINE',[x for x in cnstats if x['lines']>2])
