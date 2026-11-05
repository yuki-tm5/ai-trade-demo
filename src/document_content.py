"""T07 deterministic PDF/XLSX extraction and isolated SQLite content index."""
import argparse,io,json,logging,posixpath,sqlite3,sys,zipfile
import xml.etree.ElementTree as ET
from contextlib import closing
from datetime import date,datetime,time
from pathlib import Path
from pypdf import PdfReader
from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from basic_search import readonly,tokens
from offline_mail import digest

NS={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main','r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}

def scalar(value):
 if isinstance(value,(datetime,date,time)):return value.isoformat()
 return value

def extract(path,filename):
 data=Path(path).read_bytes();h=digest(data);ext=Path(filename).suffix.lower()
 result=dict(sha256=h,filename=filename,size_bytes=len(data),status='unsupported',format=ext.lstrip('.'),segments=[],warnings=[])
 try:
  if ext=='.pdf':
   stream=io.StringIO();handler=logging.StreamHandler(stream);logger=logging.getLogger('pypdf');logger.addHandler(handler)
   try:
    reader=PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
     result.update(status='encrypted',encrypted=True);return result
    pages=[]
    for i,page in enumerate(reader.pages,1):
     try:
      text=page.extract_text() or ''
      pages.append(dict(page=i,text=text,status='ok' if text.strip() else 'no_text'))
      if text.strip():result['segments'].append(dict(locator={'page':i},text=text))
     except Exception as exc:pages.append(dict(page=i,text=None,status='page_error',error_type=type(exc).__name__))
    result.update(pages=pages,page_count=len(pages),status='partial' if any(p['status']=='page_error' for p in pages) else 'ok' if result['segments'] else 'no_text')
   finally:
    logger.removeHandler(handler);handler.close();result['warnings']=[x for x in stream.getvalue().splitlines() if x]
  elif ext=='.xlsx':
   wb=load_workbook(io.BytesIO(data),data_only=False,keep_links=False)
   try:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
     book=ET.fromstring(z.read('xl/workbook.xml'))
     rels={r.attrib['Id']:r.attrib['Target'] for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
     paths={s.attrib['name']:posixpath.normpath('xl/'+rels[s.attrib['{'+NS['r']+'}id']]) if not rels[s.attrib['{'+NS['r']+'}id']].startswith('/') else rels[s.attrib['{'+NS['r']+'}id']].lstrip('/') for s in book.find('m:sheets',NS)}
     sheets=[]
     for ws in wb.worksheets:
      if ws.max_row*ws.max_column>100000:raise ValueError('WorksheetCellLimitExceeded')
      xml=ET.fromstring(z.read(paths[ws.title]));raw={c.attrib['r']:c for c in xml.findall('.//m:sheetData/m:row/m:c',NS)}
      merges=[str(r) for r in ws.merged_cells.ranges];cells=[]
      for row in ws.iter_rows():
       for cell in row:
        node=raw.get(cell.coordinate);v=node.find('m:v',NS) if node is not None else None;f=node.find('m:f',NS) if node is not None else None
        rawv=v.text if v is not None else None
        formula=cell.value if cell.data_type=='f' else None
        merged=next((r for r in ws.merged_cells.ranges if cell.coordinate in r),None)
        item=dict(coordinate=cell.coordinate,value=scalar(cell.value),data_type=cell.data_type,python_type=type(cell.value).__name__,raw_xml_value=rawv,raw_xml_type=node.attrib.get('t','n') if node is not None else None,raw_formula=f.text if f is not None else None,formula=formula,formula_attributes=dict(f.attrib) if f is not None else None,cached_result=rawv if formula and rawv is not None else None,cache_status=('present' if rawv is not None else 'missing') if formula else 'not_formula',number_format=cell.number_format,style_id=cell.style_id,empty=cell.value is None,explicit_xml_cell=node is not None,merged_range=str(merged) if merged else None,merged_anchor=merged.start_cell.coordinate if merged else None,merged_placeholder=isinstance(cell,MergedCell))
        cells.append(item)
        # Never index number-format strings, filenames or inferred business fields as content.
        parts=[]
        if cell.value is not None:parts.append(str(scalar(cell.value)))
        if rawv is not None and node.attrib.get('t','n')!='s' and rawv not in parts:parts.append(rawv)
        text='\n'.join(parts)
        if text:result['segments'].append(dict(locator={'sheet':ws.title,'cell':cell.coordinate},text=text))
      sheets.append(dict(name=ws.title,state=ws.sheet_state,max_row=ws.max_row,max_column=ws.max_column,merged_ranges=merges,cells=cells,worksheet_xml_path=paths[ws.title]))
     result.update(status='ok',sheets=sheets,sheet_count=len(sheets),workbook_epoch=wb.epoch.isoformat())
   finally:wb.close()
 except Exception as exc:
  result.update(status='failed',error_type=type(exc).__name__,error_reason=str(exc),segments=[])
 return result

def archive_sources(archive):
 with closing(readonly(archive)) as db:
  db.row_factory=sqlite3.Row
  return [dict(r) for r in db.execute('SELECT ea.id AS relation_id,ea.email_id,ea.attachment_sha256 AS sha256,ea.original_filename AS filename,ea.mime_type,a.stored_path AS path,e.source_path AS email_source_path,e.original_path AS archived_email_path,e.subject,e.sender,e.date_raw FROM email_attachments ea JOIN attachments a ON a.sha256=ea.attachment_sha256 JOIN emails e ON e.id=ea.email_id ORDER BY ea.id')]

def build(sources,index):
 index=Path(index)
 if index.exists():raise FileExistsError('Index exists; choose a fresh path')
 index.parent.mkdir(parents=True,exist_ok=True)
 results=[]
 with closing(sqlite3.connect(index)) as db:
  db.executescript('PRAGMA foreign_keys=ON; CREATE TABLE assets(sha256 TEXT PRIMARY KEY,result_json TEXT NOT NULL); CREATE TABLE sources(relation_id TEXT PRIMARY KEY,sha256 TEXT NOT NULL REFERENCES assets(sha256),metadata_json TEXT NOT NULL); CREATE TABLE segments(id TEXT PRIMARY KEY,sha256 TEXT NOT NULL REFERENCES assets(sha256),locator_json TEXT,text TEXT,text_fold TEXT); CREATE TABLE model_tokens(segment_id TEXT REFERENCES segments(id),token TEXT,PRIMARY KEY(segment_id,token)); CREATE INDEX token_lookup ON model_tokens(token,segment_id);')
  seen=set()
  for source in sources:
   h=digest(Path(source['path']).read_bytes())
   if source.get('sha256') and h!=source['sha256']:raise ValueError('SourceHashMismatch')
   if h not in seen:
    result=extract(source['path'],source['filename']);results.append(result)
    db.execute('INSERT INTO assets VALUES (?,?)',(h,json.dumps(result,ensure_ascii=False)))
    for i,segment in enumerate(result['segments']):
     sid=h+':'+str(i)
     db.execute('INSERT INTO segments VALUES (?,?,?,?,?)',(sid,h,json.dumps(segment['locator'],ensure_ascii=False),segment['text'],segment['text'].casefold()))
     db.executemany('INSERT INTO model_tokens VALUES (?,?)',[(sid,t) for t in tokens(segment['text'])])
    seen.add(h)
   db.execute('INSERT INTO sources VALUES (?,?,?)',(source['relation_id'],h,json.dumps(source,ensure_ascii=False)))
  db.commit()
 return results

def search(index,query,mode='auto'):
 q=query.strip().casefold()
 if not q:raise ValueError('Empty query')
 model=mode=='model' or (mode=='auto' and q in tokens(q))
 if mode not in ('auto','model','keyword') or (model and q not in tokens(q)):raise ValueError('Invalid mode or identifier')
 with closing(readonly(index)) as db:
  db.row_factory=sqlite3.Row
  rows=db.execute('SELECT * FROM segments WHERE id IN (SELECT segment_id FROM model_tokens WHERE token=?) ORDER BY id',(q,)).fetchall() if model else db.execute('SELECT * FROM segments WHERE instr(text_fold,?)>0 ORDER BY id',(q,)).fetchall()
  hits=[]
  for row in rows:
   sources=[json.loads(r[0]) for r in db.execute('SELECT metadata_json FROM sources WHERE sha256=? ORDER BY relation_id',(row['sha256'],))]
   hits.append(dict(attachment_id=row['sha256'],sha256=row['sha256'],location=json.loads(row['locator_json']),matching_text=row['text'],match_method='exact_model_token' if model else 'literal_substring',sources=sources))
 return dict(query=query,mode='model' if model else 'keyword',status='matched' if hits else 'no_match',matches=hits)

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
 b=sub.add_parser('build');b.add_argument('--archive',required=True);b.add_argument('--index',required=True)
 s=sub.add_parser('search');s.add_argument('--index',required=True);s.add_argument('--query',required=True);s.add_argument('--mode',default='auto',choices=['auto','model','keyword'])
 a=p.parse_args()
 try:
  result=build(archive_sources(a.archive),a.index) if a.action=='build' else search(a.index,a.query,a.mode)
  print(json.dumps(result,ensure_ascii=True,indent=2))
 except (ValueError,OSError,sqlite3.Error) as exc:
  print(json.dumps(dict(status='error',error_type=type(exc).__name__,message=str(exc)),ensure_ascii=True),file=sys.stderr);raise SystemExit(2)
