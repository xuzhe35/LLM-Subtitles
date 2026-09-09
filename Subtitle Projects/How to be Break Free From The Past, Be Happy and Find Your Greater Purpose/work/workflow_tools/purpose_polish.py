import json,pathlib,re,shutil
p=pathlib.Path('Subtitle Projects/How to be Break Free From The Past, Be Happy and Find Your Greater Purpose');w=p/'work';j=pathlib.Path((w/'active-job.txt').read_text());src=json.loads((j/'source.json').read_text());snapshot=w/'source.youtube.normalized.original.json'
if not snapshot.exists():shutil.copy2(j/'source.json',snapshot)
meta='youtube/9on5PnWPlk4.info.json; publisher title, uploader and description'
refs={'campbell':'https://www.my-big-toe.com/about/tom-campbell/','leadbeater':'https://www.ts-adyar.org/book/man-visible-and-invisible-hc','krohn':'https://bialikbreakdown.substack.com/p/mayims-monday-motivation-41c','lincoln':'https://talkinghearts.net/products/messages-from-the-body','unkibble':'https://www.spotandtango.com/unkibble?pc=1','genesis':'https://www.sefaria.org/Genesis.1.2?lang=bi'}
rules=[(r'\bBoore\b','Bohr','name; standard spelling in quantum founders context'),(r'\bSchroinger\b','Schrödinger','name; standard spelling in quantum founders context'),(r'\b(?:Raiki|raiki)\b','Reiki','repeated term; same-language context'),(r'\bDennis Menick\b','Dennis Mennerich',refs['campbell']),(r'Bob and Rose','Bob Monroe',refs['campbell']),(r'Elizabeth Cone','Elizabeth Krohn',refs['krohn']),(r'CW led better','C. W. Leadbeater',refs['leadbeater']),(r'Led Better|lead betterers|lead better','Leadbeater',refs['leadbeater']),(r'\baurus\b','auras','same-language context: aura colours'),(r'\bsants\b','savants','same-language context; explicit savant in cue 645'),(r'\bDowoism\b','Taoism','religion list; phonetic/context repair'),(r'\bKabala\b|\bCabala\b','Kabbalah','standard variant spelling'),(r'\bDaly Lama\b','Dalai Lama','name; phonetic/context repair'),(r'\bamiebas\b','amoebas','evolution context'),(r'\bNurion Singh\b','Narayan Singh','author identity; https://spiritrisingyoga.org/blog/books-by-michael-jlincoln'),(r'\bMayam\b','Mayim',meta),(r'My Big toe','My Big TOE',meta),(r'helixleep\.com|Helixleep\.com','helixsleep.com','Helix Sleep brand; domain spelling reconstruction; promotion not independently validated'),(r'spottango\.com|spotango\.com','spotandtango.com',refs['unkibble']),(r'Spottangle|Spottango','Spot & Tango',refs['unkibble']),(r'aspcapet insurance\.com','aspcapetinsurance.com','join URL whitespace; ASPCA ad context')]
individual={20:[("I'm Ballik","I'm Mayim Bialik",meta)],157:[('Yes, m Alex breakdown',"Yes, Mayim Bialik’s Breakdown",meta),('calm.','Calm.','brand repeated in ad')],173:[('Cal.','Calm.','same ad context')],174:[('Comm','Calm','same ad context'),('COM premium','Calm Premium','same ad context'),('com.com/break','calm.com/break','exact URL corroborated by cue 175')],175:[("comm's","Calm’s",'same ad context')],176:[('comm.com/break','calm.com/break','cue 175'),('My x breakdown',"Mayim Bialik’s Breakdown",meta)],197:[('My NBA Alex breakdown',"Mayim Bialik’s Breakdown",meta)],209:[('unkillable','UnKibble',refs['unkibble'])],220:[('codebreak','code break','separate coupon label and code')],384:[('arrow bars','error bars','two sigma; adjacent cue 383')],437:[('My Alex breakdown',"Mayim Bialik’s Breakdown",meta)],444:[('momf founded','mom-founded','repeated phrase mom founded in cue 446')],484:[('chance','chants','list of ritual tools; phonetic/context repair')],538:[('a pound','a bounce','repeated bounce in cues 536/539')],565:[('sematic','somatic','EMDR context; bodily stimulus vs auditory/eye movement')],651:[('sance','savants','cues 640/645'),('telepathy types','Telepathy Tapes','publisher description and repeated title')],862:[('do arms','do oms','meditation chant context')],908:[('had shame come up','had shamans come up','following cue 909 explicitly shaman')],923:[('daycare moment','Descartes moment','immediately followed by I think, therefore I am')],932:[('decaction','reaction','unclear token; cautious contextual reading, not audio verified')],1092:[('Toou vavu','Tohu va-vohu',refs['genesis'])],1117:[('our son','our sun','solar system context')],1263:[('seastate change','sea change','idiom; contextual reading')],1341:[('my ambolics breakdown',"Mayim Bialik’s Breakdown",meta)]}
changes=[]
for i,s in enumerate(src['segments'],1):
 old=s['text'];new=old;evidence=[]
 for pat,rep,prov in rules:
  new,count=re.subn(pat,rep,new)
  if count:evidence.append({'pattern':pat,'replacement':rep,'provenance':prov})
 for a,b,prov in individual.get(i,[]):
  if a in new:new=new.replace(a,b);evidence.append({'pattern':a,'replacement':b,'provenance':prov})
 if old!=new:
  changes.append({'source_id':s['id'],'start':s['start'],'end':s['end'],'original_text':old,'revised_text':new,'evidence_used':evidence,'reason':'ASR spelling, entity, title, URL or unambiguous cross-cue repair; timestamps unchanged'});s['text']=new
