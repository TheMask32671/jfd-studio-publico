# -*- mode: python ; coding: utf-8 -*-
"""Receita do PyInstaller: gera a pasta dist/JFD com o programa completo."""
from PyInstaller.utils.hooks import collect_all

import glob

datas = [("jfd/assets", "jfd/assets")]
datas += [(f, "modelos") for f in glob.glob("modelos/*.th")]      # modelos de IA embutidos
binaries = []
hiddenimports = ["scipy.signal", "scipy.ndimage", "PySide6.QtSvg", "PySide6.QtSvgWidgets"]

# Pacotes que carregam arquivos de dados ou módulos de forma dinâmica.
for pkg in ("demucs", "dora", "omegaconf", "antlr4", "submitit", "treetable", "julius", "openunmix",
            "einops", "lameenc", "torchaudio", "verovio", "imageio_ffmpeg", "sounddevice", "_sounddevice_data",
            "certifi"):
    try:
        d, b, h = collect_all(pkg)
    except Exception as exc:  # pacote opcional ausente: segue sem ele
        print(f"[jfd.spec] aviso: {pkg} não coletado ({exc})")
        continue
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["run_jfd.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "notebook", "PyQt5", "PyQt6", "tensorboard"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JFD",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon="jfd/assets/jfd.ico",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="JFD")
