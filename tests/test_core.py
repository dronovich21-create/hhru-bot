import json
import tempfile
import unittest
from pathlib import Path

from hh_bot.automation import HHAutomation, RunSettings, LimitReached
from hh_bot.templates import TemplateStore
from hh_bot.preferences import BrowserPreferences
import threading
from hh_bot.history import HistoryStore


class CoreTests(unittest.TestCase):
    def test_browser_selection_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'browser.json'
            store = BrowserPreferences(path)
            self.assertEqual(store.load(), 'Google Chrome')
            store.save('Microsoft Edge')
            self.assertEqual(BrowserPreferences(path).load(), 'Microsoft Edge')
            store.save('Google Chrome')
            self.assertEqual(BrowserPreferences(path).load(), 'Google Chrome')
            path.write_text('{broken', encoding='utf-8')
            self.assertEqual(store.load(), 'Google Chrome')
    def test_template_selection_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'letters.json'
            store = TemplateStore(path)
            store.save('тест', 'Моё письмо')
            store.select('тест')
            restarted = TemplateStore(path)
            self.assertEqual(restarted.selected_name(), 'тест')
            self.assertEqual(restarted.load()[restarted.selected_name()], 'Моё письмо')
    def test_corrupt_history_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'h.json'
            path.write_text('{broken', encoding='utf-8')
            store = HistoryStore(path)
            with self.assertRaises(ValueError):
                store.load()
            with self.assertRaises(ValueError):
                store.add('1', 'PTO', '', '')
            self.assertEqual(path.read_text(encoding='utf-8'), '{broken')

    def test_corrupt_templates_have_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'letters.json'
            path.write_text('{broken', encoding='utf-8')
            store = TemplateStore(path)
            self.assertEqual(store.load(), {})
            self.assertTrue(store.warning)
            backup = list(Path(folder).glob('letters.json.damaged-*'))[0]
            self.assertEqual(backup.read_text(encoding='utf-8'), '{broken')
            store.save('Новое', 'Письмо')
            self.assertEqual(store.load(), {'Новое': 'Письмо'})
    def test_templates_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'letters.json'
            TemplateStore(path).save('ПТО', 'Здравствуйте!\nМой опыт…')
            self.assertEqual(TemplateStore(path).load()['ПТО'], 'Здравствуйте!\nМой опыт…')
            TemplateStore(path).save('ПТО', 'Обновлённое письмо')
            self.assertEqual(len(TemplateStore(path).load()), 1)

    def test_custom_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            bot = HHAutomation(Path(folder), HistoryStore(Path(folder) / 'h.json'), lambda _: None, lambda _: None, lambda _: True, lambda: None, threading.Event())
            settings = RunSettings('ПТО', True, '', application_limit=7)
            self.assertEqual(bot.browser_profile('msedge'), Path(folder))
            self.assertNotEqual(bot.browser_profile('chrome'), bot.browser_profile('msedge'))
            bot.sent_this_run = 6
            bot._check_limit(settings)
            bot.sent_this_run = 7
            with self.assertRaises(LimitReached):
                bot._check_limit(settings)

    def test_remote_ignores_selected_region(self):
        url = HHAutomation.search_url('https://hh.ru/search/vacancy?area=1', RunSettings('ПТО', True, '', '134'))
        self.assertNotIn('area=', url)
        self.assertIn('work_format=REMOTE', url)

    def test_local_uses_selected_region(self):
        url = HHAutomation.search_url('https://hh.ru/search/vacancy?area=1&work_format=REMOTE', RunSettings('ПТО', False, '', '134'))
        self.assertIn('area=134', url)
        self.assertNotIn('work_format=', url)
    def test_removes_only_area_parameter(self):
        url = "https://hh.ru/search/vacancy?text=x&area=1&work_format=REMOTE"
        result = HHAutomation.without_region(url)
        self.assertNotIn("area=", result)
        self.assertIn("text=x", result)
        self.assertIn("work_format=REMOTE", result)

    def test_history_is_deduplicated(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "history.json"
            store = HistoryStore(path)
            store.add("123", "Инженер ПТО", "Компания", "https://hh.ru/vacancy/123")
            store.add("123", "Инженер ПТО", "Компания", "https://hh.ru/vacancy/123")
            self.assertEqual(1, len(store.load()))
            self.assertEqual(1, len(json.loads(path.read_text(encoding="utf-8"))))


if __name__ == "__main__":
    unittest.main()