(w/'source.youtube.reviewed.json').write_text(json.dumps(src,ensure_ascii=False,indent=2));(j/'source.json').write_text(json.dumps(src,ensure_ascii=False,indent=2));by={s['id']:s for s in src['segments']}
# Refresh only evidence text in window inputs; ownership and timings remain unchanged.
for f in (j/'windows').glob('*.source.json'):
 d=json.loads(f.read_text())
 for c in d['cues']:c['text']=by[c['id']]['text']
 f.write_text(json.dumps(d,ensure_ascii=False,indent=2))
fix={126:'你是这样描述的：人类文化不只是共享信念的共同体，在其中，共同信念造成的',176:'网址是 calm.com/break。本节目由 Helix Sleep 赞助。',197:'helixsleep.com/breakdown。本节目由 Spot & Tango 赞助。',267:'就是这么回事。',286:'我指着说：“这个。”',435:'保险由独立美国保险公司或美国火灾保险公司承保，由 PTZ 保险代理有限公司代理。',437:'本节目由 Ritual 赞助。',483:'可以用这个，也可以用其他东西。',581:'所以，这些都能解释。',602:'他们没见过这种东西。',652:'我们研究过。——对。',717:'黑点，怎样都行。',827:'协作。如果违背这个目的，比如“我怎样才能赚更多钱？”',1032:'他们的宗教告诉他们应该这样做。',1092:'“空虚混沌”，有形之物出现之前。',1339:'请收看第二期。从我们的《Breakdown》，聊到你希望从未有过的崩溃。'}
# Reviewed adjacent same-speaker passages only; no automatic speaker inference/merging.
merges=[(317,318),(381,382),(601,603),(716,717),(752,753),(785,787),(946,947),(1036,1037),(1083,1083),(1205,1208),(1270,1272)]
polish=[];merge_log=[]
for f in sorted((j/'windows').glob('*.target.json')):
 d=json.loads(f.read_text());cues=d['cues']
 for c in cues:
  n=int(c['source_ids'][0][1:])
  if n in fix:polish.append({'source_ids':c['source_ids'],'original':c['text'],'revised':fix[n],'reason':'semantic/readability review'});c['text']=fix[n]
 for lo,hi in merges:
  if lo==hi:continue
  ids=[f'c{n:06}' for n in range(lo,hi+1)];positions=[k for k,c in enumerate(cues) if any(x in ids for x in c['source_ids'])]
  if not positions:continue
  chunk=cues[positions[0]:positions[-1]+1]
  if [x for c in chunk for x in c['source_ids']]!=ids:continue
  duration=by[ids[-1]]['end']-by[ids[0]]['start']
  assert duration<=15 and len(ids)<=8
  t=''.join(c['text'] for c in chunk)
  if lo==752:t='我会想到X博士。我想请你谈谈“显化”这个概念。'
  cues[positions[0]:positions[-1]+1]=[{'source_ids':ids,'text':t}];merge_log.append({'source_ids':ids,'duration':duration,'reason':'reviewed adjacent same-speaker sentence/short phrase; original boundaries preserved as group endpoints'})
 d['cues']=cues;f.write_text(json.dumps(d,ensure_ascii=False,indent=2))
(w/'evidence-review.json').write_text(json.dumps({'timeline':'youtube-source','source_changes':changes,'translation_polish':polish,'merged_groups':merge_log,'external_references':refs},ensure_ascii=False,indent=2));print('source corrections',len(changes),'polishes',len(polish),'merges',len(merge_log))
