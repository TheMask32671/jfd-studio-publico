"""Imitação mínima do PySide6 para exercitar a lógica das telas onde o Qt não está instalado.

Não testa o Qt em si (isso é feito pelo autoteste do programa empacotado); serve para
pegar erros de lógica, nomes trocados e fluxos quebrados no código Python da interface.
Tarefas em segundo plano rodam de forma síncrona.
"""
import inspect
import sys
import types

QT_LOWER = {"show", "hide", "close", "update", "resize", "width", "height", "rect", "exec", "accept", "reject",
            "start", "stop", "wait", "grab", "load", "render", "save", "end", "widget", "layout", "quit", "font",
            "raise_", "move", "repaint", "size", "pos", "event", "parent", "children", "palette",
            "ignore", "interval"}
SIGNALS = {"clicked", "toggled", "valueChanged", "currentIndexChanged", "timeout", "finished", "sliderReleased",
           "itemClicked", "itemActivated", "cellClicked", "currentChanged", "currentRowChanged", "textChanged"}


class Dummy:
    """Aceita qualquer chamada ou atributo e devolve outro Dummy."""

    value = 0

    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return Dummy()

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return Dummy()

    def __or__(self, other):
        return self

    __ror__ = __or__

    def __int__(self):
        return 0

    __index__ = __int__

    def __iter__(self):
        return iter(())

    def __eq__(self, other):
        return isinstance(other, Dummy)

    def __hash__(self):
        return 0


