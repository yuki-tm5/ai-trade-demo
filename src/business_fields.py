"""T07.1 conservative labeled business fields; never computes business amounts."""
import argparse,json,re,sqlite3,sys
from decimal import Decimal,InvalidOperation
from datetime import datetime
from pathlib import Path
from basic_search import tokens

ALIASES={
 'product_model':['产品型号','型号','Product Model'], 'prototype_id':['原型编号','Prototype ID'],
 'material_name':['材料名称','Material'], 'material_code':['材料编码','Material Code'],
 'specification':['规格','材料规格','Specification'],'quantity':['数量','申请量','已完成副数','Quantity'],
 'unit_price':['单价','报价单价','参考价','Unit Price'],'amount':['金额','总价','Amount'],
 'delivery_date':['交货日期','交期','计划交期','Delivery Date'],
 'document_number':['订单号','订单编号','采购单号','报价单号','Document Number','PO Number'],
 'document_type':['文件类型','Document Type']}
TARGETS=list(ALIASES)

def label(value):
 if not isinstance(value,str):return None
 text=re.sub(r'[（(].*?[)）]','',value).strip().casefold()
 return next((k for k,names in ALIASES.items() if text in {n.casefold() for n in names}),None)

def normalize(kind,value,cell):
 if cell and cell.get('formula'):
  return None,'unsupported','formula_result_not_used; original expression and stored cache preserved separately'
 if value is None or value=='':return None,'not_found','recognized label has no recorded value'
 if kind in ('quantity','unit_price','amount'):
  text=str(value).strip()
  m=re.fullmatch(r'([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*(kg|pcs|mm|m|副|对|公斤)?',text)
  if not m:return None,'ambiguous','numeric expression or locale/meaning not safely interpretable'
  return str(Decimal(m.group(1))),'extracted','literal numeric value only; no arithmetic'
 if kind=='delivery_date':
  text=str(value)
  for fmt in ('%Y-%m-%d','%Y/%m/%d','%Y年%m月%d日','%Y-%m-%dT%H:%M:%S'):
   try:return datetime.strptime(text,fmt).date().isoformat(),'extracted','date as recorded, not a commitment'
   except ValueError:pass
  return None,'ambiguous','date ordering or validity cannot be safely established'
 return value,'extracted','literal labeled value; no business-state inference'

