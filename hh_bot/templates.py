import json
import shutil
from datetime import datetime
from pathlib import Path


class TemplateStore:
    def __init__(self, path: Path):
        self.path = path
        self.warning = None
        self.selection_path = path.with_name('letter-selection.json')

    def selected_name(self) -> str:
        try:
            data = json.loads(self.selection_path.read_text(encoding='utf-8'))
            return data.get('selected', '') if isinstance(data, dict) else ''
        except (OSError, ValueError):
            return ''

    def select(self, name: str) -> None:
        self.selection_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.selection_path.with_suffix('.tmp')
        temp.write_text(json.dumps({'selected': name}, ensure_ascii=False), encoding='utf-8')
        temp.replace(self.selection_path)

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or any(not isinstance(text, str) for text in data.values()):
                raise ValueError('Неверный формат писем')
            return data
        except (ValueError, UnicodeError):
            backup = self.path.with_name(self.path.name + '.damaged-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
            shutil.copy2(self.path, backup)
            self.warning = f'Файл писем повреждён. Резервная копия: {backup}. Список писем временно пуст.'
            return {}

    def save(self, name: str, text: str) -> None:
        data = self.load()
        data[name] = text
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.path)
