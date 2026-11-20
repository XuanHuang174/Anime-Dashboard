import unittest
from unittest.mock import Mock, patch

import requests

from verify_api_connectivity import verify


class ConnectivityTests(unittest.TestCase):
    def client(self, fail=False):
        client = Mock()
        def request(method, url, **kwargs):
            if fail:
                raise requests.ConnectionError('synthetic network failure')
            return Mock(status_code=200, headers={
                'Access-Control-Allow-Origin': 'https://front.example',
                'Access-Control-Allow-Headers': 'authorization, content-type',
                'Access-Control-Allow-Methods': 'GET, OPTIONS',
            }, json=lambda: {'status': 'ok'})
        client.request.side_effect = request
        client.get.side_effect = [
            Mock(status_code=200, text='<script type="module" src="/assets/index-qa.js"></script>'),
            Mock(status_code=200, text='"https://api.example"'),
        ]
        return client

    @patch('verify_api_connectivity.socket.getaddrinfo', return_value=[(None, None, None, None, ('192.0.2.1', 443))])
    def test_valid_dns_https_cors_and_bundle_are_readonly(self, _dns):
        client = self.client()
        result = verify('https://api.example', 'https://front.example', client)
        self.assertEqual(result['result'], 'passed_connectivity_readonly')
        self.assertEqual([call.args[0] for call in client.request.call_args_list], ['GET', 'GET', 'OPTIONS'])
        self.assertEqual(result['writes'], 0)
        self.assertNotIn('192.0.2.1', str(result))

    @patch('verify_api_connectivity.socket.getaddrinfo', side_effect=OSError('synthetic DNS failure'))
    def test_dns_failure_is_distinct_from_rate_limiting(self, _dns):
        result = verify('https://api.example', 'https://front.example', self.client(True))
        self.assertEqual(result['result'], 'failed_or_blocked')
        self.assertEqual(result['checks']['os_dns']['code'], 'dns_resolution_failed')
        self.assertEqual(result['checks']['public_api']['code'], 'ConnectionError')

    @patch('verify_api_connectivity.socket.getaddrinfo', return_value=[(None, None, None, None, ('192.0.2.1', 443))])
    def test_cors_failure_and_old_deployed_bundle_block_acceptance(self, _dns):
        client = self.client()
        client.request.side_effect = None
        client.request.return_value = Mock(status_code=200, headers={}, json=lambda: {})
        client.get.side_effect = [Mock(status_code=200, text='<script src="/assets/index-qa.js"></script>'), Mock(status_code=200, text='old-render-origin')]
        result = verify('https://api.example', 'https://front.example', client)
        self.assertFalse(result['checks']['cors_preflight']['passed'])
        self.assertFalse(result['checks']['public_api']['passed'])
        self.assertFalse(result['checks']['frontend_api_origin']['passed'])


if __name__ == '__main__':
    unittest.main()
