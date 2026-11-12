"""Reprodução sincronizada de várias faixas e gravação pelo microfone (sounddevice)."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from ..config import log, recordings_dir
from ..core.audio_io import SR, WavReader, save_wav
from ..core.mixer import Track, mix_block


def _sd():
    try:
        import sounddevice
        return sounddevice
    except Exception:  # noqa: BLE001
        log.exception("sounddevice indisponível")
        return None


class Player(QObject):
    """Toca um conjunto de faixas (original, remasterizado, instrumentos) na mesma linha de tempo."""

    position_changed = Signal(float, float)     # posição e duração, em segundos
    state_changed = Signal(bool)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tracks: list[Track] = []
        self.length = 0
        self.pos = 0
        self.playing = False
        self._stream = None
        self._ended = False
        self._lock = threading.Lock()
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)

    # ---- faixas -----------------------------------------------------------
    def load(self, tracks: list[Track], keep_position: bool = False) -> None:
        was = self.pos if keep_position else 0
        self.pause()
        with self._lock:
            self.tracks = list(tracks)
            self.length = max((t.frames for t in tracks), default=0)
            self.pos = min(was, self.length)
        self._notify()

    def track(self, tid: str) -> Track | None:
        return next((t for t in self.tracks if t.id == tid), None)

    def listen_only(self, tid: str | None) -> None:
        """Ouvir somente uma faixa (ou todas, com ``None``)."""
        for t in self.tracks:
            t.solo = (t.id == tid) if tid is not None else False

    # ---- transporte -------------------------------------------------------
    def _callback(self, outdata, frames, time_info, status) -> None:
        with self._lock:
            block = mix_block(self.tracks, self.pos, frames)
            self.pos += frames
            if self.pos >= self.length:
                self._ended = True
        outdata[:] = block

    def play(self) -> None:
        if self.playing or not self.tracks:
            return
        sd = _sd()
        if sd is None:
            self.error.emit("Não foi possível acessar a saída de som deste computador.")
            return
        if self.pos >= self.length:
            self.pos = 0
        try:
            self._ended = False
            self._stream = sd.OutputStream(samplerate=SR, channels=2, dtype="float32", blocksize=2048,
                                           callback=self._callback)
            self._stream.start()
        except Exception as exc:  # noqa: BLE001
            log.exception("Falha ao abrir saída de áudio")
            self._stream = None
            self.error.emit(f"Não foi possível tocar o áudio: {exc}")
            return
        self.playing = True
        self._timer.start()
        self.state_changed.emit(True)

    def pause(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                log.exception("Falha ao fechar saída de áudio")
            self._stream = None
        if self.playing:
            self.playing = False
            self._timer.stop()
            self.state_changed.emit(False)

    def toggle(self) -> None:
        self.pause() if self.playing else self.play()

    def stop(self) -> None:
        self.pause()
        self.pos = 0
        self._notify()

    def seek(self, fraction: float) -> None:
        with self._lock:
            self.pos = int(max(0.0, min(1.0, fraction)) * self.length)
        self._notify()

    def _tick(self) -> None:
        self._notify()
        if self._ended:
            self.pause()
            self.pos = 0
            self._notify()

    def _notify(self) -> None:
        self.position_changed.emit(self.pos / SR, self.length / SR)

    def shutdown(self) -> None:
        self.pause()
        for t in self.tracks:
            t.reader.close()
        self.tracks = []


def open_track(tid: str, name: str, path: str | Path) -> Track | None:
    try:
        return Track(tid, name, WavReader(path))
    except Exception:  # noqa: BLE001
        log.exception("Não foi possível abrir %s", path)
        return None


class Recorder(QObject):
    """Gravador simples: microfone ou interface de áudio, com pausa."""

    level = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stream = None
        self._blocks: list[np.ndarray] = []
        self.paused = False
        self.recording = False
        self._peak = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._emit_level)

    @staticmethod
    def devices() -> list[tuple[int, str, int]]:
        """Entradas disponíveis: (índice, nome, canais)."""
        sd = _sd()
        if sd is None:
            return []
        out = []
        try:
            for i, d in enumerate(sd.query_devices()):
                if d.get("max_input_channels", 0) > 0:
                    out.append((i, d.get("name", f"Entrada {i}"), int(d["max_input_channels"])))
        except Exception:  # noqa: BLE001
            log.exception("Falha ao listar entradas de áudio")
        return out

    def _callback(self, indata, frames, time_info, status) -> None:
        self._peak = max(self._peak, float(np.abs(indata).max()) if len(indata) else 0.0)
        if not self.paused:
            self._blocks.append(indata.copy())

    def start(self, device: int | None, channels: int = 1) -> str:
        """Começa a gravar. Retorna mensagem de erro ou ''."""
        sd = _sd()
        if sd is None:
            return "Não foi possível acessar as entradas de som deste computador."
        self._blocks, self.paused, self._peak = [], False, 0.0
        try:
            self._stream = sd.InputStream(samplerate=SR, channels=max(1, min(2, channels)), dtype="float32",
                                          device=device, callback=self._callback)
            self._stream.start()
        except Exception as exc:  # noqa: BLE001
            log.exception("Falha ao abrir entrada de áudio")
            self._stream = None
            return f"Não foi possível gravar por esta entrada: {exc}"
        self.recording = True
        self._timer.start()
        return ""

    def seconds(self) -> float:
        return sum(len(b) for b in self._blocks) / SR

    def _emit_level(self) -> None:
        self.level.emit(self._peak)
        self._peak *= 0.6

    def stop(self) -> Path | None:
        """Encerra e salva automaticamente. Retorna o arquivo WAV (ou None se nada foi gravado)."""
        self._timer.stop()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                log.exception("Falha ao fechar entrada de áudio")
            self._stream = None
        self.recording = False
        if not self._blocks:
            return None
        audio = np.concatenate(self._blocks)
        self._blocks = []
        if len(audio) < SR // 2:
            return None
        if audio.shape[1] == 1:
            audio = np.repeat(audio, 2, axis=1)
        path = recordings_dir() / f"Gravação {time.strftime('%Y-%m-%d %H-%M-%S')}.wav"
        save_wav(path, audio, SR, bits=24)
        return path
