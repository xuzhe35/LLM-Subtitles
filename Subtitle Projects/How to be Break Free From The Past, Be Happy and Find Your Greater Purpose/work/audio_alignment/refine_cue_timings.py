import json, wave, statistics
from pathlib import Path
import numpy as np

p = Path(__file__).resolve().parent
cache = {}
def acoustic_start(m):
    f = m['file']
    if f not in cache:
        d = json.loads((p/f).read_text())
        with wave.open(str(p/f.replace('.asr.json', '.wav'))) as w:
            sr = w.getframerate()
            a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(float)/32768
        frame = sr//100
        a = a[:len(a)//frame*frame].reshape(-1, frame)
        db = 20*np.log10(np.sqrt(np.mean(a*a, axis=1))+1e-9)
        cache[f] = (d['provenance']['source_start_sec'], db)
    base, db = cache[f]
    start, end = m['audio_start'], m['audio_end']
    i, j = max(0, round((start-base)*100)), min(len(db), round((end-base)*100))
    if j-i < 4: return dict(onset=start, valid=False, reason='short_or_zero_word')
    v = db[i:j]
    threshold = max(-50., float(np.percentile(v, 90))-30.)
    active = v > threshold
    # A pause of >=120 ms inside a DTW word is not part of its phonetic onset.
    cut = 0; k = 0; gaps = []
    while k < len(active):
        if active[k]: k += 1; continue
        z = k
        while k < len(active) and not active[k]: k += 1
        if k-z >= 12 and k <= len(active)-4:
            gaps.append([z,k]); cut = k
    candidates = [k for k in range(cut, len(active)-1) if active[k] and active[k+1]]
    if not candidates: return dict(onset=start, valid=False, reason='no_sustained_signal')
    onset = round(base+(i+candidates[0])/100, 3)
    return dict(onset=onset, valid=.04 <= end-onset <= .9,
                trimmed_sec=round(onset-start, 3), threshold_db=round(threshold, 2),
                silence_gaps_sec=[[round(base+(i+a)/100,3),round(base+(i+b)/100,3)] for a,b in gaps])

focus = json.loads((p/'focus-word-matches.json').read_text())
probes = json.loads((p/'probe-word-matches.json').read_text())
reviews = json.loads((p/'review-word-matches.json').read_text())
for m in focus+probes+reviews:
    m['acoustic'] = acoustic_start(m)
    m['refined_lag'] = round(m['start']-m['acoustic']['onset'],3)
bykey={m['key']:m for m in focus}
rows=json.loads((p/'cue-alignment.analysis.json').read_text())
good=[m for m in focus if m['probability']>=.8 and m['duration']>=.04 and m['acoustic']['valid'] and abs(m['refined_lag'])<=.6]
for r in rows:
    near=sorted([m for m in good if abs(m['start']-r['old_start'])<8],key=lambda m:abs(m['start']-r['old_start']))[:12]
    assert len(near)>=3
    local=round(statistics.median(m['refined_lag'] for m in near),3)
    m=bykey.get(r['first_tokens'][0]); r['refined_local_lag']=local
    if m and m['probability']>=.65 and m['acoustic']['valid'] and -.35<=m['refined_lag']<=3:
        r['new_start']=m['acoustic']['onset'];r['method']='word_match_with_acoustic_pause_trim';r['onset_evidence']=m
    else:
        r['new_start']=round(r['old_start']-local,3);r['method']='nearby_word_median_estimate';r['onset_evidence']=near
    r['advance_sec']=round(r['old_start']-r['new_start'],3)
    r['review_large_change']=abs(r['advance_sec'])>.6
(p/'cue-alignment.proposed.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
(p/'probe-word-matches.refined.json').write_text(json.dumps(probes,ensure_ascii=False,indent=2))
(p/'review-word-matches.refined.json').write_text(json.dumps(reviews,ensure_ascii=False,indent=2))
print('Methods', {k:sum(r['method']==k for r in rows) for k in set(r['method'] for r in rows)})
print('Median advance', statistics.median(r['advance_sec'] for r in rows), 'range',min(r['advance_sec'] for r in rows),max(r['advance_sec'] for r in rows))
for r in rows:
    if r['review_large_change']:
        m=r['onset_evidence'];print('REVIEW',r['index'],r['old_start'],r['new_start'],r['advance_sec'],r['text'][:35],m['word'] if isinstance(m,dict) else 'estimate')
