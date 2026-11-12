"""Remasterização automática: restauração, equilíbrio, dinâmica e volume final.

Três modos — natural, profissional e estúdio — com tratamentos cada vez mais
elaborados. Cada etapa só é aplicada quando a análise indica que é necessária,
e o arquivo original nunca é alterado.
"""
from __future__ import annotations

import numpy as np
from scipy import signal
from scipy.ndimage import median_filter, minimum_filter1d, uniform_filter1d

from .analysis import BANDS, TARGET_CURVE, average_spectrum, band_levels, loudness_lufs, quality_report, to_mono

EPS = 1e-12

MODES = {
    "natural": {"label": "Natural", "eq_max": 1.5, "eq_amount": 0.35, "nr_floor_db": -6.0,
                "ratio": 1.0, "target_lufs": -16.0, "max_limit_db": 2.0, "width": 1.0},
    "profissional": {"label": "Profissional", "eq_max": 3.0, "eq_amount": 0.5, "nr_floor_db": -10.0,
                     "ratio": 1.8, "target_lufs": -14.0, "max_limit_db": 4.0, "width": 1.0},
    "estudio": {"label": "Estúdio", "eq_max": 4.0, "eq_amount": 0.65, "nr_floor_db": -14.0,
                "ratio": 2.4, "target_lufs": -12.5, "max_limit_db": 5.0, "width": 1.06},
}
CEILING = 10 ** (-1.0 / 20.0)  # teto de pico: -1 dBFS


def _chunks(n: int, size: int, overlap: int):
    start = 0
    while start < n:
        end = min(n, start + size)
        yield start, end
        if end == n:
            break
        start = end - overlap


def process_in_chunks(x: np.ndarray, fn, sr: int, seconds: float = 20.0, overlap_s: float = 0.5,
                      cancel=None) -> np.ndarray:
    """Aplica ``fn`` em blocos com emenda suave, para limitar o uso de memória."""
    size, ov = int(seconds * sr), int(overlap_s * sr)
    if len(x) <= size:
        return fn(x)
    out = np.zeros_like(x)
    fade = np.linspace(0.0, 1.0, ov, dtype=np.float32)
    if x.ndim == 2:
        fade = fade[:, None]
    for start, end in _chunks(len(x), size, ov):
        if cancel is not None:
            cancel()
        y = fn(x[start:end])
        if start == 0:
            out[start:end] = y
        else:
            out[start:start + ov] = out[start:start + ov] * (1.0 - fade) + y[:ov] * fade
            out[start + ov:end] = y[ov:]
    return out


def _per_channel(audio: np.ndarray, fn) -> np.ndarray:
    """Aplica ``fn`` canal por canal, devolvendo float32 (economiza memória)."""
    out = np.empty_like(audio, dtype=np.float32)
    for ch in range(audio.shape[1]):
        out[:, ch] = fn(audio[:, ch])
    return out


def remove_hum(audio: np.ndarray, sr: int, base: int) -> np.ndarray:
    sos = []
    for h in range(1, 6):
        b, a = signal.iirnotch(base * h, Q=35.0 * h ** 0.5, fs=sr)
        sos.append(signal.tf2sos(b, a))
    sos = np.vstack(sos)
    return _per_channel(audio, lambda x: signal.sosfiltfilt(sos, x))


def declick(audio: np.ndarray, sr: int) -> tuple[np.ndarray, int]:
    """Remove estalos isolados e curtos. Retorna (áudio, quantidade de estalos)."""
    out = audio.copy()
    total = 0
    for ch in range(audio.shape[1]):
        x = out[:, ch]
        resid = x - median_filter(x, size=5, mode="nearest")
        local = np.sqrt(uniform_filter1d(resid ** 2, size=2048) + EPS)
        mask = (np.abs(resid) > 9.0 * local) & (np.abs(resid) > 0.03)
        if not mask.any():
            continue
        idx = np.flatnonzero(mask)
        if len(idx) > len(x) * 0.002:      # denso demais: não é estalo, é o próprio som
            continue
        runs = np.split(idx, np.flatnonzero(np.diff(idx) > 4) + 1)
        for r in runs:
            a, b = max(1, r[0] - 2), min(len(x) - 2, r[-1] + 2)
            if b - a > int(0.002 * sr):
                continue
            x[a:b + 1] = np.linspace(x[a - 1], x[b + 1], b - a + 3)[1:-1]
            total += 1
    return out, total


def noise_profile(mono: np.ndarray, n_fft: int = 2048) -> np.ndarray | None:
    """Espectro médio do ruído, medido nos trechos mais silenciosos (escala igual à do STFT)."""
    from .analysis import quiet_frames

    idx, _ = quiet_frames(mono, n_fft)
    if len(idx) < 8:
        return None
    win = signal.get_window("hann", n_fft)
    frames = np.stack([mono[i:i + n_fft] for i in idx]) * win
    return (np.abs(np.fft.rfft(frames, axis=1)) / win.sum()).mean(axis=0).astype(np.float32)


