import json
import os
from abc import ABC, abstractmethod
from pathlib import Path


class ExecutionHistoryRepository(ABC):
    @abstractmethod
    def load(self):
        """Return persisted execution records, or an empty list."""

    @abstractmethod
    def save(self, records):
        """Persist execution records without exposing unrelated state."""


class InMemoryExecutionHistoryRepository(ExecutionHistoryRepository):
    def __init__(self, records=None):
        self._records = list(records or [])

    def load(self):
        return list(self._records)

    def save(self, records):
        self._records = list(records)


class JsonFileExecutionHistoryRepository(ExecutionHistoryRepository):
    def __init__(self, history_file):
        self.history_file = Path(history_file)
        self.history_file.parent.mkdir(parents=True, exist_ok=True)

    def load(self):
        if not self.history_file.exists():
            return []
        try:
            with self.history_file.open("r", encoding="utf-8") as file:
                records = json.load(file)
            return records if isinstance(records, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def save(self, records):
        temporary_file = self.history_file.with_suffix(self.history_file.suffix + ".tmp")
        with temporary_file.open("w", encoding="utf-8") as file:
            json.dump(records, file, ensure_ascii=False, indent=4)
        os.replace(temporary_file, self.history_file)
