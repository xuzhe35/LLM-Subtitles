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

