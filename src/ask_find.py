"""T08 deterministic integration over existing archives, content and field modules."""
import argparse,json,sqlite3,sys
from pathlib import Path
from contextlib import closing
from basic_search import readonly,tokens
from document_content import archive_sources,search as content_search
from document_matching_supplement import analyze
from business_fields import fields as business_fields,TARGETS
from offline_mail import digest

INTENTS={'factory_quotes','purchase_record','material_purchase','engineering_versions','quote_documents'}
DEFAULT_FIELDS={'factory_quotes':['product_model','unit_price','quantity','amount'],'purchase_record':['product_model','document_number','quantity','amount'],'material_purchase':['product_model','material_name','material_code','specification','quantity','delivery_date'],'engineering_versions':['product_model','prototype_id'],'quote_documents':['product_model','quantity','unit_price','amount','document_type']}

def config(path):
 path=Path(path).resolve();c=json.loads(path.read_text(encoding='utf-8'));c['_base']=path.parent;return c

def location(c,value):return (c['_base']/value).resolve()

def resolve_file(c,ref):
 ds=c['datasets'].get(ref.get('dataset'))
 if not ds:raise ValueError('Unknown dataset')
 h=ref.get('sha256','')
 if len(h)!=64 or any(x not in '0123456789abcdef' for x in h):raise ValueError('Invalid attachment SHA')
 rel='attachments/'+h
 if ref.get('relative_path')!=rel:raise ValueError('Invalid relative attachment reference')
 root=location(c,ds['archive_root']);p=(root/rel).resolve()
 if not p.is_relative_to(root) or not p.is_file() or digest(p.read_bytes())!=h:raise ValueError('Missing/changed archived attachment')
 return p

def build_catalog(config_path):
 c=config(config_path);target=location(c,c['catalog'])
 if target.exists():raise FileExistsError('Catalog exists; refusing overwrite')
 reports=[]
 with closing(sqlite3.connect(target)) as out:
  out.execute('CREATE TABLE documents(dataset TEXT,sha256 TEXT,content_json TEXT,fields_json TEXT,sources_json TEXT,PRIMARY KEY(dataset,sha256))')
  for name,ds in c['datasets'].items():
   sources=archive_sources(location(c,ds['archive_root'])/'archive.sqlite3');group={}
   for s in sources:group.setdefault(s['sha256'],[]).append(s)
   with closing(readonly(location(c,ds['content_index']))) as content:
    assets={h:json.loads(r) for h,r in content.execute('SELECT sha256,result_json FROM assets')}
   cached={}
   if ds.get('field_index'):
    with closing(readonly(location(c,ds['field_index']))) as old:
     cached={h:json.loads(r) for h,r in old.execute('SELECT sha256,result_json FROM documents')}
   for h,relations in group.items():
    if h not in assets:reports.append(dict(dataset=name,sha256=h,status='missing_content_link'));continue
    # Corrected T07.1 field snapshot is reused when available; supplement uses existing extractor on stored content.
    f=cached.get(h) or business_fields(assets[h],relations)
    out.execute('INSERT INTO documents VALUES (?,?,?,?,?)',(name,h,json.dumps(assets[h],ensure_ascii=False),json.dumps(f,ensure_ascii=False),json.dumps(relations,ensure_ascii=False)))
   reports.append(dict(dataset=name,archived_assets=len(group),content_linked=len(set(group)&set(assets)),content_standalone_assets_excluded=len(set(assets)-set(group)),cached_fields_reused=len(set(group)&set(cached)),source_relations=len(sources)))
  out.commit()
 return reports

