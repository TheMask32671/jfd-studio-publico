"""Tela de resultados: áudio original, remasterizado, instrumentos, partituras, MIDI, harmonia e exportação."""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
from PySide6.QtCore import QByteArray, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QListWidget, QListWidgetItem, QMessageBox, QScrollArea, QSlider,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..config import log
from ..core import pipeline, remaster as rm, render
from ..core.audio_io import SR, WavReader, save_wav
from ..core.project import Project, slugify
from ..core.remaster import MODES
from ..core.theory import TUNING_LABEL, instruments_by_tuning
from . import theme
from .player import Player, open_track
from .widgets import TransportBar, WaveformView, button, card, fmt_time, label, open_path, print_file

TAB_ORIGINAL, TAB_REMASTER, TAB_STEMS, TAB_SCORES, TAB_MIDI, TAB_HARMONY, TAB_EXPORT = range(7)


def reader_peaks(path: Path, buckets: int = 1200) -> np.ndarray | None:
    """Envelope rápido de um WAV para desenhar a forma de onda (lê só um trecho de cada coluna)."""
    try:
        r = WavReader(path)
    except Exception:  # noqa: BLE001
        return None
    out = np.zeros((buckets, 2), dtype=np.float32)
    if r.frames:
        size = max(1, r.frames // buckets)
        for i in range(buckets):
            block = r.read(i * size, min(size, 4096)).mean(axis=1)
            out[i] = (block.min(), block.max())
    r.close()
    return out


def _scroll(inner: QWidget) -> QScrollArea:
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setWidget(inner)
    return sc


def _save_copy(parent: QWidget, src: Path, title: str = "Salvar cópia") -> None:
    if not src.exists():
        QMessageBox.warning(parent, "JFD", "O arquivo não foi encontrado no projeto.")
        return
    dest, _ = QFileDialog.getSaveFileName(parent, title, str(Path.home() / src.name), f"*{src.suffix}")
    if dest:
        try:
            shutil.copy2(src, dest)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(parent, "JFD", f"Não foi possível salvar: {exc}")


class ResultsPage(QWidget):
    go_home = Signal()
    request_pipeline = Signal(object)     # pipeline.Options: refazer/completar etapas

    def __init__(self, runner, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        theme.styled(self)
        self.runner = runner
        self.project: Project | None = None
        self.player = Player(self)
        self.player.error.connect(lambda msg: QMessageBox.warning(self, "JFD", msg))
        self._ab = "depois"
        self._stem_rows: dict[str, dict] = {}
        self._score_pages: list[QWidget] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 16, 28, 16)
        lay.setSpacing(10)
        head = QHBoxLayout()
        self.banner = label("", "big")
        self.title = label("", "h1")
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.banner)
        lay.addLayout(head)
        self.notices = label("", "muted", wrap=True)
        lay.addWidget(self.notices)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_original(), "ÁUDIO ORIGINAL")
        self.tabs.addTab(self._build_remaster(), "ÁUDIO REMASTERIZADO")
        self.tabs.addTab(self._build_stems(), "INSTRUMENTOS")
        self.tabs.addTab(self._build_scores(), "PARTITURAS")
        self.tabs.addTab(self._build_midi(), "MIDI")
        self.tabs.addTab(self._build_harmony(), "HARMONIA")
        self.tabs.addTab(self._build_export(), "EXPORTAR")
        self.tabs.currentChanged.connect(self._on_tab)
        lay.addWidget(self.tabs, 1)
        self.transport = TransportBar(self.player)
        lay.addWidget(self.transport)
        self.player.position_changed.connect(self._on_position)

    # ------------------------------------------------------------------ carga
    def load(self, project: Project, tab: int = 0, celebrate: bool = False) -> None:
        self.release()
        self.project = project
        d = project.data
        self.title.setText(project.name)
        self.banner.setText("SUA MÚSICA ESTÁ PRONTA!" if celebrate else "")
        self.notices.setText("  ".join("ℹ️ " + n for n in d.get("notices", [])))
        self.notices.setVisible(bool(d.get("notices")))
        self._fill_original()
        self._fill_remaster()
        self._fill_stems()
        self._fill_scores()
        self._fill_midi()
        self._fill_harmony()
        self._fill_export()
        self.tabs.blockSignals(True)
        self.tabs.setCurrentIndex(tab if 0 <= tab < self.tabs.count() else 0)
        self.tabs.blockSignals(False)
        self._on_tab(self.tabs.currentIndex())

    def release(self) -> None:
        """Para o som e solta os arquivos (necessário antes de regravar ou apagar algo)."""
        self.player.shutdown()

    def _path(self, rel: str) -> Path:
        return self.project.path(rel)

    # ------------------------------------------------------------------ player
    def _on_tab(self, idx: int) -> None:
        if self.project is None or idx not in (TAB_ORIGINAL, TAB_REMASTER, TAB_STEMS):
            return
        d = self.project.data
        tracks = []
        if idx == TAB_STEMS and d.get("stems"):
            for sid, meta in d["stems"].items():
                row = self._stem_rows.get(sid)
                rel = meta.get("improved") if (row and row["improved"].isChecked() and meta.get("improved")) else meta["file"]
                t = open_track(sid, meta["name"], self._path(rel))
                if t and row:
                    t.volume = row["volume"].value() / 100.0
                    t.pan = row["pan"].value() / 100.0
                    t.mute = row["mute"].isChecked()
                    t.solo = row["solo"].isChecked()
                if t:
                    tracks.append(t)
            caption = "Mesa de mixagem: instrumentos separados"
        else:
            t = open_track("original", "Original", self.project.playback_original)
            if t:
                tracks.append(t)
            caption = "Áudio original"
            if idx == TAB_REMASTER and d.get("remaster", {}).get("file"):
                t2 = open_track("remaster", "Remasterizado", self._path(d["remaster"]["file"]))
                if t2:
                    tracks.append(t2)
                    for t in tracks:
                        t.solo = (t.id == ("remaster" if self._ab == "depois" else "original"))
                    caption = "Comparando: " + ("DEPOIS (remasterizado)" if self._ab == "depois" else "ANTES (original)")
        self.player.load(tracks, keep_position=True)
        self.transport.set_caption(caption)

    def _on_position(self, pos: float, total: float) -> None:
        frac = pos / total if total > 0 else 0.0
        self.wave_original.set_position(frac)
        self.wave_remaster.set_position(frac)

    # ------------------------------------------------------------------ original
    def _build_original(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(label("Arquivo original preservado. Nada é alterado nele.", "muted"))
        self.wave_original = WaveformView()
        self.wave_original.seek.connect(self.player.seek)
        lay.addWidget(self.wave_original)
        box, bl = card()
        self.orig_info = label("", wrap=True)
        self.orig_problems = label("", "muted", wrap=True)
        bl.addWidget(label("Sobre o arquivo", "h2"))
        bl.addWidget(self.orig_info)
        bl.addWidget(label("O que o JFD encontrou", "h2"))
        bl.addWidget(self.orig_problems)
        lay.addWidget(box)
        lay.addStretch(1)
        return w

    def _fill_original(self) -> None:
        d = self.project.data
        info, q = d.get("info", {}), d.get("quality", {})
        self.wave_original.set_peaks(reader_peaks(self.project.playback_original))
        ch = {1: "mono", 2: "estéreo"}.get(info.get("channels", 2), f"{info.get('channels')} canais")
        text = (f"{info.get('file', '')}  ·  {fmt_time(info.get('duration', 0))}  ·  {info.get('codec', '').upper()}  ·  "
                f"{info.get('sample_rate', 0)} Hz  ·  {ch}")
        if q:
            text += f"\nVolume percebido: {q['lufs']:.1f} LUFS  ·  Pico: {q['peak_db']:.1f} dBFS"
        self.orig_info.setText(text)
        problems = q.get("problems", [])
        self.orig_problems.setText("\n".join("•  " + p for p in problems) if problems else
                                   "Nenhum problema evidente de qualidade.")

    # ------------------------------------------------------------------ remasterizado
    def _build_remaster(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        self.btn_before = button("ANTES", checkable=True, on_click=lambda: self._set_ab("antes"))
        self.btn_after = button("DEPOIS", checkable=True, on_click=lambda: self._set_ab("depois"))
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        grp.addButton(self.btn_before)
        grp.addButton(self.btn_after)
        self.btn_after.setChecked(True)
        row.addWidget(self.btn_before)
        row.addWidget(self.btn_after)
        row.addWidget(label("Troque entre os dois enquanto a música toca.", "muted"))
        row.addStretch(1)
        lay.addLayout(row)
        self.wave_remaster = WaveformView(color=theme.OK)
        self.wave_remaster.seek.connect(self.player.seek)
        lay.addWidget(self.wave_remaster)
        box, bl = card()
        self.rem_title = label("", "h2")
        self.rem_steps = label("", wrap=True)
        self.rem_numbers = label("", "muted", wrap=True)
        bl.addWidget(self.rem_title)
        bl.addWidget(self.rem_steps)
        bl.addWidget(self.rem_numbers)
        lay.addWidget(box)
        row2 = QHBoxLayout()
        row2.addWidget(label("Estilo:", "muted"))
        self.rem_mode = QComboBox()
        for key, cfg in MODES.items():
            self.rem_mode.addItem(cfg["label"], key)
        row2.addWidget(self.rem_mode)
        self.rem_redo = button("MELHORAR QUALIDADE DO ÁUDIO", on_click=self._redo_remaster)
        row2.addWidget(self.rem_redo)
        self.rem_save = button("Salvar cópia…", on_click=lambda: _save_copy(
            self, self._path(self.project.data["remaster"]["file"])))
        row2.addWidget(self.rem_save)
        row2.addStretch(1)
        lay.addLayout(row2)
        lay.addStretch(1)
        return w

    def _fill_remaster(self) -> None:
        r = self.project.data.get("remaster", {})
        has = bool(r.get("file")) and self._path(r["file"]).exists()
        self.btn_before.setEnabled(has)
        self.btn_after.setEnabled(has)
        self.rem_save.setEnabled(has)
        self.rem_redo.setEnabled(True)
        if has:
            self.wave_remaster.set_peaks(reader_peaks(self._path(r["file"])))
            self.rem_title.setText(f"Remasterizado no estilo {r.get('mode_label', '')}")
            self.rem_steps.setText("\n".join("•  " + s for s in r.get("steps", [])))
            b, a = r.get("before", {}), r.get("after", {})
            if b and a:
                self.rem_numbers.setText(f"Volume percebido: {b['lufs']:.1f} → {a['lufs']:.1f} LUFS   ·   "
                                         f"Pico: {b['peak_db']:.1f} → {a['peak_db']:.1f} dBFS")
            self.rem_mode.setCurrentIndex(max(0, self.rem_mode.findData(r.get("mode", "profissional"))))
            self.rem_redo.setText("Refazer neste estilo")
        else:
            self.wave_remaster.set_peaks(np.zeros((1, 2), dtype=np.float32))
            self.rem_title.setText("Este projeto ainda não tem áudio remasterizado.")
            self.rem_steps.setText("Escolha um estilo e clique no botão abaixo.")
            self.rem_numbers.setText("")
            self.rem_redo.setText("MELHORAR QUALIDADE DO ÁUDIO")

    def _set_ab(self, which: str) -> None:
        self._ab = which
        for t in self.player.tracks:
            t.solo = (t.id == ("remaster" if which == "depois" else "original"))
        self.transport.set_caption("Comparando: " + ("DEPOIS (remasterizado)" if which == "depois" else "ANTES (original)"))

    def _redo_remaster(self) -> None:
        project, mode = self.project, self.rem_mode.currentData()
        self.release()
        self.rem_redo.setEnabled(False)
        self.rem_title.setText("Melhorando o áudio…")

        def work(progress, cancel):
            return pipeline.do_remaster(project, mode, progress=lambda f, m=None: progress("remaster", f, m))

        def done(_result):
            self._ab = "depois"
            self.btn_after.setChecked(True)
            self._fill_remaster()
            self._on_tab(self.tabs.currentIndex())

        def failed(msg):
            self._fill_remaster()
            self._on_tab(self.tabs.currentIndex())
            QMessageBox.warning(self, "JFD", msg)

        self.runner.start(work, on_done=done, on_failed=failed,
                          on_progress=lambda s, f, m: self.rem_title.setText(f"{m or 'Melhorando o áudio'}… {int(f * 100)}%"))

    # ------------------------------------------------------------------ instrumentos
    def _build_stems(self) -> QWidget:
        self.stems_box = QWidget()
        self.stems_lay = QVBoxLayout(self.stems_box)
        self.stems_lay.setSpacing(10)
        return _scroll(self.stems_box)

    def _clear(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear(item.layout())

    def _fill_stems(self) -> None:
        self._clear(self.stems_lay)
        self._stem_rows = {}
        stems = self.project.data.get("stems", {})
        if not stems:
            box, bl = card()
            bl.addWidget(label("Os instrumentos ainda não foram separados neste projeto.", "h2"))
            bl.addWidget(label("A separação usa inteligência artificial e pode demorar alguns minutos.", "muted", wrap=True))
            bl.addWidget(button("SEPARAR OS INSTRUMENTOS AGORA", "primary", on_click=lambda: self.request_pipeline.emit(
                pipeline.Options(separate=True, remaster=False, scores=True))))
            self.stems_lay.addWidget(box)
            self.stems_lay.addStretch(1)
            return
        self.stems_lay.addWidget(label(
            "Cada faixa pode ser ouvida sozinha, combinada com outras, ajustada e baixada. "
            "A etiqueta mostra o quanto a separação costuma ser confiável.", "muted", wrap=True))
        chip = {"alta": ("chipOk", "confiança alta"), "média": ("chipWarn", "confiança média"),
                "baixa": ("chipWarn", "experimental"), "grupo": ("chipInfo", "grupo de instrumentos")}
        for sid, meta in stems.items():
            box, bl = card(margins=14, spacing=8)
            top = QHBoxLayout()
            top.addWidget(label(f"{meta.get('icon', '🎵')}  {meta['name']}", "h2"))
            name, text = chip.get(meta.get("reliability", "média"), ("chipWarn", ""))
            if not meta.get("present", True):
                name, text = "chipWarn", "não encontrado na gravação"
            top.addWidget(label(text, name))
            top.addStretch(1)
            solo = button("🎧 OUVIR SOMENTE ESTE INSTRUMENTO", checkable=True)
            mute = button("Silenciar", checkable=True)
            top.addWidget(solo)
            top.addWidget(mute)
            bl.addLayout(top)
            bl.addWidget(label(meta.get("note", ""), "muted", wrap=True))
            ctl = QHBoxLayout()
            vol = QSlider(Qt.Orientation.Horizontal)
            vol.setRange(0, 150)
            vol.setValue(100)
            vol.setFixedWidth(170)
            pan = QSlider(Qt.Orientation.Horizontal)
            pan.setRange(-100, 100)
            pan.setValue(0)
            pan.setFixedWidth(120)
            improved = QCheckBox("Usar versão melhorada")
            improved.setVisible(bool(meta.get("improved")))
            improved.setChecked(bool(meta.get("improved")))
            improve = button("Melhorar som", on_click=lambda _=False, s=sid: self._improve_stem(s))
            improve.setVisible(not meta.get("improved"))
            ctl.addWidget(label("Volume", "muted"))
            ctl.addWidget(vol)
            ctl.addWidget(label("Esq/Dir", "muted"))
            ctl.addWidget(pan)
            ctl.addStretch(1)
            ctl.addWidget(improved)
            ctl.addWidget(improve)
            ctl.addWidget(button("Partitura", on_click=lambda _=False, s=sid: self._score_for(s)))
            ctl.addWidget(button("Baixar áudio", on_click=lambda _=False, s=sid: self._download_stem(s)))
            bl.addLayout(ctl)
            self.stems_lay.addWidget(box)
            self._stem_rows[sid] = {"solo": solo, "mute": mute, "volume": vol, "pan": pan, "improved": improved,
                                    "improve": improve}
            solo.toggled.connect(lambda on, s=sid: self._apply_track(s, "solo", on))
            mute.toggled.connect(lambda on, s=sid: self._apply_track(s, "mute", on))
            vol.valueChanged.connect(lambda v, s=sid: self._apply_track(s, "volume", v / 100.0))
            pan.valueChanged.connect(lambda v, s=sid: self._apply_track(s, "pan", v / 100.0))
            improved.toggled.connect(lambda _on: self._on_tab(TAB_STEMS) if self.tabs.currentIndex() == TAB_STEMS else None)
        self.stems_lay.addStretch(1)

    def _apply_track(self, sid: str, attr: str, value) -> None:
        t = self.player.track(sid)
        if t is not None:
            setattr(t, attr, value)

    def _download_stem(self, sid: str) -> None:
        meta = self.project.data["stems"][sid]
        row = self._stem_rows[sid]
        rel = meta["improved"] if (meta.get("improved") and row["improved"].isChecked()) else meta["file"]
        _save_copy(self, self._path(rel), "Baixar áudio do instrumento")

    def _score_for(self, sid: str) -> None:
        self.tabs.setCurrentIndex(TAB_SCORES)
        pos = self.score_line.findData(sid)
        if pos >= 0:
            self.score_line.setCurrentIndex(pos)
        else:
            QMessageBox.information(self, "JFD", "Não há notas reconhecidas para esta faixa. A bateria não gera "
                                    "partitura melódica, e faixas vazias ou muito confusas são ignoradas.")

    def _improve_stem(self, sid: str) -> None:
        project = self.project
        meta = project.data["stems"][sid]
        row = self._stem_rows[sid]
        row["improve"].setEnabled(False)
        row["improve"].setText("Melhorando…")
        mode = project.data.get("remaster", {}).get("mode", "natural")

        def work(progress, cancel):
            audio = WavReader(project.path(meta["file"])).read_all()
            out, _ = rm.remaster(audio, SR, mode)
            rel = f"instrumentos/{slugify(project.name)}_{sid}_melhorado.wav"
            save_wav(project.path(rel), out, SR, bits=24)
            meta["improved"] = rel
            project.save()
            return rel

        def done(_rel):
            row["improve"].setVisible(False)
            row["improved"].setVisible(True)
            row["improved"].setChecked(True)
            if self.tabs.currentIndex() == TAB_STEMS:
                self._on_tab(TAB_STEMS)

        def failed(msg):
            row["improve"].setEnabled(True)
            row["improve"].setText("Melhorar som")
            QMessageBox.warning(self, "JFD", msg)

        self.runner.start(work, on_done=done, on_failed=failed)

    # ------------------------------------------------------------------ partituras
    def _build_scores(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        left = QVBoxLayout()
        box, bl = card(margins=14)
        bl.addWidget(label("Nova partitura", "h2"))
        bl.addWidget(label("Melodia reconhecida:", "muted"))
        self.score_line = QComboBox()
        bl.addWidget(self.score_line)
        bl.addWidget(label("Instrumento (afinação):", "muted"))
        self.score_inst = QComboBox()
        for tuning, items in instruments_by_tuning().items():
            for inst in items:
                self.score_inst.addItem(f"{TUNING_LABEL[tuning]}  —  {inst.name}", inst.id)
        bl.addWidget(self.score_inst)
        self.score_concert = QCheckBox("Tom de concerto (em Dó)")
        bl.addWidget(self.score_concert)
        self.score_make = button("GERAR PARTITURA", "primary", on_click=self._make_score)
        bl.addWidget(self.score_make)
        self.score_line.currentIndexChanged.connect(self._on_score_line)
        left.addWidget(box)
        left.addWidget(label("Partituras deste projeto", "h2"))
        self.score_list = QListWidget()
        self.score_list.currentRowChanged.connect(self._show_score)
        left.addWidget(self.score_list, 1)
        row = QHBoxLayout()
        self.score_pdf = button("Abrir PDF", on_click=lambda: self._score_action("pdf"))
        self.score_print = button("Imprimir", on_click=lambda: self._score_action("print"))
        self.score_xml = button("MusicXML", on_click=lambda: self._score_action("xml"),
                                tip="Abre no MuseScore ou outro editor de partituras para corrigir as notas")
        for b in (self.score_pdf, self.score_print, self.score_xml):
            row.addWidget(b)
        left.addLayout(row)
        side = QWidget()
        side.setLayout(left)
        side.setFixedWidth(360)
        lay.addWidget(side)
        right = QVBoxLayout()
        self.score_info = label("", "muted", wrap=True)
        right.addWidget(self.score_info)
        self.score_view = QWidget()
        self.score_view_lay = QVBoxLayout(self.score_view)
        self.score_view_lay.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        right.addWidget(_scroll(self.score_view), 1)
        lay.addLayout(right, 1)
        return w

    def _fill_scores(self) -> None:
        d = self.project.data
        self.score_line.blockSignals(True)
        self.score_line.clear()
        for lid, line in d.get("lines", {}).items():
            self.score_line.addItem(f"{line['name']}  ({len(line['notes'])} notas, confiança "
                                    f"{int(line['confidence'] * 100)}%)", lid)
        self.score_line.blockSignals(False)
        self.score_make.setEnabled(self.score_line.count() > 0)
        self._on_score_line()
        self._refresh_score_list()

    def _on_score_line(self) -> None:
        line = self.project.data.get("lines", {}).get(self.score_line.currentData()) if self.project else None
        if line:
            pos = self.score_inst.findData(line.get("instrument", "melodia"))
            if pos >= 0:
                self.score_inst.setCurrentIndex(pos)

    def _refresh_score_list(self, select: str | None = None) -> None:
        scores = self.project.data.get("scores", [])
        self.score_list.blockSignals(True)
        self.score_list.clear()
        pick = len(scores) - 1 if scores else -1
        for i, s in enumerate(scores):
            text = f"🎼  {s['instrument_name']}" + ("  ·  tom de concerto" if s.get("concert_pitch") else "")
            item = QListWidgetItem(f"{text}\n      {s['line_name']}  ·  {s['info'].get('written_key', '')}")
            self.score_list.addItem(item)
            if select and s["musicxml"] == select:
                pick = i
        if pick >= 0:
            self.score_list.setCurrentRow(pick)
        self.score_list.blockSignals(False)
        self._show_score(pick)

    def _current_score(self) -> dict | None:
        scores = self.project.data.get("scores", []) if self.project else []
        row = self.score_list.currentRow()
        return scores[row] if 0 <= row < len(scores) else None

    def _show_score(self, _row: int = -1) -> None:
        self._clear(self.score_view_lay)
        s = self._current_score()
        for b in (self.score_pdf, self.score_print):
            b.setEnabled(bool(s and s.get("pdf")))
        self.score_xml.setEnabled(bool(s))
        if s is None:
            if not self.project.data.get("lines"):
                self.score_info.setText("Nenhuma melodia foi reconhecida neste projeto. As partituras funcionam melhor "
                                        "com instrumentos separados ou gravações de um instrumento solo.")
            else:
                self.score_info.setText("Escolha a melodia e o instrumento e clique em GERAR PARTITURA.")
            return
        info = s["info"]
        line = self.project.data["lines"].get(s["line"], {})
        parts = [f"{info.get('measures', 0)} compassos  ·  escrita em {info.get('written_key', '')}"
                 f" (tom de concerto: {info.get('concert_key', '')})"]
        if info.get("octave_shift"):
            parts.append(f"Melodia deslocada {abs(info['octave_shift'])} oitava(s) para "
                         f"{'cima' if info['octave_shift'] > 0 else 'baixo'} para caber no instrumento.")
        if info.get("out_of_range"):
            parts.append(f"{info['out_of_range']} nota(s) ficam fora da extensão usual do instrumento.")
        if line.get("note"):
            parts.append(line["note"])
        parts.append("Transcrição automática: confira e corrija as notas antes de usar (o MusicXML abre no MuseScore).")
        self.score_info.setText("  ".join(parts))
        try:
            from PySide6.QtSvgWidgets import QSvgWidget

            pages = render.svg_pages(self._path(s["musicxml"]).read_text(encoding="utf-8"))
            for svg in pages:
                view = QSvgWidget()
                view.load(QByteArray(svg.encode("utf-8")))
                view.setFixedSize(680, int(680 * 1.4143))
                pal = view.palette()
                pal.setColor(QPalette.ColorRole.Window, QColor("white"))
                view.setPalette(pal)
                view.setAutoFillBackground(True)
                self.score_view_lay.addWidget(view)
        except Exception:  # noqa: BLE001
            log.exception("Falha ao desenhar a partitura na tela")
            self.score_view_lay.addWidget(label(
                "A visualização na tela não está disponível. Use “Abrir PDF” ou “MusicXML”.", "muted", wrap=True))

    def _score_action(self, what: str) -> None:
        s = self._current_score()
        if not s:
            return
        if what == "xml":
            open_path(self._path(s["musicxml"]))
        elif s.get("pdf"):
            (print_file if what == "print" else open_path)(self._path(s["pdf"]))

    def _make_score(self) -> None:
        project = self.project
        line, inst, concert = self.score_line.currentData(), self.score_inst.currentData(), self.score_concert.isChecked()
        if not line:
            return
        self.score_make.setEnabled(False)
        self.score_make.setText("Criando partitura…")

        def work(progress, cancel):
            return pipeline.make_score(project, line, inst, concert_pitch=concert)

        def finish():
            self.score_make.setEnabled(True)
            self.score_make.setText("GERAR PARTITURA")

        def done(entry):
            finish()
            self._refresh_score_list(select=entry["musicxml"])

        def failed(msg):
            finish()
            QMessageBox.warning(self, "JFD", msg)

        self.runner.start(work, on_done=done, on_failed=failed)

    # ------------------------------------------------------------------ MIDI
    def _build_midi(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(label("Arquivos MIDI editáveis. Os “quantizados” seguem a partitura; os demais seguem o tempo "
                            "real da gravação e ficam alinhados ao áudio.", "muted", wrap=True))
        self.midi_list = QListWidget()
        lay.addWidget(self.midi_list, 1)
        row = QHBoxLayout()
        row.addWidget(button("Abrir", on_click=lambda: self._midi_action(open_path)))
        row.addWidget(button("Salvar cópia…", on_click=lambda: self._midi_action(lambda p: _save_copy(self, p))))
        row.addWidget(button("Abrir pasta dos MIDI", on_click=lambda: open_path(self._path("midi"))))
        row.addStretch(1)
        lay.addLayout(row)
        return w

    def _fill_midi(self) -> None:
        self.midi_list.clear()
        for rel in self.project.data.get("midi", []):
            item = QListWidgetItem("🎹  " + Path(rel).name)
            item.setData(Qt.ItemDataRole.UserRole, rel)
            self.midi_list.addItem(item)
        if not self.midi_list.count():
            self.midi_list.addItem("Nenhum arquivo MIDI neste projeto.")

    def _midi_action(self, fn) -> None:
        item = self.midi_list.currentItem()
        rel = item.data(Qt.ItemDataRole.UserRole) if item else None
        if rel:
            fn(self._path(rel))
        else:
            QMessageBox.information(self, "JFD", "Selecione um arquivo MIDI na lista.")

    # ------------------------------------------------------------------ harmonia
    def _build_harmony(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        self.h_values = {}
        for key, title in (("key", "Tonalidade"), ("bpm", "Andamento"), ("meter", "Compasso")):
            box, bl = card(margins=16, spacing=4)
            bl.addWidget(label(title, "muted"))
            val = label("—", "value")
            sub = label("", "muted")
            bl.addWidget(val)
            bl.addWidget(sub)
            self.h_values[key] = (val, sub)
            row.addWidget(box)
        lay.addLayout(row)
        self.h_extra = label("", "muted", wrap=True)
        lay.addWidget(self.h_extra)
        self.h_table = QTableWidget(0, 4)
        self.h_table.setHorizontalHeaderLabels(["Compasso", "Tempo", "Acorde", "Confiança"])
        self.h_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.h_table.verticalHeader().setVisible(False)
        self.h_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.h_table.cellClicked.connect(self._chord_clicked)
        lay.addWidget(self.h_table, 1)
        lay.addWidget(button("Exportar a sequência de acordes (CSV)…", on_click=lambda: _save_copy(
            self, self._path("analise/harmonia.csv"))), 0, Qt.AlignmentFlag.AlignLeft)
        return w

    def _fill_harmony(self) -> None:
        m = self.project.data.get("music", {})
        if not m:
            return
        self.h_values["key"][0].setText(m.get("key_name", "—"))
        self.h_values["key"][1].setText(f"confiança {int(m.get('key_confidence', 0) * 100)}%")
        self.h_values["bpm"][0].setText(f"{m.get('bpm', 0):.0f} BPM")
        stable = m.get("tempo_stability", 1.0)
        self.h_values["bpm"][1].setText("andamento estável" if stable > 0.7 else "andamento varia ao longo da música")
        self.h_values["meter"][0].setText(m.get("time_signature", "4/4"))
        self.h_values["meter"][1].setText("provável")
        extra = []
        if m.get("secondary_keys"):
            extra.append("Tonalidades secundárias: " + ", ".join(m["secondary_keys"]) + ".")
        extra.append("Clique em um acorde para ouvir a música a partir dele.")
        self.h_extra.setText("  ".join(extra))
        chords = m.get("chords", [])
        self.h_table.setRowCount(len(chords))
        for i, c in enumerate(chords):
            for j, text in enumerate((str(c["bar"]), fmt_time(c["start"]), c["chord"], f"{int(c['confidence'] * 100)}%")):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.h_table.setItem(i, j, item)

    def _chord_clicked(self, row: int, _col: int) -> None:
        chords = self.project.data.get("music", {}).get("chords", [])
        if 0 <= row < len(chords) and self.player.length:
            self.player.seek(chords[row]["start"] * SR / self.player.length)

    # ------------------------------------------------------------------ exportar
    def _build_export(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        box, bl = card()
        bl.addWidget(label("Leve tudo com você", "h2"))
        bl.addWidget(label("Um arquivo ZIP com o áudio original, o remasterizado, os instrumentos em WAV, as partituras "
                           "em PDF e MusicXML, os MIDI e o relatório da análise.", "muted", wrap=True))
        self.export_btn = button("⬇  BAIXAR TUDO", "primary", on_click=self._export)
        bl.addWidget(self.export_btn)
        self.export_info = label("", "muted", wrap=True)
        bl.addWidget(self.export_info)
        lay.addWidget(box)
        row = QHBoxLayout()
        row.addWidget(button("Abrir a pasta do projeto", on_click=lambda: open_path(self.project.folder)))
        row.addWidget(button("Ver o relatório da análise", on_click=lambda: open_path(self._path("analise/relatorio.txt"))))
        row.addStretch(1)
        row.addWidget(button("Apagar este projeto", "danger", on_click=self._delete))
        lay.addLayout(row)
        lay.addStretch(1)
        return w

    def _fill_export(self) -> None:
        try:
            self.export_info.setText(f"Este projeto ocupa {self.project.size_mb():.0f} MB no computador, em "
                                     f"{self.project.folder}")
        except Exception:  # noqa: BLE001
            self.export_info.setText("")

    def _export(self) -> None:
        project = self.project
        dest, _ = QFileDialog.getSaveFileName(self, "Baixar tudo", str(Path.home() / f"{slugify(project.name)}_JFD.zip"),
                                              "Arquivo ZIP (*.zip)")
        if not dest:
            return
        self.export_btn.setEnabled(False)

        def work(progress, cancel):
            return pipeline.export_zip(project, dest, progress=lambda f, m=None: progress("export", f, m))

        def done(path):
            self.export_btn.setEnabled(True)
            self.export_btn.setText("⬇  BAIXAR TUDO")
            self.export_info.setText(f"Pronto! Arquivo salvo em {path}")

        def failed(msg):
            self.export_btn.setEnabled(True)
            self.export_btn.setText("⬇  BAIXAR TUDO")
            QMessageBox.warning(self, "JFD", msg)

        self.runner.start(work, on_done=done, on_failed=failed,
                          on_progress=lambda s, f, m: self.export_btn.setText(f"Compactando… {int(f * 100)}%"))

    def _delete(self) -> None:
        ask = QMessageBox.question(self, "Apagar projeto",
                                   f"Apagar “{self.project.name}” e todos os arquivos gerados?\n"
                                   "O áudio que você importou continua onde estava no seu computador.")
        if ask == QMessageBox.StandardButton.Yes:
            self.release()
            self.project.delete()
            self.project = None
            self.go_home.emit()