def fields(extraction,sources=None):
 result=[];issues=[];sources=sources or [];name=extraction['filename'];h=extraction['sha256']
 def emit(kind,header,value,loc,record,method,cell=None,unit=None,header_location=None,row_context=None):
  normalized,status,reason=normalize(kind,value,cell)
  f=dict(field_name=header,canonical_field=kind,raw_value=value,normalized_value=normalized,unit_or_currency=unit,source_attachment_id=h,source_filename=name,source_location=loc,source_text='' if value is None else str(value),extraction_method=method,validation_status=status,validation_reason=reason,record_id=record,record_scope='same_explicit_table_row' if row_context is not None else 'isolated_labeled_field',header_location=header_location,row_context=row_context,source_mail_relations=sources)
  # A quantity suffix is evidence only when the entire value matched the safe numeric grammar.
  if kind=='quantity' and status=='extracted':
   quantity_match=re.fullmatch(r'([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*(kg|pcs|mm|m|副|对|公斤)?',str(value).strip())
   suffix=quantity_match.group(2) if quantity_match else None
   if suffix and unit is None:
    f.update(unit_or_currency=suffix,unit_evidence={'field':'raw_value','text':str(value)})
   elif suffix and str(unit).casefold()!=suffix.casefold():
    f.update(validation_status='ambiguous',normalized_value=None,validation_reason='quantity suffix conflicts with explicitly recorded unit column/header',unit_candidates=[unit,suffix])
  if cell:f.update(raw_xml_value=cell.get('raw_xml_value'),data_type=cell.get('data_type'),number_format=cell.get('number_format'),formula_expression=cell.get('formula'),stored_formula_cache=cell.get('cached_result'),cache_status=cell.get('cache_status'))
  filename_ids={t for t in tokens(name) if not re.fullmatch(r'(?:v|rev)\d+',t)}
  if kind=='product_model' and value is not None and filename_ids and str(value).casefold() not in filename_ids:
   f.update(validation_status='ambiguous',validation_reason='filename identifier conflicts with labeled internal model; neither selected as authoritative',normalized_value=None,filename_identifier_candidates=sorted(filename_ids))
  if kind in ('unit_price','amount') and unit and (unit=='$' or '/' in unit or ',' in unit):
   f.update(validation_status='ambiguous',normalized_value=None,validation_reason='currency or price-unit has multiple interpretations')
  result.append(f)
 if extraction['status'] not in ('ok','partial'):
  return dict(source_attachment_id=h,source_filename=name,content_status=extraction['status'],fields=[],issues=[dict(status='unsupported',reason='No usable extracted content; not evidence that business fields do not exist')],field_summary={k:'unsupported' for k in TARGETS},sources=sources)
 for sheet in extraction.get('sheets',[]):
  rows={}
  for c in sheet['cells']:
   row=int(re.search(r'\d+$',c['coordinate']).group());rows.setdefault(row,[]).append(c)
  active=None
  for row,cells in sorted(rows.items()):
   headers=[(i,c,label(c['value'])) for i,c in enumerate(cells) if label(c['value'])]
   populated=[(i,c) for i,c in enumerate(cells) if c['value'] not in (None,'')]
   structural_names={'项目','计价单位','报价','报价说明','加工费','特殊logo','备注','单位','币种','状态','候选工厂','材料状态','周报日期','说明','工厂备注','currency','unit'}
   def looks_like_header(value):
    if not isinstance(value,str):return False
    base=re.sub(r'[（(].*?[)）]','',value).strip().casefold()
    return label(value) is not None or base in structural_names
   structural_count=sum(looks_like_header(c['value']) for _,c in populated)
   if len(headers)>=2 or (len(headers)==1 and len(populated)>=3 and structural_count>=2):
    active=(row,headers);continue
   if len(headers)==1 and active is None:
    i,c,kind=headers[0]
    value=cells[i+1] if i+1<len(cells) else None
    valid_pair=(len(populated)==2 and populated[0][0]==i and populated[1][0]==i+1 and value is not None)
    if valid_pair and kind!='document_type' and looks_like_header(value['value']):valid_pair=False
    if valid_pair and kind in ('product_model','prototype_id') and str(value['value']).strip().casefold() not in tokens(str(value['value'])):valid_pair=False
    if valid_pair:
     emit(kind,c['value'],value['value'],{'sheet':sheet['name'],'cell':value['coordinate']},f"sheet:{sheet['name']}:kv:{row}",'explicit_adjacent_label',value,header_location={'sheet':sheet['name'],'cell':c['coordinate']})
    else:issues.append(dict(status='unsupported',sheet=sheet['name'],row=row,text=[c['value'] for c in cells],reason='Cannot safely distinguish header row from adjacent label/value layout'))
    continue
   if not any(c['value'] is not None for c in cells):active=None;continue
   if active:
    headerrow,head=active
    context={c['coordinate']:c['value'] for c in cells}
    # Unit/currency columns are resolved ONLY within the same explicit table row.
    currency=None;unit=None
    for i,hc in enumerate(rows[headerrow]):
     if i>=len(cells):continue
     if hc['value'] in ('币种','Currency'):currency=cells[i]['value']
     if hc['value'] in ('单位','计价单位','Unit'):unit=cells[i]['value']
    for i,hc,kind in head:
     if i>=len(cells):continue
     cell=cells[i];rawunit=currency if kind in ('unit_price','amount') else unit if kind=='quantity' else None
     if rawunit is None:
      annotation=re.search(r'[（(]([^()（）]+)[)）]',str(hc['value']))
      if annotation and kind in ('quantity','unit_price','amount'):rawunit=annotation.group(1)
     emit(kind,hc['value'],cell['value'],{'sheet':sheet['name'],'cell':cell['coordinate']},f"sheet:{sheet['name']}:table:{headerrow}:row:{row}",'explicit_header_same_row',cell,rawunit,{'sheet':sheet['name'],'cell':hc['coordinate']},context)
 for page in extraction.get('pages',[]):
  if page.get('text') is None:continue
  lines=page['text'].splitlines();active=None
  for i,line in enumerate(lines):
   parts=[p.strip() for p in line.split('|')]
   kinds=[label(v) for v in parts]
   if len(parts)>1 and sum(k is not None for k in kinds)>=2:
    active=(i,parts,kinds);continue
   if len(parts)>1:
    if active and len(parts)==len(active[1]):
     headerline,headers,keys=active
     for col,(header,kind,value) in enumerate(zip(headers,keys,parts),1):
      if kind:
       curr=next((parts[j] for j,s in enumerate(headers) if s=='Currency'),None)
       emit(kind,header,value,{'page':page['page'],'line':i+1,'column':col},f"pdf:{page['page']}:table:{headerline+1}:line:{i+1}",'same_page_delimited_table',unit=curr if kind in ('unit_price','amount') else None,header_location={'page':page['page'],'line':headerline+1,'column':col},row_context=parts)
    else:issues.append(dict(status='unsupported',page=page['page'],line=i+1,text=line,reason='Unlinked table row; no same-page header. No cross-page association guessed'))
    continue
   m=re.match(r'^([^:：]+)[:：]\s*(.*)$',line)
   if m and label(m.group(1)):
    emit(label(m.group(1)),m.group(1),m.group(2),{'page':page['page'],'line':i+1},f"pdf:{page['page']}:label:{i+1}",'explicit_same_line_label');continue
   kind=label(line.strip())
   if kind and i+1<len(lines) and not label(lines[i+1].strip()) and ':' not in lines[i+1]:
    emit(kind,line.strip(),lines[i+1].strip(),{'page':page['page'],'line':i+2},f"pdf:{page['page']}:label:{i+1}",'same_page_adjacent_label',header_location={'page':page['page'],'line':i+1})
  if active and not any(f['source_location'].get('page')==page['page'] and f['extraction_method']=='same_page_delimited_table' for f in result):issues.append(dict(status='unsupported',page=page['page'],reason='Table header without a linked same-page value row'))
 summary={k:('unsupported' if not any(f['canonical_field']==k for f in result) and issues else 'not_found') for k in TARGETS}
 for k in TARGETS:
  values=[f for f in result if f['canonical_field']==k]
  if values:summary[k]='ambiguous' if any(f['validation_status']=='ambiguous' for f in values) else 'unsupported' if any(f['validation_status']=='unsupported' for f in values) else 'extracted' if any(f['validation_status']=='extracted' for f in values) else 'not_found'
 return dict(source_attachment_id=h,source_filename=name,content_status=extraction['status'],fields=result,field_summary=summary,issues=issues,sources=sources,record_policy='No cross-record or cross-file selection; recorded date/type is not a committed delivery or confirmed business state')

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--extractions',required=True);p.add_argument('--sources');a=p.parse_args()
 source_map=json.loads(Path(a.sources).read_text(encoding='utf-8')) if a.sources else {}
 print(json.dumps([fields(r,source_map.get(r['sha256'],[])) for r in json.loads(Path(a.extractions).read_text(encoding='utf-8'))],ensure_ascii=True,indent=2))
