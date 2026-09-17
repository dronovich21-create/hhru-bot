from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock


@dataclass(frozen=True)
class ApplicationRecord:
    vacancy_id: str
    title: str
    company: str
    url: str
    applied_at: str


class HistoryStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[ApplicationRecord]:
        with self._lock:
            if not self.path.exists():
                return []
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                return [ApplicationRecord(**item) for item in raw]
            except (json.JSONDecodeError, TypeError, KeyError) as exc:
                raise ValueError(f"История повреждена: {self.path}. Файл сохранён без изменений; восстановите его перед запуском.") from exc

    def contains(self, vacancy_id: str) -> bool:
        return any(item.vacancy_id == vacancy_id for item in self.load())

    def add(self, vacancy_id: str, title: str, company: str, url: str) -> ApplicationRecord:
        record = ApplicationRecord(
            vacancy_id=vacancy_id,
            title=title.strip() or "Без названия",
            company=company.strip(),
            url=url,
            applied_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        with self._lock:
            records: list[dict] = []
            if self.path.exists():
                try:
                    records = json.loads(self.path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, TypeError) as exc:
                    raise ValueError(f"История повреждена: {self.path}. Перезапись запрещена.") from exc
                if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
                    raise ValueError("Неверный формат истории. Перезапись запрещена.")
                for item in records:
                    ApplicationRecord(**item)
            if any(str(item.get("vacancy_id")) == vacancy_id for item in records):
                return record
            records.append(asdict(record))
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.path)
        return record
