from pathlib import Path
import sys,json,logging,uuid
from tempfile import TemporaryDirectory
import streamlit as st
ROOT=Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT/'src'))
from ask_find import ask,config,resolve_file
from workspace_ops import initial,parse_events,ingest,change
from workspace_increment import build_snapshot
from workspace_store import Store,BackendError,Conflict,public_config
@st.cache_resource
def incremental_snapshot():
    storage = TemporaryDirectory(prefix='trade-demo-')
    try:
        path, events = build_snapshot(ROOT, Path(storage.name))
    except Exception:
        storage.cleanup()
        raise
    return storage, path, events

st.set_page_config(page_title='AI Trade · Operations Workspace',layout='wide')
st.title('AI Trade Operations · 规则检索 Demo')
st.caption('Demo Data · 完全虚构 · 无真实QQ/微信连接 · 无AI语义理解')
try:settings=dict(st.secrets)
except Exception:settings={}
try:credentials=public_config(settings)
except BackendError as e:st.error(str(e));credentials=None
configured=credentials is not None
search_config=ROOT/'config.json';store=None;can_write=False;version=0;state=ingest(initial(),parse_events(ROOT/'workspace_fixtures',0))
if configured:
 if st.session_state.get('access_token'):
  st.session_state.pop('login_password',None)
  store=Store(*credentials,token=st.session_state['access_token'])
  try:
   version,state=store.load()
   pending_config=search_config
   events=parse_events(ROOT/'workspace_fixtures',state['simulation_stage'])
   if state['simulation_stage']>=1:
    try:_,pending_config,new_events=incremental_snapshot()
    except Exception as exc:
     logging.getLogger('ai_trade.demo').error('snapshot_rebuild_failed type=%s',type(exc).__name__)
     if st.sidebar.button('退出测试者登录'):st.session_state.clear();st.rerun()
     st.error('模拟资料索引暂不可用。已保存任务保留，请重试。')
     st.stop()
    events+=new_events
   seeded=ingest(state,events)
   if seeded!=state:version=store.save(version,seeded);state=seeded
   search_config=pending_config
   can_write=True
  except (BackendError,Conflict) as e:
   st.error(str(e));st.warning('任务保存不可用；当前只读，未假装保存。')
   if state['simulation_stage']>=1:
    if st.sidebar.button('退出测试者登录'):st.session_state.clear();st.rerun()
    st.stop()
  if st.sidebar.button('退出测试者登录'):st.session_state.clear();st.rerun()
 else:
  with st.sidebar.form('login'):
   email=st.text_input('测试者邮箱',key='login_email');password=st.text_input('测试者密码',type='password',key='login_password')
   if st.form_submit_button('登录独立工作区'):
    try:st.session_state['access_token']=Store(*credentials).login(email,password);st.rerun()
    except BackendError as e:st.error(str(e))
  st.info('登录后使用数据库持久化个人任务；未登录只能查看虚构预览。')
else:st.warning('数据库尚未授权配置：当前为只读预览，任务写入与模拟更新已禁用。请按 deployment/SETUP.md 配置 Supabase 后再开展真实用户任务测试。')
if configured:
 with st.sidebar.expander('连接诊断（不登录、不写入）'):
  from urllib.parse import urlsplit
  st.caption('项目主机：'+str(urlsplit(credentials[0]).hostname))
  import os
  key_name='SUPABASE_PUBLISHABLE_KEY' if settings.get('SUPABASE_PUBLISHABLE_KEY') or os.environ.get('SUPABASE_PUBLISHABLE_KEY') else 'SUPABASE_ANON_KEY（兼容）'
  st.caption('读取变量：'+key_name+'；key值不显示')
  if st.button('检查Supabase Auth连接'):
   try:
    diagnostic=Store(*credentials).health()
    st.success(f"Auth HTTPS 可达，HTTP {diagnostic['http_status']}。用户登录、写入和 RLS 待验证。")
   except BackendError as exc:
    st.error(str(exc))
   except Exception as exc:
    reference=uuid.uuid4().hex[:12]
    logging.getLogger('ai_trade.supabase').error(
        'supabase_diagnostic reference=%s category=internal_error operation=auth_health exception_type=%s',
        reference,type(exc).__name__)
    st.error(f'应用内部诊断异常（{type(exc).__name__}）。诊断编号 {reference}。')
