"""Resolves runtime paths for both source runs and frozen (PyInstaller) runs."""
import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_root() -> Path:
    """Directory that holds config/, models/, logs/, snapshots/, recordings/.

    When frozen, this is the folder containing the .exe so operators can edit
    config.json next to the executable without rebuilding.
    """
    env = os.environ.get("INTECK_HOME")
    if env:
        return Path(env).resolve()
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_root() -> Path:
    """Directory that holds read-only bundled resources (templates, static)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_root()))
    return Path(__file__).resolve().parent.parent


def resolve(relative: str) -> Path:
    p = Path(relative)
    return p if p.is_absolute() else (app_root() / p)


def ensure_dirs() -> None:
    for name in ("config", "models", "logs", "snapshots", "recordings"):
        (app_root() / name).mkdir(parents=True, exist_ok=True)
