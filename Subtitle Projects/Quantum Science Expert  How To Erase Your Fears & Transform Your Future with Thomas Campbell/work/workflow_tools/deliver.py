import json,re,textwrap,hashlib,statistics
from pathlib import Path
from codex_subtitles.translation_service import materialize_translation,translation_status
from codex_subtitles.storage import update_manifest
from utils.subtitle_formatter import parse_srt
w=Path(__file__).resolve().parents[1];p=w.parent;j=Path((w/'active-job.txt').read_text().strip());out=p/'final';out.mkdir(exist_ok=True)
source=json.loads((j/'source.json').read_text())['segments'];by={s['id']:s for s in source};tr=materialize_translation(j)
def stamp(v):
 m=round(v*1000);s,z=divmod(m,1000);n,s=divmod(s,60);h,n=divmod(n,60);return f'{h:02}:{n:02}:{s:02},{z:03}'
def wrap_zh(t):
 tokens=re.findall(r"[A-Za-z0-9][A-Za-z0-9’'&.:/\-]*|.",t)
 def width(v):return sum(.5 if ord(c)<128 else 1 for c in v)
 if width(t)<=34:return t
 options=[]
 for k in range(1,len(tokens)):
  a=''.join(tokens[:k]).strip();b=''.join(tokens[k:]).strip()
  if not a or not b or b[0] in '，。；：？！、）】》”’' or a[-1] in '（【《“‘':continue
  x,y=width(a),width(b)
  if max(x,y)>36:continue
  bonus=4 if a[-1] in '，。；：？！、' else 0
  options.append((max(x,y)+abs(x-y)*.15-bonus,a+'\n'+b))
 return min(options)[1] if options else t
wrap_en=lambda t:'\n'.join(textwrap.wrap(t,width=68,break_long_words=False,break_on_hyphens=False))
def write(records,f,kind):
 blocks=[]
 for n,r in enumerate(records,1):
  if kind=='en':body=wrap_en(r['text'])
  else:
   body=wrap_zh(r['text'])
   if kind=='bi':body+='\n'+wrap_en(' '.join(by[k]['text'] for k in r['source_ids']))
  blocks.append(f'{n}\n{stamp(r["start"])} --> {stamp(r["end"])}\n{body}')
 f.write_text('\n\n'.join(blocks)+'\n\n')
zh=out/'Erase Your Fears.youtube.zh-Hans.srt';bi=out/'Erase Your Fears.youtube.zh-Hans-en.srt';en=out/'Erase Your Fears.youtube.en.reviewed.srt'
write(tr,zh,'zh');write(tr,bi,'bi');write(source,en,'en')
aa,bb=parse_srt(str(zh)),parse_srt(str(bi));assert len(aa)==len(bb)==len(tr)==1301
owned=[sid for r in tr for sid in r['source_ids']];assert owned==[s['id'] for s in source];assert len(owned)==1314
assert source[474]['id']=='c000475' and source[475]['id']=='c000476'
assert source[474]['text']==source[475]['text']
for x,y,r in zip(aa,bb,tr):
 assert (round(x['start']*1000),round(x['end']*1000))==(round(y['start']*1000),round(y['end']*1000))==(round(r['start']*1000),round(r['end']*1000))
 assert x['start']<x['end'];assert re.sub(r'\s','',x['text'])==re.sub(r'\s','',r['text'])
 expected=r['text']+' '.join(by[sid]['text'] for sid in r['source_ids'])
 assert re.sub(r'\s','',y['text'])==re.sub(r'\s','',expected)
