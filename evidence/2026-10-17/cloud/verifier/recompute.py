# Copied from the private verification folder on 2026-10-17. The only change: the two absolute paths
# below now point at this evidence folder and this repository, so the script runs from a clone.
# The library (backend/data/corpus/paragraphs.json) is not committed: build it with `make corpus`.
import json, re, unicodedata, collections, os
C=os.path.join(os.path.dirname(os.path.abspath(__file__)),'..')+'/'
A=os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','..','..','..')+'/'
paras=json.load(open(A+'backend/data/corpus/paragraphs.json'))
exp=json.load(open(A+'backend/argus_api/samples/expected.json'))
idx=collections.defaultdict(list)
for p in paras: idx[(p['instrument_ref'],p['para'])].append(p['text'])
def norm(s): s=unicodedata.normalize('NFKC',s); s=s.replace('’',"'").replace('‘',"'").replace('“','"').replace('”','"'); return re.sub(r'\s+',' ',s).strip()
def qok(c):
    texts=idx.get((c.get('instrument_ref'),c.get('para')))
    if not texts:
        # try children paras
        texts=[p['text'] for p in paras if p['instrument_ref']==c.get('instrument_ref') and (p['para']==c.get('para') or p['para'].startswith(str(c.get('para'))+'('))]
    q=norm(c.get('quote') or '')
    return bool(texts) and any(q in norm(t) for t in texts), bool(texts)
def check(runid,r,policy):
    out={}
    run=r['run']; f=r['findings']; st=r['steps']; au=r['audit']
    if isinstance(f,list): f=dict(f)
    out['readback']=all(v==200 for v in r['statuses'].values())
    out['done']=run.get('status')=='done'
    out['failed0']=(run.get('summary') or {}).get('failed')==0 and not any(d.get('error') for d in f.values())
    out['vertex']=run.get('provider')=='vertex'
    out['ids']=sorted(f)==sorted(exp['policies'][policy])
    ev=[a['event'] for a in au]; seq=[a['seq'] for a in au]
    out['audit']=ev[0]=='run_started' and 'run_finished' in ev and seq==list(range(1,len(seq)+1)) and sum(e in('finding_recorded','clause_failed') for e in ev)==8
    bad=[];n=0
    for cid,d in f.items():
        for k in ('citation','replaced_citation'):
            c=d.get(k)
            if isinstance(c,dict):
                n+=1; ok,found=qok(c)
                if not ok: bad.append((cid,k,c.get('instrument_ref'),c.get('para'),found))
    out['quotes']=(n,bad)
    out['model_turn']= 'model turn' in json.dumps(r).lower()
    if policy=='policy_a':
        c2=f['c2']; out['c2']=(c2['label'],c2['replaced_citation']['instrument_ref'],c2['replaced_citation']['withdrawn_by'],c2['citation']['instrument_ref'],c2['citation']['para'],c2['citation']['updated_as_on'])
        rev=[s for s in st if s.get('clause_id')=='c6' and s.get('kind')=='monitor' and isinstance(s.get('verdict'),dict) and s['verdict'].get('action')=='revise']
        out['c6']=(len(rev),f['c6']['label'])
    else:
        stops=[s for s in st if s.get('clause_id')=='c2' and s.get('kind')=='monitor' and isinstance(s.get('verdict'),dict) and s['verdict'].get('action')=='stop']
        det=[ch.get('detail','') for s in stops for ch in s['verdict'].get('checks',[]) if ch.get('name')=='no_injection']
        out['b_c2']=(len(stops), any('pre-cleared' in d.lower() for d in det))
    out['labels']=sum(f.get(c,{}).get('label')==g['label'] for c,g in exp['policies'][policy].items())
    out['times']=(run.get('started_at'),run.get('finished_at'))
    return out
for fn in ['G-SMOKE','G-HERO']:
    d=json.load(open(C+fn+'.data.json'))
    for rid,r in d.items():
        pol=r['run'].get('policy_id')
        print(fn,rid,pol,check(rid,r,pol))
