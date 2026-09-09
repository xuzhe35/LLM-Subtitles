from pathlib import Path
import re, json, hashlib
from codex_subtitles.source_service import clean_caption_text
from codex_subtitles.storage import atomic_write_json
from utils.subtitle_formatter import generate_srt
p=Path("Subtitle Projects/Anthropic's Chloe Lubinski explains how AI works (in 14 minutes)")
f=p/'work/evidence/youtube.auto.en-orig.vtt'
def sec(t):
 a=t.split(':'); return int(a[0])*3600+int(a[1])*60+float(a[2])
segments=[]; skipped=[]
for n,b in enumerate(f.read_text().strip().split('\n\n')):
 lines=b.splitlines(); times=next((l for l in lines if '-->' in l),None)
 if not times: continue
 m=re.match(r'(\S+) --> (\S+)', times); start,end=map(sec,m.groups())
 content=[l for l in lines[lines.index(times)+1:] if l.strip()]
 fresh=content[-1:] if end-start>0.02 else []
 if fresh:
  text=' '.join(clean_caption_text(l) for l in fresh)
  segments.append({'start':start,'end':end,'text':text,'raw_block':n})
 else: skipped.append({'raw_block':n,'start':start,'end':end,'text':clean_caption_text(' '.join(lines[lines.index(times)+1:]))})
# Retain the final marker-only block if it carries previously unseen text.
for x in skipped:
 if x['text'] and not any(x['text']==s['text'] for s in segments): print('UNMATCHED DISPLAY:',x)
atomic_write_json(p/'work/evidence/deroll-review.json',{'method':'Keep last nonempty newly spoken line from each substantive block, including untagged one-word lines; discard rolling carry-over lines and 10ms display duplicates. Preserve retained source cue start/end times exactly.','raw_sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'segments':segments,'display_only_blocks':skipped})
atomic_write_json(p/'work/source.youtube.en.json',{'segments':segments})
generate_srt(segments,p/'work/source.youtube.en.srt',max_line_chars=0)
(p/'work/source-readable.txt').write_text('\n'.join(f"{i+1:03d} [{s['start']:.3f}-{s['end']:.3f}] {s['text']}" for i,s in enumerate(segments)))
print('CUES',len(segments),'RANGE',segments[0]['start'],segments[-1]['end'])
