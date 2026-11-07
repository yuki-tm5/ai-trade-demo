from pathlib import Path
import sys,json,logging,io,requests
from unittest.mock import patch
R=Path.cwd();sys.path.insert(0,str(R/'src'))
from workspace_store import Store,BackendError
out=R/'validation/auth_network_diagnostic_tests';out.mkdir()
tests=[];secret='FAKE_PASSWORD_MUST_NOT_LEAK';key='sb_publishable_FAKE_KEY';jwt='FAKE_JWT_MUST_NOT_LEAK';stream=io.StringIO();handler=logging.StreamHandler(stream);logger=logging.getLogger('ai_trade.supabase');logger.addHandler(handler)
def check(n,ok):tests.append(dict(name=n,passed=bool(ok)))
for exc,category in [(requests.exceptions.InvalidHeader(secret+key),'invalid_request_header'),(requests.exceptions.SSLError(secret),'tls_failure'),(requests.exceptions.ProxyError(secret),'proxy_failure'),(requests.exceptions.ConnectTimeout(secret),'connect_timeout'),(requests.exceptions.ReadTimeout(secret),'read_timeout'),(requests.exceptions.ConnectionError('NameResolutionError '+secret),'dns_failure'),(requests.exceptions.ConnectionError(secret),'connection_failure')]:
 with patch('workspace_store.requests.request',side_effect=exc):
  try:Store('https://example.supabase.co',key).login('fake@example.invalid',secret);ok=False
  except BackendError as e:ok=e.diagnostic['category']==category and e.status is None and secret not in str(e)
 check(category+'_classified_and_redacted',ok)
class Response:
 def __init__(self,status,payload):self.status_code=status;self.ok=status<400;self.content=b'json';self.payload=payload
 def json(self):return self.payload
for status,payload,expected in [(400,{'error_code':'invalid_credentials','msg':secret},'invalid_credentials'),(401,{'message':'Invalid API key'},'invalid_api_key'),(403,{'code':'42501','message':secret},'42501'),(500,{'code':secret,'message':secret},'unclassified')]:
 with patch('workspace_store.requests.request',return_value=Response(status,payload)):
  try:Store('https://example.supabase.co',key).login('fake@example.invalid',secret);ok=False
  except BackendError as e:ok=e.status==status and e.diagnostic['remote_error_code']==expected and secret not in str(e)
 check('http_'+str(status)+'_retains_status_no_body_leak',ok)
try:Store('https://example.supabase.co',key+'\n');ok=False
except BackendError as e:ok=key not in str(e)
check('newline_key_rejected_before_network',ok)
calls=[]
def success(method,url,**kwargs):calls.append(dict(method=method,url=url,**kwargs));return Response(200,{'access_token':jwt,'id':'fake-user'})
with patch('workspace_store.requests.request',success):
 s=Store('https://example.supabase.co',key);s.login('fake@example.invalid',secret);s.user()
check('correct_auth_endpoint_and_post',calls[0]['method']=='POST' and calls[0]['url']=='https://example.supabase.co/auth/v1/token?grant_type=password')
check('publishable_header_not_bearer',calls[0]['headers']['apikey']==key and 'Authorization' not in calls[0]['headers'])
check('rls_requests_use_user_jwt',calls[1]['headers']['Authorization']=='Bearer '+jwt)
check('tls_not_disabled_and_redirects_not_forwarded',calls[0]['allow_redirects'] is False and 'verify' not in calls[0])
logger.removeHandler(handler);handler.close();logs=stream.getvalue();check('server_logs_never_contain_credentials',secret not in logs and key not in logs and jwt not in logs and 'fake@example.invalid' not in logs)
(out/'sanitized_server_log.txt').write_text(logs,encoding='utf-8')
report=dict(passed=sum(t['passed'] for t in tests),failed=sum(not t['passed'] for t in tests),tests=tests,live_cloud_root_cause='Pending real cloud diagnostic; local no-key Auth request returned HTTP401, not a password conclusion',live_login_task_persistence_verified=False)
(out/'test_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report));raise SystemExit(bool(report['failed']))
