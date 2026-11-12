"""Transcrição monofônica (uma nota por vez): áudio -> notas -> grade rítmica."""
from __future__ import annotations

import numpy as np
from scipy import signal
from scipy.ndimage import median_filter

EPS = 1e-12
TR_SR = 22050
HOP = 221          # ~10 ms


def _yin(x: np.ndarray, sr: int, fmin: float, fmax: float, win: int = 1024, thresh: float = 0.15, cancel=None):
    """YIN vetorizado. Retorna (f0 [T], confiança [T], rms [T])."""
    tau_min = max(2, int(sr / fmax))
    tau_max = int(sr / fmin) + 1
    n = win + tau_max
    nfft = int(2 ** np.ceil(np.log2(n + win)))
    if len(x) < n + HOP:
        x = np.pad(x, (0, n + HOP - len(x)))
    starts = np.arange(0, len(x) - n, HOP)
    f0 = np.zeros(len(starts))
    conf = np.zeros(len(starts))
    rms = np.zeros(len(starts))
    view = np.lib.stride_tricks.sliding_window_view(x, n)
    taus = np.arange(1, tau_max + 1)
    for c0 in range(0, len(starts), 1500):
        if cancel is not None:
            cancel()
        sl = slice(c0, min(len(starts), c0 + 1500))
        fr = view[starts[sl]].astype(np.float64)
        a = np.fft.rfft(fr, nfft, axis=1)
        b = np.fft.rfft(fr[:, :win], nfft, axis=1)
        corr = np.fft.irfft(a * np.conj(b), nfft, axis=1)[:, : tau_max + 1]
        cs = np.concatenate([np.zeros((len(fr), 1)), np.cumsum(fr ** 2, axis=1)], axis=1)
        energy = cs[:, win: win + tau_max + 1] - cs[:, : tau_max + 1]
        d = np.maximum(energy[:, :1] + energy - 2.0 * corr, 0.0)
        cmnd = np.ones_like(d)
        cmnd[:, 1:] = d[:, 1:] * taus / (np.cumsum(d[:, 1:], axis=1) + EPS)
        search = cmnd[:, tau_min: tau_max + 1]
        below = search < thresh
        has = below.any(axis=1)
        idx = np.where(has, below.argmax(axis=1), search.argmin(axis=1))
        rows = np.arange(len(fr))
        for _ in range(40):  # desce até o mínimo local
            nxt = np.minimum(idx + 1, search.shape[1] - 1)
            move = search[rows, nxt] < search[rows, idx]
            if not move.any():
                break
            idx = np.where(move, nxt, idx)
        tau = idx + tau_min
        t0 = np.clip(tau - 1, 0, tau_max)
        t2 = np.clip(tau + 1, 0, tau_max)
        y0, y1, y2 = cmnd[rows, t0], cmnd[rows, tau], cmnd[rows, t2]
        denom = y0 - 2 * y1 + y2
        shift = np.where(np.abs(denom) > EPS, 0.5 * (y0 - y2) / np.where(np.abs(denom) > EPS, denom, 1.0), 0.0)
        tau_f = tau + np.clip(shift, -1.0, 1.0)
        f0[sl] = sr / tau_f
        conf[sl] = 1.0 - np.clip(y1, 0.0, 1.0)
        rms[sl] = np.sqrt((fr[:, :win] ** 2).mean(axis=1))
    return f0, conf, rms