class _Meta(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return Dummy()


class Bound:
    def __init__(self):
        self.slots = []
        self.blocked = False

    def connect(self, fn):
        self.slots.append(fn)

    def emit(self, *args):
        if self.blocked:
            return
        for fn in list(self.slots):
            if isinstance(fn, Bound):
                fn.emit(*args)
                continue
            try:
                params = [p for p in inspect.signature(fn).parameters.values()
                          if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
                variadic = any(p.kind == p.VAR_POSITIONAL for p in inspect.signature(fn).parameters.values())
                n = len(args) if variadic else min(len(args), len(params))
            except (TypeError, ValueError):
                n = len(args)
            fn(*args[:n])


class Signal:
    def __init__(self, *types_):
        self._name = None

    def __set_name__(self, owner, name):
        self._name = "_sig_" + name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        bound = obj.__dict__.get(self._name)
        if bound is None:
            bound = obj.__dict__[self._name] = Bound()
        return bound


class QObject(metaclass=_Meta):
    def __init__(self, *a, **k):
        self._blocked = False

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in SIGNALS:
            bound = self.__dict__.setdefault("_sig_" + name, Bound())
            return bound
        if any(c.isupper() for c in name) or name in QT_LOWER:
            return Dummy()
        raise AttributeError(f"{type(self).__name__} não tem o atributo '{name}'")

    def blockSignals(self, on):  # noqa: N802
        for k, v in self.__dict__.items():
            if k.startswith("_sig_"):
                v.blocked = bool(on)
        self._blocked = bool(on)

    def deleteLater(self):  # noqa: N802
        pass


class QWidget(QObject):
    def __init__(self, *a, **k):
        super().__init__()
        self._text = a[0] if a and isinstance(a[0], str) else ""
        self._visible = True
        self._enabled = True
        self._checked = False

    def setText(self, t):  # noqa: N802
        assert isinstance(t, str), f"setText recebeu {type(t).__name__}"
        self._text = t

    def text(self):
        return self._text

    def setVisible(self, v):  # noqa: N802
        self._visible = bool(v)

    def setEnabled(self, v):  # noqa: N802
        self._enabled = bool(v)

    def isEnabled(self):  # noqa: N802
        return self._enabled

    def setChecked(self, v):  # noqa: N802
        changed = self._checked != bool(v)
        self._checked = bool(v)
        if changed:
            self.toggled.emit(self._checked)

    def isChecked(self):  # noqa: N802
        return self._checked

    def click(self):
        if self._enabled:
            self.clicked.emit(False)

    def width(self):
        return 800

    def height(self):
        return 120


class QLabel(QWidget):
    pass


class QPushButton(QWidget):
    pass


class QCheckBox(QWidget):
    pass


class QFrame(QWidget):
    pass


class QDialog(QWidget):
    def exec(self):
        return 0


class QMainWindow(QWidget):
    pass


class QScrollArea(QWidget):
    pass


class QProgressBar(QWidget):
    def __init__(self, *a, **k):
        super().__init__()
        self._value = 0

    def setValue(self, v):  # noqa: N802
        assert isinstance(v, int), f"setValue recebeu {type(v).__name__}"
        self._value = v

    def value(self):
        return self._value


class QSlider(QProgressBar):
    def setValue(self, v):  # noqa: N802
        super().setValue(v)
        self.valueChanged.emit(v)

    def isSliderDown(self):  # noqa: N802
        return False


class QComboBox(QWidget):
    def __init__(self, *a, **k):
        super().__init__()
        self._items = []
        self._index = -1

    def addItem(self, text, data=None):  # noqa: N802
        self._items.append((text, data))
        if self._index < 0:
            self._index = 0
            self.currentIndexChanged.emit(0)

    def clear(self):
        self._items, self._index = [], -1

    def count(self):
        return len(self._items)

    def findData(self, data):  # noqa: N802
        return next((i for i, it in enumerate(self._items) if it[1] == data), -1)

    def currentData(self):  # noqa: N802
        return self._items[self._index][1] if 0 <= self._index < len(self._items) else None

    def currentIndex(self):  # noqa: N802
        return self._index

    def setCurrentIndex(self, i):  # noqa: N802
        assert isinstance(i, int)
        if i != self._index:
            self._index = i
            self.currentIndexChanged.emit(i)


class QListWidgetItem(metaclass=_Meta):
    def __init__(self, text=""):
        self._text, self._data = text, {}

    def setData(self, role, value):  # noqa: N802
        self._data[0] = value

    def data(self, role):
        return self._data.get(0)

    def text(self):
        return self._text


class QTableWidgetItem(QListWidgetItem):
    def setTextAlignment(self, a):  # noqa: N802
        pass


class QListWidget(QWidget):
    def __init__(self, *a, **k):
        super().__init__()
        self._items, self._row = [], -1

    def addItem(self, item):  # noqa: N802
        self._items.append(item if isinstance(item, QListWidgetItem) else QListWidgetItem(item))

    def clear(self):
        self._items, self._row = [], -1

    def count(self):
        return len(self._items)

    def currentRow(self):  # noqa: N802
        return self._row

    def setCurrentRow(self, r):  # noqa: N802
        if r != self._row:
            self._row = r
            self.currentRowChanged.emit(r)

    def currentItem(self):  # noqa: N802
        return self._items[self._row] if 0 <= self._row < len(self._items) else None


class QTableWidget(QWidget):
    pass


class QTabWidget(QWidget):
    def __init__(self, *a, **k):
        super().__init__()
        self._tabs, self._index = [], -1

    def addTab(self, w, title):  # noqa: N802
        self._tabs.append(w)
        if self._index < 0:
            self._index = 0

    def count(self):
        return len(self._tabs)

    def currentIndex(self):  # noqa: N802
        return self._index

    def setCurrentIndex(self, i):  # noqa: N802
        if i != self._index:
            self._index = i
            self.currentChanged.emit(i)


class QStackedWidget(QWidget):
    def __init__(self, *a, **k):
        super().__init__()
        self._pages, self.current = [], None

    def addWidget(self, w):  # noqa: N802
        self._pages.append(w)

    def setCurrentWidget(self, w):  # noqa: N802
        assert w in self._pages
        self.current = w

    def currentWidget(self):  # noqa: N802
        return self.current


class _Item:
    def __init__(self, obj):
        self.obj = obj

    def widget(self):
        return self.obj if isinstance(self.obj, QWidget) else None

    def layout(self):
        return self.obj if isinstance(self.obj, QLayout) else None


class QLayout(QObject):
    def __init__(self, parent=None, *a, **k):
        super().__init__()
        self._children = []

    def addWidget(self, w, *a, **k):  # noqa: N802
        assert isinstance(w, QWidget), f"addWidget recebeu {type(w).__name__}"
        self._children.append(w)

    def addLayout(self, lay, *a, **k):  # noqa: N802
        assert isinstance(lay, QLayout), f"addLayout recebeu {type(lay).__name__}"
        self._children.append(lay)

    def count(self):
        return len(self._children)

    def takeAt(self, i):  # noqa: N802
        return _Item(self._children.pop(i))


class QVBoxLayout(QLayout):
    pass


class QHBoxLayout(QLayout):
    pass


class QGridLayout(QLayout):
    pass


class QButtonGroup(QObject):
    pass


class QTimer(QObject):
    pass


class QThread(QObject):
    def start(self):
        self.run()
        self.finished.emit()

    def isRunning(self):  # noqa: N802
        return False

    def wait(self, *a):
        return True


class QMessageBox(metaclass=_Meta):
    log = []
    answer_yes = True

    class StandardButton:
        Yes, No = "yes", "no"

    @classmethod
    def warning(cls, parent, title, text):
        cls.log.append(("warning", text))

    @classmethod
    def information(cls, parent, title, text):
        cls.log.append(("information", text))

    @classmethod
    def question(cls, parent, title, text):
        cls.log.append(("question", text))
        return cls.StandardButton.Yes if cls.answer_yes else cls.StandardButton.No


class QFileDialog(metaclass=_Meta):
    next_path = ""

    @classmethod
    def getOpenFileName(cls, *a, **k):  # noqa: N802
        return cls.next_path, ""

    @classmethod
    def getSaveFileName(cls, *a, **k):  # noqa: N802
        return cls.next_path, ""


class QApplication(QObject):
    _inst = None

    def __init__(self, *a, **k):
        super().__init__()
        QApplication._inst = self

    @classmethod
    def instance(cls):
        return cls._inst


def install() -> bool:
    """Instala a imitação em ``sys.modules``. Retorna False se o PySide6 real existir."""
    try:
        import PySide6  # noqa: F401
        return False
    except ImportError:
        pass
    here = sys.modules[__name__]
    pkg = types.ModuleType("PySide6")
    pkg.__path__ = []
    pkg.__version__ = "imitação"
    sys.modules["PySide6"] = pkg
    for mod in ("QtCore", "QtGui", "QtWidgets", "QtSvg", "QtSvgWidgets"):
        m = types.ModuleType(f"PySide6.{mod}")

        def _getattr(name, _here=here):
            if name.startswith("__"):
                raise AttributeError(name)
            return getattr(_here, name, None) or _Meta(name, (Dummy,), {})

        m.__getattr__ = _getattr
        sys.modules[f"PySide6.{mod}"] = m
        setattr(pkg, mod, m)
    return True