if st.sidebar.button('Morning Workspace'):st.session_state['page']='workspace';st.rerun()
if st.sidebar.button('Ask & Find'):st.session_state['page']='find';st.rerun()
page=st.session_state.get('page','workspace')
def save_action(action,**kwargs):
 try:store.save(version,change(state,action,**kwargs));st.rerun()
 except (ValueError,BackendError,Conflict) as e:st.error(str(e))
def evidence(event):
 st.caption('Demo Data · 模拟日期 '+event['simulation_date']+' · '+event['confirmation_status'])
 st.write('来源邮件：'+event['source_email_id']);st.write('主题：'+event['subject']);st.write('邮件事件时间：'+event['event_time']);st.code(event['evidence'],language=None)
 st.download_button('下载模拟来源邮件',((ROOT/'workspace_increment' if event['source_file']=='quote_increment.eml' else ROOT/'workspace_fixtures')/event['source_file']).read_bytes(),file_name=event['source_file'],key='eml_'+event['id']+'_'+str(st.session_state.get('evidence_count',0)))
 st.session_state['evidence_count']=st.session_state.get('evidence_count',0)+1
if page=='workspace':
 st.header('Morning Workspace')
 tasks=list(state['tasks'].values());counts={s:sum(t['status']==s for t in tasks) for s in ('suggested','todo','done','dismissed')}
 cols=st.columns(4)
 for col,label,key in zip(cols,['待核对建议','待跟进','已完成任务','不适用'],['suggested','todo','done','dismissed']):col.metric(label,counts[key])
 st.caption('任务完成只表示用户工作进展，不代表材料到货、订单确认或图纸获批。')
 if st.button('模拟收到新邮件 · Demo 日期 2026-11-04',disabled=not can_write or state['simulation_stage']>=1):
  try:
   try:_,_,new_events=incremental_snapshot()
   except Exception as exc:
    logging.getLogger('ai_trade.demo').error('snapshot_build_failed type=%s',type(exc).__name__)
    st.error('模拟资料构建失败，继续使用原有资料和任务。')
    new_events=None
   if new_events is not None:
    updated=ingest(state,parse_events(ROOT/'workspace_fixtures',1)+new_events);updated['simulation_stage']=1;store.save(version,updated);st.rerun()
  except (BackendError,Conflict) as e:st.error(str(e))
 st.subheader('Needs Your Attention · 项目事项')
 projects=sorted({t['project'] for t in tasks})
 for project in projects:
  with st.container(border=True):
   st.markdown('### '+project)
   if st.button('查看该项目资料',key='find_'+project):st.session_state.update(page='find',project=project);st.rerun()
   for t in [t for t in tasks if t['project']==project and t['status'] not in ('done','dismissed')]:
    st.write(t['title']);st.caption(t['item_type']+' · '+t['status']+' · '+t['confirmation_status'])
    with st.expander('来源证据与任务操作',expanded=False):
     for eid in t['sources']:evidence(state['events'][eid])
     note=st.text_area('备注',value=t['notes'],key='note_'+t['id'])
     cols=st.columns(5)
     for col,text,action in zip(cols,['确认建议','完成跟进','不适用','保存备注','撤销上次操作'],['confirm','done','dismiss','notes','undo']):
      if col.button(text,key=action+t['id'],disabled=not can_write):save_action(action,task_id=t['id'],notes=note)
 st.subheader('Waiting for Confirmation')
 for t in tasks:
  if t['sources']:
   dates=sorted({d for eid in t['sources'] for d in state['events'][eid]['recorded_dates']})
   st.write(t['project']+' · '+t['title']+' · '+('证据冲突，保留所有日期：'+', '.join(dates) if len(dates)>1 else '邮件中尚未发现足够业务确认，待人工确认'))
 st.subheader('Recent Updates · 按项目汇总')
 for project in projects:
  events=[e for e in state['events'].values() if e['project']==project]
  with st.expander(project+' · '+str(len(events))+' 条邮件来源'):
   for e in events:st.write(e['observation']);evidence(e)
 with st.expander('手动新增任务'):
  with st.form('manual_task'):
   p=st.text_input('项目或型号');title=st.text_input('任务内容');notes=st.text_area('任务备注')
   if st.form_submit_button('保存个人任务',disabled=not can_write):save_action('add',project=p,title=title,notes=notes)
 st.subheader('Completed / History')
 for t in tasks:
  if t['status'] in ('done','dismissed'):
   with st.expander(t['project']+' · '+t['title']+' · '+t['status']):
    st.write('完成时间：'+str(t['completed_at']));st.write(t['notes'])
    for eid in t['sources']:evidence(state['events'][eid])
    if st.button('撤销任务操作',key='historyundo'+t['id'],disabled=not can_write):save_action('undo',task_id=t['id'])
 with st.expander('操作历史（个人工作区）'):
  st.dataframe([{'time':h['time'],'actor':h['actor'],'action':h['action'],'task_id':h.get('task_id')} for h in reversed(state['history'])],hide_index=True)