def transcribe_mono(audio: np.ndarray, sr: int, fmin: float = 55.0, fmax: float = 1600.0,
                    win: int | None = None, cancel=None) -> dict:
    """Detecta as notas de uma linha melódica.

    Retorna {"notes": [{start, end, midi, velocity}], "confidence": 0..1, "tuning_cents": …}.
    """
    mono = audio.mean(axis=1) if audio.ndim == 2 else audio
    g = int(np.gcd(TR_SR, sr))
    x = signal.resample_poly(mono, TR_SR // g, sr // g).astype(np.float64) if sr != TR_SR else mono.astype(np.float64)
    x = signal.sosfilt(signal.butter(2, 45.0, "highpass", fs=TR_SR, output="sos"), x)
    if win is None:
        win = 2048 if fmin < 50.0 else 1024       # graves precisam de janela maior
    f0, conf, rms = _yin(x, TR_SR, fmin, fmax, win=win, cancel=cancel)
    if len(f0) == 0 or rms.max() < 1e-5:
        return {"notes": [], "confidence": 0.0, "tuning_cents": 0.0}
    rms_db = 20 * np.log10(rms + EPS)
    gate = max(rms_db.max() - 42.0, -65.0)
    voiced = (conf > 0.78) & (rms_db > gate) & (f0 >= fmin) & (f0 <= fmax)
    midi = 69.0 + 12.0 * np.log2(np.maximum(f0, 1.0) / 440.0)
    if voiced.sum() < 5:
        return {"notes": [], "confidence": 0.0, "tuning_cents": 0.0}
    # afinação geral da gravação (nem tudo está em Lá = 440 Hz)
    frac = midi[voiced] - np.round(midi[voiced])
    ang = np.angle(np.mean(np.exp(2j * np.pi * frac))) / (2 * np.pi)
    midi = midi - ang
    smooth = median_filter(midi, size=5, mode="nearest")
    t_frame = HOP / TR_SR
    offset = (win / 2) / TR_SR

    notes = []
    cur: list[int] = []

    def close():
        if len(cur) >= 6:
            seg = smooth[cur]
            pitch = int(np.round(np.median(seg)))
            level = float(np.percentile(rms_db[cur], 80))
            notes.append({"start": cur[0] * t_frame + offset, "end": (cur[-1] + 1) * t_frame + offset,
                          "midi": pitch, "level": level,
                          "conf": float(conf[cur].mean())})
        cur.clear()

    for t in range(len(midi)):
        if not voiced[t]:
            close()
            continue
        if cur:
            centre = np.median(smooth[cur[-12:]])
            jumped = abs(smooth[t] - centre) > 0.7
            recent = rms_db[cur[-6:]]
            reattack = len(cur) >= 10 and rms_db[t] > recent.min() + 7.0 and recent.min() < rms_db[cur].max() - 6.0
            if jumped or reattack:
                close()
        cur.append(t)
    close()

    # junta fragmentos da mesma nota separados por falhas muito curtas
    merged: list[dict] = []
    for nt in notes:
        if merged and nt["midi"] == merged[-1]["midi"] and nt["start"] - merged[-1]["end"] < 0.035:
            merged[-1]["end"] = nt["end"]
            merged[-1]["level"] = max(merged[-1]["level"], nt["level"])
        else:
            merged.append(nt)
    # remove saltos isolados e curtos (erros de oitava típicos)
    clean = []
    for i, nt in enumerate(merged):
        dur = nt["end"] - nt["start"]
        prev_m = merged[i - 1]["midi"] if i > 0 else nt["midi"]
        next_m = merged[i + 1]["midi"] if i + 1 < len(merged) else nt["midi"]
        if dur < 0.12 and abs(nt["midi"] - prev_m) >= 11 and abs(nt["midi"] - next_m) >= 11:
            continue
        clean.append(nt)
    if not clean:
        return {"notes": [], "confidence": 0.0, "tuning_cents": float(ang * 100)}
    top = max(n["level"] for n in clean)
    out = [{"start": round(n["start"], 4), "end": round(n["end"], 4), "midi": int(n["midi"]),
            "velocity": int(np.clip(100 + (n["level"] - top) * 2.2, 35, 115))} for n in clean]
    voiced_ratio = float(voiced.mean())
    confidence = float(np.clip(np.mean([n["conf"] for n in clean]) * min(1.0, voiced_ratio * 4), 0.0, 1.0))
    return {"notes": out, "confidence": round(confidence, 2), "tuning_cents": round(float(ang * 100), 1)}


def quantize(notes: list[dict], beats: list[float], beats_per_bar: int, downbeat: int,
             sub: int = 4) -> dict:
    """Encaixa as notas na grade de semicolcheias seguindo as batidas detectadas.

    Retorna {"notes": [{start, dur, midi, velocity}] (em unidades de 1/sub de tempo),
             "units_per_bar": …, "first_bar_time": tempo em segundos do compasso 1}.
    """
    upb = beats_per_bar * sub
    if not notes or len(beats) < 2:
        return {"notes": [], "units_per_bar": upb, "first_bar_time": 0.0}
    b = np.asarray(beats, dtype=float)
    k = np.arange(len(b), dtype=float)

    def pos(t: float) -> float:
        if t <= b[0]:
            return (t - b[0]) / (b[1] - b[0])
        if t >= b[-1]:
            return k[-1] + (t - b[-1]) / (b[-1] - b[-2])
        return float(np.interp(t, b, k))

    raw = []
    for n in notes:
        s = int(np.round((pos(n["start"]) - downbeat) * sub))
        e = int(np.round((pos(n["end"]) - downbeat) * sub))
        raw.append([s, max(e, s + 1), n["midi"], n.get("velocity", 90)])
    raw.sort(key=lambda r: (r[0], -(r[1] - r[0])))
    mono: list[list[int]] = []
    for r in raw:
        if mono and r[0] <= mono[-1][0]:
            continue                      # duas notas no mesmo ponto: fica a mais longa
        if mono and mono[-1][1] > r[0]:
            mono[-1][1] = r[0]
        mono.append(r)
    first_bar = int(np.floor(mono[0][0] / upb))
    shift = -first_bar * upb
    out = [{"start": r[0] + shift, "dur": r[1] - r[0], "midi": int(r[2]), "velocity": int(r[3])} for r in mono]

    def time_of(beat_pos: float) -> float:
        if beat_pos <= 0:
            return float(b[0] + beat_pos * (b[1] - b[0]))
        if beat_pos >= k[-1]:
            return float(b[-1] + (beat_pos - k[-1]) * (b[-1] - b[-2]))
        return float(np.interp(beat_pos, k, b))

    return {"notes": out, "units_per_bar": upb,
            "first_bar_time": round(time_of(downbeat + first_bar * beats_per_bar), 4)}
