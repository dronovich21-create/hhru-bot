import json
import hashlib
import hmac
import secrets
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from hh_bot.chrome_bridge import ChromeBridge
from hh_bot.automation import StoppedByUser


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = ChromeBridge(Path(self.temp.name), port=0)
        self.base = 'http://127.0.0.1:' + str(self.bridge.server.server_port)

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def post(self, path, body=None, key=None, nonce=None, stamp=None, client=None):
        raw = json.dumps(body or {}).encode()
        nonce = nonce or secrets.token_hex(32)
        stamp = str(stamp if stamp is not None else int(time.time()))
        client = client or 'a' * 64
        message = f'request\n{path}\n{stamp}\n{nonce}\n{client}\n'.encode() + raw
        signature = hmac.new((key or self.bridge.token).encode(), message, hashlib.sha256).hexdigest()
        request = Request(self.base + path, data=raw, method='POST',
            headers={'X-HH-Time': stamp, 'X-HH-Nonce': nonce, 'X-HH-Client': client,
                     'X-HH-Signature': signature, 'Content-Type': 'application/json'})
        with urlopen(request, timeout=2) as response:
            result = response.read()
            expected = hmac.new(self.bridge.token.encode(), f'response\n{nonce}\n{response.status}\n'.encode() + result, hashlib.sha256).hexdigest()
            self.assertEqual(response.headers['X-HH-Signature'], expected)
            return json.loads(result)

    def test_replay_is_rejected(self):
        nonce = secrets.token_hex(32)
        self.post('/ping', nonce=nonce)
        with self.assertRaises(HTTPError) as caught:
            self.post('/ping', nonce=nonce)
        self.assertEqual(caught.exception.code, 403)

    def test_expired_request_is_rejected(self):
        with self.assertRaises(HTTPError) as caught:
            self.post('/ping', stamp=int(time.time()) - 121)
        self.assertEqual(caught.exception.code, 400)
        self.assertFalse(self.bridge.connected())

    def test_other_profile_cannot_take_active_connection(self):
        self.post('/ping')
        with self.assertRaises(HTTPError) as caught:
            self.post('/poll', client='b' * 64)
        self.assertEqual(caught.exception.code, 409)

    def test_other_profile_can_connect_after_disconnect(self):
        self.post('/ping')
        self.bridge.last_seen -= 9
        self.assertTrue(self.post('/ping', client='b' * 64)['ok'])

    def test_old_bearer_protocol_is_rejected(self):
        request = Request(self.base + '/poll', data=b'{}', headers={'Authorization': 'Bearer ' + self.bridge.token})
        with self.assertRaises(HTTPError):
            urlopen(request, timeout=2)
        self.assertFalse(self.bridge.connected())

    def test_unauthorized_client_is_rejected(self):
        with self.assertRaises(HTTPError) as caught:
            self.post('/poll', key='wrong')
        self.assertEqual(caught.exception.code, 403)

    def test_command_is_delivered_once_and_result_returned(self):
        self.post('/ping')
        results = []
        worker = threading.Thread(target=lambda: results.append(self.bridge.command('snapshot', {}, threading.Event())))
        worker.start()
        command = None
        for _ in range(30):
            command = self.post('/poll')['command']
            if command:
                break
            time.sleep(0.01)
        self.assertIsNotNone(command)
        self.assertIsNone(self.post('/poll')['command'])
        self.post('/result', {'id': command['id'], 'value': {'cards': []}})
        worker.join(2)
        self.assertEqual(results, [{'cards': []}])

    def test_stop_cancels_command_and_prevents_claim(self):
        self.post('/ping')
        stop = threading.Event()
        errors = []
        def execute():
            try:
                self.bridge.command('submit', {}, stop)
            except StoppedByUser:
                errors.append('stopped')
        worker = threading.Thread(target=execute)
        worker.start()
        for _ in range(30):
            command = self.post('/poll')['command']
            if command:
                break
            time.sleep(0.01)
        stop.set()
        worker.join(2)
        self.assertEqual(errors, ['stopped'])
        self.assertFalse(self.post('/claim', {'id': command['id']})['active'])
