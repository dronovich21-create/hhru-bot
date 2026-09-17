"""Exercise the extension's real WebCrypto transport against the Python bridge."""
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright
from hh_bot.chrome_bridge import ChromeBridge


class ExtensionTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bridge = ChromeBridge(Path(self.temp.name), port=0)
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(channel='msedge', headless=True)
        self.page = self.browser.new_page()
        base = f'http://127.0.0.1:{self.bridge.server.server_port}'
        self.page.route(base + '/', lambda route: route.fulfill(body='<html></html>', content_type='text/html'))
        self.page.goto(base + '/')
        self.page.evaluate('''key => {
          globalThis.chrome = {
            storage: {
              local: {get: async () => ({key, enabled: false}), setAccessLevel: async () => {}},
              session: {get: async () => ({}), set: async () => {}}
            },
            runtime: {id: 'test', getURL: p => p, onMessage: {addListener: () => {}}},
            alarms: {create: () => {}, onAlarm: {addListener: () => {}}}
          };
        }''', self.bridge.token)
        script = Path('chrome-extension/background.js').read_text(encoding='utf-8')
        script = script.replace("http://127.0.0.1:8765", base)
        self.page.evaluate('() => {\n' + script + '\nglobalThis.bridgePost = post;\n}')

    def tearDown(self):
        self.browser.close()
        self.pw.stop()
        self.bridge.close()
        self.temp.cleanup()

    def test_webcrypto_and_python_signatures_match(self):
        self.assertEqual(self.page.evaluate("bridgePost('/ping')"), {'ok': True})
        self.assertTrue(self.bridge.connected())

    def test_fake_server_response_is_rejected_without_disclosing_key(self):
        result = self.page.evaluate('''async () => {
          let headers;
          globalThis.fetch = async (_, options) => {
            headers = options.headers;
            return new Response('{"command":{"action":"open"}}', {status: 200});
          };
          try { await bridgePost('/poll'); return {rejected: false}; }
          catch (_) { return {rejected: true, headers}; }
        }''')
        self.assertTrue(result['rejected'])
        self.assertNotIn('Authorization', result['headers'])
        self.assertNotIn(self.bridge.token, str(result['headers']))
