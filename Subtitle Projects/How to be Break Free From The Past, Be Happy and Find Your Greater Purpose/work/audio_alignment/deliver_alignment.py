"""Export a separate, auditable timing revision without modifying translation text."""
import json, re, statistics, hashlib
from pathlib import Path

p=Path(__file__).resolve().parent; project=p.parent.parent; final=project/'final'
rows=json.loads((p/'cue-alignment.proposed.json').read_text()); byindex={r['index']:r for r in rows}
baseline=json.loads((p/'timing-audit.before.json').read_text())
sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
for rel,h in baseline['input_sha256'].items(): assert sha(project/rel)==h, rel
def ms(t):
    h,m,s,z=map(int,re.split('[:,]',t));return ((h*60+m)*60+s)*1000+z
def stamp(t):
    s,z=divmod(t,1000);m,s=divmod(s,60);h,m=divmod(m,60)
    return f'{h:02d}:{m:02d}:{s:02d},{z:03d}'
def read(f):
    records=[]
    for block in f.read_text().strip().split('\n\n'):
        n,t,body=block.split('\n',2); a,b=t.split(' --> ')
        records.append(dict(index=int(n),start=ms(a),end=ms(b),body=body))
    return records
def write(f,records):
    f.write_text('\n\n'.join(f"{r['index']}\n{stamp(r['start'])} --> {stamp(r['end'])}\n{r['body']}" for r in records)+'\n\n')
oldzh=final/'Break Free From The Past.youtube.zh-Hans.srt'
oldbi=final/'Break Free From The Past.youtube.zh-Hans-en.srt'
zh,bi=read(oldzh),read(oldbi)
assert len(zh)==len(bi)==1326
starts=[round(byindex[r['index']]['new_start']*1000) if r['index'] in byindex else r['start'] for r in zh]
newzh=[];newbi=[];newen=[];changes=[]
for i,(a,b) in enumerate(zip(zh,bi)):
    assert (a['start'],a['end'])==(b['start'],b['end'])
    start=starts[i]
    if i+1<len(zh) and a['end']==zh[i+1]['start']:
        end=starts[i+1]
    elif a['index'] in byindex:
        end=a['end']+(start-a['start'])
    else: end=a['end']
    if i+1<len(zh): end=min(end,starts[i+1])
    assert 0<=start<end<=6371000, (a['index'],start,end)
    assert b['body'].startswith(a['body']+'\n')
    newzh.append(dict(a,start=start,end=end));newbi.append(dict(b,start=start,end=end))
    newen.append(dict(b,start=start,end=end,body=b['body'][len(a['body'])+1:]))
    if (start,end)!=(a['start'],a['end']):
        changes.append(dict(index=a['index'],old_start_ms=a['start'],old_end_ms=a['end'],new_start_ms=start,new_end_ms=end,
                            method=byindex[a['index']]['method'] if a['index'] in byindex else 'adjacent_boundary_join'))
paths={
 'zh':final/'Break Free From The Past.youtube.audio-aligned.zh-Hans.srt',
 'bilingual':final/'Break Free From The Past.youtube.audio-aligned.zh-Hans-en.srt',
 'english_reference':final/'Break Free From The Past.youtube.audio-aligned.en.srt'}
for key,records in zip(paths,[newzh,newbi,newen]):
    write(paths[key],records); assert read(paths[key])==records
assert [r['body'] for r in newzh]==[r['body'] for r in zh]
assert [r['body'] for r in newbi]==[r['body'] for r in bi]
assert all(a['end']<=b['start'] for a,b in zip(newzh,newzh[1:]))
assert all((a['start'],a['end'])==(b['start'],b['end']) for a,b in zip(newzh,newbi))
assert all(c['index'] in byindex or c['new_start_ms']==c['old_start_ms'] for c in changes)

