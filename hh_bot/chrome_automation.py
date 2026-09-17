import random
import time

from .automation import HHAutomation, StoppedByUser


class ChromeAutomation:
    def __init__(self, bridge, history, log, status, request_action, on_applied, stop_event, clipboard):
        self.bridge, self.history = bridge, history
        self.log, self.status, self.request_action = log, status, request_action
        self.on_applied, self.stop_event, self.clipboard = on_applied, stop_event, clipboard
        self.pending = set()
        self.sent = 0

    def check(self):
        if self.stop_event.is_set():
            raise StoppedByUser

    def pause(self, message):
        self.status('Требуется действие пользователя')
        self.log(message)
        if not self.request_action(message):
            raise StoppedByUser
        self.check()

    def command(self, action, payload=None, retry=True):
        while True:
            self.check()
            try:
                return self.bridge.command(action, payload or {}, self.stop_event)
            except RuntimeError as exc:
                self.pause(str(exc))
                if not retry:
                    return None  # A mutation with unknown outcome MUST NOT be resent.

    def ready(self):
        while True:
            state = self.command('snapshot')
            if state.get('loading'):
                self.status('Ожидаю загрузку вкладки hh.ru…')
                if self.stop_event.wait(0.5):
                    raise StoppedByUser
                continue
            if state.get('blocker'):
                self.pause(state['blocker'] + '\nЗагруженный код: расширение ' + str(state.get('extensionVersion', 'неизвестно')) + ', страница ' + str(state.get('contentVersion', 'старая версия')))
                continue
            return state

    def run(self, settings):
        try:
            while not self.bridge.connected():
                self.pause('Подключите расширение «HH.ru помощник» в обычном Chrome, затем нажмите «Продолжить».')
            opened = self.command('open', retry=False)
            if opened is None:
                raise RuntimeError('Не удалось подтвердить открытие рабочей вкладки. Запустите заново после проверки Chrome.')
            state = self.ready()
            while state.get('login'):
                self.pause('Войдите в hh.ru в рабочей вкладке и нажмите «Продолжить».')
                state = self.ready()
            url = HHAutomation.search_url('https://hh.ru/search/vacancy', settings)
            self.command('navigate', {'url': url}, retry=False)
            visited = set()
            while True:
                state = self.ready()
                if state['url'] in visited:
                    self.pause('Выдача вернулась на уже обработанную страницу. Откройте следующую страницу вручную.')
                    continue
                if not state.get('cards') and not state.get('empty'):
                    self.pause('Карточки не обнаружены. Проверьте, что в рабочей вкладке открыта выдача hh.ru.')
                    continue
                visited.add(state['url'])
                self.log(f"Страница: {state['url']}; вакансий: {len(state.get('cards', []))}")
                for card in state.get('cards', []):
                    self.check()
                    if self.sent >= settings.application_limit:
                        break
                    if card['sent'] or self.history.contains(card['id']):
                        self.log(f"Пропуск: {card['title']}")
                        continue
                    if not card['canRespond']:
                        continue
                    self.status(f"Отклик {self.sent + 1}/{settings.application_limit}: {card['title']}")
                    self.command('openResponse', {'id': card['id']}, retry=False)
                    used_response_form = False
                    form_wait_started = time.monotonic()
                    while True:
                        current = self.ready()
                        found = next((c for c in current.get('cards', []) if c['id'] == card['id']), None)
                        if found and found['sent']:
                            if not used_response_form and found.get('canAttachLetter'):
                                if settings.cover_letter:
                                    self.clipboard(settings.cover_letter)
                                    result = self.command('attachLetter', {'id': card['id'], 'letter': settings.cover_letter}, retry=False)
                                    if not result or not result.get('attached'):
                                        self.pause('Сам отклик отправлен. Проверьте приложение сопроводительного письма вручную; повторной отправки письма не будет.')
                                else:
                                    self.pause('Сам отклик отправлен. Нажмите «Приложить письмо» и добавьте письмо вручную: шаблон приложения пуст.')
                            break
                        modal = current.get('modal')
                        if not modal:
                            if time.monotonic() - form_wait_started < 8:
                                self.status('Ожидаю появления формы отклика…')
                                if self.stop_event.wait(0.3):
                                    raise StoppedByUser
                                continue
                            self.pause('Не удалось распознать форму отклика, а отправка не подтверждена. Проверьте рабочую вкладку; повторная отправка не выполняется.\nЗагруженный код: расширение ' + str(current.get('extensionVersion', 'неизвестно')) + ', страница ' + str(current.get('contentVersion', 'старая версия')))
                            form_wait_started = time.monotonic()
                            continue
                        if card['id'] in self.pending:
                            self.pause('Отправка уже начата. Дождитесь подтверждения hh.ru или проверьте отклик вручную.')
                            continue
                        used_response_form = True
                        if modal.get('requiredLetter') or modal.get('wantsLetter'):
                            if not settings.cover_letter:
                                self.pause('Добавьте сопроводительное письмо вручную в браузере, затем продолжите. Для следующих вакансий заполните письмо в приложении перед запуском.')
                                # The extension rechecks the actual textarea before submitting.
                            if settings.cover_letter:
                                self.clipboard(settings.cover_letter)
                        self.check()
                        self.pending.add(card['id'])
                        result = self.command('submit', {'id': card['id'], 'title': card['title'], 'letter': settings.cover_letter}, retry=False)
                        if result and result.get('preparePending'):
                            self.pending.discard(card['id'])
                            if self.stop_event.wait(0.5):
                                raise StoppedByUser
                            continue
                        if result and result.get('notSubmitted'):
                            self.pending.discard(card['id'])
                            self.pause(result['notSubmitted'])
                            continue
                        # Wait before rechecking, but never resend the final click.
                        if self.stop_event.wait(2):
                            raise StoppedByUser
                    while True:
                        try:
                            self.history.add(card['id'], card['title'], card['company'], card['url'])
                            break
                        except (OSError, ValueError) as exc:
                            self.pause(f'Отклик отправлен, но история не сохранена: {exc}')
                    self.sent += 1
                    self.on_applied()
                    self.log(f"Отклик подтверждён: {card['title']}")
                    if self.sent >= settings.application_limit:
                        break
                    if self.stop_event.wait(random.randint(5, 10)):
                        raise StoppedByUser
                if self.sent >= settings.application_limit:
                    self.status(f'Лимит достигнут: {self.sent}')
                    break
                current = self.ready()
                if not current.get('next'):
                    self.status('Выдача обработана')
                    break
                self.command('navigate', {'url': current['next']}, retry=False)
        except StoppedByUser:
            self.status('Остановлено')
        finally:
            self.log('Рабочая вкладка Chrome оставлена открытой. Другие вкладки не затронуты.')
