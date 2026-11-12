"""Leitura de qualquer formato via FFmpeg e escrita/leitura de WAV PCM."""
from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

from . import JFDError

SR = 44100  # frequência de trabalho interna (a mesma dos modelos de separação)
SUPPORTED = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".aiff", ".aif", ".mp4", ".opus", ".wma")


def ffmpeg_exe() -> str:
    env = os.environ.get("JFD_FFMPEG")
    if env and Path(env).exists():
        return env
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise JFDError("O componente FFmpeg não foi encontrado. Reinstale o JFD.")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          stdin=subprocess.DEVNULL, creationflags=flags)


def probe(path: str | Path) -> dict:
    """Informações básicas do arquivo (formato, duração, canais, amostragem)."""
    path = Path(path)
    if not path.exists():
        raise JFDError(f"Arquivo não encontrado: {path.name}")
    res = _run([ffmpeg_exe(), "-hide_banner", "-i", str(path)])
    text = res.stderr.decode("utf-8", "replace")
    m = re.search(r"Stream #\d+:\d+.*?: Audio: ([^\n]+)", text)
    if not m:
        raise JFDError("Este arquivo não contém áudio que o JFD consiga ler, ou está corrompido.")
    desc = m.group(1)
    info = {"file": path.name, "size_bytes": path.stat().st_size, "codec": desc.split(",")[0].split(" ")[0].strip()}
    sr = re.search(r"(\d+) Hz", desc)
    info["sample_rate"] = int(sr.group(1)) if sr else 0
    if "mono" in desc:
        info["channels"] = 1
    elif "stereo" in desc:
        info["channels"] = 2
    else:
        ch = re.search(r"(\d+) channels", desc) or re.search(r"(\d)\.(\d)", desc)
        info["channels"] = int(ch.group(1)) + (int(ch.group(2)) if ch and ch.lastindex == 2 else 0) if ch else 2
    dur = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", text)
    info["duration"] = (int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3))) if dur else 0.0
    br = re.search(r"bitrate: (\d+) kb/s", text)
    info["bitrate_kbps"] = int(br.group(1)) if br else 0
    return info


def load(path: str | Path, sr: int = SR) -> tuple[np.ndarray, dict]:
    """Decodifica para estéreo float32 em ``sr`` Hz. Retorna (áudio [n, 2], info)."""
    info = probe(path)
    res = _run([ffmpeg_exe(), "-v", "error", "-i", str(path), "-vn", "-ac", "2",
                "-ar", str(sr), "-f", "f32le", "-acodec", "pcm_f32le", "-"])
    if res.returncode != 0 or len(res.stdout) < 8:
        raise JFDError("Não foi possível ler o áudio. O arquivo pode estar corrompido.")
    audio = np.frombuffer(res.stdout, dtype="<f4").reshape(-1, 2).copy()
    if not np.isfinite(audio).all():
        audio = np.nan_to_num(audio, nan=0.0, posinf=1.0, neginf=-1.0)
    info["duration"] = len(audio) / sr
    return audio, info


def save_wav(path: str | Path, audio: np.ndarray, sr: int = SR, bits: int = 24) -> None:
    """Grava WAV PCM de 16 ou 24 bits. ``audio`` é [n] ou [n, canais] em -1..1."""
    if bits not in (16, 24):
        raise ValueError("bits deve ser 16 ou 24")
    a = np.asarray(audio, dtype=np.float32)
    if a.ndim == 1:
        a = a[:, None]
    n, ch = a.shape
    a = np.clip(a, -1.0, 1.0)
    if bits == 16:
        rng = np.random.default_rng(0)
        dither = (rng.random(a.shape, dtype=np.float32) - rng.random(a.shape, dtype=np.float32)) / 32768.0
        data = np.round(np.clip(a + dither, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    else:
        i32 = np.round(a.astype(np.float64) * 8388607.0).astype("<i4")
        data = i32.view(np.uint8).reshape(n, ch, 4)[:, :, :3].tobytes()
    block = ch * bits // 8
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack(
        "<IHHIIHH", 16, 1, ch, sr, sr * block, block, bits) + b"data" + struct.pack("<I", len(data))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(header)
        f.write(data)
    os.replace(tmp, path)


class WavReader:
    """Leitura por blocos de WAV PCM 16/24 bits usando mapeamento em memória.

    Usado pelo motor de reprodução para tocar várias faixas sem carregar tudo na RAM.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        with open(self.path, "rb") as f:
            head = f.read(12)
            if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
                raise JFDError("Arquivo WAV inválido.")
            fmt = None
            while True:
                chunk = f.read(8)
                if len(chunk) < 8:
                    raise JFDError("Arquivo WAV sem dados de áudio.")
                cid, size = chunk[:4], struct.unpack("<I", chunk[4:])[0]
                if cid == b"fmt ":
                    fmt = struct.unpack("<HHIIHH", f.read(16))
                    f.seek(size - 16 + (size & 1), 1)
                elif cid == b"data":
                    self._offset = f.tell()
                    self._bytes = size
                    break
                else:
                    f.seek(size + (size & 1), 1)
        if fmt is None or fmt[0] not in (1, 0xFFFE) or fmt[5] not in (16, 24):
            raise JFDError("Formato WAV não suportado para reprodução.")
        self.channels, self.sr, self.bits = fmt[1], fmt[2], fmt[5]
        self._bps = self.bits // 8
        real = min(self._bytes, os.path.getsize(self.path) - self._offset)
        self.frames = real // (self._bps * self.channels)
        shape = (self.frames, self.channels) if self.bits == 16 else (self.frames, self.channels, 3)
        dtype = "<i2" if self.bits == 16 else np.uint8
        self._mm = np.memmap(self.path, dtype=dtype, mode="r", offset=self._offset, shape=shape) if self.frames else None

    def read(self, start: int, frames: int) -> np.ndarray:
        """Retorna float32 [frames, 2]; completa com silêncio fora do arquivo."""
        out = np.zeros((frames, 2), dtype=np.float32)
        if self._mm is None or start >= self.frames or frames <= 0:
            return out
        start = max(0, start)
        end = min(self.frames, start + frames)
        raw = self._mm[start:end]
        if self.bits == 16:
            x = raw.astype(np.float32) / 32768.0
        else:
            b = raw.astype(np.int32)
            v = b[..., 0] | (b[..., 1] << 8) | (b[..., 2] << 16)
            v = np.where(v >= 0x800000, v - 0x1000000, v)
            x = v.astype(np.float32) / 8388608.0
        if self.channels == 1:
            x = np.repeat(x, 2, axis=1)
        out[: end - start] = x[:, :2]
        return out

    def read_all(self) -> np.ndarray:
        return self.read(0, self.frames)

    def close(self) -> None:
        self._mm = None


def peaks(audio: np.ndarray, buckets: int = 1600) -> np.ndarray:
    """Envelope (mín, máx) por coluna para desenhar a forma de onda. Retorna [buckets, 2]."""
    mono = audio.mean(axis=1) if audio.ndim == 2 else audio
    n = len(mono)
    if n == 0:
        return np.zeros((buckets, 2), dtype=np.float32)
    size = max(1, n // buckets)
    usable = (n // size) * size
    blocks = mono[:usable].reshape(-1, size)
    out = np.stack([blocks.min(axis=1), blocks.max(axis=1)], axis=1).astype(np.float32)
    return out[:buckets] if len(out) >= buckets else np.pad(out, ((0, buckets - len(out)), (0, 0)))
