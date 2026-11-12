"""Ponto de entrada do JFD e autoteste usado na montagem do instalador."""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

from . import APP_NAME, __version__
from .config import log, setup_environment


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    setup_environment()
    if "--selftest" in argv:
        out = argv[argv.index("--selftest") + 1] if len(argv) > argv.index("--selftest") + 1 else "selftest.txt"
        return selftest(Path(out), full="--full" in argv)
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .ui import theme
    from .ui.main_window import MainWindow, install_error_dialog

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    theme.apply(app)
    icon = Path(__file__).parent / "assets" / "jfd.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    install_error_dialog()
    log.info("JFD %s iniciado", __version__)
    win = MainWindow()
    win.show()
    for arg in argv:
        if not arg.startswith("-") and Path(arg).is_file():
            win.prepare_file(Path(arg))
            break
    return app.exec()


def selftest(report: Path, full: bool = False) -> int:
    """Confere, sem intervenção humana, se o programa empacotado funciona de ponta a ponta.

    Gera uma música sintética, roda o pipeline, cria partitura/PDF e abre as telas em modo
    invisível. Com ``full`` também baixa o modelo de separação e separa um trecho curto.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    lines: list[str] = []
    failures = 0

    def check(name: str, fn, fatal: bool = True):
        nonlocal failures
        try:
            detail = fn()
            lines.append(f"OK    {name}" + (f" — {detail}" if detail else ""))
        except Exception:  # noqa: BLE001
            failures += 1 if fatal else 0
            lines.append(f"{'FALHA' if fatal else 'AVISO'} {name}\n{traceback.format_exc()}")

    import numpy as np

    from .core import pipeline, project as prj, render, separate
    from .core.audio_io import SR, ffmpeg_exe, save_wav

    tmp = Path(tempfile.mkdtemp(prefix="jfd-selftest-"))
    prj.projects_dir = lambda: tmp          # projetos de teste fora da pasta do usuário
    state: dict = {}

    def synth() -> np.ndarray:
        beat = 0.5          # 120 BPM
        n = int(16 * beat * SR) + SR
        x = np.zeros(n, dtype=np.float32)
        t_note = np.arange(int(beat / 2 * 0.9 * SR)) / SR
        for i, m in enumerate([60, 62, 64, 65, 67, 65, 64, 62] * 4):
            f = 440.0 * 2 ** ((m - 69) / 12)
            seg = sum(a * np.sin(2 * np.pi * f * (k + 1) * t_note) for k, a in enumerate((1.0, 0.5, 0.25)))
            seg = seg * np.minimum(1.0, t_note / 0.01) * np.minimum(1.0, (t_note[-1] - t_note) / 0.03) * 0.3
            a0 = int(i * beat / 2 * SR)
            x[a0:a0 + len(seg)] += seg.astype(np.float32)
        t_k = np.arange(int(0.12 * SR)) / SR
        for i in range(16):
            kick = np.sin(2 * np.pi * (55 + 60 * np.exp(-t_k * 40)) * t_k) * np.exp(-t_k * 30) * 0.6
            a0 = int(i * beat * SR)
            x[a0:a0 + len(kick)] += kick.astype(np.float32)
        return np.stack([x, x], axis=1) * 0.6

    def t_ffmpeg():
        return ffmpeg_exe()

    def t_pipeline():
        src = tmp / "Teste JFD.wav"
        save_wav(src, synth(), SR, bits=16)
        proj = prj.Project.create(src)
        pipeline.run_all(proj, pipeline.Options(separate=full, profile="leve", remaster_mode="profissional"))
        state["project"] = proj
        d = proj.data
        assert d["lines"] and d["midi"], "pipeline não gerou linhas melódicas/MIDI"
        assert not full or d["stems"], "a separação não gerou instrumentos"
        if not d["scores"]:
            pipeline.make_score(proj, next(iter(d["lines"])), "melodia")
        return (f"{d['music']['bpm']} BPM, {d['music']['key_name']}, {len(d['stems'])} instrumento(s), "
                f"linhas: {', '.join(d['lines'])}, {len(d['scores'])} partitura(s)")

    def t_pdf():
        proj = state["project"]
        entry = pipeline.make_score(proj, next(iter(proj.data["lines"])), "sax_alto")
        assert entry["pdf"], "PDF não foi gerado"
        size = proj.path(entry["pdf"]).stat().st_size
        state["pdf"] = proj.path(entry["pdf"])
        return f"{entry['engine']}, {size} bytes"

    def t_verovio():
        assert render.verovio_available(), "Verovio não está instalado"
        import verovio

        proj = state["project"]
        xml = proj.path(proj.data["scores"][0]["musicxml"]).read_text(encoding="utf-8")
        raw = render.raw_svg_pages(xml)
        (report.parent / "selftest-partitura-original.svg").write_text(raw[0], encoding="utf-8")
        flat = render.flatten_svg(raw[0])
        (report.parent / "selftest-partitura.svg").write_text(flat, encoding="utf-8")
        render.svg_to_png(flat, report.parent / "selftest-partitura.png")
        assert "<use" not in flat and "<symbol" not in flat, "SVG não foi simplificado"
        return f"Verovio {getattr(verovio.toolkit(), 'getVersion', lambda: '?')()}, {len(raw)} página(s), {raw[0].count('<use')} símbolos"

    def t_qt():
        from PySide6 import __version__ as pyside_version
        from PySide6.QtWidgets import QApplication

        state["app"] = QApplication.instance() or QApplication([])
        return f"PySide6 {pyside_version}"

    def t_gui():
        from .ui import theme
        from .ui.main_window import MainWindow

        app = state["app"]
        theme.apply(app)
        win = MainWindow()
        win.show()
        app.processEvents()
        from .core.audio_io import peaks as wave_peaks

        def shot(name: str) -> None:
            app.processEvents()
            win.grab().save(str(report.parent / f"tela-{name}.png"))

        win.home.refresh()
        shot("1-inicio")
        win.prepare.set_file(tmp / "Teste JFD.wav", "tudo")
        win.prepare.set_loaded({"duration": 9.0, "codec": "pcm_s16le", "sample_rate": SR, "channels": 2},
                               wave_peaks(synth(), 1600))
        win.stack.setCurrentWidget(win.prepare)
        shot("2-preparar")
        win.progress.begin("Teste JFD")
        win.progress.update_step("separate", 0.5, "Separando os instrumentos")
        win.stack.setCurrentWidget(win.progress)
        shot("3-progresso")
        win.progress.end()
        win.results.load(state["project"], tab=0, celebrate=True)
        win.stack.setCurrentWidget(win.results)
        for i in range(win.results.tabs.count()):
            win.results.tabs.setCurrentIndex(i)
            shot(f"4-resultado-aba{i + 1}")
        win.home.refresh()
        win.results.release()
        win.close()
        return f"{win.results.tabs.count()} abas, {win.results.score_list.count()} partitura(s) na lista"

    def t_audio_out():
        import sounddevice as sd

        return f"PortAudio {sd.get_portaudio_version()[1]}"

    def t_separation_import():
        ok, why = separate.available()
        assert ok, why
        import torch

        return f"torch {torch.__version__}"

    def t_separation_run():
        audio = synth()[: SR * 8]
        res = separate.separate(audio, SR, "leve")
        assert set(res["stems"]) >= {"vocals", "drums", "bass", "other"}
        return ", ".join(f"{k} {v['presence_db']} dB" for k, v in res["report"].items())

    check("FFmpeg", t_ffmpeg)
    check("Qt", t_qt)
    check("Pipeline (análise, remasterização, transcrição, MIDI)", t_pipeline)
    check("Telas", t_gui)
    check("Verovio (partitura na tela)", t_verovio)
    check("PDF da partitura", t_pdf)
    check("Saída de som (PortAudio)", t_audio_out, fatal=False)
    check("Motor de separação (PyTorch + Demucs)", t_separation_import)
    if full:
        check("Separação de um trecho com o modelo de IA", t_separation_run)
    lines.append("")
    lines.append("RESULTADO: " + ("SUCESSO" if failures == 0 else f"{failures} FALHA(S)"))
    text = "\n".join(lines)
    try:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(text, encoding="utf-8")
        if state.get("pdf"):
            import shutil

            shutil.copy2(state["pdf"], report.parent / "selftest-partitura.pdf")
    except Exception:  # noqa: BLE001
        pass
    print(text)
    return 0 if failures == 0 else 1