assert all(a['end']<=b['start'] for a,b in zip(aa,aa[1:]));assert aa[-1]['end']<=5825.041
sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
ledger=json.loads((w/'evidence-ledger.json').read_text());assert sha(p/'URL.md')==ledger['original_inventory'][0]['sha256']
rows=json.loads((w/'audio/alignment.accepted.json').read_text());review=json.loads((w/'evidence-review.json').read_text())
readability=[dict(index=i+1,source_ids=tr[i]['source_ids'],duration_sec=round(x['end']-x['start'],3),cjk_cps=round(len(re.findall(r'[\u3400-\u9fff]',x['text']))/(x['end']-x['start']),2),lines=len(x['text'].splitlines())) for i,x in enumerate(aa)]
assert max(r['cjk_cps'] for r in readability)<=9
artifacts=[p/'URL.md',w/'youtube/gFka_huRM38.en-orig.json3',w/'youtube/gFka_huRM38.info.json',w/'audio/source.youtube.webm',w/'source.youtube.segmented.json',w/'source.fused.json',zh,bi,en]
q=dict(status='complete',source_url='https://www.youtube.com/watch?v=gFka_huRM38',timeline_target='youtube-source',source_audio_duration_sec=5825.041,
 primary_text_evidence='English (Original) automatic YouTube captions',human_caption_tracks=[],source_cues=1314,translated_cues=1301,bilingual_cues=1301,
 dropped_source_cues=0,exact_ordered_coverage=True,all_raw_cues_retained_including_adjacent_repeat=True,source_text_corrections=len(review['source_changes']),
 merged_groups=len(review['merged_groups']),final_validation=translation_status(j),positive_durations=True,nonoverlapping=True,exact_bilingual_timing_match=True,
 source_to_target_text_mapping_verified=True,source_to_original_word_provenance_verified=True,original_url_hash_unchanged=True,
 audio_asr_chunks=25,audio_repeat_check_clips=12,full_audio_transcribed=True,human_listening_verified=False,
 local_runtime='mlx-whisper 0.4.3 in .venv-subtitle-alignment',model='mlx-community/whisper-large-v3-turbo',model_sha256='951ed3fc1203e6a62467abb2144a96ce7eafca8fa77e3704fdb8635ff3e7f8a6',
 onset_methods={m:sum(r['method']==m for r in rows) for m in set(r['method'] for r in rows)},
 median_onset_advance_sec=statistics.median(r['advance_sec'] for r in rows),min_onset_advance_sec=min(r['advance_sec'] for r in rows),max_onset_advance_sec=max(r['advance_sec'] for r in rows),
 rejected_large_onset_changes=review['rejected_timing_changes'],end_time_policy='shared next-cue start, final tail limited to original-source audio duration; no previous-video offset',
 min_cue_duration_sec=min(r['duration_sec'] for r in readability),max_cjk_cps=max(r['cjk_cps'] for r in readability),max_chinese_lines=max(r['lines'] for r in readability),
 cues_under_1_sec=[r for r in readability if r['duration_sec']<1],hard_ocr_run=False,paid_api_used=False,
 sha256={str(f.relative_to(p)):sha(f) for f in artifacts})
(out/'quality.youtube.json').write_text(json.dumps(q,ensure_ascii=False,indent=2));(w/'delivery-readability.json').write_text(json.dumps(readability,ensure_ascii=False,indent=2))
manifest={str(f.relative_to(p)):{'bytes':f.stat().st_size,'sha256':sha(f)} for f in sorted((w/'audio').glob('*.asr.json'))+sorted((w/'audio').glob('*.wav'))}
(w/'audio/evidence-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
ledger.update(used_local_asr=True,source_audio_sha256=q['sha256']['work/audio/source.youtube.webm'],reused_model=True,selected_source='work/source.fused.json',source_review='work/evidence-review.json',caption_file='work/youtube/gFka_huRM38.en-orig.json3');(w/'evidence-ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2))
update_manifest(j,status='complete',preferred_delivery={'translated':str(zh),'bilingual':str(bi),'source':str(en)},quality_report=str(out/'quality.youtube.json'))
print(json.dumps({k:v for k,v in q.items() if k not in ['sha256','rejected_large_onset_changes']},ensure_ascii=False,indent=2))
