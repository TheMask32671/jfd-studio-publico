"""Tarefas em segundo plano, para a interface nunca congelar."""
from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from ..config import log
from ..core import Cancelled, JFDError


class TaskThread(QThread):
    """Executa ``fn(progress, cancel_event)`` fora da interface.

    ``progress(etapa, fração, mensagem)`` pode ser chamado pela tarefa a qualquer momento.
    """

    progress = Signal(str, float, str)
    done = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def _emit(self, step, frac, message=None) -> None:
        self.progress.emit(str(step), float(frac), message or "")

    def run(self) -> None:  # noqa: D401 - executado pelo Qt
        try:
            result = self._fn(self._emit, self.cancel_event)
        except Cancelled:
            self.cancelled.emit()
        except JFDError as exc:
            log.warning("Tarefa falhou: %s", exc)
            self.failed.emit(str(exc))
        except MemoryError:
            log.exception("Memória insuficiente")
            self.failed.emit("O computador ficou sem memória para esta operação. Feche outros programas "
                             "e tente novamente com o perfil Leve.")
        except Exception as exc:  # noqa: BLE001 - qualquer erro vira mensagem amigável
            log.exception("Erro inesperado em tarefa")
            self.failed.emit(f"Aconteceu um erro inesperado ({exc.__class__.__name__}). "
                             "Os detalhes foram gravados no registro de erros do JFD.")
        else:
            self.done.emit(result)


class TaskRunner:
    """Guarda as tarefas em andamento (evita que sejam destruídas antes de terminar)."""

    def __init__(self):
        self._threads: list[TaskThread] = []

    def start(self, fn, on_done=None, on_failed=None, on_progress=None, on_cancelled=None) -> TaskThread:
        th = TaskThread(fn)
        if on_progress:
            th.progress.connect(on_progress)
        if on_done:
            th.done.connect(on_done)
        if on_failed:
            th.failed.connect(on_failed)
        if on_cancelled:
            th.cancelled.connect(on_cancelled)
        th.finished.connect(lambda t=th: self._forget(t))
        self._threads.append(th)
        th.start()
        return th

    def _forget(self, th: TaskThread) -> None:
        if th in self._threads:
            self._threads.remove(th)
        th.deleteLater()

    def busy(self) -> bool:
        return any(t.isRunning() for t in self._threads)

    def cancel_all(self, wait_ms: int = 4000) -> None:
        for t in list(self._threads):
            t.cancel()
        for t in list(self._threads):
            t.wait(wait_ms)
