"""Configuration and path resolution for Hikari Neural Memory."""

import json
import os
from pathlib import Path
from typing import Optional

from core.path_literals import HIKARI_MEMORY_DB
from core.runtime_paths import hikari_home


def resolve_neural_db_path() -> Path:
    """Resolve neural SQLite path; honors HIKARI_NEURAL_MEMORY_DB when set."""
    explicit = os.getenv("HIKARI_NEURAL_MEMORY_DB", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    brain_dir = resolve_neural_brain_dir()
    return brain_dir / HIKARI_MEMORY_DB


def resolve_neural_brain_dir() -> Path:
    """Parent directory for neural DB and optional brain tree."""
    explicit_db = os.getenv("HIKARI_NEURAL_MEMORY_DB", "").strip()
    if explicit_db:
        return Path(explicit_db).expanduser().resolve().parent
    env_brain = os.getenv("HIKARI_BRAIN_DIR", "").strip()
    if env_brain:
        return Path(env_brain).expanduser().resolve()
    return hikari_home() / "brain"


def uses_explicit_neural_db_override() -> bool:
    return bool(os.getenv("HIKARI_NEURAL_MEMORY_DB", "").strip())


class MemoryConfig:
    # Anonymous default; override in live brain config.json -> "user_id"
    DEFAULT_USER_ID = "local_user"
    DB_NAME = HIKARI_MEMORY_DB
    SCHEMA_PATH = Path(__file__).parent / "db" / "memory_schema.sql"

    CACHE_MAX_SIZE = 1000
    CACHE_TTL_SECONDS = 3600

    CONSOLIDATION_INTERVALS = {"micro": 0, "bounded": 10, "session": 0, "daily": 0}

    SALIENCE_DECAY_RATE = 0.01
    MIN_SALIENCE_THRESHOLD = 0.1
    ARCHIVE_SALIENCE_THRESHOLD = 0.05

    MAX_NODES_PER_RETRIEVAL = 50
    MAX_EDGES_PER_RETRIEVAL = 100

    _instance: Optional["MemoryConfig"] = None
    _config: dict = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_config()
        return cls._instance

    def _load_config(self):
        self.BRAIN_DIR = resolve_neural_brain_dir()
        self.DB_PATH = resolve_neural_db_path()
        self.CACHE_DIR = self.BRAIN_DIR / "cache"
        self.EMBEDDINGS_DIR = self.BRAIN_DIR / "embeddings"
        self.LOGS_DIR = self.BRAIN_DIR / "logs"
        self.BACKUPS_DIR = self.BRAIN_DIR / "backups"
        self.CONFIG_FILE = self.BRAIN_DIR / "config.json"

        if uses_explicit_neural_db_override():
            self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._config = self._read_config_file_or_default()
            return

        self.BRAIN_DIR.mkdir(parents=True, exist_ok=True)
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)
        self.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

        if self.CONFIG_FILE.exists():
            self._config = self._read_config_file_or_default()
        else:
            self._config = self._default_config()
            self._save_config()

    def _read_config_file_or_default(self) -> dict:
        if self.CONFIG_FILE.exists():
            try:
                with open(self.CONFIG_FILE) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return self._default_config()

    def _default_config(self) -> dict:
        return {
            "version": 1,
            "user_id": self.DEFAULT_USER_ID,
            "brain_path": str(self.BRAIN_DIR),
            "salience_decay_rate": self.SALIENCE_DECAY_RATE,
            "cache_enabled": True,
            "vector_fallback_enabled": True,
            "auto_consolidation": True,
        }

    def _save_config(self):
        with open(self.CONFIG_FILE, "w") as f:
            json.dump(self._config, f, indent=2)

    def get(self, key: str, default=None):
        return self._config.get(key, default)

    def set(self, key: str, value):
        self._config[key] = value
        self._save_config()

    @property
    def user_id(self) -> str:
        return self._config.get("user_id", self.DEFAULT_USER_ID)

    @user_id.setter
    def user_id(self, value: str):
        self._config["user_id"] = value
        self._save_config()

    def ensure_directories(self):
        for d in [
            self.BRAIN_DIR,
            self.CACHE_DIR,
            self.EMBEDDINGS_DIR,
            self.LOGS_DIR,
            self.BACKUPS_DIR,
        ]:
            d.mkdir(parents=True, exist_ok=True)


def reset_memory_config_singleton() -> None:
    """Test helper: drop cached singleton so env overrides re-resolve paths."""
    global config
    MemoryConfig._instance = None
    config = MemoryConfig()


config = MemoryConfig()
