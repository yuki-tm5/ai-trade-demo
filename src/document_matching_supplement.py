"""T06.1 explicit metadata evidence; no automatic production release."""
import argparse,json,re,sqlite3
from pathlib import Path
from contextlib import closing
from basic_search import tokens,readonly

def identifiers(text):
 return {t for t in tokens(text) if not re.fullmatch(r'(?:v|rev)[0-9]+',t)}
def classify(name):
 for key,word in [('engineering','工程图'),('quotation','报价单'),('weekly','周报')]:
  if word in name:return key
 return 'unknown'
def analyze(archive,model,document_type='all'):
 q=model.strip().casefold()
 if not q or q not in tokens(q): raise ValueError('One complete identifier required')
 if document_type not in ('all','engineering','quotation','weekly','unknown'):raise ValueError('Unsupported document type')
 with closing(readonly(archive)) as db:
  db.row_factory=sqlite3.Row
  rows=db.execute('SELECT ea.*,a.stored_path,e.subject,e.body,e.date_utc,e.date_raw,e.sender,e.source_path,e.original_path FROM email_attachments ea JOIN attachments a ON a.sha256=ea.attachment_sha256 JOIN emails e ON e.id=ea.email_id ORDER BY ea.id').fetchall()
 groups={}
 for row in rows:
  r=dict(row);name=r['original_filename'] or '';kind=classify(name)
  if document_type!='all' and kind!=document_type:continue
  fields={'filename':name,'subject':r['subject'] or '', 'body':r['body'] or ''}
  # Capture complete identifiers, then validate with the shared T05 token rules.
  identifier=r'([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)(?![A-Za-z0-9_-])'
  def pairs(phrase):
   found=[]
   for m in re.finditer(r'产品\s*'+identifier+r'\s*'+phrase+r'\s*'+identifier,fields['body']):
    a,b=m.group(1).casefold(),m.group(2).casefold()
    if a in tokens(a) and b in tokens(b):found.append((a,b,m.group(0)))
   return found
  mappings=pairs('对应原型')
  negative_pairs=pairs('不对应原型')
  bases=[dict(field=f,text=v,method='exact_identifier') for f,v in fields.items() if q in tokens(v)]
  named=identifiers(name);subject_ids=identifiers(fields['subject'])
  relevant=[(a,b,text) for a,b,text in mappings if q in (a,b) and (a in named or b in named)]
  if not bases and not relevant:continue
  negative=[]
  for a,b,text in negative_pairs:
   if q in (a,b) and (a in named or b in named):negative.append(text)
  attachment_count=sum(other['email_id']==r['email_id'] for other in rows)
  for m in re.finditer(r'产品\s*'+identifier+r'\s*(?:与本附件无关|与该附件无关)',fields['body']):
   if m.group(1).casefold()==q and attachment_count==1:negative.append(m.group(0))
  for text in negative:bases.append(dict(field='body',text=text,method='explicit_non_association'))
  for a,b,text in relevant:bases.append(dict(field='body',text=text,method='explicit_product_prototype_correspondence'))
  declared_pairs=[{a,b} for a,b,_ in mappings]
  conflict=bool(named and subject_ids and named!=subject_ids and not any(named|subject_ids<=pair for pair in declared_pairs))
  association='evidence_conflict' if negative and relevant else 'explicitly_unrelated' if negative else 'candidate_conflict' if conflict else 'candidate_with_explicit_correspondence' if relevant else 'candidate' if q in named else 'weak_mention'
  evidence=[]
  for field,text in fields.items():
   if field=='filename':continue
   for line in text.splitlines():
    if any(w in line for w in ('审核','批准','审批','投产','对应原型')):
     evidence.append(dict(field=field,text=line,source_email=r['source_path']))
  denial=any('图纸尚未批准' in fields[f] or '图纸未批准' in fields[f] for f in ('subject','body'))
  factory=any('候选工厂审核通过' in fields[f] for f in ('subject','body'))
  positive=name+' 已批准用于生产' in fields['body']
  revision=re.search(r'(?i)(?:^|_)V(\d+)(?=\.|_|$)',name)
  h=r['attachment_sha256'];g=groups.setdefault(h,dict(sha256=h,archived_path=r['stored_path'],document_type=kind,filenames=[],sources=[],version_status='待确认',effective_production_version=False))
  if name not in g['filenames']:g['filenames'].append(name)
  g['sources'].append(dict(relation_id=r['id'],email_id=r['email_id'],source_path=r['source_path'],archived_email_path=r['original_path'],filename=name,subject=r['subject'],date_utc=r['date_utc'],date_raw=r['date_raw'],sender=r['sender'],association_status=association,association_uncertainty='explicit_negative_not_a_positive_candidate' if association=='explicitly_unrelated' else 'requires_human_confirmation',association_basis=bases,document_type_basis=dict(field='filename',text=name,rule=kind),revision=int(revision.group(1)) if revision else None,revision_basis=dict(field='filename',text=name),approval_observation='explicitly_not_approved' if denial else 'factory_qualification_only' if factory else 'production_approval_claim_unverified' if positive else 'no_reliable_document_approval',version_evidence=evidence,version_reason='No verified approval authority, signature or document-scoped release register; filename and mail date cannot establish effective production validity'))
 docs=list(groups.values());dates=[s['date_utc'] for d in docs for s in d['sources'] if s['date_utc']];latest=max(dates,default=None)
 for d in docs:d['latest_received']=any(s['date_utc']==latest for s in d['sources']) if latest else False
 # Route whole content groups conservatively; per-email judgments remain untouched.
 normal=[];uncertain=[];audit=[]
 positive={'candidate','candidate_with_explicit_correspondence'}
 audit_only={'explicitly_unrelated','candidate_conflict','evidence_conflict'}
 for d in docs:
  statuses={source['association_status'] for source in d['sources']}
  if statuses <= positive:bucket=normal;label='normal_candidate'
  elif statuses <= audit_only:bucket=audit;label='audit_only'
  else:bucket=uncertain;label='requires_confirmation'
  d['association_bucket']=label
  bucket.append(d)
 evidence_latest=latest
 candidate_dates=[s['date_utc'] for d in normal+uncertain for s in d['sources'] if s['date_utc']]
 latest=max(candidate_dates,default=None)
 for d in docs:d['latest_received']=d['association_bucket']!='audit_only' and any(s['date_utc']==latest for s in d['sources']) if latest else False
 status='candidates' if normal else 'uncertain_candidates' if uncertain else 'audit_only' if audit else 'no_match'
 return dict(model=model,document_type=document_type,status=status,documents=normal,uncertain_documents=uncertain,audit_documents=audit,latest_evidence_received_utc=evidence_latest,latest_received_utc=latest,latest_effective_production_version=None,limitations=['PDF/XLSX contents and CAD geometry not parsed','Document type inferred from explicit filename labels only','Correspondence requires explicit product-to-prototype wording, never fuzzy identity','Explicitly unrelated and evidence-conflict records are retained for audit, not positive matches','Negative wording is scoped to the queried identifier; generic attachment denial requires a single attachment; unrecognized language remains uncertain','Approval claims lack verified authority; production release always requires human confirmation'])
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--archive',required=True);p.add_argument('--model',required=True);p.add_argument('--type',default='all');a=p.parse_args()
 try:print(json.dumps(analyze(a.archive,a.model,a.type),ensure_ascii=True,indent=2))
 except (ValueError,OSError,sqlite3.Error) as exc:
  import sys
  print(json.dumps(dict(status='error',error_type=type(exc).__name__,message=str(exc))),file=sys.stderr);raise SystemExit(2)