def ask(config_path,query):
 c=config(config_path)
 output=dict(query=query,intent=query.get('intent'),query_status='unsupported_intent',matched_product_ids=[],documents=[],uncertain_documents=[],audit_documents=[],fields=[],warnings=[])
 intent=query.get('intent')
 if intent not in INTENTS:
  output['warnings']=['Only explicit predefined intent + complete product_id JSON is supported; no natural-language semantics'];return output
 product=str(query.get('product_id','')).strip();q=product.casefold()
 if not q or q not in tokens(q):output.update(query_status='invalid_input',warnings=['One complete product identifier is required']);return output
 selected=query.get('dataset');names=[selected] if selected else list(c['datasets'])
 if any(n not in c['datasets'] for n in names):output.update(query_status='invalid_input',warnings=['Unknown dataset']);return output
 requested=query.get('field_names',DEFAULT_FIELDS[intent])
 if not isinstance(requested,list) or any(f not in TARGETS for f in requested):output.update(query_status='invalid_input',warnings=['Unsupported field list']);return output
 warnings=['Recorded delivery dates are not commitments','Quotation/RFQ evidence does not confirm an order','No latest effective production drawing or price authority inferred','Missing indexed evidence is not proof an event did not happen; other channels may exist']
 for dataset in names:
  ds=c['datasets'][dataset];root=location(c,ds['archive_root'])
  linked=analyze(root/'archive.sqlite3',product)
  routing={}
  for bucket in ('documents','uncertain_documents','audit_documents'):
   for d in linked[bucket]:routing[d['sha256']]=(bucket,d)
  inside=content_search(location(c,ds['content_index']),product,'model');inside_hashes={h['sha256'] for h in inside['matches']}
  with closing(readonly(location(c,c['catalog']))) as db:
   rows=db.execute('SELECT sha256,content_json,fields_json,sources_json FROM documents WHERE dataset=? ORDER BY sha256',(dataset,)).fetchall()
  for h,content_raw,field_raw,source_raw in rows:
   content=json.loads(content_raw);data=json.loads(field_raw);relations=json.loads(source_raw);all_fields=data['fields']
   internal=[f for f in all_fields if f['canonical_field'] in ('product_model','prototype_id') and str(f['raw_value']).casefold()==q]
   strong_internal=[f for f in internal if f['validation_status']=='extracted']
   if h not in routing and h not in inside_hashes:continue
   if h in routing:bucket,assoc=routing[h]
   else:
    bucket='documents' if strong_internal else 'uncertain_documents'
    assoc=dict(sources=[dict(email_id=s['email_id'],relation_id=s['relation_id'],source_path=s['email_source_path'],association_status='content_labeled_identifier' if strong_internal else 'weak_content_mention',association_basis=[dict(field='attachment_content',text=f['source_text'],location=f['source_location']) for f in internal]) for s in relations],version_status='待确认',effective_production_version=False)
   if bucket=='uncertain_documents' and strong_internal and all(s['association_status']=='weak_mention' for s in assoc['sources']):
    bucket='documents'
    assoc=dict(assoc,integration_corroboration=[dict(location=f['source_location'],text=f['source_text'],method='explicit_internal_labeled_identifier') for f in strong_internal])
   explicit_pair=any(s['association_status']=='candidate_with_explicit_correspondence' for s in assoc['sources'])
   model_fields=[f for f in all_fields if f['canonical_field']=='product_model' and f['raw_value'] is not None]
   if bucket=='documents' and model_fields and not internal and not explicit_pair:
    bucket='audit_documents';assoc=dict(assoc,integration_conflict='Internal model records disagree with metadata product; no cross-product recommendation')
   fnames=sorted({s['filename'] for s in relations});type_values={str(f['raw_value']) for f in all_fields if f['canonical_field']=='document_type'}
   if intent=='factory_quotes':eligible=any('工厂报价' in n for n in fnames) or '工厂报价' in type_values
   elif intent=='quote_documents':eligible=any('报价' in n or 'quote' in n.casefold() for n in fnames) or bool(type_values&{'工厂报价','报价','Quotation'})
   elif intent=='purchase_record':eligible=any('采购单' in n for n in fnames) or bool(type_values&{'采购单','Purchase Order'})
   elif intent=='material_purchase':eligible=any(f['canonical_field'] in ('material_name','material_code','specification') for f in all_fields)
   else:eligible=any('工程图' in n for n in fnames)
   if not eligible:continue
   records={f['record_id'] for f in internal}
   # Multiple product rows are never cross-joined; unscoped PDF scalars require a single explicit internal product.
   unique_models={str(f['raw_value']).casefold() for f in model_fields}
   same_row=[f for f in all_fields if f['record_id'] in records]
   if len(unique_models)==1 and (internal or explicit_pair):
    relevant=[f for f in all_fields if f['record_id'] in records or f['record_scope']=='isolated_labeled_field']
   else:relevant=same_row
   docnum=query.get('document_number')
   if docnum and not any(f['canonical_field']=='document_number' and str(f['raw_value']).casefold()==str(docnum).casefold() for f in relevant):continue
   ref=dict(dataset=dataset,attachment_id=h,sha256=h,relative_path='attachments/'+h)
   resolve_file(c,ref)
   document=dict(attachment_id=h,filenames=fnames,file_reference=ref,content_status=content['status'],sources=relations,association_evidence=assoc,version_status='待确认',latest_effective_production_version=None)
   output[bucket].append(document)
   if bucket=='audit_documents':continue
   output['matched_product_ids']=[product]
   selected_fields=[f for f in relevant if f['canonical_field'] in requested]
   for field in selected_fields:
    f=dict(field,source_email_ids=[s['email_id'] for s in relations],file_reference=ref,record_id=dataset+':'+h+':'+field['record_id'],original_record_id=field['record_id'],document_bucket=bucket)
    if bucket=='uncertain_documents' and f['validation_status']=='extracted':f.update(validation_status='ambiguous',normalized_value=None,association_warning='Attachment association requires confirmation')
    output['fields'].append(f)
   for missing in sorted(set(requested)-{f['canonical_field'] for f in selected_fields}):
    state='unsupported' if content['status'] not in ('ok','partial') else 'not_found'
    output['fields'].append(dict(field_name=missing,canonical_field=missing,raw_value=None,normalized_value=None,unit_or_currency=None,source_attachment_id=h,source_filename=fnames[0],source_email_ids=[s['email_id'] for s in relations],file_reference=ref,source_location=None,source_text='',extraction_method='explicit_absence_of_supported_labeled_evidence',validation_status=state,document_bucket=bucket,validation_reason='Cannot extract content' if state=='unsupported' else 'No reliable labeled field in selected product record; not business nonexistence'))
   if content['status'] not in ('ok','partial'):warnings.append('Attachment content unavailable: '+h)
 if output['documents']:
  essential=query.get('field_names',{'factory_quotes':['unit_price'],'purchase_record':['quantity','amount'],'material_purchase':['quantity','delivery_date'],'engineering_versions':[],'quote_documents':[]}[intent])
  partial=any(any(not any(f['source_attachment_id']==d['attachment_id'] and f['canonical_field']==k and f['validation_status']=='extracted' for f in output['fields']) for k in essential) for d in output['documents'])
  output['query_status']='partial' if partial else 'matched'
 elif output['uncertain_documents']:output['query_status']='uncertain'
 elif output['audit_documents']:output['query_status']='audit_only'
 else:output['query_status']='not_found'
 output['warnings']=warnings
 return output

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);g=p.add_mutually_exclusive_group(required=True);g.add_argument('--build',action='store_true');g.add_argument('--query-file');a=p.parse_args()
 try:
  r=build_catalog(a.config) if a.build else ask(a.config,json.loads(Path(a.query_file).read_text(encoding='utf-8')))
  print(json.dumps(r,ensure_ascii=True,indent=2))
 except (ValueError,OSError,sqlite3.Error) as exc:print(json.dumps(dict(query_status='error',error_type=type(exc).__name__,message=str(exc))),file=sys.stderr);raise SystemExit(2)
