"""Identidade visual: azul profundo, azul elétrico, branco, grafite e detalhes em roxo."""

DEEP = "#0A1230"
PANEL = "#111C44"
PANEL_HI = "#18265A"
ELECTRIC = "#2F7BFF"
ELECTRIC_HI = "#5B98FF"
PURPLE = "#8B5CF6"
GRAPHITE = "#2A2F3D"
TEXT = "#FFFFFF"
MUTED = "#A9B4D6"
OK = "#34D399"
WARN = "#FBBF24"

QSS = f"""
* {{ font-family: "Segoe UI", "Inter", "Noto Sans", sans-serif; font-size: 14px; color: {TEXT}; }}
QMainWindow, QDialog, QWidget#page {{ background: {DEEP}; }}
QLabel#logo {{ font-size: 34px; font-weight: 800; color: {TEXT}; letter-spacing: 2px; }}
QLabel#logoSmall {{ font-size: 22px; font-weight: 800; }}
QLabel#tagline {{ font-size: 16px; color: {MUTED}; }}
QLabel#h1 {{ font-size: 26px; font-weight: 700; }}
QLabel#h2 {{ font-size: 18px; font-weight: 700; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#big {{ font-size: 30px; font-weight: 800; color: {OK}; }}
QLabel#value {{ font-size: 28px; font-weight: 800; }}
QLabel#chipOk {{ background: #0f3d33; color: {OK}; border-radius: 9px; padding: 2px 10px; font-size: 12px; }}
QLabel#chipWarn {{ background: #473a12; color: {WARN}; border-radius: 9px; padding: 2px 10px; font-size: 12px; }}
QLabel#chipInfo {{ background: #2a2456; color: #c4b5fd; border-radius: 9px; padding: 2px 10px; font-size: 12px; }}
QFrame#card {{ background: {PANEL}; border-radius: 16px; }}
QFrame#topbar {{ background: #070d24; }}
QPushButton {{ background: {PANEL_HI}; border: none; border-radius: 10px; padding: 9px 16px; }}
QPushButton:hover {{ background: #223579; }}
QPushButton:disabled {{ color: #6b7699; background: #141c3d; }}
QPushButton:checked {{ background: {ELECTRIC}; }}
QPushButton#primary {{ background: {ELECTRIC}; font-size: 17px; font-weight: 700; padding: 16px 26px; border-radius: 14px; }}
QPushButton#primary:hover {{ background: {ELECTRIC_HI}; }}
QPushButton#tile {{ background: {PANEL}; border-radius: 20px; font-size: 18px; font-weight: 700; padding: 26px 12px; text-align: center; }}
QPushButton#tile:hover {{ background: {PANEL_HI}; border: 2px solid {ELECTRIC}; }}
QPushButton#help {{ background: transparent; border: 1px solid #2b3c7a; border-radius: 18px; padding: 8px 14px; color: {MUTED}; }}
QPushButton#help:hover {{ border-color: {PURPLE}; color: {TEXT}; }}
QPushButton#link {{ background: transparent; color: {ELECTRIC_HI}; padding: 4px 6px; }}
QPushButton#danger {{ background: #4a1d2a; }}
QComboBox, QLineEdit {{ background: {PANEL_HI}; border: 1px solid #2b3c7a; border-radius: 8px; padding: 7px 10px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; selection-background-color: {ELECTRIC}; }}
QCheckBox {{ spacing: 8px; }}
QProgressBar {{ background: {PANEL}; border-radius: 9px; height: 18px; text-align: center; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ELECTRIC}, stop:1 {PURPLE}); border-radius: 9px; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 10px 11px; font-weight: 600; border-bottom: 3px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 3px solid {ELECTRIC}; }}
QListWidget, QTableWidget {{ background: {PANEL}; border: none; border-radius: 12px; padding: 6px; }}
QListWidget::item {{ padding: 9px; border-radius: 8px; }}
QListWidget::item:selected, QTableWidget::item:selected {{ background: {PANEL_HI}; }}
QHeaderView::section {{ background: {PANEL}; color: {MUTED}; border: none; padding: 6px; }}
QSlider::groove:horizontal {{ height: 6px; background: #24305f; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::sub-page:horizontal {{ background: {ELECTRIC}; border-radius: 3px; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: #2b3c7a; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QToolTip {{ background: {GRAPHITE}; color: {TEXT}; border: none; padding: 6px; }}
QMessageBox {{ background: {PANEL}; }}
"""


def apply(app) -> None:
    """Aplica o tema ao aplicativo inteiro (estilo, paleta escura, fonte e folha de estilos)."""
    from PySide6.QtGui import QColor, QFont, QPalette

    app.setStyle("Fusion")
    pal = QPalette()
    roles = QPalette.ColorRole
    for role, color in ((roles.Window, DEEP), (roles.Base, PANEL), (roles.AlternateBase, PANEL_HI),
                        (roles.Button, PANEL_HI), (roles.ToolTipBase, GRAPHITE), (roles.Highlight, ELECTRIC),
                        (roles.WindowText, TEXT), (roles.Text, TEXT), (roles.ButtonText, TEXT),
                        (roles.ToolTipText, TEXT), (roles.HighlightedText, TEXT), (roles.PlaceholderText, MUTED)):
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(QSS)


def styled(widget) -> None:
    """Faz um QWidget personalizado respeitar o fundo definido na folha de estilos."""
    from PySide6.QtCore import Qt

    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