else:
 st.header('Ask & Find')
 labels={'工厂报价':'factory_quotes','采购记录':'purchase_record','材料与交货日期':'material_purchase','工程图与版本':'engineering_versions','报价资料':'quote_documents'}
 with st.form('find'):
  intent=st.selectbox('查询类型',list(labels));product=st.text_input('完整产品或原型编号',value=st.session_state.get('project','ZZ81001'));number=st.text_input('单据编号（可选）')
  if st.form_submit_button('查询'):
   q=dict(intent=labels[intent],product_id=product,dataset='supplementary')
   if number:q['document_number']=number
   st.session_state['result']=(str(search_config),ask(search_config,q))
 if isinstance(st.session_state.get('result'),tuple) and st.session_state['result'][0]==str(search_config):
  r=st.session_state['result'][1];st.write('查询状态：'+r['query_status'])
  for bucket,title in [('documents','正常候选'),('uncertain_documents','待确认'),('audit_documents','审计证据（不推荐）')]:
   st.subheader(title)
   for i,d in enumerate(r[bucket]):
    with st.container(border=True):
     st.write(' / '.join(d['filenames']));st.caption('来源邮件 '+str(len(d['sources']))+' 封 · 版本仍待确认')
     reliable=[f for f in r['fields'] if f['source_attachment_id']==d['attachment_id'] and f['validation_status']=='extracted' and f['canonical_field'] in ('quantity','amount','unit_price','delivery_date')]
     if reliable:st.dataframe([{'字段':f['field_name'],'原值':str(f['raw_value']),'单位或币种':f['unit_or_currency'],'定位':str(f['source_location'])} for f in reliable],hide_index=True)
     elif bucket!='audit_documents':st.caption('未找到足够可靠的数量、金额或日期字段；不能推断业务事件未发生。')
     with st.expander('查看全部来源邮件'):
      for s in d['sources']:
       st.write(s['subject']);st.caption(s['email_id']+' · '+s['date_raw']);st.write('发件人：'+s['sender'])
       from email.parser import BytesParser
       from email import policy
       ep=search_config.parent/config(search_config)['datasets'][d['file_reference']['dataset']]['archive_root']/'originals'/Path(s['archived_email_path']).name
       if ep.is_file():
        m=BytesParser(policy=policy.default).parsebytes(ep.read_bytes());body=m.get_body(preferencelist=('plain',));st.code(body.get_content() if body else '无纯文本正文',language=None)
     p=resolve_file(config(search_config),d['file_reference']);st.download_button('下载原始附件',p.read_bytes(),file_name=d['filenames'][0],key=bucket+str(i))
  st.caption('报价不代表订单；记录日期不代表承诺交期；未找到索引证据不代表业务未发生。')
