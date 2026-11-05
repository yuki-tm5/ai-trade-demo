"""Per-user durable Supabase state. No privileged key and no local/session-state fallback."""
import requests,base64,json,os
from workspace_ops import initial
class Conflict(Exception):pass
class BackendError(Exception):pass
class Store:
 def __init__(self,url,key,token=None):
  if not isinstance(url,str) or not url.startswith('https://') or not url.rstrip('/').endswith('.supabase.co'):raise BackendError('Invalid Supabase URL')
  if not isinstance(key,str) or not key:raise BackendError('Missing public project key')
  if key.startswith('sb_secret_'):raise BackendError('禁止使用管理员密钥；请使用公开publishable/anon key')
  try:
   payload=json.loads(base64.urlsafe_b64decode(key.split('.')[1]+'==='));role=payload.get('role') if isinstance(payload,dict) else None
  except (ValueError,IndexError,TypeError):role=None
  if role=='service_role':raise BackendError('禁止使用service_role密钥')
  self.url=url.rstrip('/');self.key=key;self.token=token
 def request(self,method,path,**kwargs):
  headers={'apikey':self.key,'Content-Type':'application/json','Prefer':'return=representation'}
  if self.token:headers['Authorization']='Bearer '+self.token
  try:r=requests.request(method,self.url+path,headers=headers,timeout=25,**kwargs)
  except requests.RequestException:raise BackendError('网络请求失败；操作未确认保存，请刷新核对') from None
  if not r.ok:raise BackendError('数据库或登录请求失败，HTTP '+str(r.status_code))
  return r.json() if r.content else None
 def login(self,email,password):
  r=self.request('POST','/auth/v1/token?grant_type=password',json={'email':email,'password':password});self.token=r['access_token'];return self.token
 def user(self):return self.request('GET','/auth/v1/user')['id']
 def load(self):
  uid=self.user();rows=self.request('GET','/rest/v1/operations_workspace',params={'user_id':'eq.'+uid,'select':'version,data'})
  if not rows:
   try:self.request('POST','/rest/v1/operations_workspace',json={'user_id':uid,'version':0,'data':initial()})
   except BackendError:pass
   rows=self.request('GET','/rest/v1/operations_workspace',params={'user_id':'eq.'+uid,'select':'version,data'})
  if len(rows)!=1:raise BackendError('无法加载独立工作区；请检查SQL和登录用户')
  return rows[0]['version'],rows[0]['data']
 def save(self,version,data):
  uid=self.user();rows=self.request('PATCH','/rest/v1/operations_workspace',params={'user_id':'eq.'+uid,'version':'eq.'+str(version)},json={'version':version+1,'data':data})
  if not rows:raise Conflict('另一个窗口已更新，请刷新后重试；本次操作未覆盖其他修改')
  return rows[0]['version']


def public_config(settings,environ=None):
 """Prefer new publishable variable, with legacy anon migration fallback. Never prints keys."""
 env=os.environ if environ is None else environ
 url=settings.get('SUPABASE_URL') or env.get('SUPABASE_URL')
 key=(settings.get('SUPABASE_PUBLISHABLE_KEY') or env.get('SUPABASE_PUBLISHABLE_KEY')
      or settings.get('SUPABASE_ANON_KEY') or env.get('SUPABASE_ANON_KEY'))
 if not url or not key:return None
 Store(url,key) # Validate public-only configuration without any network request.
 return url,key
