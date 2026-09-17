import json
from pathlib import Path


class BrowserPreferences:
    OPTIONS = ('Google Chrome', 'Microsoft Edge')

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> str:
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            name = data.get('browser') if isinstance(data, dict) else None
            return name if name in self.OPTIONS else self.OPTIONS[0]
        except (OSError, ValueError):
            return self.OPTIONS[0]

    def save(self, name: str) -> None:
        if name not in self.OPTIONS:
            raise ValueError('Неизвестный браузер')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps({'browser': name}, ensure_ascii=False), encoding='utf-8')
        temp.replace(self.path)
