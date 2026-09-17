"""Local, atomic storage of user-defined search professions."""
import json
from pathlib import Path


class ProfessionStore:
    DEFAULT = 'Инженер ПТО'

    def __init__(self, path: Path):
        self.path = path

    def load(self):
        if not self.path.exists():
            return [self.DEFAULT]
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, list) or any(not isinstance(n, str) or not n.strip() for n in data):
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise ValueError('Список профессий повреждён. Исходный файл сохранён без изменений.') from exc
        names = [self.DEFAULT]
        for name in data:
            name = name.strip()
            if name.casefold() not in {n.casefold() for n in names}:
                names.append(name)
        return names

    def add(self, name):
        name = name.strip()
        if not name or len(name) > 200:
            raise ValueError('Введите профессию длиной от 1 до 200 символов.')
        names = self.load()
        existing = next((n for n in names if n.casefold() == name.casefold()), None)
        if existing:
            return existing
        names.append(name)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)
        return name
