"""Gerenciador de projetos: uma pasta por música, com tudo o que o JFD produziu."""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import unicodedata
from pathlib import Path

from .. import __version__
from ..config import log, projects_dir

SUBDIRS = ("original", "remasterizado", "instrumentos", "partituras", "midi", "analise", "trabalho")


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    return text[:60] or "musica"


class Project:
    """Estado de um projeto, salvo em ``projeto.json`` dentro da pasta."""

    def __init__(self, folder: Path, data: dict):
        self.folder = Path(folder)
        self.data = data

    # ---- criação e carga -------------------------------------------------
    @classmethod
    def create(cls, source: str | Path, name: str | None = None) -> "Project":
        source = Path(source)
        name = name or source.stem
        folder = projects_dir() / f"{slugify(name)}-{time.strftime('%Y%m%d-%H%M%S')}"
        for sub in SUBDIRS:
            (folder / sub).mkdir(parents=True, exist_ok=True)
        target = folder / "original" / source.name
        shutil.copy2(source, target)
        data = {"version": __version__, "name": name, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "original": f"original/{source.name}", "done": {}, "info": {}, "quality": {}, "music": {},
                "stems": {}, "remaster": {}, "lines": {}, "scores": [], "midi": [], "notices": []}
        proj = cls(folder, data)
        proj.save()
        return proj

    @classmethod
    def load(cls, folder: str | Path) -> "Project":
        folder = Path(folder)
        path = folder / "projeto.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            backup = folder / "projeto.json.bak"
            data = json.loads(backup.read_text(encoding="utf-8"))   # recuperação de arquivo corrompido
            log.warning("projeto.json corrompido; cópia de segurança usada em %s", folder)
        for key, default in (("done", {}), ("lines", {}), ("scores", []), ("midi", []), ("notices", []),
                             ("stems", {}), ("remaster", {}), ("music", {}), ("quality", {}), ("info", {})):
            data.setdefault(key, default)
        return cls(folder, data)

    def save(self) -> None:
        path = self.folder / "projeto.json"
        tmp = self.folder / "projeto.json.tmp"
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        if path.exists():
            shutil.copy2(path, self.folder / "projeto.json.bak")
        os.replace(tmp, path)

    # ---- atalhos ----------------------------------------------------------
    @property
    def name(self) -> str:
        return self.data.get("name", self.folder.name)

    def path(self, rel: str) -> Path:
        return self.folder / rel

    @property
    def original_path(self) -> Path:
        return self.folder / self.data["original"]

    @property
    def playback_original(self) -> Path:
        return self.folder / "trabalho" / "original.wav"

    def is_done(self, step: str) -> bool:
        return bool(self.data["done"].get(step))

    def mark(self, step: str, value=True) -> None:
        self.data["done"][step] = value
        self.save()

    def notice(self, text: str) -> None:
        if text not in self.data["notices"]:
            self.data["notices"].append(text)

    def size_mb(self) -> float:
        return sum(f.stat().st_size for f in self.folder.rglob("*") if f.is_file()) / 2 ** 20

    def delete(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)


def recent_projects(limit: int = 12) -> list[dict]:
    items = []
    for folder in projects_dir().iterdir():
        meta = folder / "projeto.json"
        if not meta.exists():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            items.append({"folder": str(folder), "name": data.get("name", folder.name),
                          "created": data.get("created", ""), "mtime": meta.stat().st_mtime,
                          "complete": bool(data.get("done", {}).get("pipeline"))})
        except Exception:
            continue
    items.sort(key=lambda it: -it["mtime"])
    return items[:limit]


def free_disk_gb() -> float:
    try:
        return shutil.disk_usage(projects_dir()).free / 2 ** 30
    except Exception:
        return 0.0
