import tempfile
import threading
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright
from hh_bot.automation import HHAutomation
from hh_bot.history import HistoryStore


class BrowserFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel="msedge", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.page = self.browser.new_page()
        self.bot = HHAutomation(Path(self.temp.name), HistoryStore(Path(self.temp.name) / "h.json"),
            lambda _: None, lambda _: None, lambda _: True, lambda: None, threading.Event())

    def tearDown(self):
        self.page.close()
        self.temp.cleanup()

    def fixture(self, required=False):
        letter = '<textarea required></textarea>' if required else ''
        self.page.set_content('''<div id="card">Инженер ПТО</div>
            <div role="dialog">Отклик на вакансию''' + letter + '''
            <button data-qa="vacancy-response-submit-popup" onclick="
            if (document.querySelector('textarea') && !document.querySelector('textarea').value) return;
            document.querySelector('#card').textContent='Вы откликнулись';
            this.parentElement.remove();">Откликнуться</button></div>''')
        return self.page.locator('#card')

    def test_without_letter(self):
        card = self.fixture()
        self.bot._finish_response(self.page, '', card)
        self.assertTrue(self.bot._response_sent(card))

    def extension_fixture(self):
        self.page.set_content('''<div data-qa="vacancy-serp__vacancy"><a data-qa="serp-item__title" href="https://hh.ru/vacancy/123">Инженер ПТО</a><button>Откликнуться</button></div>
            <div role="dialog">Отклик на вакансию Инженер ПТО<textarea required></textarea>
            <button onclick="window.submits=(window.submits||0)+1">Откликнуться</button></div>''')
        self.page.evaluate("globalThis.chrome={runtime:{id:'test',onMessage:{addListener:f=>globalThis.hhHandler=f}}}")
        script = Path('chrome-extension/content.js').read_text(encoding='utf-8')
        self.page.evaluate('() => {\n' + script + '\n}')

    def test_extension_reads_cards_and_required_letter(self):
        self.extension_fixture()
        result = self.page.evaluate("new Promise(resolve=>hhHandler({action:'snapshot',payload:{}},{id:'test'},resolve))")
        self.assertEqual(result['cards'][0]['id'], '123')
        self.assertTrue(result['modal']['requiredLetter'])

    def test_visibility_status_does_not_block_normal_response(self):
        self.extension_fixture()
        self.page.evaluate("document.querySelector('textarea').remove(); document.body.insertAdjacentHTML('beforeend', '<div>Видно всем работодателям</div>')")
        self.assertFalse(self.bot._has_visibility_request(self.page))
        state = self.page.evaluate("new Promise(resolve=>hhHandler({action:'snapshot',payload:{}},{id:'test'},resolve))")
        self.assertIsNone(state['blocker'])
        result = self.page.evaluate("new Promise(resolve=>hhHandler({action:'submit',payload:{id:'123',title:'Инженер ПТО',letter:''}},{id:'test'},resolve))")
        self.assertTrue(result['submitted'])
        self.assertEqual(self.page.evaluate('window.submits'), 1)

    def test_response_form_without_legacy_dialog_attributes(self):
        self.extension_fixture()
        self.page.evaluate('''() => {
          const dialog = document.querySelector('[role=dialog]');
          dialog.removeAttribute('role');
          dialog.firstChild.textContent = '';
          dialog.querySelector('textarea').remove();
          dialog.insertAdjacentHTML('afterbegin', '<h2>Отклик на вакансию</h2><div>Инженер ПТО</div><div>Видно всем работодателям</div>');
        }''')
        self.assertIsNotNone(self.bot._response_dialog(self.page))
        state = self.page.evaluate("new Promise(resolve=>hhHandler({action:'snapshot',payload:{}},{id:'test'},resolve))")
        self.assertIsNotNone(state['modal'])
        result = self.page.evaluate("new Promise(resolve=>hhHandler({action:'submit',payload:{id:'123',title:'Инженер ПТО',letter:''}},{id:'test'},resolve))")
        self.assertTrue(result['submitted'])
        self.assertEqual(self.page.evaluate('window.submits'), 1)

    def test_page_heading_and_card_button_are_not_a_response_form(self):
        self.extension_fixture()
        self.page.evaluate("document.querySelector('[role=dialog]').remove(); document.body.insertAdjacentHTML('afterbegin', '<h2>Отклик на вакансию</h2>')")
        self.assertIsNone(self.bot._response_dialog(self.page))
        state = self.page.evaluate("new Promise(resolve=>hhHandler({action:'snapshot',payload:{}},{id:'test'},resolve))")
        self.assertIsNone(state['modal'])

    def test_real_visibility_request_still_requires_manual_action(self):
        self.extension_fixture()
        self.page.evaluate("document.querySelector('[role=dialog]').insertAdjacentHTML('beforeend', '<p>Поменяйте видимость резюме</p>')")
        self.assertTrue(self.bot._has_visibility_request(self.page))
        result = self.page.evaluate("new Promise(resolve=>hhHandler({action:'submit',payload:{id:'123',title:'Инженер ПТО',letter:'тест'}},{id:'test'},resolve))")
        self.assertIn('видимость', result['notSubmitted'])
        self.assertIsNone(self.page.evaluate('window.submits || null'))

    def test_hidden_visibility_prompts_do_not_block_submission(self):
        self.extension_fixture()
        self.page.evaluate('''() => {
          const dialog = document.querySelector('[role=dialog]');
          dialog.insertAdjacentHTML('beforeend',
            '<p style="opacity:0">Поменяйте видимость резюме</p>' +
            '<p aria-hidden="true">Измените видимость резюме</p>' +
            '<p style="position:fixed;left:-10000px">Поменяйте видимость резюме</p>' +
            '<p style="visibility:hidden">Измените видимость резюме</p>');
        }''')
        state = self.page.evaluate("new Promise(resolve=>hhHandler({action:'snapshot',payload:{}},{id:'test'},resolve))")
        self.assertIsNone(state['blocker'])
        result = self.page.evaluate("new Promise(resolve=>hhHandler({action:'submit',payload:{id:'123',title:'Инженер ПТО',letter:'тест'}},{id:'test'},resolve))")
        self.assertTrue(result['submitted'])
        self.assertEqual(self.page.evaluate('window.submits'), 1)

    def test_covered_visibility_prompt_is_not_an_active_request(self):
        self.extension_fixture()
        self.page.evaluate('''() => {
          document.querySelector('[role=dialog]').insertAdjacentHTML('beforeend',
            '<p style="position:fixed;left:10px;top:10px">Поменяйте видимость резюме</p>');
          document.body.insertAdjacentHTML('beforeend',
            '<div style="position:fixed;left:0;top:0;width:600px;height:100px;background:white;z-index:999"></div>');
        }''')
        state = self.page.evaluate("new Promise(resolve=>hhHandler({action:'snapshot',payload:{}},{id:'test'},resolve))")
        self.assertIsNone(state['blocker'])
        self.assertEqual(state['contentVersion'], '0.2.3')

    def test_extension_checks_vacancy_before_submit(self):
        self.extension_fixture()
        result = self.page.evaluate("new Promise(resolve=>hhHandler({action:'submit',payload:{id:'123',title:'Другая вакансия',letter:'Привет'}},{id:'test'},resolve))")
        self.assertIn('notSubmitted', result)
        self.assertIsNone(self.page.evaluate('window.submits'))

    def test_extension_inserts_letter_and_submits_matching_form(self):
        self.extension_fixture()
        self.page.evaluate("new Promise(resolve=>hhHandler({action:'submit',payload:{id:'123',title:'Инженер ПТО',letter:'Здравствуйте!'}},{id:'test'},resolve))")
        self.assertEqual(self.page.locator('textarea').input_value(), 'Здравствуйте!')
        self.assertEqual(self.page.evaluate('window.submits'), 1)

    def test_optional_letter_is_opened_filled_then_submitted(self):
        self.extension_fixture()
        self.page.evaluate('''() => {
          const m = document.querySelector('[role=dialog]');
          m.querySelector('textarea').remove();
          m.insertAdjacentHTML('beforeend', '<button id="add-letter">Добавить сопроводительное</button>');
          document.querySelector('#add-letter').onclick = () => {
            window.addClicks = (window.addClicks || 0) + 1;
            m.insertAdjacentHTML('beforeend', '<textarea></textarea>');
            document.querySelector('#add-letter').remove();
          };
        }''')
        command = "new Promise(resolve=>hhHandler({action:'submit',payload:{id:'123',title:'Инженер ПТО',letter:'Наше письмо'}},{id:'test'},resolve))"
        self.assertTrue(self.page.evaluate(command)['preparePending'])
        self.assertIsNone(self.page.evaluate('window.submits || null'))
        self.assertTrue(self.page.evaluate(command)['submitted'])
        self.assertEqual(self.page.locator('textarea').input_value(), 'Наше письмо')
        self.assertEqual(self.page.evaluate('window.addClicks'), 1)
        self.assertEqual(self.page.evaluate('window.submits'), 1)

    def test_edge_fills_optional_letter(self):
        card = self.fixture()
        self.page.locator('[role=dialog]').evaluate("m => m.insertAdjacentHTML('beforeend', '<textarea></textarea>')")
        self.bot._finish_response(self.page, 'Наше письмо', card)
        self.assertTrue(self.bot._response_sent(card))

    def sent_letter_fixture(self):
        self.extension_fixture()
        self.page.evaluate('''() => {
          document.querySelector('[role=dialog]').remove();
          const card = document.querySelector('[data-qa="vacancy-serp__vacancy"]');
          card.querySelector('button').remove();
          card.insertAdjacentHTML('beforeend', '<p>Ваш отклик отправлен работодателю. Вы откликнулись</p><a id="attach">Приложить письмо</a>');
          document.querySelector('#attach').onclick = () => {
            card.insertAdjacentHTML('beforeend', '<div id="letter-form">Сопроводительное письмо<textarea></textarea><button id="send-letter">Отправить</button></div>');
            document.querySelector('#send-letter').onclick = () => {
              window.sentLetter = card.querySelector('textarea').value;
              window.letterSends = (window.letterSends || 0) + 1;
              document.querySelector('#letter-form').remove();
              document.querySelector('#attach').remove();
              card.insertAdjacentHTML('beforeend', '<p>Письмо отправлено</p>');
            };
          };
        }''')

    def test_chrome_attaches_letter_after_direct_response(self):
        self.sent_letter_fixture()
        result = self.page.evaluate("new Promise(resolve=>hhHandler({action:'attachLetter',payload:{id:'123',letter:'Наше письмо'}},{id:'test'},resolve))")
        self.assertTrue(result['attached'])
        self.assertEqual(self.page.evaluate('window.sentLetter'), 'Наше письмо')
        self.assertEqual(self.page.evaluate('window.letterSends'), 1)

    def test_edge_attaches_letter_after_direct_response(self):
        self.sent_letter_fixture()
        card = self.page.locator('[data-qa="vacancy-serp__vacancy"]')
        self.bot._attach_sent_letter(self.page, card, 'Наше письмо', card.get_by_text('Приложить письмо', exact=True))
        self.assertEqual(self.page.evaluate('window.sentLetter'), 'Наше письмо')

    def test_attach_letter_does_not_touch_another_vacancy(self):
        self.sent_letter_fixture()
        result = self.page.evaluate("new Promise(resolve=>hhHandler({action:'attachLetter',payload:{id:'999',letter:'Наше письмо'}},{id:'test'},resolve))")
        self.assertIn('error', result)
        self.assertIsNone(self.page.evaluate('window.letterSends || null'))

    def test_pending_submit_is_not_clicked_twice(self):
        self.page.set_content('<div id="card">PTO</div><div role="dialog">Отклик на вакансию<button onclick="window.clicks=(window.clicks||0)+1">Откликнуться</button></div>')
        self.page.wait_for_timeout = lambda _: None
        card = self.page.locator('#card')
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                self.bot._finish_response(self.page, '', card, '123')
        self.assertEqual(self.page.evaluate('window.clicks'), 1)

    def test_stage_error_pauses_then_retries(self):
        from unittest.mock import Mock
        operation = Mock(side_effect=[RuntimeError('unknown form'), 'done'])
        requests = []
        self.bot.request_user_action = lambda message: requests.append(message) or True
        self.assertEqual(self.bot._retry_stage(self.page, operation, 'Поиск'), 'done')
        self.assertEqual(len(requests), 1)

    def test_search_waits_for_new_document_not_old_load_state(self):
        from unittest.mock import Mock
        page = Mock()
        page.is_closed.return_value = False
        self.bot._has_blocker = lambda _: False
        callbacks = {}
        page.on.side_effect = lambda name, callback: callbacks.update({name: callback})
        waits = []
        def wait(_):
            waits.append(1)
            if len(waits) == 3:
                callbacks['domcontentloaded']()
        page.wait_for_timeout.side_effect = wait
        button = Mock()
        self.bot._click_search(page, button)
        self.assertEqual(len(waits), 3)
        self.assertEqual(button.click.call_count, 1)
        page.wait_for_load_state.assert_not_called()

    def test_required_letter(self):
        card = self.fixture(True)
        self.bot._finish_response(self.page, 'Здравствуйте!', card)
        self.assertTrue(self.bot._response_sent(card))

    def test_empty_required_letter_does_not_submit(self):
        card = self.fixture(True)
        with self.assertRaises(RuntimeError):
            self.bot._finish_response(self.page, '', card)
        self.assertFalse(self.bot._response_sent(card))

    def test_unknown_form_does_not_click_page_button(self):
        self.page.set_content('<div id="card">ПТО</div><button>Откликнуться</button>')
        with self.assertRaises(RuntimeError):
            self.bot._finish_response(self.page, '', self.page.locator('#card'))

    def test_pause_rechecks_condition(self):
        calls = []
        self.bot.request_user_action = lambda _: calls.append(1) or True
        states = iter([True, False])
        self.bot._manual_pause(self.page, 'Проверка', unresolved=lambda: next(states))
        self.assertEqual(len(calls), 2)

    def test_next_page_preserves_filters(self):
        self.page.route('https://hh.ru/**', lambda route: route.fulfill(body='<a data-qa="pager-next" href="/search/vacancy?page=1">Дальше</a>', content_type='text/html'))
        self.page.goto('https://hh.ru/search/vacancy?text=PTO&work_format=REMOTE')
        target = self.bot._next_page_url(self.page)
        self.assertIn('page=1', target)
        self.assertIn('work_format=REMOTE', target)
        self.assertIn('text=PTO', target)

    def test_last_page_has_no_next(self):
        self.page.set_content('<div data-qa="pager-block"><a>1</a></div>')
        self.assertIsNone(self.bot._next_page_url(self.page))

    def test_navigation_waits_after_timeout_without_restarting(self):
        from unittest.mock import Mock
        from playwright.sync_api import TimeoutError
        fake = Mock()
        fake.goto.side_effect = TimeoutError('slow site')
        fake.is_closed.return_value = False
        callbacks = {}
        fake.on.side_effect = lambda name, callback: callbacks.update({name: callback})
        fake.wait_for_timeout.side_effect = lambda _: callbacks['domcontentloaded']()
        self.bot._navigate(fake, 'https://hh.ru/')
        self.assertEqual(fake.goto.call_count, 1)

    def test_stop_interrupts_navigation_wait(self):
        from unittest.mock import Mock
        from playwright.sync_api import TimeoutError
        from hh_bot.automation import StoppedByUser
        fake = Mock()
        fake.goto.side_effect = TimeoutError('slow site')
        fake.is_closed.return_value = False
        fake.wait_for_timeout.side_effect = lambda _: self.bot.stop_event.set()
        with self.assertRaises(StoppedByUser):
            self.bot._navigate(fake, 'https://hh.ru/')
