"""Janela principal: liga as telas e conduz o fluxo do usuário."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QMainWindow, QMessageBox, QStackedWidget,
                               QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..config import log, load_settings, save_settings
from ..core import hardware, pipeline
from ..core.audio_io import SUPPORTED, load, peaks
from ..core.project import Project, free_disk_gb
from .pages import FILE_FILTER, GOALS, HomePage, PreparePage, ProgressPage, RecorderDialog
from .results import ResultsPage
from .widgets import button, label
from .workers import TaskRunner


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1180, 800)
        self.setMinimumSize(980, 680)
        self.setAcceptDrops(True)
        self.runner = TaskRunner()
        self.hw = hardware.detect()
        self.goal = "tudo"
        self._pipeline_thread = None
        self._load_token = 0

        central = QWidget()
        central.setObjectName("page")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QFrame()
        bar.setObjectName("topbar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(20, 8, 20, 8)
        bl.addWidget(label("JFD", "logoSmall"))
        bl.addWidget(label("Intelligent Audio Studio", "muted"))
        bl.addStretch(1)
        gpu = f"placa de vídeo {self.hw['gpu']}" if self.hw.get("cuda") else "processamento pelo processador"
        bl.addWidget(label(f"{self.hw['ram_gb']:.0f} GB de memória  ·  {gpu}  ·  v{__version__}", "muted"))
        self.home_btn = button("⌂  Início", on_click=self.show_home)
        bl.addWidget(self.home_btn)
        root.addWidget(bar)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.home = HomePage()
        self.prepare = PreparePage(self.hw)
        self.progress = ProgressPage()
        self.results = ResultsPage(self.runner)
        for page in (self.home, self.prepare, self.progress, self.results):
            self.stack.addWidget(page)
        self.setCentralWidget(central)

        self.home.want_record.connect(self.record)
        self.home.want_file.connect(self.choose_file)
        self.home.open_project.connect(self.open_project)
        self.prepare.start.connect(self.start_pipeline)
        self.prepare.back.connect(self.show_home)
        self.progress.cancel.connect(self.cancel_pipeline)
        self.results.go_home.connect(self.show_home)
        self.results.request_pipeline.connect(self.rerun_pipeline)
        self.project: Project | None = None
        self.show_home()

    # ------------------------------------------------------------------ navegação
    def show_home(self) -> None:
        if self._pipeline_thread is not None:
            return
        self.results.player.pause()
        self.home.refresh()
        self.stack.setCurrentWidget(self.home)

    def choose_file(self, goal: str = "tudo") -> None:
        settings = load_settings()
        start_dir = settings.get("last_dir") or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Escolher áudio", start_dir, FILE_FILTER)
        if path:
            settings["last_dir"] = str(Path(path).parent)
            save_settings(settings)
            self.prepare_file(Path(path), goal)

    def record(self) -> None:
        dlg = RecorderDialog(self)
        if dlg.exec() and dlg.result_path:
            self.prepare_file(dlg.result_path, "tudo")

    def prepare_file(self, path: Path, goal: str = "tudo") -> None:
        if self._pipeline_thread is not None:
            return
        self.goal = goal if goal in GOALS else "tudo"
        self.results.player.pause()
        self.prepare.set_file(path, self.goal)
        self.stack.setCurrentWidget(self.prepare)
        self._load_token += 1
        token = self._load_token

        def work(progress, cancel):
            audio, info = load(path)
            return info, peaks(audio, 1600)

        def done(result):
            if token == self._load_token:
                self.prepare.set_loaded(*result)

        def failed(msg):
            if token == self._load_token:
                self.prepare.set_error(msg)

        self.runner.start(work, on_done=done, on_failed=failed)

    # ------------------------------------------------------------------ arrastar e soltar
    def dragEnterEvent(self, event) -> None:  # noqa: N802 - API do Qt
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if any(u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in SUPPORTED for u in urls):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - API do Qt
        for u in event.mimeData().urls():
            p = Path(u.toLocalFile())
            if u.isLocalFile() and p.suffix.lower() in SUPPORTED:
                self.prepare_file(p, "tudo")
                break

    # ------------------------------------------------------------------ processamento
    def start_pipeline(self, options: pipeline.Options) -> None:
        path = self.prepare.path
        if path is None or self._pipeline_thread is not None:
            return
        if free_disk_gb() < 1.5:
            QMessageBox.warning(self, "JFD", "Há pouco espaço livre no disco. Libere pelo menos 2 GB antes de continuar.")
            return
        try:
            self.project = Project.create(path)
        except Exception as exc:  # noqa: BLE001
            log.exception("Falha ao criar projeto")
            QMessageBox.warning(self, "JFD", f"Não foi possível criar o projeto: {exc}")
            return
        self._run(self.project, options)

    def rerun_pipeline(self, options: pipeline.Options) -> None:
        """Completa etapas que ficaram de fora (por exemplo, separar os instrumentos depois)."""
        if self.project is None or self._pipeline_thread is not None:
            return
        if options.separate and not self.project.is_done("separate"):
            for step in ("transcribe", "scores", "midi", "pipeline"):
                self.project.data["done"].pop(step, None)
        options.profile = self.prepare.profile.currentData() or self.hw.get("suggested_profile", "equilibrado")
        options.remaster = False
        self.goal = "separar"
        self._run(self.project, options)

    def _run(self, project: Project, options: pipeline.Options) -> None:
        self.results.release()
        self.progress.begin(project.name)
        self.stack.setCurrentWidget(self.progress)
        self.home_btn.setEnabled(False)

        def work(progress, cancel_event):
            return pipeline.run_all(project, options, progress=progress, cancel_event=cancel_event)

        thread = self.runner.start(work, on_done=self._pipeline_done, on_failed=self._pipeline_failed,
                                   on_progress=self.progress.update_step, on_cancelled=self._pipeline_cancelled)
        if thread.isRunning():
            self._pipeline_thread = thread

    def cancel_pipeline(self) -> None:
        if self._pipeline_thread is not None:
            self._pipeline_thread.cancel()

    def _finish_run(self) -> None:
        self._pipeline_thread = None
        self.progress.end()
        self.home_btn.setEnabled(True)

    def _pipeline_done(self, project: Project) -> None:
        self._finish_run()
        self.project = project
        self.results.load(project, tab=GOALS.get(self.goal, GOALS["tudo"])[4], celebrate=True)
        self.stack.setCurrentWidget(self.results)

    def _pipeline_failed(self, message: str) -> None:
        self._finish_run()
        QMessageBox.warning(self, "JFD", message + "\n\nO que já foi feito ficou salvo no projeto e pode ser retomado.")
        self.show_home()

    def _pipeline_cancelled(self) -> None:
        self._finish_run()
        self.show_home()

    # ------------------------------------------------------------------ projetos
    def open_project(self, folder: str) -> None:
        if self._pipeline_thread is not None:
            return
        try:
            project = Project.load(folder)
        except Exception:  # noqa: BLE001
            log.exception("Falha ao abrir projeto %s", folder)
            QMessageBox.warning(self, "JFD", "Não foi possível abrir este projeto; os arquivos parecem danificados.")
            return
        self.project = project
        if project.is_done("pipeline"):
            self.results.load(project)
            self.stack.setCurrentWidget(self.results)
            return
        ask = QMessageBox.question(self, "Projeto incompleto",
                                   "Este projeto foi interrompido antes de terminar.\nQuer continuar de onde parou?")
        if ask == QMessageBox.StandardButton.Yes:
            self.goal = "tudo"
            self._run(project, pipeline.Options(profile=self.hw.get("suggested_profile", "equilibrado")))
        elif project.is_done("import"):
            self.results.load(project)
            self.stack.setCurrentWidget(self.results)

    def closeEvent(self, event) -> None:  # noqa: N802 - API do Qt
        if self._pipeline_thread is not None:
            ask = QMessageBox.question(self, "Sair do JFD", "Há um processamento em andamento. Sair mesmo assim?\n"
                                       "O projeto fica salvo e pode ser retomado depois.")
            if ask != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.results.release()
        self.runner.cancel_all()
        event.accept()


def install_error_dialog() -> None:
    """Mostra erros não tratados em uma caixa simples, em vez de fechar o programa em silêncio."""
    import sys

    def hook(exc_type, exc, tb):
        log.error("Erro não tratado", exc_info=(exc_type, exc, tb))
        try:
            QMessageBox.warning(None, "JFD", f"Aconteceu um erro inesperado ({exc_type.__name__}). "
                                "Os detalhes foram gravados no registro de erros.")
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = hook
