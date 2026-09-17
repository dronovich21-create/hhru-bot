import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from hh_bot.automation import RunSettings
from hh_bot.chrome_automation import ChromeAutomation
from hh_bot.history import HistoryStore


class ChromeAutomationTests(unittest.TestCase):
    def test_second_form_can_appear_after_several_snapshots(self):
        with tempfile.TemporaryDirectory() as folder:
            history = HistoryStore(Path(folder) / 'history.json')
            bridge, stop, pause = Mock(), Mock(), Mock(return_value=True)
            bridge.connected.return_value = True
            stop.is_set.return_value = False
            stop.wait.return_value = False
            first = {'id': '123', 'title': 'ПТО', 'company': '', 'url': 'https://hh.ru/vacancy/123', 'sent': False, 'canRespond': True}
            second = {**first, 'id': '456', 'url': 'https://hh.ru/vacancy/456'}
            url = 'https://hh.ru/search/vacancy'
            sent_first = {**first, 'sent': True}
            states = iter([
                {'url': 'https://hh.ru/'},
                {'url': url, 'cards': [first, second]},
                {'url': url, 'cards': [first, second], 'modal': {'requiredLetter': False}},
                {'url': url, 'cards': [sent_first, second]},
                {'url': url, 'cards': [sent_first, second]},
                {'url': url, 'cards': [sent_first, second]},
                {'url': url, 'cards': [sent_first, second], 'modal': {'requiredLetter': False}},
                {'url': url, 'cards': [sent_first, {**second, 'sent': True}]},
            ])
            calls = []
            def command(action, payload, _):
                calls.append((action, payload))
                return next(states) if action == 'snapshot' else {'ok': True}
            bridge.command.side_effect = command
            bot = ChromeAutomation(bridge, history, Mock(), Mock(), pause, Mock(), stop, Mock())
            bot.run(RunSettings('ПТО', True, '', application_limit=2, browser_channel='chrome'))
            pause.assert_not_called()
            self.assertEqual(len(history.load()), 2)
            self.assertEqual([p['id'] for a, p in calls if a == 'submit'], ['123', '456'])
            self.assertEqual([p['id'] for a, p in calls if a == 'openResponse'], ['123', '456'])

    def test_regular_chrome_flow_saves_only_confirmed_response(self):
        with tempfile.TemporaryDirectory() as folder:
            history = HistoryStore(Path(folder) / 'history.json')
            bridge = Mock()
            bridge.connected.return_value = True
            stop = Mock()
            stop.is_set.return_value = False
            stop.wait.return_value = False
            card = {'id': '123', 'title': 'ПТО', 'company': 'Фирма', 'url': 'https://hh.ru/vacancy/123', 'sent': False, 'canRespond': True}
            states = iter([
                {'url': 'https://hh.ru/', 'cards': []},
                {'url': 'https://hh.ru/search/vacancy', 'cards': [card]},
                {'url': 'https://hh.ru/search/vacancy', 'cards': [card], 'modal': {'requiredLetter': True}},
                {'url': 'https://hh.ru/search/vacancy', 'cards': [{**card, 'sent': True}]},
            ])
            calls = []
            def command(action, payload, _):
                calls.append(action)
                return next(states) if action == 'snapshot' else {'ok': True}
            bridge.command.side_effect = command
            changed, clipboard = Mock(), Mock()
            bot = ChromeAutomation(bridge, history, Mock(), Mock(), Mock(return_value=True), changed, stop, clipboard)
            bot.run(RunSettings('ПТО', True, 'Письмо', application_limit=1, browser_channel='chrome'))
            self.assertEqual(calls.count('open'), 1)
            self.assertEqual(calls.count('submit'), 1)
            self.assertEqual(len(history.load()), 1)
            clipboard.assert_called_once_with('Письмо')
            changed.assert_called_once()
