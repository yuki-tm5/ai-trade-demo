"""Deterministic evidence updates and user-owned task transitions. No mailbox access."""
import copy,hashlib,json,re,uuid
from datetime import datetime,timezone
from email import policy
from email.parser import BytesParser
from basic_search import tokens

def now():return datetime.now(timezone.utc).isoformat()
def initial():return dict(tasks={},events={},history=[],simulation_stage=0)
def parse_events(folder,stage):
 result=[]
 for item in json.loads((folder/'manifest.json').read_text()):
  if item['stage']>stage:continue
  raw=(folder/item['file']).read_bytes()
  if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('Demo evidence changed')
  m=BytesParser(policy=policy.default).parsebytes(raw);body=m.get_body(preferencelist=('plain',)).get_content();subject=str(m['Subject'])
  products=sorted(t.upper() for t in tokens(subject) if re.fullmatch(r'zz\d+',t))
  if len(products)!=1:continue
  project=products[0]
  if '报价已发送' in body and '尚未收到正式订单确认' in body:kind='order_confirmation';suggestion='建议核实订单状态；已发送报价不代表获得订单';observation='邮件明确表述报价已发送，订单确认待核实';state='waiting_confirmation'
  elif re.search(r'材料预计交货日期：\d{4}-\d{2}-\d{2}',body):kind='delivery_confirmation';suggestion='联系供应商核对材料预计交期';observation='邮件记录预计交货日期，不代表承诺或到货';state='waiting_confirmation'
  elif '候选工厂审核通过' in body and '图纸尚未批准' in body:kind='drawing_confirmation';suggestion='建议核实图纸生产批准证据';observation='邮件表述候选工厂资格通过，图纸批准待确认';state='waiting_confirmation'
  else:continue
  evidence=body.strip();eid=str(m['Message-ID']);event_id=hashlib.sha256((eid+kind).encode()).hexdigest()
  dates=re.findall(r'材料预计交货日期：(\d{4}-\d{2}-\d{2})',body)
  result.append(dict(id=event_id,project=project,item_type=kind,observation=observation,suggestion=suggestion,source_email_id=eid,evidence=evidence,subject=subject,event_time=str(m['Date']),simulation_date=item['simulation_date'],confirmation_status=state,classification='explicit_mail_statement_not_external_confirmation',recorded_dates=dates,source_file=item['file']))
 return result

def ingest(state,events):
 s=copy.deepcopy(state)
 for e in events:
  if e['id'] in s['events']:continue
  s['events'][e['id']]=e
  key=hashlib.sha256((e['project']+':'+e['item_type']).encode()).hexdigest()
  if key not in s['tasks']:
   s['tasks'][key]=dict(id=key,project=e['project'],item_type=e['item_type'],title=e['suggestion'],status='suggested',notes='',completed_at=None,sources=[],origin='system_suggestion',created_at=now())
  task=s['tasks'][key];task['sources'].append(e['id']);task['updated_at']=now()
  dates={d for eid in task['sources'] for d in s['events'][eid]['recorded_dates']}
  task['confirmation_status']='conflicting_evidence' if len(dates)>1 else 'waiting_confirmation'
  s['history'].append(dict(id=uuid.uuid4().hex,time=now(),actor='system',action='evidence_added',task_id=key,event_id=e['id']))
 return s

def change(state,action,task_id=None,project=None,title=None,notes=None):
 s=copy.deepcopy(state)
 if action=='add':
  if not project or not title or not title.strip():raise ValueError('Project and task title required')
  if len(title)>500 or len(notes or '')>10000:raise ValueError('Input too long')
  task_id=uuid.uuid4().hex;s['tasks'][task_id]=dict(id=task_id,project=project.strip(),item_type='manual_progress',title=title.strip(),status='todo',notes=notes or '',completed_at=None,sources=[],origin='manual',created_at=now(),confirmation_status='manual_record_not_business_fact');before=None
 else:
  if task_id not in s['tasks']:raise ValueError('Unknown task')
  before=copy.deepcopy(s['tasks'][task_id]);task=s['tasks'][task_id]
  if action in ('confirm','done','dismiss'):
   task['status']={'confirm':'todo','done':'done','dismiss':'dismissed'}[action];task['completed_at']=now() if action=='done' else None
  elif action=='notes':
   if len(notes or '')>10000:raise ValueError('Notes too long')
   task['notes']=notes or ''
  elif action=='undo':
   entry=next((h for h in reversed(s['history']) if h.get('task_id')==task_id and h.get('actor')=='user' and not h.get('undone') and h['action']!='undo'),None)
   if not entry:raise ValueError('Nothing to undo')
   # Restore only manual fields; new mail evidence must survive undo.
   if entry['before'] is None:task['status']='dismissed';task['completed_at']=None
   else:
    for key in ('status','notes','completed_at'):task[key]=entry['before'][key]
   entry['undone']=True
  else:raise ValueError('Unsupported action')
 s['history'].append(dict(id=uuid.uuid4().hex,time=now(),actor='user',action=action,task_id=task_id,before=before,classification='user_task_operation_not_business_event'))
 return s
