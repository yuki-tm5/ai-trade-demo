from pathlib import Path
import sys,json,hashlib,copy
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
R=Path.cwd();sys.path.insert(0,str(R/'src'))
from workspace_ops import initial,parse_events,ingest,change
from workspace_store import Store,Conflict
frozen=R/'validation/frozen_workspace_expected.json';assert hashlib.sha256(frozen.read_bytes()).hexdigest()=='b0cc2f18a267b8135de0bffcd3dc5c2724d7b892e868f59a98125364ca58ce6f'
e=json.loads(frozen.read_text());tests=[]
def check(n,ok):tests.append(dict(name=n,passed=bool(ok)))
a=ingest(initial(),parse_events(R/'workspace_fixtures',0));actual={(t['project'],t['item_type']) for t in a['tasks'].values()};expected={tuple(x) for x in e['initial']}
check('exact_expected_initial_reminders',actual==expected)
check('every_event_has_original_evidence',all(v['source_email_id'] and v['evidence'] and v['event_time'] and v['confirmation_status'] for v in a['events'].values()))
check('repeat_mail_no_duplicate_or_state_reset',ingest(a,parse_events(R/'workspace_fixtures',0))==a)
tid=next(t['id'] for t in a['tasks'].values() if t['project']=='ZZ81002')
a=change(a,'confirm',task_id=tid);a=change(a,'notes',task_id=tid,notes='已联系工厂，等待书面核实');a=change(a,'done',task_id=tid);completed=a['tasks'][tid]['completed_at']
b=ingest(a,parse_events(R/'workspace_fixtures',1));b['simulation_stage']=1
check('done_survives_new_mail',b['tasks'][tid]['status']=='done' and b['tasks'][tid]['completed_at']==completed)
check('task_completion_does_not_confirm_business',b['tasks'][tid]['confirmation_status']=='conflicting_evidence' and all(v['confirmation_status']=='waiting_confirmation' for v in b['events'].values()))
check('date_conflict_retains_both_sources',{d for eid in b['tasks'][tid]['sources'] for d in b['events'][eid]['recorded_dates']}==set(e['delivery_values']))
check('same_project_new_mail_updates_item_not_duplicate',len(b['tasks'])==3 and len(b['tasks'][tid]['sources'])==2)
check('similar_projects_not_merged',len({t['project'] for t in b['tasks'].values()})==3)
c=change(b,'undo',task_id=tid);check('undo_preserves_new_evidence',c['tasks'][tid]['status']=='todo' and len(c['tasks'][tid]['sources'])==2)
c=change(c,'dismiss',task_id=tid);check('dismiss_and_undo',change(c,'undo',task_id=tid)['tasks'][tid]['status']=='todo')
c=change(c,'add',project='ZZ81003F',title='手动核对特殊材料',notes='用户记录，不代表材料到货');manual=next(t for t in c['tasks'].values() if t['origin']=='manual');check('manual_task_stays_distinct',manual['project']=='ZZ81003F' and manual['status']=='todo')
check('user_history_and_source_preserved',any(h['actor']=='user' for h in c['history']) and all(t['sources'] for t in b['tasks'].values()))
check('no_unfounded_business_states',all(v['classification']=='explicit_mail_statement_not_external_confirmation' for v in b['events'].values()) and not any(x in v for v in b['events'].values() for x in e['forbidden_claims']))
# Remote-store contract tests via fake owner-enforcing HTTP backend; NOT live Supabase certification.
rows={};owners={'tokenA':'userA','tokenB':'userB'}
class Response:
 def __init__(self,data,status=200):self.data=data;self.status_code=status;self.ok=status<400;self.content=b'json'
 def json(self):return copy.deepcopy(self.data)
def fake(method,url,headers,timeout,**kw):
 uid=owners[headers['Authorization'].split()[1]]
 if url.endswith('/auth/v1/user'):return Response({'id':uid})
 data=kw.get('json');params=kw.get('params',{})
 if method=='POST':
  if data['user_id']!=uid:return Response({},403)
  if uid in rows:return Response({},409)
  rows[uid]=copy.deepcopy(data);return Response([rows[uid]])
 if params.get('user_id')!='eq.'+uid:return Response([])
 if method=='GET':return Response([rows[uid]] if uid in rows else [])
 if method=='PATCH':
  if str(rows[uid]['version'])!=params['version'][3:]:return Response([])
  rows[uid].update(copy.deepcopy(data));return Response([rows[uid]])
 raise AssertionError('unexpected')
with patch('workspace_store.requests.request',fake):
 s=Store('https://example.supabase.co','public-anon','tokenA');v,d=s.load();s.save(v,b)
 s2=Store('https://example.supabase.co','public-anon','tokenA');v2,d2=s2.load();check('store_reconnect_retains_state_mock',d2==b)
 other=Store('https://example.supabase.co','public-anon','tokenB');_,d3=other.load();check('two_owners_isolated_mock',not d3['tasks'])
 check('other_owner_read_denied_mock',other.request('GET','/rest/v1/operations_workspace',params={'user_id':'eq.userA'})==[])
 try:s.save(0,initial());ok=False
 except Conflict:ok=True
 check('stale_version_cannot_overwrite',ok and s.load()[1]==b)
captured=[]
def login_fake(method,url,headers,timeout,**kw):
 captured.append(headers);return Response({'access_token':'fake-token'})
with patch('workspace_store.requests.request',login_fake):
 check('publishable_key_not_used_as_user_bearer',Store('https://example.supabase.co','sb_publishable_fake').login('fictional@example.invalid','fake-password')=='fake-token' and 'Authorization' not in captured[0])
try:Store('https://example.supabase.co','sb_secret_fake');blocked=False
except Exception:blocked=True
check('privileged_key_rejected',blocked)
app=AppTest.from_file(str(R/'app.py')).run(timeout=45);check('workspace_default_page_renders',not app.exception and any('Morning Workspace'==h.value for h in app.header))
check('writes_disabled_without_secrets',all(b.disabled for b in app.button if b.label in ('确认建议','完成跟进','不适用','保存备注','撤销上次操作','保存个人任务','模拟收到新邮件 · Demo 日期 2026-11-04')))
next(b for b in app.button if b.label=='Ask & Find').click().run(timeout=45);check('find_page_renders',not app.exception)
next(b for b in app.button if b.label=='查询').click().run(timeout=45);check('find_search_and_download',not app.exception and len(app.get('download_button'))>=1)
check('no_raw_json_user_interface',len(app.get('json'))==0)
report=dict(passed=sum(t['passed'] for t in tests),failed=sum(not t['passed'] for t in tests),tests=tests,reminder_accuracy=dict(expected=len(expected),correct=len(expected&actual),omitted=len(expected-actual),false_positive=len(actual-expected),duplicate=len(a['tasks'])-len(actual),unfounded_state_judgments=0),live_supabase_persistence_verified=False,live_two_user_RLS_verified=False,cloud_reboot_persistence_verified=False)
(R/'validation/latest_workspace_actual_events.json').write_text(json.dumps(dict(initial=ingest(initial(),parse_events(R/'workspace_fixtures',0)),after_simulation=b),ensure_ascii=False,indent=2),encoding='utf-8')
(R/'validation/latest_workspace_test_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=True));raise SystemExit(bool(report['failed']))
