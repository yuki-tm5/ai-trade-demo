"""Per-user durable Supabase state. No privileged key and no local/session-state fallback."""
import requests,base64,json,os,logging,uuid
from urllib.parse import urlsplit
from workspace_ops import initial
class Conflict(Exception):pass
class BackendError(Exception):
 def __init__(self,message,status=None,diagnostic=None):
  super().__init__(message);self.status=status;self.diagnostic=diagnostic

LOG=logging.getLogger('ai_trade.supabase')
DETAILS={
 'invalid_credentials':'Supabase已响应：账号验证未通过；不是网络连接故障',
 'email_not_confirmed':'Supabase已响应：测试账号邮箱尚未确认',
 'invalid_api_key':'Supabase已响应：公开项目key无效或不属于该项目',
 'invalid_jwt':'Supabase已响应：用户登录令牌无效或已过期',
 '42501':'数据库拒绝操作：检查authenticated授权及RLS策略',
 'PGRST205':'数据库API未找到工作区表或缓存尚未刷新'}

class Store:
 def __init__(self,url,key,token=None):
  if not isinstance(url,str) or not url.startswith('https://') or not url.rstrip('/').endswith('.supabase.co'):raise BackendError('Invalid Supabase URL')
  parsed=urlsplit(url)
  if parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:raise BackendError('SUPABASE_URL只能包含项目HTTPS主机，不能带路径、账号或查询参数')
  if not isinstance(key,str) or not key:raise BackendError('Missing public project key')
  if any(ch.isspace() or ord(ch)<32 or ord(ch)>126 for ch in key):raise BackendError('公开项目key含空白、换行或非ASCII字符；请在Secrets核对复制格式（未发送请求）')
  if key.startswith('sb_secret_'):raise BackendError('禁止使用管理员密钥；请使用公开publishable/anon key')
  try:
   payload=json.loads(base64.urlsafe_b64decode(key.split('.')[1]+'==='));role=payload.get('role') if isinstance(payload,dict) else None
  except (ValueError,IndexError,TypeError):role=None
  if role=='service_role':raise BackendError('禁止使用service_role密钥')
  self.url=url.rstrip('/');self.key=key;self.token=token
 def failure(self,category,method,path,status=None,code=None,types=None):
  reference=uuid.uuid4().hex[:12]
  diagnostic=dict(reference=reference,category=category,method=method,host=urlsplit(self.url).hostname,path=path.split('?')[0],http_status=status,remote_error_code=code,exception_types=types or [])
  LOG.warning('supabase_diagnostic %s',json.dumps(diagnostic,ensure_ascii=True))
  labels={'dns_failure':'Supabase域名解析失败','tls_failure':'Supabase TLS证书或握手失败','connect_timeout':'连接Supabase超时','read_timeout':'等待Supabase响应超时','proxy_failure':'到Supabase的代理连接失败','connection_failure':'与Supabase建立或保持连接失败','invalid_request_header':'请求头格式无效；请核对Secrets复制格式','invalid_response':'Supabase响应不是预期JSON','http_error':'Supabase返回HTTP错误','redirect_rejected':'Supabase返回意外重定向；未向其他主机转发key'}
  description=DETAILS.get(code) or labels.get(category,'Supabase请求异常')
  suffix=('；HTTP '+str(status)) if status is not None else '；未取得HTTP响应'
  raise BackendError(description+suffix+'；诊断编号 '+reference+'。请勿重复提交写入，刷新核对。',status,diagnostic) from None
 def request(self,method,path,**kwargs):
  headers={'apikey':self.key,'Content-Type':'application/json'}
  if path.startswith('/rest/'):headers['Prefer']='return=representation'
  if self.token:headers['Authorization']='Bearer '+self.token
  try:r=requests.request(method,self.url+path,headers=headers,timeout=(8,25),allow_redirects=False,**kwargs)
  except requests.RequestException as exc:
   types=[type(exc).__name__];text=str(exc).lower() # Used only to categorize DNS; NEVER output exception text/args.
   if isinstance(exc,requests.exceptions.InvalidHeader):kind='invalid_request_header'
   elif isinstance(exc,requests.exceptions.SSLError):kind='tls_failure'
   elif isinstance(exc,requests.exceptions.ProxyError):kind='proxy_failure'
   elif isinstance(exc,requests.exceptions.ConnectTimeout):kind='connect_timeout'
   elif isinstance(exc,requests.exceptions.ReadTimeout):kind='read_timeout'
   elif any(v in text for v in ('nameresolutionerror','getaddrinfo','name or service not known','failed to resolve')):kind='dns_failure'
   elif isinstance(exc,requests.exceptions.ConnectionError):kind='connection_failure'
   else:kind='connection_failure'
   self.failure(kind,method,path,types=types)
  self.last_http_status=r.status_code
  if 300<=r.status_code<400:self.failure('redirect_rejected',method,path,status=r.status_code)
  if not r.ok:
   try:payload=r.json()
   except ValueError:payload={}
   code=payload.get('error_code') or payload.get('code') if isinstance(payload,dict) else None
   if not code and isinstance(payload,dict) and payload.get('message')=='Invalid API key':code='invalid_api_key'
   if code not in {'invalid_credentials','email_not_confirmed','invalid_api_key','invalid_jwt','42501','PGRST205','23505','PGRST116','over_request_rate_limit','user_banned','unexpected_failure','request_timeout','validation_failed','bad_jwt','no_authorization','email_address_invalid','captcha_failed','signup_disabled'}:code='unclassified'
   self.failure('http_error',method,path,status=r.status_code,code=code)
  if not r.content:return None
  try:return r.json()
  except ValueError:self.failure('invalid_response',method,path,status=r.status_code)
 def health(self):
  self.request('GET','/auth/v1/health')
  return dict(reachable=True,http_status=self.last_http_status,service='Supabase Auth',user_authenticated=False,database_write_tested=False)
 def login(self,email,password):
  r=self.request('POST','/auth/v1/token?grant_type=password',json={'email':email,'password':password});self.token=r['access_token'];return self.token
 def user(self):return self.request('GET','/auth/v1/user')['id']
 def load(self):
  uid=self.user();rows=self.request('GET','/rest/v1/operations_workspace',params={'user_id':'eq.'+uid,'select':'version,data'})
  if not rows:
   try:self.request('POST','/rest/v1/operations_workspace',json={'user_id':uid,'version':0,'data':initial()})
   except BackendError as exc:
    if exc.status!=409:raise
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
