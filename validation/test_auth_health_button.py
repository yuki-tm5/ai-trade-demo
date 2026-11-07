import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from workspace_store import BackendError, Store

URL = 'https://example.supabase.co'
KEY = 'sb_publishable_FAKE_DIAGNOSTIC_KEY'

class HealthTests(unittest.TestCase):
    def test_health_uses_read_only_get_and_returns_response_status(self):
        response = Mock(status_code=200, ok=True, content=b'{}')
        response.json.return_value = {}
        with patch('workspace_store.requests.request', return_value=response) as send:
            result = Store(URL, KEY).health()
        args, kwargs = send.call_args
        self.assertEqual(args, ('GET', URL + '/auth/v1/health'))
        self.assertEqual(kwargs['headers']['apikey'], KEY)
        self.assertNotIn('Authorization', kwargs['headers'])
        self.assertNotIn('json', kwargs)
        self.assertEqual(result['http_status'], 200)

    def test_nonstring_provider_code_preserves_http_error(self):
        response = Mock(status_code=503, ok=False)
        response.json.return_value = {'error_code': [KEY]}
        with patch('workspace_store.requests.request', return_value=response), self.assertLogs('ai_trade.supabase') as logs:
            with self.assertRaises(BackendError) as error:
                Store(URL, KEY).health()
        self.assertEqual(error.exception.status, 503)
        self.assertEqual(error.exception.diagnostic['category'], 'http_error')
        self.assertNotIn(KEY, '\n'.join(logs.output))

    def test_transport_categories_remain_distinct(self):
        cases = [(requests.exceptions.ConnectionError('NameResolutionError'), 'dns_failure'),
                 (requests.exceptions.SSLError(KEY), 'tls_failure'),
                 (requests.exceptions.ConnectTimeout(KEY), 'connect_timeout'),
                 (requests.exceptions.ReadTimeout(KEY), 'read_timeout')]
        for exc, category in cases:
            with self.subTest(category=category), patch('workspace_store.requests.request', side_effect=exc), self.assertLogs('ai_trade.supabase') as logs:
                with self.assertRaises(BackendError) as error:
                    Store(URL, KEY).health()
                self.assertEqual(error.exception.diagnostic['category'], category)
                self.assertNotIn(KEY, '\n'.join(logs.output))

    def test_streamlit_click_contains_internal_attribute_error(self):
        app = AppTest.from_file(str(ROOT / 'app.py'))
        app.secrets['SUPABASE_URL'] = URL
        app.secrets['SUPABASE_PUBLISHABLE_KEY'] = KEY
        app.run(timeout=30)
        button = next(b for b in app.button if b.label == '检查Supabase Auth连接')
        with patch.object(Store, 'health', side_effect=AttributeError(KEY)), self.assertLogs('ai_trade.supabase') as logs:
            button.click().run(timeout=30)
        self.assertFalse(app.exception)
        self.assertTrue(any('应用内部诊断异常（AttributeError）' in e.value for e in app.error))
        self.assertNotIn(KEY, '\n'.join(logs.output) + str([e.value for e in app.error]))

if __name__ == '__main__':
    unittest.main(verbosity=2)
