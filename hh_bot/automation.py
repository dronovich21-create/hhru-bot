from __future__ import annotations

import random
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit, urljoin

from .history import HistoryStore


@dataclass(frozen=True)
class RunSettings:
    profession: str
    remote: bool
    cover_letter: str
    region_id: str | None = None
    application_limit: int = 20
    browser_channel: str = "msedge"


class StoppedByUser(Exception):
    pass


class LimitReached(Exception):
    pass


class HHAutomation:
    def __init__(
        self,
        profile_dir: Path,
        history: HistoryStore,
        log: Callable[[str], None],
        status: Callable[[str], None],
        request_user_action: Callable[[str], bool],
        on_applied: Callable[[], None],
        stop_event: threading.Event,
        copy_to_clipboard: Callable[[str], None] | None = None,
    ):
        self.profile_dir = profile_dir
        self.history = history
        self.log = log
        self.status = status
        self.request_user_action = request_user_action
        self.on_applied = on_applied
        self.stop_event = stop_event
        self.copy_to_clipboard = copy_to_clipboard
        self.sent_this_run = 0
        self.pending_submissions: set[str] = set()

    def run(self, settings: RunSettings) -> None:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        from playwright.sync_api import sync_playwright

        if settings.browser_channel not in ("msedge", "chrome"):
            raise ValueError("Выберите Microsoft Edge или Google Chrome.")
        profile = self.browser_profile(settings.browser_channel)
        profile.mkdir(parents=True, exist_ok=True)
        self.sent_this_run = 0
        self.pending_submissions.clear()
        browser_name = "Edge" if settings.browser_channel == "msedge" else "Chrome"
        self.status(f"Открываю {browser_name}…")
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                str(profile), channel=settings.browser_channel, headless=False,
                viewport={"width": 1360, "height": 900}, locale="ru-RU",
                args=["--start-maximized"],
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(8_000)
            try:
                self._retry_stage(page, lambda: self._navigate(page, "https://hh.ru/"), 'Открытие сайта')
                self._retry_stage(page, lambda: self._wait_until_ready(page), 'Вход в аккаунт')
                self._retry_stage(page, lambda: self._search(page, settings), 'Поиск')
                self._retry_stage(page, lambda: self._configure_region(page, settings), 'Настройка региона')
                self._retry_stage(page, lambda: self._process_all_pages(page, settings, PlaywrightTimeout), 'Обработка выдачи')
                self.status("Готово")
            except StoppedByUser:
                self.status("Остановлено")
                self.log("Работа остановлена пользователем.")
            except LimitReached:
                self.status(f"Лимит достигнут: {self.sent_this_run} откликов")
                self.log(f"Лимит запуска достигнут: {self.sent_this_run}. Работа завершена.")
            finally:
                context.close()
                self.log(f"Сессия сохранена; отдельное окно {browser_name} закрыто.")

    def browser_profile(self, channel: str) -> Path:
        # Preserve the existing Edge login, keep Chrome cookies separate.
        return self.profile_dir if channel == "msedge" else self.profile_dir.with_name("chrome-profile")

    def _check_stop(self) -> None:
        if self.stop_event.is_set():
            raise StoppedByUser

    def _retry_stage(self, page, operation, title):
        from playwright.sync_api import Error as PlaywrightError
        while True:
            self._check_stop()
            try:
                return operation()
            except (PlaywrightError, RuntimeError, ValueError, OSError) as exc:
                if page.is_closed():
                    raise StoppedByUser from exc
                self._manual_pause(page, f'{title}: {exc}\nВыполните действие в браузере или восстановите файл, затем нажмите «Продолжить».')

    def _click_search(self, page, button):
        loaded = threading.Event()
        def on_loaded():
            loaded.set()
        page.on('domcontentloaded', on_loaded)
        try:
            button.click(no_wait_after=True, timeout=1000)
            self.status('Ожидаю новую страницу поиска…')
            while not loaded.is_set():
                self._check_stop()
                if page.is_closed():
                    raise StoppedByUser
                if self._has_blocker(page):
                    self._manual_pause(page, 'Пройдите проверку в браузере, затем продолжите.')
                page.wait_for_timeout(500)
        finally:
            page.remove_listener('domcontentloaded', on_loaded)

    def _navigate(self, page, url: str) -> None:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        self._check_stop()
        self.status("Ожидаю загрузку сайта… Можно нажать «Остановить».")
        loaded = threading.Event()
        def on_loaded():
            loaded.set()
        page.on("domcontentloaded", on_loaded)
        try:
            try:
                # Start once; keep waiting for THIS document, not the old page.
                page.goto(url, wait_until="domcontentloaded", timeout=1000)
                loaded.set()
            except PlaywrightTimeout:
                while not loaded.is_set():
                    self._check_stop()
                    if page.is_closed():
                        raise RuntimeError("Окно браузера закрыто пользователем.")
                    page.wait_for_timeout(500)
        finally:
            page.remove_listener("domcontentloaded", on_loaded)
        self._check_stop()

    def _manual_pause(self, page, message: str, unresolved=None) -> None:
        self.status("Ожидается действие пользователя")
        self.log(message)
        while True:
            self._check_stop()
            if not self.request_user_action(message):
                raise StoppedByUser
            page.wait_for_timeout(500)
            if not self._has_blocker(page) and not (unresolved and unresolved()):
                self.log("Ручное действие принято, продолжаю.")
                return
            message = "Препятствие всё ещё видно. Завершите действие в браузере и снова нажмите «Продолжить»."

    def _wait_until_ready(self, page) -> None:
        def needs_login():
            return self._visible(page.get_by_role("link", name="Войти", exact=True).first) or self._visible(page.get_by_role("button", name="Войти", exact=True).first)
        if needs_login():
            self._manual_pause(page, "Войдите в аккаунт hh.ru в открывшемся браузере, затем нажмите «Продолжить».", unresolved=needs_login)

    def _search(self, page, settings: RunSettings) -> None:
        self._check_stop()
        query = settings.profession + (" удаленно" if settings.remote else "")
        self.status(f"Ищу: {query}")
        search = page.locator('[data-qa="search-input"]').first
        if not search.count():
            search = page.locator('#a11y-search-input').first
        if not search.count():
            search = page.get_by_placeholder(re.compile("Профессия|Поиск по вакансиям", re.I)).first
        search.fill(query)
        button = page.locator('[data-qa="search-button"]').first
        if not button.count():
            button = page.get_by_role("button", name="Найти", exact=True).first
        self._click_search(page, button)
        page.wait_for_timeout(1200)

    @staticmethod
    def without_region(url: str) -> str:
        parts = urlsplit(url)
        query = [(key, value) for key, value in parse_qsl(parts.query) if key != "area"]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    @staticmethod
    def search_url(url: str, settings: RunSettings) -> str:
        parts = urlsplit(url)
        query = [(k, v) for k, v in parse_qsl(parts.query) if k not in ("text", "work_format", "area")]
        query.append(("text", settings.profession))
        if settings.remote:
            query.append(("work_format", "REMOTE"))
        elif settings.region_id:
            query.append(("area", settings.region_id))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))

    def _configure_region(self, page, settings: RunSettings) -> None:
        self._check_stop()
        if urlsplit(page.url).path != '/search/vacancy':
            raise RuntimeError('Страница поиска ещё не открыта. Откройте выдачу вакансий перед продолжением.')
        self._navigate(page, self.search_url(page.url, settings))
        page.wait_for_timeout(1200)
        self.log("Удалённый поиск по всем регионам." if settings.remote else f"Поиск в выбранном регионе (ID: {settings.region_id or 'все'}).")

    def _process_all_pages(self, page, settings: RunSettings, timeout_type) -> None:
        visited: set[int] = set()
        while True:
            self._check_stop()
            if self._has_blocker(page):
                self._manual_pause(page, "Выполните требуемое действие в браузере перед обработкой страницы.")
            current = int(dict(parse_qsl(urlsplit(page.url).query)).get("page", "0"))
            if current in visited:
                raise RuntimeError("Выдача вернулась на уже обработанную страницу. Работа остановлена во избежание цикла.")
            visited.add(current)
            self.log(f"Обрабатываю страницу {current + 1}.")
            self._process_page(page, settings, timeout_type)
            self._check_stop()
            next_url = self._next_page_url(page)
            if next_url is None:
                self.log("Следующей страницы нет. Выдача обработана.")
                return
            self.log(f"Страница {current + 1} завершена. Перехожу на страницу {current + 2}.")
            self._navigate(page, next_url)
            page.wait_for_timeout(1200)

    def _next_page_url(self, page) -> str | None:
        current_parts = urlsplit(page.url)
        current = int(dict(parse_qsl(current_parts.query)).get("page", "0"))
        candidates = page.locator('a[data-qa="pager-next"]')
        if not candidates.count():
            candidates = page.locator('[data-qa="pager-block"], [aria-label="Пагинация"]').get_by_role("link", name=str(current + 2), exact=True)
        for index in range(candidates.count()):
            link = candidates.nth(index)
            if link.get_attribute("aria-disabled") == "true":
                continue
            href = link.get_attribute("href")
            if not href:
                continue
            target = urlsplit(urljoin(page.url, href))
            query = dict(parse_qsl(target.query))
            if target.hostname != current_parts.hostname or target.path != current_parts.path:
                raise RuntimeError("Ссылка следующей страницы ведёт вне текущей выдачи.")
            if query.get("page") != str(current + 1):
                raise RuntimeError("Номер следующей страницы не соответствует ожидаемому.")
            # Keep the run's existing filters even if the pager link omits them.
            filters = [(k, v) for k, v in parse_qsl(current_parts.query) if k != "page"]
            filters.append(("page", str(current + 1)))
            return urlunsplit((target.scheme, target.netloc, target.path, urlencode(filters), ""))
        return None

    def _process_page(self, page, settings: RunSettings, timeout_type) -> None:
        cards = page.locator('[data-qa="vacancy-serp__vacancy"]')
        if not cards.count():
            cards = page.locator('[data-qa="vacancy-serp__vacancy-card"]')
        total = cards.count()
        if total == 0:
            if self._visible(page.get_by_text(re.compile(r"ничего не\s+найдено", re.I)).first):
                self.log("По запросу нет вакансий.")
                return
            raise RuntimeError("Карточки вакансий не обнаружены. Нужна проверка разметки hh.ru; отклики не отправлены.")
        self.log(f"На странице найдено карточек: {total}.")
        for index in range(total):
            self._check_stop()
            self._check_limit(settings)
            if self._has_blocker(page):
                self._manual_pause(page, "На странице появилась CAPTCHA или другое препятствие. Решите его вручную.")
            card = cards.nth(index)
            title_link = card.locator('[data-qa="serp-item__title"]').first
            if not title_link.count():
                title_link = card.get_by_role("link").first
            title = (title_link.inner_text() or "Без названия").strip()
            url = title_link.get_attribute("href") or ""
            vacancy_id = self._vacancy_id(url, index)
            company_node = card.locator('[data-qa="vacancy-serp__vacancy-employer-text"]').first
            company = company_node.inner_text().strip() if company_node.count() else ""
            if self.history.contains(vacancy_id):
                self.log(f"Пропуск (уже откликались): {title}")
                continue
            respond = card.locator('[data-qa="vacancy-serp__vacancy_response"]').first
            if not respond.count():
                respond = card.get_by_role("button", name="Откликнуться", exact=True).first
            if not respond.count() or not respond.is_visible():
                self.log(f"Пропуск (нет кнопки отклика): {title}")
                continue
            self.status(f"Отклик {index + 1} из {total}: {title}")
            initiated = False
            used_response_form = False
            attachment_attempted = False
            while True:
                self._check_stop()
                try:
                    if not self._response_sent(card):
                        if not self._response_dialog(page):
                            if initiated or vacancy_id in self.pending_submissions:
                                raise RuntimeError("Форма исчезла, но отправка не подтверждена. Повторный отклик автоматически не выполняется.")
                            initiated = True
                            respond.click()
                            page.wait_for_timeout(700)
                        used_response_form = used_response_form or self._response_dialog(page) is not None
                        self._finish_response(page, settings.cover_letter, card, vacancy_id)
                    if not self._response_sent(card):
                        raise RuntimeError("hh.ru не подтвердил отправку отклика. Не отправляйте повторно без проверки.")
                    attach = card.get_by_text('Приложить письмо', exact=True).first
                    if not used_response_form and not attachment_attempted and self._visible(attach):
                        attachment_attempted = True
                        self._attach_sent_letter(page, card, settings.cover_letter, attach)
                    self.history.add(vacancy_id, title, company, url)
                    self.sent_this_run += 1
                    self.on_applied()
                    self.log(f"Отклик подтверждён: {title}" + (f" — {company}" if company else ""))
                    self._check_limit(settings)
                    break
                except (timeout_type, RuntimeError) as exc:
                    self._manual_pause(page, f"{exc}\nВакансия: {title}\nПроверьте текущий отклик в браузере, затем продолжите.")
            delay = random.randint(5, 10)
            self.status(f"Пауза {delay} сек.")
            for _ in range(delay * 2):
                self._check_stop()
                page.wait_for_timeout(500)

    def _attach_sent_letter(self, page, card, letter, attach):
        if not letter.strip():
            raise RuntimeError('Отклик отправлен, но шаблон письма пуст. Приложите письмо вручную.')
        attach.click()
        scope = None
        for _ in range(30):
            self._check_stop()
            if self._has_blocker(page):
                raise RuntimeError('Отклик отправлен; при приложении письма требуется ручное действие.')
            if self._visible(card.locator('textarea').first):
                scope = card
                break
            dialogs = page.locator('[role="dialog"], .bloko-modal-content, [data-qa="vacancy-response-popup"]')
            matches = [dialogs.nth(i) for i in range(dialogs.count()) if self._visible(dialogs.nth(i)) and re.search('сопроводительное|приложить письмо', dialogs.nth(i).inner_text(), re.I) and self._visible(dialogs.nth(i).locator('textarea').first)]
            if len(matches) == 1:
                scope = matches[0]
                break
            page.wait_for_timeout(100)
        if scope is None:
            raise RuntimeError('Не распознана форма приложения письма. Сам отклик уже отправлен; добавьте письмо вручную.')
        area = scope.locator('textarea').first
        self._insert_letter(area, letter)
        submit = scope.get_by_role('button', name=re.compile(r'^(Отправить|Отправить письмо|Приложить письмо|Добавить письмо)$', re.I)).first
        if not self._visible(submit) or submit.is_disabled():
            raise RuntimeError('Не распознана кнопка отправки письма. Добавьте его вручную.')
        self._check_stop()
        submit.click()
        for _ in range(30):
            self._check_stop()
            page.wait_for_timeout(100)
            if re.search('письмо отправлено|письмо приложено', card.inner_text(), re.I) or (not self._visible(area) and not self._visible(card.get_by_text('Приложить письмо', exact=True).first)):
                return
        raise RuntimeError('Результат отправки письма не подтверждён. Проверьте вручную; письмо повторно не отправляется.')

    def _finish_response(self, page, cover_letter: str, card, vacancy_id=None) -> None:
        if self._has_visibility_request(page):
            self._manual_pause(
                page,
                "hh.ru просит изменить видимость резюме. В тестовой сборке решите это вручную или закройте окно отклика.",
                unresolved=lambda: self._has_visibility_request(page),
            )
        if self._has_blocker(page):
            self._manual_pause(page, "На странице появилась CAPTCHA или неизвестное окно. Выполните требуемое действие вручную.")

        if self._response_sent(card):
            return
        key = vacancy_id or str(card)
        if key in self.pending_submissions:
            raise RuntimeError('Отправка уже была начата. Повторное нажатие запрещено; дождитесь подтверждения или проверьте отклик вручную.')
        scope = self._response_dialog(page)
        if scope is None:
            raise RuntimeError("Неизвестное окно или результат отклика. Требуется ручная проверка.")
        required = scope.locator("textarea[required]")
        add_letter = scope.get_by_role("button", name=re.compile("Добавить сопроводительное", re.I))
        letter_labels = scope.get_by_text(re.compile("Сопроводительное письмо.*обяз", re.I))
        if required.count() or letter_labels.count() or add_letter.count() or scope.locator('textarea').count():
            if not cover_letter.strip():
                raise RuntimeError("Для отклика требуется сопроводительное письмо, но поле шаблона пустое.")
            if add_letter.count() and add_letter.first.is_visible():
                add_letter.first.click()
            area = scope.locator("textarea").first
            self._insert_letter(area, cover_letter)

        submit = scope.locator('[data-qa="vacancy-response-submit-popup"]').first
        if not submit.count():
            submit = scope.get_by_role("button", name="Откликнуться", exact=True).last
        if not submit.count():
            raise RuntimeError("Не найдена финальная кнопка «Откликнуться».")
        if submit.is_disabled() and add_letter.count():
            if not cover_letter.strip():
                raise RuntimeError("Форма требует сопроводительное письмо, но шаблон пустой.")
            add_letter.first.click()
            self._insert_letter(page.locator("textarea").last, cover_letter)
        self._check_stop()
        self.pending_submissions.add(key)
        submit.click()
        for _ in range(20):
            page.wait_for_timeout(300)
            if self._response_sent(card):
                return
            if self._has_blocker(page):
                break
        raise RuntimeError("Отправка пока не подтверждена. Проверьте форму вручную.")

    def _check_limit(self, settings: RunSettings) -> None:
        if settings.application_limit < 1:
            raise ValueError("Лимит откликов должен быть положительным числом.")
        if self.sent_this_run >= settings.application_limit:
            raise LimitReached

    def _insert_letter(self, area, text: str) -> None:
        if self.copy_to_clipboard:
            self.copy_to_clipboard(text)
            area.click()
            area.press("Control+A")
            area.press("Control+V")
            if area.input_value() != text:
                raise RuntimeError("Не удалось вставить сопроводительное письмо из буфера обмена.")
        else:
            area.fill(text)

    def _response_dialog(self, page):
        candidates = page.locator('[role="dialog"], [data-qa="vacancy-response-popup"], .bloko-modal-content')
        for index in range(candidates.count()):
            candidate = candidates.nth(index)
            if candidate.is_visible() and "Отклик" in candidate.inner_text():
                return candidate
        headings = page.get_by_text('Отклик на вакансию', exact=True)
        for index in range(headings.count()):
            heading = headings.nth(index)
            if not self._visible(heading):
                continue
            ancestors = heading.locator('xpath=ancestor::*[not(self::body) and not(self::html)]')
            for ancestor_index in reversed(range(ancestors.count())):
                candidate = ancestors.nth(ancestor_index)
                if candidate.locator('[data-qa="vacancy-serp__vacancy"], [data-qa="vacancy-serp__vacancy-card"]').count():
                    break
                if self._visible(candidate.get_by_role('button', name='Откликнуться', exact=True).first):
                    return candidate
        return None

    @staticmethod
    def _response_sent(card) -> bool:
        text = card.inner_text().lower()
        return any(marker in text for marker in ("вы откликнулись", "отклик отправлен", "отозвать отклик"))

    @staticmethod
    def _visible(locator) -> bool:
        try:
            return locator.count() > 0 and locator.is_visible()
        except Exception:
            return False

    def _has_visibility_request(self, page) -> bool:
        dialogs = page.locator('[role="dialog"], [data-qa="vacancy-response-popup"], .bloko-modal-content')
        for index in range(dialogs.count()):
            dialog = dialogs.nth(index)
            if self._visible(dialog) and re.search("поменяйте видимость резюме|измените видимость резюме", dialog.inner_text(), re.I):
                return True
        return False

    def _has_blocker(self, page) -> bool:
        body = ""
        try:
            body = page.locator("body").inner_text(timeout=1500).lower()
        except Exception:
            return False
        markers = ("captcha", "капча", "я не робот", "подтвердите, что вы не робот")
        return any(marker in body for marker in markers)

    @staticmethod
    def _vacancy_id(url: str, fallback: int) -> str:
        match = re.search(r"/vacancy/(\d+)", url)
        return match.group(1) if match else f"unknown-{fallback}-{url}"