def repeatability(matches):
    bykey={m['key']:m for m in matches}; records=[]
    for r in rows:
        m=bykey.get(r['first_tokens'][0])
        if m and m['probability']>=.65 and m['acoustic']['valid']:
            onset=m['acoustic']['onset']
            records.append(dict(index=r['index'],old_residual_sec=round(r['old_start']-onset,3),
                                new_residual_sec=round(r['new_start']-onset,3),file=m['file']))
    return dict(cues=len(records),mean_absolute_residual_before_sec=round(statistics.mean(abs(r['old_residual_sec']) for r in records),3),
                mean_absolute_residual_after_sec=round(statistics.mean(abs(r['new_residual_sec']) for r in records),3),
                interpretation='Same model, separately cut audio windows: repeatability check, not independent ground-truth accuracy.',records=records)
repeat=repeatability(json.loads((p/'probe-word-matches.refined.json').read_text()))
review=repeatability(json.loads((p/'review-word-matches.refined.json').read_text()))
large_review=[]
for r in rows:
    if r['review_large_change']:
        v=next(x for x in review['records'] if x['index']==r['index'])
        assert abs(v['new_residual_sec'])<=.12,(r['index'],v)
        large_review.append(dict(index=r['index'],old_start_sec=r['old_start'],new_start_sec=r['new_start'],advance_sec=r['advance_sec'],
                                word=r['onset_evidence']['word'],repeat_residual_sec=v['new_residual_sec']))
def cps(records):
    return [dict(index=r['index'],duration_sec=(r['end']-r['start'])/1000,cjk_cps=round(len(re.findall(r'[\u3400-\u9fff]',r['body']))*1000/(r['end']-r['start']),2)) for r in records]
oldstats,newstats=cps(zh),cps(newzh)
quality=dict(status='complete',timeline_target='youtube-source-audio',source_url='https://www.youtube.com/watch?v=9on5PnWPlk4',
 requested_range_sec=[1200,3000],analyzed_focus_range_sec=[1120,3080],source_audio=str(p/'source.youtube.webm'),
 source_audio_sha256=sha(p/'source.youtube.webm'),source_audio_duration_sec=6370.481,
 original_timing_conversion_max_error_ms=0,translated_cues=1326,bilingual_cues=1326,source_cues_covered=1344,
 examined_focus_cues=len(rows),changed_cues=len(changes),changed_starts=sum(c['old_start_ms']!=c['new_start_ms'] for c in changes),
 outside_focus_changed_cues=[c for c in changes if c['index'] not in byindex],
 onset_methods={k:sum(r['method']==k for r in rows) for k in set(r['method'] for r in rows)},
 median_onset_advance_sec=statistics.median(r['advance_sec'] for r in rows),
 min_onset_advance_sec=min(r['advance_sec'] for r in rows),max_onset_advance_sec=max(r['advance_sec'] for r in rows),
 positive_durations=True,monotonic_nonoverlapping=True,exact_bilingual_timing_match=True,all_translation_text_and_line_breaks_unchanged=True,
 original_evidence_and_deliveries_hash_verified_unchanged=True,local_asr_run=True,hard_ocr_run=False,paid_api_used=False,
 human_listening_verified=False,acoustic_signal_pause_trimming=True,model='mlx-community/whisper-large-v3-turbo',
 model_sha256='951ed3fc1203e6a62467abb2144a96ce7eafca8fa77e3704fdb8635ff3e7f8a6',
 repeatability=repeat,large_change_review=large_review,changes=changes,
 min_cue_duration_sec=min(r['duration_sec'] for r in newstats),max_cjk_cps_before=max(r['cjk_cps'] for r in oldstats),
 max_cjk_cps_after=max(r['cjk_cps'] for r in newstats),new_cues_above_9_cjk_cps=[n for o,n in zip(oldstats,newstats) if n['cjk_cps']>9 and o['cjk_cps']<=9],
 output_sha256={str(f.relative_to(project)):sha(f) for f in paths.values()},original_input_sha256=baseline['input_sha256'])
(final/'quality.youtube.audio-aligned.json').write_text(json.dumps(quality,ensure_ascii=False,indent=2))
(p/'cue-alignment.applied.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in quality.items() if k not in ['changes','repeatability','output_sha256','original_input_sha256']},ensure_ascii=False,indent=2))
