import json, re, difflib, statistics
from pathlib import Path
from codex_subtitles.translation_service import materialize_translation

p = Path(__file__).resolve().parent
work = p.parent
raw = json.loads((work / 'youtube/9on5PnWPlk4.en-orig.json3').read_text())
norm = lambda t: re.findall(r"[a-z]+(?:'[a-z]+)?|[0-9]+", t.lower().replace('’', "'"))
yt = []
for ei, e in enumerate(raw['events']):
    for si, s in enumerate(e.get('segs', [])):
        for ti, token in enumerate(norm(s['utf8'])):
            yt.append(dict(word=token, start=(e['tStartMs'] + s.get('tOffsetMs', 0))/1000,
                           key=f'{ei}:{si}:{ti}'))

def match_file(f):
    d = json.loads(f.read_text()); prov = d['provenance']
    base, end = prov['source_start_sec'], prov['source_end_sec']
    a = [v for v in yt if base-5 <= v['start'] < end+5]; b = []
    for s in d['segments']:
        for w in s.get('words', []):
            for token in norm(w['word']):
                b.append(dict(word=token, start=base+w['start'], end=base+w['end'],
                              probability=w.get('probability', 0)))
    matches = []
    for block in difflib.SequenceMatcher(None, [v['word'] for v in a], [v['word'] for v in b], autojunk=False).get_matching_blocks():
        if block.size < 5: continue
        for k in range(block.size):
            x, y = a[block.a+k], b[block.b+k]
            if not base+3 < y['start'] < end-3 or abs(x['start']-y['start']) > 5: continue
            matches.append(dict(**x, audio_start=y['start'], audio_end=y['end'],
                                lag=round(x['start']-y['start'], 3), probability=y['probability'],
                                duration=round(y['end']-y['start'], 3), block_length=block.size,
                                file=f.name, core=prov.get('core_start_sec', base) <= x['start'] < prov.get('core_end_sec', end)))
    return matches

focus = [m for f in sorted(p.glob('focus.*.asr.json')) for m in match_file(f) if m['core']]
probes = [m for f in sorted(p.glob('probe.*.asr.json')) for m in match_file(f)]
reviews = [m for f in sorted(p.glob('review.*.asr.json')) for m in match_file(f)]
bykey = {m['key']:m for m in focus}
good = [m for m in focus if m['probability'] >= .65 and m['duration'] >= .03]
prov = {v['id']: v['event_segments'] for v in json.loads((work/'source-word-provenance.json').read_text())}
job = Path((work/'active-job.txt').read_text().strip())
cues = materialize_translation(job)
rows = []
for i, c in enumerate(cues):
    if not 1200 <= c['start'] < 3000: continue
    keys = [f'{e}:{s}:{ti}' for sid in c['source_ids'] for e,s in prov[sid]
            for ti, t in enumerate(norm(raw['events'][e]['segs'][s]['utf8']))]
    direct = bykey.get(keys[0])
    local = [m for m in good if abs(m['start']-c['start']) <= 4]
    nearby = [m for m in local if c['start'] <= m['start'] <= c['start']+1.5]
    if len(nearby) < 3: nearby = sorted(local, key=lambda m: abs(m['start']-c['start']))[:8]
    med = round(statistics.median(m['lag'] for m in nearby), 3) if nearby else None
    rows.append(dict(index=i+1, old_start=c['start'], old_end=c['end'], source_ids=c['source_ids'],
                     text=c['text'], first_tokens=keys[:4], direct=direct, local_lag=med, local_anchors=nearby))
(p/'focus-word-matches.json').write_text(json.dumps(focus, ensure_ascii=False, indent=2))
(p/'probe-word-matches.json').write_text(json.dumps(probes, ensure_ascii=False, indent=2))
(p/'review-word-matches.json').write_text(json.dumps(reviews, ensure_ascii=False, indent=2))
(p/'cue-alignment.analysis.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
print('cues',len(rows),'direct',sum(r['direct'] is not None for r in rows),'good_words',len(good))
print('local med range',min(r['local_lag'] for r in rows),max(r['local_lag'] for r in rows))
print('direct outliers:')
for r in rows:
    d=r['direct']
    if d is None or abs(d['lag']) > .5 or abs(d['lag']-r['local_lag']) > .3:
        print(r['index'],r['old_start'],r['text'][:20], 'direct', ({k:d[k] for k in ['word','lag','duration','probability']} if d else None), 'local',r['local_lag'])