def reduce_noise(audio: np.ndarray, sr: int, floor_db: float, cancel=None) -> np.ndarray:
    """Redução de ruído constante por máscara espectral suave."""
    n_fft, hop = 2048, 512
    profile = noise_profile(to_mono(audio), n_fft)
    if profile is None:
        return audio
    noise = profile[:, None] * 1.6
    floor = 10 ** (floor_db / 20.0)

    def fn(block: np.ndarray) -> np.ndarray:
        _, _, zz = signal.stft(block, sr, nperseg=n_fft, noverlap=n_fft - hop, axis=0)
        # zz: [bins, canais, quadros]
        m = np.abs(zz).mean(axis=1)
        gain = 1.0 - (noise / (m + EPS)) ** 2
        gain = np.clip(gain, floor, 1.0)
        gain = uniform_filter1d(uniform_filter1d(gain, 5, axis=1), 3, axis=0)
        _, y = signal.istft(zz * gain[:, None, :], sr, nperseg=n_fft, noverlap=n_fft - hop, time_axis=-1, freq_axis=0)
        y = y.T
        if len(y) < len(block):
            y = np.pad(y, ((0, len(block) - len(y)), (0, 0)))
        return y[: len(block)].astype(np.float32)

    return process_in_chunks(audio, fn, sr, cancel=cancel)


def tonal_eq(audio: np.ndarray, sr: int, amount: float, max_db: float) -> tuple[np.ndarray, list[float]]:
    """Equalização corretiva suave em direção a uma curva de referência."""
    f, p = average_spectrum(to_mono(audio), sr)
    levels = band_levels(f, p)
    ref = levels[1:6].mean()
    deviation = (levels - ref) - (TARGET_CURVE - TARGET_CURVE[1:6].mean())
    gains = np.clip(-deviation * amount, -max_db, max_db)
    gains[0] = min(gains[0], 1.5)       # não inventar subgrave
    gains[-1] = min(gains[-1], 2.0)     # nem "ar" em gravações que não têm
    if np.abs(gains).max() < 0.4:
        return audio, [0.0] * len(BANDS)
    centers = np.array([np.sqrt(lo * hi) for lo, hi in BANDS])
    grid = np.concatenate([[0.0], centers, [sr / 2.0]])
    resp = 10 ** (np.concatenate([[gains[0]], gains, [gains[-1]]]) / 20.0)
    grid, keep = np.unique(np.minimum(grid, sr / 2.0), return_index=True)
    fir = signal.firwin2(1023, grid, resp[keep], fs=sr)
    out = _per_channel(audio, lambda x: signal.fftconvolve(x, fir.astype(np.float32), mode="same"))
    return out, [round(float(g), 1) for g in gains]


def compress(audio: np.ndarray, sr: int, ratio: float) -> tuple[np.ndarray, float]:
    """Compressor suave com detecção em estéreo ligado. Retorna (áudio, redução média em dB)."""
    if ratio <= 1.0:
        return audio, 0.0
    step = 32
    level = np.sqrt(uniform_filter1d((audio ** 2).mean(axis=1), size=int(0.01 * sr)).astype(np.float64)[::step] + EPS)
    ldb = 20 * np.log10(level + EPS)
    active = ldb[ldb > ldb.max() - 50]
    thr = float(np.percentile(active, 70)) if len(active) else -20.0
    over = np.maximum(ldb - thr, 0.0)
    knee = 6.0
    soft = np.where(over < knee, over ** 2 / (2 * knee), over - knee / 2)
    target = -soft * (1.0 - 1.0 / ratio)
    rate = sr / step
    att, rel = np.exp(-1.0 / (0.015 * rate)), np.exp(-1.0 / (0.18 * rate))
    g = np.empty_like(target)
    cur = 0.0
    for i, t in enumerate(target):
        coef = att if t < cur else rel
        cur = coef * cur + (1.0 - coef) * t
        g[i] = cur
    gain_db = np.interp(np.arange(len(audio)), np.arange(len(g)) * step, g)
    out = audio * (10 ** (gain_db / 20.0))[:, None]
    return out.astype(np.float32), float(-g[ldb > ldb.max() - 50].mean()) if len(active) else 0.0


def limit(audio: np.ndarray, sr: int, ceiling: float = CEILING) -> np.ndarray:
    """Limitador de picos com antecipação; garante que nada passe do teto."""
    peak = np.abs(audio).max(axis=1)
    if peak.max() <= ceiling:
        return audio
    need = np.minimum(1.0, ceiling / np.maximum(peak, EPS))
    look = max(8, int(0.004 * sr))
    g = minimum_filter1d(need, size=2 * look + 1, mode="nearest")
    g = minimum_filter1d(uniform_filter1d(g, size=look, mode="nearest"), size=3, mode="nearest")
    g = np.minimum(g, need)
    return (audio * g[:, None]).astype(np.float32)


