from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from hh_bot.automation import RunSettings
from hh_bot.history import HistoryStore
from hh_bot.templates import TemplateStore
from hh_bot.display import enable_dpi_awareness, scaled_size
from hh_bot.professions import ProfessionStore
from hh_bot.chrome_bridge import ChromeBridge
from hh_bot.chrome_automation import ChromeAutomation


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
REGIONS = {"Все регионы": None, "Москва": "1", "Санкт-Петербург": "2", "Самара": "78", "Тольятти": "212"}


class App(tk.Tk):
    def __init__(self):
        enable_dpi_awareness()
        super().__init__()
        self.title("HH.ru — помощник откликов (тестовая сборка)")
        self.geometry(scaled_size(self, 760, 620))
        minimum = scaled_size(self, 680, 540).split('x')
        self.minsize(int(minimum[0]), int(minimum[1]))
        self.history = HistoryStore(DATA / "applications.json")
        self.templates = TemplateStore(DATA / 'letters.json')
        self.professions = ProfessionStore(DATA / 'professions.json')
        self.profession_warning = None
        try:
            self.profession_names = self.professions.load()
        except (OSError, ValueError) as exc:
            self.profession_names = [ProfessionStore.DEFAULT]
            self.profession_warning = str(exc)
        self.chrome_bridge = None
        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.profession = tk.StringVar(value="Инженер ПТО")
        self.remote = tk.BooleanVar(value=True)
        self.region = tk.StringVar(value="Самара")
        self.limit = tk.StringVar(value="20")
        self.template_name = tk.StringVar()
        self.platform = tk.StringVar(value='hh.ru')
        self.status_text = tk.StringVar(value="Готово к запуску")
        self.count_text = tk.StringVar()
        self._build()
        selected = self.templates.selected_name()
        if selected in self.templates.load():
            self.template_name.set(selected)
            self._load_template()
        self._refresh_count()
        if self.templates.warning:
            self._log(self.templates.warning)
        if self.profession_warning:
            self._log(self.profession_warning)
        self.after(100, self._drain_events)
        self.protocol('WM_DELETE_WINDOW', self._close_app)

    def _build(self):
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)
        header = ttk.Frame(root)
        header.pack(fill="x")
        ttk.Label(header, text="Автоотклики hh.ru", font=("Segoe UI", 18, "bold")).pack(side="left")
        ttk.Label(header, textvariable=self.count_text, font=("Segoe UI", 11, "bold")).pack(side="right")
        browser_row = ttk.Frame(root)
        browser_row.pack(fill='x', pady=(8, 0))
        ttk.Radiobutton(browser_row, text='Авито (скоро)', variable=self.platform, value='avito', state='disabled').pack(side='right')
        ttk.Radiobutton(browser_row, text='hh.ru', variable=self.platform, value='hh.ru').pack(side='right', padx=8)
        ttk.Label(browser_row, text='Площадка:').pack(side='right', padx=8)
        ttk.Button(browser_row, text='Подключить Chrome', command=self._connect_chrome).pack(side='left')

        box = ttk.LabelFrame(root, text="Параметры поиска", padding=14)
        box.pack(fill="x", pady=(18, 10))
        ttk.Label(box, text="Профессия").grid(row=0, column=0, sticky="w")
        profession_row = ttk.Frame(box)
        profession_row.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(4, 10))
        self.profession_combo = ttk.Combobox(profession_row, textvariable=self.profession, values=self.profession_names, state='readonly')
        self.profession_combo.pack(side='left', fill='x', expand=True)
        ttk.Button(profession_row, text='Добавить', command=self._add_profession).pack(side='right', padx=(8, 0))
        ttk.Checkbutton(box, text="Только удалённая работа", variable=self.remote, command=self._update_region_state).grid(row=2, column=0, sticky="w")
        region_frame = ttk.Frame(box)
        region_frame.grid(row=2, column=1, sticky="e", padx=(20, 0))
        ttk.Label(region_frame, text="Регион").pack(side="left", padx=(0, 8))
        self.region_combo = ttk.Combobox(region_frame, textvariable=self.region, values=list(REGIONS), state="disabled", width=20)
        self.region_combo.pack(side="left")
        box.columnconfigure(0, weight=1)
        limit_frame = ttk.Frame(box)
        limit_frame.grid(row=3, column=0, columnspan=2, sticky='w', pady=(12, 0))
        ttk.Label(limit_frame, text='Лимит откликов за запуск:').pack(side='left')
        ttk.Entry(limit_frame, textvariable=self.limit, width=9).pack(side='left', padx=8)

        letter_header = ttk.Frame(root)
        letter_header.pack(fill='x', pady=(8, 4))
        ttk.Label(letter_header, text='Сопроводительное письмо').pack(side='left')
        ttk.Button(letter_header, text='Сохранить', command=self._save_template).pack(side='right')
        self.template_combo = ttk.Combobox(letter_header, textvariable=self.template_name, values=list(self.templates.load()), state='readonly', width=24)
        self.template_combo.pack(side='right', padx=8)
        self.template_combo.bind('<<ComboboxSelected>>', self._load_template)
        self.letter = tk.Text(root, height=8, wrap="word", font=("Segoe UI", 10))
        self.letter.pack(fill="x")

        controls = ttk.Frame(root)
        controls.pack(fill="x", pady=12)
        self.start_btn = ttk.Button(controls, text="Запустить", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(controls, text="Остановить", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        ttk.Button(controls, text="История откликов", command=self.show_history).pack(side="right")

        ttk.Label(root, textvariable=self.status_text).pack(anchor="w", pady=(0, 5))
        self.log_box = tk.Text(root, height=12, state="disabled", wrap="word", bg="#f4f4f4")
        self.log_box.pack(fill="both", expand=True)

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            self.history.load()
        except (OSError, ValueError) as exc:
            messagebox.showerror('История откликов', str(exc))
            return
        profession = self.profession.get().strip()
        if not profession:
            messagebox.showwarning("Параметры", "Выберите профессию.")
            return
        try:
            limit = int(self.limit.get().strip())
            if limit < 1:
                raise ValueError
        except ValueError:
            messagebox.showwarning('Лимит', 'Введите целое число больше нуля.')
            return
        if self.platform.get() != 'hh.ru':
            messagebox.showwarning('Площадка', 'Авито пока недоступно.')
            return
        channel = 'chrome'
        settings = RunSettings(profession, self.remote.get(), self.letter.get("1.0", "end").strip(), REGIONS[self.region.get()], limit, channel)
        if channel == 'chrome':
            try:
                self._ensure_chrome_bridge()
            except OSError as exc:
                messagebox.showerror('Подключение Chrome', f'Не удалось открыть локальный порт 8765: {exc}\nВозможно, уже запущен второй экземпляр приложения.')
                return
        self.stop_event.clear()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._log("Запуск новой сессии.")
        self.worker = threading.Thread(target=self._worker, args=(settings,), daemon=True)
        self.worker.start()

    def _update_region_state(self):
        self.region_combo.configure(state="disabled" if self.remote.get() else "readonly")

    def _add_profession(self):
        name = simpledialog.askstring('Добавить профессию', 'Введите профессию или должность:', parent=self)
        if name is None:
            return
        try:
            name = self.professions.add(name)
            self.profession_combo.configure(values=self.professions.load())
            self.profession.set(name)
            self._log(f'Профессия «{name}» сохранена.')
        except (OSError, ValueError) as exc:
            messagebox.showerror('Профессии', str(exc))

    def _save_template(self):
        text = self.letter.get('1.0', 'end').strip()
        if not text:
            messagebox.showwarning('Письмо', 'Сначала напишите сопроводительное письмо.')
            return
        name = simpledialog.askstring('Сохранить письмо', 'Название письма:', initialvalue=self.template_name.get(), parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in self.templates.load() and not messagebox.askyesno('Сохранить письмо', f'Заменить письмо «{name}»?'):
            return
        self.templates.save(name, text)
        self.template_combo.configure(values=list(self.templates.load()))
        self.template_name.set(name)
        self.templates.select(name)
        self._log(f'Письмо «{name}» сохранено.')

    def _load_template(self, event=None):
        text = self.templates.load().get(self.template_name.get(), '')
        self.letter.delete('1.0', 'end')
        self.letter.insert('1.0', text)
        self.templates.select(self.template_name.get())

    def _worker(self, settings):
        try:
            if settings.browser_channel == 'chrome':
                bot = ChromeAutomation(
                    self.chrome_bridge, self.history,
                    lambda text: self.events.put(('log', text)),
                    lambda text: self.events.put(('status', text)),
                    self._request_user_action,
                    lambda: self.events.put(('history', None)),
                    self.stop_event, self._copy_to_clipboard,
                )
            else:
                raise ValueError('Приложение работает только через Google Chrome.')
            bot.run(settings)
        except ModuleNotFoundError:
            self.events.put(("error", "Не установлен Playwright. Запустите install.bat."))
        except Exception as exc:
            self.events.put(("error", f"Ошибка: {exc}"))
        finally:
            self.events.put(("finished", None))

    def _ensure_chrome_bridge(self):
        if self.chrome_bridge is None:
            self.chrome_bridge = ChromeBridge(DATA)

    def _connect_chrome(self):
        try:
            self._ensure_chrome_bridge()
            self.clipboard_clear()
            self.clipboard_append(self.chrome_bridge.token)
            messagebox.showinfo('Подключение обычного Chrome',
                'Ключ подключения скопирован.\n\n'
                '1. В chrome://extensions включите режим разработчика.\n'
                '2. Нажмите «Загрузить распакованное расширение» и выберите папку:\n'
                f'{ROOT / "chrome-extension"}\n\n'
                '3. Откройте расширение «HH.ru помощник», вставьте ключ и нажмите «Подключить».\n'
                '4. Вернитесь в приложение и нажмите «Запустить».\n\n'
                'Ключ личный: не публикуйте его.')
        except OSError as exc:
            messagebox.showerror('Подключение Chrome', str(exc))

    def _close_app(self):
        self.stop_event.set()
        if self.worker and self.worker.is_alive():
            self.status_text.set('Останавливаю работу перед закрытием…')
            self.after(200, self._close_app)
            return
        if self.chrome_bridge:
            self.chrome_bridge.close()
        self.destroy()

    def _request_user_action(self, message: str) -> bool:
        answer = threading.Event()
        result = {"continue": False}
        self.events.put(("action", (message, answer, result)))
        while not answer.wait(0.2):
            if self.stop_event.is_set():
                return False
        return result["continue"]

    def _copy_to_clipboard(self, text: str):
        completed = threading.Event()
        self.events.put(("clipboard", (text, completed)))
        while not completed.wait(0.2):
            if self.stop_event.is_set():
                from hh_bot.automation import StoppedByUser
                raise StoppedByUser

    def _drain_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log": self._log(payload)
                elif kind == "status": self.status_text.set(payload)
                elif kind == "history": self._refresh_count()
                elif kind == "clipboard":
                    text, completed = payload
                    self.clipboard_clear()
                    self.clipboard_append(text)
                    self.update_idletasks()
                    completed.set()
                elif kind == "error":
                    self._log(payload); messagebox.showerror("HH.ru помощник", payload)
                elif kind == "action": self._show_action_dialog(*payload)
                elif kind == "finished":
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _show_action_dialog(self, message, answer, result):
        dialog = tk.Toplevel(self)
        dialog.title("Требуется действие")
        dialog.geometry(scaled_size(dialog, 520, 220))
        dialog.transient(self)
        dialog.attributes("-topmost", True)
        ttk.Label(dialog, text="Требуется действие в браузере", font=("Segoe UI", 14, "bold")).pack(pady=(18, 8))
        ttk.Label(dialog, text=message, wraplength=470, justify="left").pack(fill="x", padx=20)
        buttons = ttk.Frame(dialog); buttons.pack(side="bottom", fill="x", padx=20, pady=18)
        def finish(value):
            result["continue"] = value; answer.set(); dialog.destroy()
        ttk.Button(buttons, text="Остановить", command=lambda: finish(False)).pack(side="left")
        ttk.Button(buttons, text="Продолжить", command=lambda: finish(True)).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))

    def stop(self):
        self.stop_event.set()
        self.status_text.set("Останавливаю после текущего действия…")

    def _log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _refresh_count(self):
        try:
            self.count_text.set(f"Успешных откликов: {len(self.history.load())}")
        except (OSError, ValueError) as exc:
            self.count_text.set('История недоступна')
            self._log(str(exc))

    def show_history(self):
        try:
            records = list(reversed(self.history.load()))
        except (OSError, ValueError) as exc:
            messagebox.showerror('История', str(exc))
            return
        window = tk.Toplevel(self)
        window.title("История откликов")
        window.geometry(scaled_size(window, 720, 460))
        ttk.Label(window, text=f"Всего откликов: {len(records)}", font=("Segoe UI", 13, "bold")).pack(anchor="ne", padx=14, pady=12)
        frame = ttk.Frame(window, padding=(14, 0, 14, 14)); frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=("title", "company", "date"), show="headings")
        tree.heading("title", text="Вакансия"); tree.heading("company", text="Компания"); tree.heading("date", text="Дата")
        tree.column("title", width=330); tree.column("company", width=210); tree.column("date", width=145)
        for row in records:
            tree.insert("", "end", values=(row.title, row.company, row.applied_at.replace("T", " ")))
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview); tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True); scroll.pack(side="right", fill="y")


if __name__ == "__main__":
    App().mainloop()
