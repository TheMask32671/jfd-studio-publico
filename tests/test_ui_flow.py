"""Percorre o fluxo das telas com uma imitação do Qt (pulado quando o PySide6 real está instalado)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import fake_qt  # noqa: E402

if not fake_qt.install():
    pytest.skip("PySide6 real instalado: o fluxo das telas é coberto pelo autoteste (--selftest)",
                allow_module_level=True)

from test_core import SR, make_melody, make_song  # noqa: E402

from jfd.core import project as prj  # noqa: E402
from jfd.core.audio_io import save_wav  # noqa: E402


def test_full_ui_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(prj, "projects_dir", lambda: tmp_path)
    import jfd.config as cfg

    monkeypatch.setattr(cfg, "app_data_dir", lambda: tmp_path)
    from jfd.ui import main_window as mw
    from jfd.ui.results import TAB_REMASTER, TAB_SCORES, TAB_STEMS

    monkeypatch.setattr(mw, "free_disk_gb", lambda: 100.0)
    box = fake_qt.QMessageBox
    box.log.clear()
    audio, _ = make_song(bars=8)
    src = tmp_path / "Canção de Teste.wav"
    save_wav(src, audio, SR, bits=16)

    win = mw.MainWindow()
    assert win.stack.current is win.home
    win.home.refresh()

    # escolher arquivo pelo atalho "Quero a partitura do trombone"
    fake_qt.QFileDialog.next_path = str(src)
    win.home.want_file.emit("partitura")
    assert win.stack.current is win.prepare and win.prepare.go.isEnabled()
    assert "estéreo" in win.prepare.info.text()
    assert win.prepare.cb_sep.isChecked() and win.prepare.cb_sco.isChecked() and not win.prepare.cb_rem.isChecked()
    win.prepare.cb_rem.setChecked(True)
    assert "TUDO" in win.prepare.go.text()

    # processar (sem motor de separação neste ambiente) e chegar aos resultados
    win.prepare.go.click()
    assert win.stack.current is win.results, box.log
    res = win.results
    assert res.banner.text() == "SUA MÚSICA ESTÁ PRONTA!" and res.tabs.currentIndex() == TAB_SCORES
    d = win.project.data
    assert d["done"]["pipeline"] and d["remaster"]["file"] and d["lines"] and d["scores"]
    assert res.score_list.count() == len(d["scores"]) and res.score_line.count() == len(d["lines"])
    assert "Dó" in res.h_values["key"][0].text() or "Lá" in res.h_values["key"][0].text()
    assert res.midi_list.count() == len(d["midi"])

    # todas as abas, comparação antes/depois e nova partitura transposta
    for i in range(res.tabs.count()):
        res.tabs.setCurrentIndex(i)
    res.tabs.setCurrentIndex(TAB_REMASTER)
    assert [t.id for t in res.player.tracks] == ["original", "remaster"]
    res.btn_before.click()
    assert res.player.track("original").solo and not res.player.track("remaster").solo
    res.btn_after.click()
    assert res.player.track("remaster").solo
    res.player.seek(0.5)
    res.rem_mode.setCurrentIndex(0)
    res.rem_redo.click()
    assert win.project.data["remaster"]["mode"] == "natural" and "Natural" in res.rem_title.text()

    res.tabs.setCurrentIndex(TAB_SCORES)
    res.score_inst.setCurrentIndex(res.score_inst.findData("sax_alto"))
    n = res.score_list.count()
    res.score_make.click()
    assert res.score_list.count() == n + 1 and "Saxofone alto" in res.score_list.currentItem().text()
    assert "Lá" in res.score_info.text() or "Fá" in res.score_info.text()

    # sem instrumentos separados, a aba oferece separar agora
    res.tabs.setCurrentIndex(TAB_STEMS)
    assert res.stems_lay.count() >= 1 and not res._stem_rows

    # exportar tudo
    fake_qt.QFileDialog.next_path = str(tmp_path / "saida.zip")
    res.export_btn.click()
    assert (tmp_path / "saida.zip").stat().st_size > 10_000

    # voltar ao início, reabrir o projeto e apagar
    win.show_home()
    assert win.stack.current is win.home and win.home.recent.count() == 1
    win.open_project(str(win.project.folder))
    assert win.stack.current is win.results
    folder = win.project.folder
    res._delete()
    assert not folder.exists() and win.stack.current is win.home
    assert not [m for m in box.log if m[0] == "warning"], box.log


def test_stems_tab_with_fake_separation(tmp_path, monkeypatch):
    """Simula um projeto com instrumentos separados para exercitar a mesa de mixagem."""
    monkeypatch.setattr(prj, "projects_dir", lambda: tmp_path)
    from jfd.core import pipeline, separate
    from jfd.ui.results import TAB_STEMS, ResultsPage
    from jfd.ui.workers import TaskRunner

    audio, truth = make_song(bars=4)
    melody = make_melody(truth, len(audio))

    def fake_separate(a, sr, profile, progress=None, cancel=None):
        stems = {"vocals": melody, "drums": a * 0.3, "bass": a * 0.2, "other": a * 0.0}
        report = {}
        for sid in stems:
            meta = separate.STEMS[sid]
            report[sid] = {"id": sid, "name": meta["name"], "icon": meta["icon"], "present": sid != "other",
                           "presence_db": -6.0, "reliability": meta["reliability"], "note": "nota",
                           "instrument": meta["instrument"]}
        return {"stems": stems, "report": report, "model": "falso", "device": "cpu"}

    monkeypatch.setattr(separate, "available", lambda: (True, ""))
    monkeypatch.setattr(separate, "separate", fake_separate)
    src = tmp_path / "Banda.wav"
    save_wav(src, audio, SR, bits=16)
    proj = prj.Project.create(src)
    pipeline.run_all(proj, pipeline.Options())
    assert set(proj.data["stems"]) == {"vocals", "drums", "bass", "other"}
    assert "vocals" in proj.data["lines"] and "other" not in proj.data["lines"]

    page = ResultsPage(TaskRunner())
    page.load(proj, tab=TAB_STEMS)
    assert [t.id for t in page.player.tracks] == ["vocals", "drums", "bass", "other"]
    rows = page._stem_rows
    rows["vocals"]["solo"].setChecked(True)
    rows["drums"]["mute"].setChecked(True)
    rows["bass"]["volume"].setValue(50)
    rows["bass"]["pan"].setValue(-100)
    assert page.player.track("vocals").solo and page.player.track("drums").mute
    assert page.player.track("bass").volume == 0.5 and page.player.track("bass").pan == -1.0
    from jfd.core.mixer import mix_block

    block = mix_block(page.player.tracks, SR, 1024)          # só a voz (solo) deve soar
    assert abs(float(abs(block).max()) - float(abs(melody[SR:SR + 1024]).max())) < 1e-3
    rows["vocals"]["improve"].click()
    assert proj.data["stems"]["vocals"].get("improved") and rows["vocals"]["improved"].isChecked()
    page._score_for("vocals")
    assert page.score_line.currentData() == "vocals"
    page.release()
