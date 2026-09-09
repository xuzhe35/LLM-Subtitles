import pathlib,json,re,statistics,difflib
p=pathlib.Path(__file__).resolve().parent
raw=json.loads((p.parent/'youtube/9on5PnWPlk4.en-orig.json3').read_text());yt=[]
def norm(t):return re.findall(r"[a-z]+(?:'[a-z]+)?|[0-9]+",t.lower().replace('’',"'"))
for ei,e in enumerate(raw['events']):
 for si,s in enumerate(e.get('segs',[])):
  for token in norm(s['utf8']):yt.append({'word':token,'start':(e['tStartMs']+s.get('tOffsetMs',0))/1000,'event':ei,'seg':si})
reports=[]
for f in sorted(p.glob('probe.*.asr.json')):
 d=json.loads(f.read_text());base=d['provenance']['source_start_sec'];end=d['provenance']['source_end_sec'];a=[v for v in yt if base-5<=v['start']<end+5];b=[]
 for s in d['segments']:
  for w in s.get('words',[]):
   for t in norm(w['word']):b.append({'word':t,'start':base+w['start'],'end':base+w['end'],'probability':w.get('probability',0)})
 matched=[]
 for block in difflib.SequenceMatcher(None,[v['word'] for v in a],[v['word'] for v in b],autojunk=False).get_matching_blocks():
  if block.size<5:continue
  for k in range(block.size):
   x,y=a[block.a+k],b[block.b+k]
   if y['probability']<.65 or y['end']-y['start']<.03 or not base+3<y['start']<end-3:continue
   if abs(x['start']-y['start'])>5:continue
   matched.append({'word':x['word'],'youtube_start':x['start'],'audio_start':y['start'],'audio_end':y['end'],'lag_sec':round(x['start']-y['start'],3),'probability':y['probability'],'match_block_length':block.size})
 lag=[v['lag_sec'] for v in matched];q=statistics.quantiles(lag,n=4) if len(lag)>3 else []
 report={'file':f.name,'range':[base,end],'anchors':len(matched),'median_lag_sec':round(statistics.median(lag),3) if lag else None,'quartiles':q,'matches':matched};reports.append(report)
 print({k:v for k,v in report.items() if k!='matches'})
(p/'probe-comparison.json').write_text(json.dumps({'positive_lag_means':'YouTube caption later than local ASR word onset','method':'exact ordered matching blocks >=5 tokens, word probability >=0.65, duration >=0.03, discard clip boundary 3 sec','probes':reports},ensure_ascii=False,indent=2))
