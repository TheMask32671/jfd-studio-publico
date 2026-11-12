"""Componentes visuais reutilizados: forma de onda, barra de transporte e pequenos auxiliares."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout,
                               QWidget)

from . import theme


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


def button(text: str, name: str = "", on_click=None, tip: str = "", checkable: bool = False) -> QPushButton:
    bt = QPushButton(text)
    if name:
        bt.setObjectName(name)
    if tip:
        bt.setToolTip(tip)
    bt.setCheckable(checkable)
    bt.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click is not None:
        bt.clicked.connect(on_click)
    return bt


def card(layout_cls=QVBoxLayout, margins: int = 18, spacing: int = 10) -> tuple[QFrame, QVBoxLayout | QHBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    lay = layout_cls(frame)
    lay.setContentsMargins(margins, margins, margins, margins)
    lay.setSpacing(spacing)
    return frame, lay


def open_path(path: str | Path) -> None:
    """Abre um arquivo ou pasta com o programa padrão do sistema."""
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def print_file(path: str | Path) -> bool:
    """Manda um PDF para a impressora padrão (Windows); em outros sistemas apenas abre o arquivo."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path), "print")  # type: ignore[attr-defined]
            return True
        subprocess.Popen(["xdg-open", str(path)])
        return True
    except Exception:  # noqa: BLE001
        open_path(path)
        return False


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class WaveformView(QWidget):
    """Desenha a forma de onda; clicar muda a posição de reprodução."""

    seek = Signal(float)

    def __init__(self, parent=None, color: str = theme.ELECTRIC):
        super().__init__(parent)
        self._peaks = np.zeros((0, 2), dtype=np.float32)
        self._pos = 0.0
        self._color = QColor(color)
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_peaks(self, peaks: np.ndarray | None) -> None:
        self._peaks = peaks if peaks is not None else np.zeros((0, 2), dtype=np.float32)
        self.update()

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def set_position(self, fraction: float) -> None:
        self._pos = max(0.0, min(1.0, fraction))
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - API do Qt
        if self.width() > 0:
            self.seek.emit(event.position().x() / self.width())

    def paintEvent(self, event) -> None:  # noqa: N802 - API do Qt
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(theme.PANEL))
        mid = h / 2.0
        n = len(self._peaks)
        if n:
            scale = max(1e-6, float(np.abs(self._peaks).max()))
            played = QColor(theme.PURPLE)
            for x in range(w):
                lo, hi = self._peaks[min(n - 1, int(x * n / w))]
                p.setPen(QPen(played if x / max(1, w) < self._pos else self._color))
                y1 = int(mid - hi / scale * (mid - 4))
                y2 = int(mid - lo / scale * (mid - 4))
                p.drawLine(x, y1, x, max(y2, y1 + 1))
        else:
            p.setPen(QPen(QColor(theme.MUTED)))
            p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter.value), "Carregando a forma de onda…")
        p.setPen(QPen(QColor(theme.TEXT), 2))
        x = int(self._pos * w)
        p.drawLine(x, 0, x, h)
        p.end()


class TransportBar(QFrame):
    """Tocar/pausar, parar, tempo e posição — ligado a um Player."""

    def __init__(self, player, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        theme.styled(self)
        self.player = player
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self.play_btn = button("▶  Tocar", on_click=player.toggle)
        self.play_btn.setMinimumWidth(110)
        self.stop_btn = button("■", on_click=player.stop, tip="Parar e voltar ao início")
        self.time_lbl = label("0:00 / 0:00", "muted")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderReleased.connect(lambda: player.seek(self.slider.value() / 1000.0))
        self.what = label("", "muted")
        for wdg in (self.play_btn, self.stop_btn, self.time_lbl):
            lay.addWidget(wdg)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.what)
        player.position_changed.connect(self._on_pos)
        player.state_changed.connect(lambda playing: self.play_btn.setText("❚❚  Pausar" if playing else "▶  Tocar"))

    def _on_pos(self, pos: float, total: float) -> None:
        self.time_lbl.setText(f"{fmt_time(pos)} / {fmt_time(total)}")
        if not self.slider.isSliderDown():
            self.slider.setValue(int(1000 * pos / total) if total > 0 else 0)

    def set_caption(self, text: str) -> None:
        self.what.setText(text)
