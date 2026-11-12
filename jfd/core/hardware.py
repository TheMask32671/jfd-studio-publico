"""Reconhece o hardware disponível e sugere um perfil de processamento."""
from __future__ import annotations

import ctypes
import importlib.util
import os
import platform
import sys

PROFILES = {
    "leve": {"label": "Leve", "model": "htdemucs", "shifts": 0, "overlap": 0.1,
             "hint": "Mais rápido. Separa voz, bateria, baixo e demais instrumentos."},
    "equilibrado": {"label": "Equilibrado", "model": "htdemucs_6s", "shifts": 0, "overlap": 0.25,
                    "hint": "Separa também guitarra/violão e piano."},
    "maxima": {"label": "Máxima qualidade", "model": "htdemucs_6s", "shifts": 2, "overlap": 0.25,
               "hint": "Processa cada trecho mais de uma vez. Bem mais demorado."},
}


def ram_gb() -> tuple[float, float]:
    """(memória total, memória livre) em GB; (0, 0) se não der para descobrir."""
    try:
        if sys.platform == "win32":
            class MEMSTAT(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMSTAT()
            st.dwLength = ctypes.sizeof(MEMSTAT)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return st.ullTotalPhys / 2 ** 30, st.ullAvailPhys / 2 ** 30
        info = {}
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = float(v.strip().split()[0]) / 2 ** 20
        return info.get("MemTotal", 0.0), info.get("MemAvailable", 0.0)
    except Exception:
        return 0.0, 0.0


def detect(probe_gpu: bool = False) -> dict:
    """Descreve o computador. ``probe_gpu`` carrega o PyTorch para consultar a placa de vídeo
    (demora alguns segundos, por isso não é feito na abertura do programa)."""
    total, free = ram_gb()
    hw = {"cpu": platform.processor() or platform.machine(), "cores": os.cpu_count() or 1,
          "ram_gb": round(total, 1), "ram_free_gb": round(free, 1),
          "gpu": "", "vram_gb": 0.0, "cuda": False, "torch": False}
    try:
        hw["torch"] = importlib.util.find_spec("torch") is not None and importlib.util.find_spec("demucs") is not None
    except Exception:
        hw["torch"] = False
    if probe_gpu and hw["torch"]:
        try:
            import torch

            if torch.cuda.is_available():
                hw["cuda"] = True
                hw["gpu"] = torch.cuda.get_device_name(0)
                hw["vram_gb"] = round(torch.cuda.get_device_properties(0).total_memory / 2 ** 30, 1)
        except Exception:
            pass
    if hw["cuda"] and hw["vram_gb"] >= 6:
        hw["suggested_profile"] = "maxima"
    elif total and total < 7.5:
        hw["suggested_profile"] = "leve"
    else:
        hw["suggested_profile"] = "equilibrado"
    return hw


def worker_threads() -> int:
    """Deixa sempre um núcleo livre para o computador continuar respondendo."""
    cores = os.cpu_count() or 2
    return max(1, cores - 1 if cores <= 4 else cores - 2)


def estimate_minutes(duration_s: float, profile: str, cuda: bool) -> float:
    """Estimativa grosseira do tempo de separação, para avisar o usuário."""
    factor = {"leve": 0.6, "equilibrado": 1.0, "maxima": 2.6}.get(profile, 1.0)
    per_audio_minute = 0.08 if cuda else 1.2
    return max(0.5, duration_s / 60.0 * per_audio_minute * factor)
