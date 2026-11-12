"""Telas de entrada: início, gravação, preparação e progresso."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout, QListWidget,
                               QListWidgetItem, QProgressBar, QScrollArea, QVBoxLayout, QWidget)

from ..core import hardware, pipeline
from ..core.audio_io import SUPPORTED
from ..core.project import recent_projects
from ..core.remaster import MODES
from . import theme
from .player import Recorder
from .widgets import WaveformView, button, card, fmt_time, label

GOALS = {
    "tudo": ("Fazer tudo", True, True, True, 0),
    "separar": ("Quero separar os instrumentos.", True, False, False, 2),
    "melhorar": ("Quero melhorar o som.", False, True, False, 1),
    "ouvir": ("Quero ouvir somente o saxofone.", True, False, False, 2),
    "partitura": ("Quero a partitura do trombone.", True, False, True, 3),
    "midi": ("Quero transformar a música em MIDI.", True, False, True, 4),
    "harmonia": ("Quero descobrir os acordes e a tonalidade.", False, False, False, 5),
}
FILE_FILTER = "Áudio (" + " ".join(f"*{ext}" for ext in SUPPORTED) + ");;Todos os arquivos (*)"


def _page() -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    w.setObjectName("page")
    theme.styled(w)
    lay = QVBoxLayout(w)
    lay.setContentsMargins(36, 24, 36, 24)
    lay.setSpacing(16)
    return w, lay


class HomePage(QWidget):
    """Tela inicial: quatro botões grandes, atalhos de ajuda e projetos recentes."""

    want_record = Signal()
    want_file = Signal(str)            # objetivo
    open_project = Signal(str)         # pasta

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        theme.styled(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body, lay = _page()
        scroll.setWidget(body)

        logo = label("JFD", "logo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tag = label("Seu estúdio musical inteligente", "tagline")
        tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(logo)
        lay.addWidget(tag)

        tiles = QGridLayout()
        tiles.setSpacing(16)
        items = [("🎙️\nGRAVAR MÚSICA", lambda: self.want_record.emit()),
                 ("📁\nENVIAR ÁUDIO", lambda: self.want_file.emit("tudo")),
                 ("🎼\nCRIAR PARTITURA", lambda: self.want_file.emit("partitura")),
                 ("🎧\nREMASTERIZAR ÁUDIO", lambda: self.want_file.emit("melhorar"))]
        for i, (text, fn) in enumerate(items):
            bt = button(text, "tile", on_click=fn)
            bt.setMinimumHeight(130)
            tiles.addWidget(bt, 0, i)
        lay.addLayout(tiles)
        hint = label("Você também pode arrastar um arquivo de áudio para esta janela.", "muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(hint)

        box, bl = card()
        bl.addWidget(label("COMO POSSO AJUDAR COM SUA MÚSICA HOJE?", "h2"))
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, key in enumerate(("separar", "melhorar", "ouvir", "partitura", "midi", "harmonia")):
            grid.addWidget(button(GOALS[key][0], "help", on_click=lambda _=False, k=key: self.want_file.emit(k)),
                           i // 3, i % 3)
        bl.addLayout(grid)
        lay.addWidget(box)

        lay.addWidget(label("Projetos recentes", "h2"))
        self.recent = QListWidget()
        self.recent.setMinimumHeight(170)
        self.recent.itemClicked.connect(self._open)
        lay.addWidget(self.recent)
        self.empty = label("Seus projetos aparecerão aqui depois da primeira música.", "muted")
        lay.addWidget(self.empty)
        lay.addStretch(1)

    def _open(self, item: QListWidgetItem) -> None:
        self.open_project.emit(item.data(Qt.ItemDataRole.UserRole))

    def refresh(self) -> None:
        self.recent.clear()
        try:
            items = recent_projects()
        except Exception:  # noqa: BLE001
            items = []
        for it in items:
            state = "pronto" if it["complete"] else "incompleto — toque para continuar"
            row = QListWidgetItem(f"🎵  {it['name']}\n      {it['created']}  ·  {state}")
            row.setData(Qt.ItemDataRole.UserRole, it["folder"])
            self.recent.addItem(row)
        self.recent.setVisible(bool(items))
        self.empty.setVisible(not items)


class RecorderDialog(QDialog):
    """Gravador integrado: escolher a entrada, gravar, pausar, continuar e finalizar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gravar música")
        self.setMinimumWidth(460)
        self.result_path: Path | None = None
        self.rec = Recorder(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(14)
        lay.addWidget(label("🎙️  Gravar música", "h1"))
        lay.addWidget(label("Escolha por onde gravar:", "muted"))
        self.device = QComboBox()
        self._devices = Recorder.devices()
        for idx, name, ch in self._devices:
            self.device.addItem(f"{name} ({'estéreo' if ch >= 2 else 'mono'})", idx)
        lay.addWidget(self.device)
        self.time = label("0:00", "value")
        self.time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.time)
        self.meter = QProgressBar()
        self.meter.setRange(0, 100)
        self.meter.setTextVisible(False)
        lay.addWidget(self.meter)
        self.status = label("Pronto para gravar.", "muted", wrap=True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.rec_btn = button("●  Gravar", "primary", on_click=self._record)
        self.pause_btn = button("Pausar", on_click=self._pause)
        self.done_btn = button("Finalizar", on_click=self._finish)
        self.pause_btn.setEnabled(False)
        self.done_btn.setEnabled(False)
        for b in (self.rec_btn, self.pause_btn, self.done_btn):
            row.addWidget(b)
        lay.addLayout(row)
        lay.addWidget(button("Cancelar", "link", on_click=self.reject))
        self.rec.level.connect(lambda v: self.meter.setValue(int(min(1.0, v) * 100)))
        self._clock = QTimer(self)
        self._clock.setInterval(200)
        self._clock.timeout.connect(lambda: self.time.setText(fmt_time(self.rec.seconds())))
        if not self._devices:
            self.status.setText("Nenhum microfone ou entrada de áudio foi encontrado neste computador.")
            self.rec_btn.setEnabled(False)

    def _record(self) -> None:
        pos = self.device.currentIndex()
        idx, _, ch = self._devices[pos] if 0 <= pos < len(self._devices) else (None, "", 1)
        err = self.rec.start(idx, ch)
        if err:
            self.status.setText(err)
            return
        self.status.setText("Gravando…")
        self.rec_btn.setEnabled(False)
        self.device.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.done_btn.setEnabled(True)
        self._clock.start()

    def _pause(self) -> None:
        self.rec.paused = not self.rec.paused
        self.pause_btn.setText("Continuar" if self.rec.paused else "Pausar")
        self.status.setText("Em pausa." if self.rec.paused else "Gravando…")

    def _finish(self) -> None:
        self._clock.stop()
        self.result_path = self.rec.stop()
        if self.result_path is None:
            self.status.setText("A gravação ficou curta demais. Tente novamente.")
            self.rec_btn.setEnabled(True)
            self.device.setEnabled(True)
            self.pause_btn.setEnabled(False)
            self.done_btn.setEnabled(False)
            return
        self.accept()

    def reject(self) -> None:
        self._clock.stop()
        if self.rec.recording:
            self.rec.stop()
        super().reject()


class PreparePage(QWidget):
    """Depois de escolher o áudio: forma de onda, opções simples e o botão principal."""

    start = Signal(object)     # pipeline.Options
    back = Signal()

    def __init__(self, hw: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        theme.styled(self)
        self.hw = hw
        self.path: Path | None = None
        self.goal = "tudo"
        self.duration = 0.0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 24, 36, 24)
        lay.setSpacing(14)
        self.title = label("", "h1")
        self.info = label("", "muted", wrap=True)
        lay.addWidget(self.title)
        lay.addWidget(self.info)
        self.wave = WaveformView()
        lay.addWidget(self.wave)

        box, bl = card()
        bl.addWidget(label("O que você quer que o JFD faça?", "h2"))
        self.cb_sep = QCheckBox("Separar os instrumentos")
        self.cb_rem = QCheckBox("Melhorar a qualidade do áudio")
        self.cb_sco = QCheckBox("Reconhecer as notas e criar partituras e MIDI")
        for cb in (self.cb_sep, self.cb_rem, self.cb_sco):
            bl.addWidget(cb)
            cb.toggled.connect(self._update_estimate)
        row = QHBoxLayout()
        row.addWidget(label("Estilo da melhoria:", "muted"))
        self.mode = QComboBox()
        for key, cfg in MODES.items():
            self.mode.addItem(cfg["label"], key)
        self.mode.setCurrentIndex(1)
        row.addWidget(self.mode)
        row.addSpacing(24)
        row.addWidget(label("Perfil de processamento:", "muted"))
        self.profile = QComboBox()
        for key, cfg in hardware.PROFILES.items():
            self.profile.addItem(cfg["label"], key)
            self.profile.setItemData(self.profile.count() - 1, cfg["hint"], Qt.ItemDataRole.ToolTipRole)
        self.profile.setCurrentIndex(max(0, self.profile.findData(hw.get("suggested_profile", "equilibrado"))))
        self.profile.currentIndexChanged.connect(self._update_estimate)
        row.addWidget(self.profile)
        row.addStretch(1)
        bl.addLayout(row)
        self.estimate = label("", "muted", wrap=True)
        bl.addWidget(self.estimate)
        lay.addWidget(box)

        self.go = button("✨  ANALISAR E FAZER TUDO AUTOMATICAMENTE", "primary", on_click=self._go)
        lay.addWidget(self.go)
        lay.addWidget(button("← Escolher outro áudio", "link", on_click=lambda: self.back.emit()), 0, Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)

    def set_file(self, path: Path, goal: str) -> None:
        self.path, self.goal = Path(path), goal if goal in GOALS else "tudo"
        _, sep, rem, sco, _ = GOALS[self.goal]
        self.cb_sep.setChecked(sep)
        self.cb_rem.setChecked(rem)
        self.cb_sco.setChecked(sco)
        self.title.setText(self.path.stem)
        self.info.setText("Lendo o arquivo…")
        self.wave.set_peaks(None)
        self.duration = 0.0
        self.go.setEnabled(False)
        self._update_estimate()

    def set_loaded(self, info: dict, peaks) -> None:
        self.duration = float(info.get("duration", 0.0))
        ch = {1: "mono", 2: "estéreo"}.get(info.get("channels", 2), f"{info.get('channels')} canais")
        self.info.setText(f"{fmt_time(self.duration)}  ·  {info.get('codec', '').upper()}  ·  "
                          f"{info.get('sample_rate', 0)} Hz  ·  {ch}")
        self.wave.set_peaks(peaks)
        self.go.setEnabled(True)
        self._update_estimate()

    def set_error(self, message: str) -> None:
        self.info.setText("⚠️  " + message)
        self.go.setEnabled(False)

    def _update_estimate(self) -> None:
        self.go.setText("✨  ANALISAR E FAZER TUDO AUTOMATICAMENTE" if
                        (self.cb_sep.isChecked() and self.cb_rem.isChecked() and self.cb_sco.isChecked())
                        else "✨  ANALISAR MINHA MÚSICA")
        if not self.duration:
            self.estimate.setText("")
            return
        parts = []
        if self.cb_sep.isChecked():
            if not self.hw.get("torch"):
                parts.append("A separação de instrumentos não está disponível nesta instalação.")
            else:
                mins = hardware.estimate_minutes(self.duration, self.profile.currentData(), self.hw.get("cuda", False))
                parts.append(f"A separação deve levar cerca de {max(1, round(mins))} min neste computador"
                             + ("" if self.hw.get("cuda") else " (sem placa de vídeo dedicada, usa o processador)") + ".")
                if self.hw.get("ram_free_gb") and self.hw["ram_free_gb"] < 3.0:
                    parts.append("Há pouca memória livre: feche outros programas antes de começar.")
        if self.duration > 12 * 60:
            parts.append("Música longa: o processamento vai demorar mais.")
        self.estimate.setText("  ".join(parts))

    def _go(self) -> None:
        self.start.emit(pipeline.Options(separate=self.cb_sep.isChecked(), remaster=self.cb_rem.isChecked(),
                                         scores=self.cb_sco.isChecked(), profile=self.profile.currentData(),
                                         remaster_mode=self.mode.currentData()))


class ProgressPage(QWidget):
    """Barra de progresso com as etapas e mensagens simples."""

    cancel = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        theme.styled(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(60, 40, 60, 40)
        lay.setSpacing(16)
        self.title = label("Trabalhando na sua música", "h1")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.title)
        self.message = label("", "tagline")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.message)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        lay.addWidget(self.bar)
        box, bl = card()
        self.rows: dict[str, object] = {}
        for sid, text, _ in pipeline.STEPS:
            lb = label("○  " + text.rstrip("."), "muted")
            self.rows[sid] = lb
            bl.addWidget(lb)
        lay.addWidget(box)
        self.note = label("Você pode continuar usando o computador. O JFD deixa parte do processador livre.",
                          "muted", wrap=True)
        self.note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.note)
        self.cancel_btn = button("Cancelar", on_click=self._cancel)
        lay.addWidget(self.cancel_btn, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addStretch(1)
        self._dots = 0
        self._base = ""
        self._anim = QTimer(self)
        self._anim.setInterval(450)
        self._anim.timeout.connect(self._pulse)

    def begin(self, name: str) -> None:
        self.title.setText(f"Trabalhando em “{name}”")
        self.bar.setValue(0)
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText("Cancelar")
        for sid, text, _ in pipeline.STEPS:
            self.rows[sid].setText("○  " + text.rstrip("."))
            self.rows[sid].setObjectName("muted")
            self.rows[sid].setStyleSheet("")
        self._base = "Preparando"
        self._anim.start()

    def update_step(self, step: str, frac: float, message: str) -> None:
        self.bar.setValue(pipeline.overall_percent(step, frac))
        seen = False
        for sid, text, _ in pipeline.STEPS:
            plain = text.rstrip(".")
            if sid == step:
                seen = True
                self.rows[sid].setText("●  " + plain)
                self.rows[sid].setStyleSheet("color: #FFFFFF; font-weight: 700;")
                self._base = message or plain
            elif not seen:
                self.rows[sid].setText("✓  " + plain)
                self.rows[sid].setStyleSheet("color: #34D399;")
        self._pulse()

    def _pulse(self) -> None:
        self._dots = (self._dots + 1) % 4
        self.message.setText(self._base.rstrip(".") + "." * self._dots)

    def _cancel(self) -> None:
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("Cancelando…")
        self.cancel.emit()

    def end(self) -> None:
        self._anim.stop()
