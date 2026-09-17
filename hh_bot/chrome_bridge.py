"""Authenticated loopback transport for the user's regular Chrome extension."""
import json
import hashlib
import hmac
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .automation import StoppedByUser


class ChromeBridge:
    def __init__(self, data_dir: Path, port=8765):
        data_dir.mkdir(parents=True, exist_ok=True)
        key_path = data_dir / 'chrome-bridge-key.txt'
        if not key_path.exists():
            key_path.write_text(secrets.token_urlsafe(32), encoding='utf-8')
        self.token = key_path.read_text(encoding='utf-8').strip()
        self.lock = threading.Lock()
        self.jobs = {}
        self.last_seen = 0.0
        self.client = None
        self.nonces = {}
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.request_nonce = ''
                self.connection.settimeout(5)
                try:
                    if self.headers.get('Host') != '127.0.0.1:' + str(bridge.server.server_port):
                        raise ValueError('Invalid host')
                    size = int(self.headers.get('Content-Length', '0'))
                    if size > 100000 or size < 0:
                        raise ValueError('Invalid body size')
                    raw = self.rfile.read(size)
                    stamp = self.headers.get('X-HH-Time', '')
                    nonce = self.headers.get('X-HH-Nonce', '')
                    client = self.headers.get('X-HH-Client', '')
                    if abs(time.time() - int(stamp)) > 60 or len(nonce) != 64 or len(client) != 64:
                        raise ValueError('Invalid authentication metadata')
                    message = f'request\n{self.path}\n{stamp}\n{nonce}\n{client}\n'.encode() + raw
                    signature = hmac.new(bridge.token.encode(), message, hashlib.sha256).hexdigest()
                    if not secrets.compare_digest(self.headers.get('X-HH-Signature', ''), signature):
                        self.reply(403, {'error': 'Unauthorized'})
                        return
                    self.request_nonce = nonce
                    body = json.loads(raw or b'{}')
                    if not isinstance(body, dict):
                        raise ValueError('Expected object')
                    with bridge.lock:
                        now = time.monotonic()
                        bridge.nonces = {n: t for n, t in bridge.nonces.items() if now - t < 120}
                        if nonce in bridge.nonces or len(bridge.nonces) >= 4096:
                            self.reply(403, {'error': 'Replay rejected'})
                            return
                        bridge.nonces[nonce] = now
                        if bridge.client not in (None, client) and now - bridge.last_seen < 8:
                            self.reply(409, {'error': 'Другой профиль Chrome уже подключён.'})
                            return
                        bridge.client = client
                        bridge.last_seen = time.monotonic()
                        if self.path == '/ping':
                            response = {'ok': True}
                        elif self.path == '/poll':
                            job = next((j for j in bridge.jobs.values() if not j['delivered']), None)
                            if job:
                                job['delivered'] = True
                            response = {'command': {k: job[k] for k in ('id', 'action', 'payload')} if job else None}
                        elif self.path == '/claim':
                            response = {'active': body.get('id') in bridge.jobs}
                        elif self.path == '/result':
                            job = bridge.jobs.get(body.get('id'))
                            if job:
                                job['result'] = body
                                job['event'].set()
                            response = {'ok': True}
                        else:
                            self.reply(404, {})
                            return
                    self.reply(200, response)
                except (ValueError, TypeError, TimeoutError, OSError):
                    self.reply(400, {'error': 'Invalid JSON'})

            def reply(self, code, body):
                raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(raw)))
                if self.request_nonce:
                    message = f'response\n{self.request_nonce}\n{code}\n'.encode() + raw
                    self.send_header('X-HH-Signature', hmac.new(bridge.token.encode(), message, hashlib.sha256).hexdigest())
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def connected(self):
        return time.monotonic() - self.last_seen < 8

    def command(self, action, payload, stop_event):
        job_id = secrets.token_hex(16)
        job = {'id': job_id, 'action': action, 'payload': payload, 'delivered': False,
               'event': threading.Event(), 'result': None}
        with self.lock:
            self.jobs[job_id] = job
        started = time.monotonic()
        try:
            while not job['event'].wait(0.2):
                if stop_event.is_set():
                    raise StoppedByUser
                if not self.connected():
                    raise RuntimeError('Расширение Chrome отключено. Откройте его и нажмите «Подключить».')
                if time.monotonic() - started > 15:
                    raise RuntimeError('Ответ на команду расширения потерян. Проверьте рабочую вкладку; повторная отправка автоматически не выполняется.')
            result = job['result']
            if result.get('error'):
                raise RuntimeError(result['error'])
            return result.get('value', {})
        finally:
            with self.lock:
                self.jobs.pop(job_id, None)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
