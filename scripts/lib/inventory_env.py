"""Загрузка inventory: hosts.local.env (прод) предпочтительнее hosts.env (шаблон)."""
from __future__ import annotations

from pathlib import Path


def inventory_env_path(root: Path) -> Path:
    local = root / "inventory" / "hosts.local.env"
    public = root / "inventory" / "hosts.env"
    if local.is_file():
        return local
    return public


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip("'").strip('"')
    return env


def load_inventory(root: Path) -> dict[str, str]:
    return load_env_file(inventory_env_path(root))
