"""Pastas do aplicativo, configurações e registro de erros."""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA") if sys.platform == "win32" else None
    root = Path(base) / "JFD" if base else Path.home() / ".jfd"
    root.mkdir(parents=True, exist_ok=True)
    return root


def models_dir() -> Path:
    d = app_data_dir() / "modelos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def recordings_dir() -> Path:
    d = projects_dir() / "_gravacoes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_path() -> Path:
    return app_data_dir() / "jfd.log"


def _settings_path() -> Path:
    return app_data_dir() / "config.json"


DEFAULTS = {
    "projects_dir": "",
    "profile": "",           # vazio = escolher pelo hardware
    "remaster_mode": "profissional",
}


def load_settings() -> dict:
    data = dict(DEFAULTS)
    try:
        data.update(json.loads(_settings_path().read_text(encoding="utf-8")))
    except Exception:
        pass
    return data


def save_settings(data: dict) -> None:
    tmp = _settings_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _settings_path())


def projects_dir() -> Path:
    custom = load_settings().get("projects_dir") or ""
    if custom:
        d = Path(custom)
    else:
        docs = Path.home() / "Documents"
        d = (docs if docs.exists() else Path.home()) / "JFD Projetos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup_environment() -> None:
    """Prepara variáveis de ambiente e redireciona saídas ausentes.

    Em um executável com janela (sem console) ``sys.stdout``/``sys.stderr``
    são ``None``; bibliotecas que escrevem neles (tqdm, torch.hub) falhariam.
    """
    os.environ.setdefault("TORCH_HOME", str(models_dir()))
    # O Python no Windows só enxerga os certificados já presentes no repositório do sistema,
    # que pode estar incompleto ou vencido; isso impedia baixar os modelos de IA.
    if not os.environ.get("SSL_CERT_FILE"):
        try:
            import certifi

            os.environ["SSL_CERT_FILE"] = certifi.where()
        except Exception:
            pass
    if sys.stdout is None or sys.stderr is None:
        stream = open(app_data_dir() / "saida.log", "w", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream
    logging.basicConfig(
        filename=str(log_path()),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        encoding="utf-8",
    )


log = logging.getLogger("jfd")