def remaster(audio: np.ndarray, sr: int, mode: str = "profissional", report: dict | None = None,
             progress=None, cancel=None) -> tuple[np.ndarray, dict]:
    """Remasteriza uma gravação. Retorna (áudio novo, relatório com as etapas aplicadas)."""
    cfg = MODES.get(mode, MODES["profissional"])
    rep = report or quality_report(audio, sr)
    steps: list[str] = []

    def tick(frac: float, msg: str):
        if cancel is not None:
            cancel()
        if progress is not None:
            progress(frac, msg)

    x = audio.astype(np.float32).copy()
    if np.abs(x).max() < 1e-5:
        return x, {"mode": mode, "steps": ["Áudio em silêncio: nada a melhorar."], "before": rep, "after": rep}

    tick(0.02, "Limpando o sinal")
    x = x - x.mean(axis=0, keepdims=True)
    hp = signal.butter(2, 22.0, "highpass", fs=sr, output="sos")
    x = _per_channel(x, lambda c: signal.sosfiltfilt(hp, c))
    steps.append("Removidos componentes inaudíveis abaixo de 22 Hz.")

    if rep.get("hum_hz"):
        tick(0.08, "Removendo zumbido elétrico")
        x = remove_hum(x, sr, int(rep["hum_hz"]))
        steps.append(f"Zumbido elétrico de {rep['hum_hz']} Hz atenuado.")

    tick(0.15, "Procurando estalos")
    x, clicks = declick(x, sr)
    if clicks:
        steps.append(f"{clicks} estalo(s) removido(s).")

    if rep.get("noisy"):
        tick(0.25, "Reduzindo ruído de fundo")
        x = reduce_noise(x, sr, cfg["nr_floor_db"], cancel=cancel)
        steps.append(f"Ruído de fundo reduzido em até {abs(cfg['nr_floor_db']):.0f} dB.")

    tick(0.5, "Equilibrando graves, médios e agudos")
    x, gains = tonal_eq(x, sr, cfg["eq_amount"], cfg["eq_max"])
    if any(abs(g) >= 0.4 for g in gains):
        low, mid, high = np.mean(gains[:3]), np.mean(gains[3:6]), np.mean(gains[6:])
        steps.append(f"Equalização: graves {low:+.1f} dB, médios {mid:+.1f} dB, agudos {high:+.1f} dB.")

    bal = rep.get("balance_db", 0.0)
    if abs(bal) > 1.0 and rep.get("stereo_correlation", 1.0) > 0.3:
        corr = float(np.clip(bal, -3.0, 3.0)) / 2.0
        x[:, 0] *= 10 ** (-corr / 20.0)
        x[:, 1] *= 10 ** (corr / 20.0)
        steps.append("Equilíbrio entre os lados esquerdo e direito corrigido.")

    if cfg["width"] != 1.0 and rep.get("stereo_correlation", 1.0) < 0.98:
        mid_s, side = (x[:, 0] + x[:, 1]) / 2, (x[:, 0] - x[:, 1]) / 2 * cfg["width"]
        x = np.stack([mid_s + side, mid_s - side], axis=1).astype(np.float32)

    tick(0.65, "Controlando a dinâmica")
    x, reduction = compress(x, sr, cfg["ratio"])
    if reduction > 0.2:
        steps.append(f"Compressão suave ({cfg['ratio']:.1f}:1), redução média de {reduction:.1f} dB.")

    tick(0.8, "Ajustando o volume final")
    lufs = loudness_lufs(x, sr)
    peak_db = 20 * np.log10(np.abs(x).max() + EPS)
    gain_db = cfg["target_lufs"] - lufs
    headroom = -1.0 - peak_db
    gain_db = min(gain_db, headroom + cfg["max_limit_db"])   # limita o quanto o limitador trabalha
    gain_db = float(np.clip(gain_db, -12.0, 30.0))
    x = x * 10 ** (gain_db / 20.0)
    limited = np.abs(x).max() > CEILING
    x = limit(x, sr)
    x = np.clip(x, -CEILING, CEILING).astype(np.float32)
    steps.append(f"Volume ajustado em {gain_db:+.1f} dB" + (", com limitação de picos em -1 dB." if limited else "."))
    if rep.get("clip_ratio", 0) > 0.0005:
        steps.append("Aviso: a gravação original tem distorção por saturação; o reparo completo disso ainda não é feito.")

    tick(0.95, "Conferindo o resultado")
    after = quality_report(x, sr)
    return x, {"mode": mode, "mode_label": cfg["label"], "steps": steps, "before": rep, "after": after,
               "gain_db": round(gain_db, 1)}
