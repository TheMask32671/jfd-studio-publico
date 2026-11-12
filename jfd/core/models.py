"""Gerenciador dos modelos de IA de separação (arquivos do Demucs).

Ordem de busca: modelo já instalado no computador -> modelo embutido no instalador ->
download. Todo arquivo é conferido pelo código de verificação (SHA-256) antes de ser usado.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import ssl
import sys
import urllib.request
from pathlib import Path

from . import JFDError
from ..config import log

# O nome oficial de cada arquivo termina com os 8 primeiros dígitos do seu SHA-256.
# "sha256" guarda o código completo, conferido na montagem do instalador.
MODELS = {
    "htdemucs": {"file": "955717e8-8726e21a.th",
                 "sha256": "8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4"},
    "htdemucs_6s": {"file": "5c90dfd2-34c22ccb.th",
                    "sha256": "34c22ccb381c6f9fdbf324f04e1e2fe21aaaf293f5ded163a162697ff9a02ddd"},
}
OFFICIAL = "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/"
MIRRORS = [
    "https://huggingface.co/iBoostAI/Demucs-v4/resolve/main/",
    "https://huggingface.co/lainlives/audio-separator-models/resolve/main/",
    "https://huggingface.co/NeoPy/VoiceConversion-Models/resolve/main/uvr5_models/Demucs/",
    "https://huggingface.co/AnhP/Vietnamese-RVC-Project/resolve/main/uvr5_models/Demucs/",
]


def cache_dir() -> Path:
    """Pasta onde o PyTorch procura os modelos já baixados."""
    home = os.environ.get("TORCH_HOME") or str(Path.home() / ".cache" / "torch")
    return Path(home) / "hub" / "checkpoints"


def bundled_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / "modelos"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def is_valid(path: Path, name: str) -> bool:
    meta = MODELS[name]
    if not path.is_file() or path.stat().st_size < 1_000_000:
        return False
    digest = sha256_of(path)
    prefix = meta["file"].rsplit("-", 1)[1].split(".")[0]
    return digest.startswith(prefix) and (not meta["sha256"] or digest == meta["sha256"])


def _context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def download(url: str, dest: Path, progress=None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "JFD-Intelligent-Audio-Studio"})
    with urllib.request.urlopen(req, timeout=60, context=_context()) as resp, open(part, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        while True:
            block = resp.read(1 << 18)
            if not block:
                break
            f.write(block)
            got += len(block)
            if progress and total:
                progress(got / total)
    os.replace(part, dest)


def ensure(name: str, progress=None) -> Path:
    """Garante que o modelo está pronto para o PyTorch usar. Retorna o caminho do arquivo."""
    if name not in MODELS:
        raise JFDError(f"Modelo desconhecido: {name}")
    meta = MODELS[name]
    target = cache_dir() / meta["file"]
    if is_valid(target, name):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    packed = bundled_dir() / meta["file"]
    if is_valid(packed, name):
        try:
            if target.exists():
                target.unlink()
            try:
                os.link(packed, target)          # mesmo disco: não ocupa espaço extra
            except OSError:
                shutil.copy2(packed, target)
            return target
        except OSError:
            log.exception("Não foi possível preparar o modelo embutido %s", name)
    sources = [OFFICIAL] + (MIRRORS if meta["sha256"] else [])
    for base in sources:
        try:
            if progress:
                progress(0.0, "Baixando o modelo de inteligência artificial (só na primeira vez)")
            download(base + meta["file"], target, (lambda f: progress(f * 0.04, None)) if progress else None)
            if is_valid(target, name):
                return target
            target.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 - tenta a próxima fonte
            log.exception("Falha ao baixar %s de %s", name, base)
    raise JFDError("Não foi possível obter o modelo de separação de instrumentos. Verifique a conexão com a "
                   "internet e tente de novo; se continuar, reinstale o JFD.")


def fetch_all(dest: Path) -> int:
    """Usado na montagem do instalador: baixa e confere todos os modelos para ``dest``."""
    dest.mkdir(parents=True, exist_ok=True)
    failures = 0
    for name, meta in MODELS.items():
        out = dest / meta["file"]
        if is_valid(out, name):
            print(f"[modelos] {name}: já presente, sha256={sha256_of(out)}")
            continue
        ok = False
        try:
            download(OFFICIAL + meta["file"], out)
            ok = is_valid(out, name)
            print(f"[modelos] {name}: fonte oficial {'ok' if ok else 'com arquivo inválido'}")
        except Exception as exc:  # noqa: BLE001
            print(f"[modelos] {name}: fonte oficial indisponível ({exc})")
        if not ok:
            # Sem a fonte oficial: aceita apenas um arquivo idêntico em fontes independentes
            # (ou igual ao código completo já registrado) e com o prefixo oficial correto.
            seen: dict[str, list[str]] = {}
            for i, base in enumerate(MIRRORS):
                tmp = dest / f"{meta['file']}.m{i}"
                try:
                    download(base + meta["file"], tmp)
                    if is_valid(tmp, name):
                        seen.setdefault(sha256_of(tmp), []).append(str(tmp))
                        print(f"[modelos] {name}: espelho {i} ok ({base})")
                    else:
                        print(f"[modelos] {name}: espelho {i} com arquivo inválido ({base})")
                except Exception as exc:  # noqa: BLE001
                    print(f"[modelos] {name}: espelho {i} indisponível ({exc})")
                good = [d for d, files in seen.items() if len(files) >= 2 or d == meta["sha256"]]
                if good:
                    os.replace(seen[good[0]][0], out)
                    ok = True
                    break
            for leftover in dest.glob(f"{meta['file']}.m*"):
                leftover.unlink(missing_ok=True)
        if ok:
            print(f"[modelos] {name}: pronto, {out.stat().st_size} bytes, sha256={sha256_of(out)}")
        else:
            failures += 1
            print(f"[modelos] {name}: NÃO FOI POSSÍVEL OBTER")
    return failures


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--fetch":
        sys.exit(1 if fetch_all(Path(sys.argv[2])) else 0)
    print("uso: python -m jfd.core.models --fetch PASTA")
    sys.exit(2)
