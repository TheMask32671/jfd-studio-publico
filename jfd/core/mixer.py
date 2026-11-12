"""Mistura das faixas para reprodução (solo, mudo, volume e panorâmica)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .audio_io import WavReader


@dataclass
class Track:
    id: str
    name: str
    reader: WavReader
    volume: float = 1.0      # 0 … 1.5
    pan: float = 0.0         # −1 (esquerda) … +1 (direita)
    mute: bool = False
    solo: bool = False

    @property
    def frames(self) -> int:
        return self.reader.frames


def mix_block(tracks: list[Track], start: int, frames: int) -> np.ndarray:
    """Soma as faixas audíveis no trecho pedido. Todas ficam sincronizadas pela mesma posição."""
    out = np.zeros((frames, 2), dtype=np.float32)
    any_solo = any(t.solo for t in tracks)
    for t in tracks:
        if t.mute or (any_solo and not t.solo) or t.volume <= 0.0:
            continue
        x = t.reader.read(start, frames)
        out[:, 0] += x[:, 0] * (t.volume * min(1.0, 1.0 - t.pan))
        out[:, 1] += x[:, 1] * (t.volume * min(1.0, 1.0 + t.pan))
    np.clip(out, -1.0, 1.0, out=out)
    return out
