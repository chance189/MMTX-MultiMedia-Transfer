"""Persisted settings (plain JSON in the per-user config folder)."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field

from .hashing import default_algorithm


def config_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "MMTX")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/MMTX")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "mmtx")


def logs_dir() -> str:
    return os.path.join(config_dir(), "logs")


def cache_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "MMTX", "cache")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Caches/MMTX")
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "mmtx")


@dataclass
class Settings:
    algo: str = field(default_factory=default_algorithm)
    verify_readback: bool = True
    uncached: bool = True
    spaces_to_underscores: bool = True
    keep_structure: bool = False
    rename_gopro: bool = False
    workers: int = 0  # files at once; 0 = auto by drive type
    thumbnails: bool = True
    # offload
    offload_dest: str = ""
    default_title: str = "DailyRun"
    camera_subfolders: bool = True
    skip_proxies: bool = False
    delete_from_card: bool = False
    device_aliases: dict = field(default_factory=dict)  # detected label -> folder name
    # archive
    archive_source: str = ""
    archive_dest: str = ""
    archive_layout: str = "year"
    archive_move: bool = True
    older_than_days: int = 30
    # verify
    verify_path: str = ""
    verify_recursive: bool = True
    window_geometry: str = ""

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        try:
            with open(os.path.join(config_dir(), "settings.json"), "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                if hasattr(s, k) and isinstance(v, type(getattr(s, k))):
                    setattr(s, k, v)
        except (OSError, ValueError):
            pass
        return s

    def save(self) -> None:
        try:
            os.makedirs(config_dir(), exist_ok=True)
            path = os.path.join(config_dir(), "settings.json")
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2)
            os.replace(path + ".tmp", path)
        except OSError:
            pass
