import pathlib,json,sys
from codex_subtitles.translation_service import translation_status
p=pathlib.Path('Subtitle Projects/How to be Break Free From The Past, Be Happy and Find Your Greater Purpose/work');j=pathlib.Path((p/'active-job.txt').read_text());b=pathlib.Path(sys.argv[1]);lines=b.read_text().splitlines();trans={}
for line in lines:
 if not line.strip():continue
 n,t=line.split('|',1);trans[f'c{int(n):06}']=t
for f in sorted((j/'windows').glob('*.target.json')):
 d=json.loads(f.read_text());s=json.loads(f.with_name(f.name.replace('.target.','.source.')).read_text());ids=s['core_ids']
 if any(x in trans for x in ids):
  old={x['source_ids'][0]:x['text'] for x in d['cues']};old.update({x:trans[x] for x in ids if x in trans});d['cues']=[{'source_ids':[x],'text':old[x]} for x in ids if x in old];f.write_text(json.dumps(d,ensure_ascii=False,indent=2))
print(json.dumps(translation_status(j)))
