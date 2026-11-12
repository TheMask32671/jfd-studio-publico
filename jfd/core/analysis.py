"""Análise de qualidade sonora, andamento, compasso, tonalidade e acordes."""
from __future__ import annotations

import numpy as np
from scipy import signal
from scipy.ndimage import uniform_filter1d

from .theory import KeySig, chord_name

EPS = 1e-12


def db(x) -> float:
    return float(20.0 * np.log10(max(float(x), EPS)))


def to_mono(audio: np.ndarray) -> np.ndarray:
    return audio.mean(axis=1) if audio.ndim == 2 else audio


def iter_spectrogram(x: np.ndarray, n_fft: int, hop: int, block: int = 1024):
    """Gera blocos de magnitude |STFT| [frames, bins] sem ocupar muita memória."""
    win = np.hanning(n_fft).astype(np.float32)
    pad = n_fft // 2
    xp = np.pad(x.astype(np.float32), (pad, pad))
    total = 1 + (len(xp) - n_fft) // hop if len(xp) >= n_fft else 0
    for b0 in range(0, total, block):
        idx = np.arange(b0, min(total, b0 + block))
        frames = np.lib.stride_tricks.sliding_window_view(xp, n_fft)[idx * hop]
        yield np.abs(np.fft.rfft(frames * win, axis=1)).astype(np.float32)


# --------------------------------------------------------------------------
# Volume percebido (ITU-R BS.1770, simplificado)
# --------------------------------------------------------------------------

def _k_weight_sos(sr: int) -> np.ndarray:
    def shelf(fc, gain_db, q):
        a = 10 ** (gain_db / 40.0)
        w0 = 2 * np.pi * fc / sr
        alpha = np.sin(w0) / (2 * q)
        cosw = np.cos(w0)
        b0 = a * ((a + 1) + (a - 1) * cosw + 2 * np.sqrt(a) * alpha)
        b1 = -2 * a * ((a - 1) + (a + 1) * cosw)
        b2 = a * ((a + 1) + (a - 1) * cosw - 2 * np.sqrt(a) * alpha)
        a0 = (a + 1) - (a - 1) * cosw + 2 * np.sqrt(a) * alpha
        a1 = 2 * ((a - 1) - (a + 1) * cosw)
        a2 = (a + 1) - (a - 1) * cosw - 2 * np.sqrt(a) * alpha
        return np.array([b0, b1, b2, a0, a1, a2]) / a0

    def highpass(fc, q):
        w0 = 2 * np.pi * fc / sr
        alpha = np.sin(w0) / (2 * q)
        cosw = np.cos(w0)
        b0, b1, b2 = (1 + cosw) / 2, -(1 + cosw), (1 + cosw) / 2
        a0, a1, a2 = 1 + alpha, -2 * cosw, 1 - alpha
        return np.array([b0, b1, b2, a0, a1, a2]) / a0

    return np.vstack([shelf(1500.0, 4.0, 0.7071), highpass(38.0, 0.5)])


def loudness_lufs(audio: np.ndarray, sr: int) -> float:
    """Volume integrado aproximado em LUFS (com portões de -70 e -10 LU)."""
    a = audio if audio.ndim == 2 else audio[:, None]
    if len(a) < sr // 2:
        return -70.0
    y = signal.sosfilt(_k_weight_sos(sr), a, axis=0)
    block, hop = int(0.4 * sr), int(0.1 * sr)
    power = uniform_filter1d((y ** 2).sum(axis=1), size=block, mode="constant")[block // 2::hop]
    if len(power) == 0:
        return -70.0
    l_blocks = -0.691 + 10 * np.log10(np.maximum(power, EPS))
    keep = power[l_blocks > -70.0]
    if len(keep) == 0:
        return -70.0
    rel = -0.691 + 10 * np.log10(keep.mean()) - 10.0
    keep2 = keep[(-0.691 + 10 * np.log10(keep)) > rel]
    if len(keep2) == 0:
        keep2 = keep
    return float(-0.691 + 10 * np.log10(keep2.mean()))


# --------------------------------------------------------------------------
# Qualidade sonora
# --------------------------------------------------------------------------

BANDS = [(20, 90), (90, 180), (180, 355), (355, 710), (710, 1400), (1400, 2800), (2800, 5600), (5600, 11200), (11200, 20000)]
# Curva média de música bem equilibrada, em dB por banda de oitava (relativa)
TARGET_CURVE = np.array([-1.0, 0.0, -1.0, -2.5, -4.5, -7.0, -10.0, -14.0, -21.0])


def average_spectrum(mono: np.ndarray, sr: int, n_fft: int = 16384) -> tuple[np.ndarray, np.ndarray]:
    seg = min(n_fft, max(256, len(mono)))
    f, p = signal.welch(mono, sr, nperseg=seg, noverlap=seg // 2)
    return f, p


def band_levels(f: np.ndarray, p: np.ndarray) -> np.ndarray:
    out = []
    for lo, hi in BANDS:
        m = (f >= lo) & (f < hi)
        out.append(10 * np.log10(max(float(p[m].sum()), EPS)) if m.any() else -120.0)
    return np.array(out)


def stationary_spectrum(mono: np.ndarray, sr: int, n_fft: int = 16384) -> tuple[np.ndarray, np.ndarray]:
    """Espectro do que é constante na gravação (percentil baixo ao longo do tempo), até ~1 kHz.

    Zumbido elétrico e ruído de fundo ficam; notas musicais, que mudam, somem.
    """
    bins = int(1000 * n_fft / sr)
    frames = [mag[:, :bins] for mag in iter_spectrogram(mono, n_fft, n_fft // 2, block=64)]
    if not frames:
        return np.zeros(1), np.zeros(1)
    mags = np.concatenate(frames)
    level = mags.mean(axis=1)
    mags = mags[level > level.max() * 1e-4] if len(mags) > 8 else mags
    spec = np.percentile(mags, 10, axis=0) if len(mags) >= 8 else mags.mean(axis=0)
    return np.fft.rfftfreq(n_fft, 1.0 / sr)[:bins], spec ** 2


def detect_hum(mono: np.ndarray, sr: int) -> int:
    """Retorna 50 ou 60 se houver zumbido elétrico evidente, senão 0."""
    f, p = stationary_spectrum(mono, sr)
    if len(f) < 100:
        return 0
    best, best_score = 0, 0.0
    for base in (50, 60):
        gains = []
        for h in (1, 2, 3):
            fc = base * h
            peak = p[(f > fc - 2.5) & (f < fc + 2.5)]
            side = p[((f > fc - 16) & (f < fc - 6)) | ((f > fc + 6) & (f < fc + 16))]
            gains.append(10 * np.log10((peak.max() + EPS) / (np.median(side) + EPS)) if len(peak) and len(side) else 0.0)
        strong = sum(g > 14.0 for g in gains)
        score = float(sum(gains))
        if strong >= 2 and score > best_score:
            best, best_score = base, score
    return best


def quiet_frames(mono: np.ndarray, n: int = 2048, fraction: float = 0.03) -> tuple[np.ndarray, np.ndarray]:
    """Índices iniciais dos quadros mais silenciosos e o nível (dB) de todos os quadros."""
    hop = n // 2
    count = (len(mono) - n) // hop + 1 if len(mono) >= n else 0
    if count < 16:
        return np.zeros(0, dtype=int), np.zeros(0)
    starts = np.arange(count) * hop
    sq = np.concatenate([[0.0], np.cumsum(mono.astype(np.float64) ** 2)])
    level = 10 * np.log10((sq[starts + n] - sq[starts]) / n + EPS)
    order = np.argsort(level)
    k = int(np.clip(count * fraction, 8, 400))
    near = int(np.searchsorted(level[order], level[order[7]] + 3.0, side="right"))   # até 3 dB acima do 8º mais baixo
    return starts[order[: int(np.clip(near, 8, k))]], level


def noise_estimate(mono: np.ndarray, sr: int) -> dict:
    """Procura ruído de fundo constante nos trechos mais silenciosos da gravação.

    Só considera ruído o que tem espectro "cheio" (chiado), para não confundir com
    notas musicais sustentadas ou cauda de reverberação.
    """
    n = 2048
    idx, level = quiet_frames(mono, n)
    if len(idx) == 0:
        return {"floor_db": -120.0, "noisy": False, "flatness": 0.0}
    floor_db = float(np.median(np.sort(level)[: len(idx)]))
    if not np.isfinite(floor_db):
        floor_db = -120.0
    loud_db = float(np.percentile(level, 95))
    if floor_db < -78.0:
        return {"floor_db": floor_db, "noisy": False, "flatness": 0.0}
    win = signal.get_window("hann", n)
    frames = np.stack([mono[i:i + n] for i in idx]) * win
    power = (np.abs(np.fft.rfft(frames, axis=1)) ** 2).mean(axis=0)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    band = power[(freqs > 400) & (freqs < 9000)] + EPS
    flatness = float(np.exp(np.mean(np.log(band))) / np.mean(band))
    snr = loud_db - floor_db
    noisy = flatness > 0.12 and 12.0 < snr < 58.0 and floor_db > -72.0
    return {"floor_db": floor_db, "noisy": bool(noisy), "flatness": round(flatness, 3)}


def quality_report(audio: np.ndarray, sr: int) -> dict:
    """Mede o áudio e lista, em linguagem simples, os problemas encontrados."""
    mono = to_mono(audio)
    peak = float(np.abs(audio).max()) if len(audio) else 0.0
    rms = float(np.sqrt(np.mean(mono ** 2))) if len(mono) else 0.0
    frame = max(1, int(0.05 * sr))
    usable = (len(mono) // frame) * frame
    fr = np.sqrt((mono[:usable].reshape(-1, frame) ** 2).mean(axis=1) + EPS) if usable else np.array([EPS])
    fr_db = 20 * np.log10(fr)
    active = fr_db[fr_db > fr_db.max() - 60]
    loud_level = float(np.percentile(active, 95)) if len(active) else -120.0
    noise = noise_estimate(mono, sr)
    noise_floor = noise["floor_db"]
    clipped = np.abs(audio) >= 0.999
    clip_ratio = float((clipped[1:] & clipped[:-1]).mean()) if len(audio) > 1 else 0.0
    f, p = average_spectrum(mono, sr)
    levels = band_levels(f, p)
    ref = levels[1:6].mean()
    deviation = (levels - ref) - (TARGET_CURVE - TARGET_CURVE[1:6].mean())
    l_rms = float(np.sqrt(np.mean(audio[:, 0] ** 2) + EPS))
    r_rms = float(np.sqrt(np.mean(audio[:, 1] ** 2) + EPS))
    balance_db = 20 * np.log10(l_rms / r_rms)
    corr = float(np.corrcoef(audio[::8, 0], audio[::8, 1])[0, 1]) if peak > 0 and audio[:, 0].std() > 0 and audio[:, 1].std() > 0 else 1.0
    lufs = loudness_lufs(audio, sr)
    hum = detect_hum(mono, sr)
    dynamic = loud_level - noise_floor

    problems = []
    if peak < 1e-4:
        problems.append("O arquivo está praticamente em silêncio.")
    if lufs < -23 and peak >= 1e-4:
        problems.append("Volume muito baixo.")
    if clip_ratio > 0.0005:
        problems.append("Distorção por saturação (clipping) em alguns trechos.")
    if hum:
        problems.append(f"Zumbido elétrico de {hum} Hz.")
    if noise["noisy"]:
        problems.append("Ruído de fundo ou chiado constante.")
    if deviation[6:8].mean() > 5:
        problems.append("Excesso de agudos.")
    if deviation[6:8].mean() < -7:
        problems.append("Som abafado, com poucos agudos.")
    if deviation[2] > 5 or deviation[0:2].mean() > 6:
        problems.append("Graves embolados ou em excesso.")
    if deviation[0:2].mean() < -9:
        problems.append("Pouco grave.")
    if abs(balance_db) > 1.5 and corr > 0.3:
        problems.append("Um lado (esquerdo/direito) está mais alto que o outro.")
    if abs(float(mono.mean())) > 0.01:
        problems.append("Deslocamento elétrico (DC) no sinal.")

    return {
        "peak_db": db(peak), "rms_db": db(rms), "lufs": lufs,
        "noise_floor_db": noise_floor, "noisy": noise["noisy"], "dynamic_range_db": float(dynamic),
        "clip_ratio": clip_ratio, "hum_hz": hum,
        "band_levels_db": [float(v) for v in levels], "band_deviation_db": [float(v) for v in deviation],
        "balance_db": float(balance_db), "stereo_correlation": corr,
        "dc_offset": float(mono.mean()) if len(mono) else 0.0,
        "problems": problems,
    }


# --------------------------------------------------------------------------
# Andamento e batidas
# --------------------------------------------------------------------------

FPS_HOP = 512
N_FFT_ONSET = 2048


def onset_envelopes(mono: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, float]:
    """Força de ataque (toda a banda e só graves) e quadros por segundo."""
    prev = None
    full, low = [], []
    low_bins = int(200 * N_FFT_ONSET / sr)
    for mag in iter_spectrogram(mono, N_FFT_ONSET, FPS_HOP):
        s = np.log1p(20.0 * mag)
        first = np.zeros_like(s[:1]) if prev is None else prev
        d = np.maximum(np.diff(np.vstack([first, s]), axis=0), 0.0)
        full.append(d.sum(axis=1))
        low.append(d[:, 1:low_bins].sum(axis=1))
        prev = s[-1:]
    env = np.concatenate(full) if full else np.zeros(1)
    env_low = np.concatenate(low) if low else np.zeros(1)
    fps = sr / FPS_HOP
    win = max(3, int(0.6 * fps))
    env = np.maximum(env - uniform_filter1d(env, win), 0.0)
    env_low = np.maximum(env_low - uniform_filter1d(env_low, win), 0.0)
    return env, env_low, fps


def estimate_bpm(env: np.ndarray, fps: float) -> tuple[float, float]:
    """Retorna (BPM, confiança 0..1) pela autocorrelação da força de ataque."""
    if len(env) < int(fps * 4) or env.std() < EPS:
        return 120.0, 0.0
    e = (env - env.mean()) / (env.std() + EPS)
    n = int(2 ** np.ceil(np.log2(2 * len(e))))
    spec = np.fft.rfft(e, n)
    acf = np.fft.irfft(spec * np.conj(spec), n)[: len(e)]
    acf /= acf[0] + EPS
    bpms = np.arange(50.0, 210.01, 0.25)
    lags = 60.0 * fps / bpms
    idx = np.arange(len(acf), dtype=float)

    def at(lag):
        return np.interp(lag, idx, acf, right=0.0)

    score = at(lags) + 0.5 * at(2 * lags) + 0.25 * at(4 * lags)
    prior = np.exp(-0.5 * (np.log2(bpms / 112.0) / 0.8) ** 2)
    weighted = score * prior
    best = int(np.argmax(weighted))
    conf = float(np.clip(at(lags[best]), 0.0, 1.0))
    return float(bpms[best]), conf


def track_beats(env: np.ndarray, fps: float, bpm: float, tightness: float = 100.0) -> np.ndarray:
    """Programação dinâmica (Ellis, 2007): tempos das batidas em segundos."""
    period = 60.0 * fps / bpm
    if len(env) < 4 * period or env.std() < EPS:
        return np.arange(0.0, len(env) / fps, 60.0 / bpm)
    local = env / (env.std() + EPS)
    local = uniform_filter1d(local, 3)
    n = len(local)
    cum = np.zeros(n)
    back = np.full(n, -1, dtype=np.int64)
    lo, hi = int(round(period / 2)), int(round(period * 2))
    offs = np.arange(-hi, -lo + 1)
    cost = -tightness * (np.log(-offs / period) ** 2)
    for t in range(n):
        start = t + offs[0]
        if start < 0:
            cum[t] = local[t]
            continue
        cand = cum[start: start + len(offs)] + cost
        j = int(np.argmax(cand))
        cum[t] = local[t] + cand[j]
        back[t] = start + j
    tail = max(0, n - int(round(period)))
    t = tail + int(np.argmax(cum[tail:]))
    beats = []
    while t >= 0:
        beats.append(t)
        t = back[t]
    beats = np.array(beats[::-1], dtype=float) / fps
    if len(beats) >= 4:
        first, last = float(np.median(np.diff(beats[:4]))), float(np.median(np.diff(beats[-4:])))
        while beats[0] - first >= -0.1 * first:
            beats = np.insert(beats, 0, max(0.0, beats[0] - first))
        end = len(env) / fps
        while beats[-1] + last <= end:
            beats = np.append(beats, beats[-1] + last)
    return beats


def detect_meter(beats: np.ndarray, env_low: np.ndarray, fps: float) -> tuple[int, int, float]:
    """(tempos por compasso, índice do primeiro tempo forte, confiança). Só 3/4 e 4/4."""
    if len(beats) < 12:
        return 4, 0, 0.0
    idx = np.clip(np.round(beats * fps).astype(int), 0, len(env_low) - 1)
    strength = np.array([env_low[max(0, i - 2): i + 3].max() for i in idx])
    if strength.max() < EPS:
        return 4, 0, 0.0
    results = {}
    for n in (3, 4):
        means = np.array([strength[k::n].mean() for k in range(n)])
        results[n] = (float(means.max() / (means.mean() + EPS)), int(np.argmax(means)))
    n = 3 if results[3][0] > results[4][0] * 1.2 else 4
    contrast, phase = results[n]
    return n, phase, float(np.clip((contrast - 1.0) / 1.0, 0.0, 1.0))


# --------------------------------------------------------------------------
# Tonalidade e acordes
# --------------------------------------------------------------------------

CHROMA_FFT = 16384
CHROMA_HOP = 4096
KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def _chroma_map(sr: int, lo: float, hi: float) -> np.ndarray:
    freqs = np.fft.rfftfreq(CHROMA_FFT, 1.0 / sr)
    m = np.zeros((len(freqs), 12), dtype=np.float32)
    valid = (freqs >= lo) & (freqs <= hi)
    midi = 69.0 + 12.0 * np.log2(np.maximum(freqs, 1.0) / 440.0)
    nearest = np.round(midi)
    weight = np.exp(-0.5 * ((midi - nearest) / 0.25) ** 2)
    pcs = (nearest.astype(int) % 12)
    m[np.where(valid)[0], pcs[valid]] = weight[valid]
    return m


def chromagram(mono: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, float]:
    """Croma geral e croma dos graves [quadros, 12], e quadros por segundo."""
    full_map, bass_map = _chroma_map(sr, 60.0, 2100.0), _chroma_map(sr, 50.0, 260.0)
    full, bass = [], []
    for mag in iter_spectrogram(mono, CHROMA_FFT, CHROMA_HOP, block=128):
        full.append(mag @ full_map)
        bass.append(mag @ bass_map)
    if not full:
        z = np.zeros((1, 12), dtype=np.float32)
        return z, z, sr / CHROMA_HOP
    return np.concatenate(full), np.concatenate(bass), sr / CHROMA_HOP


def detect_key(chroma_sum: np.ndarray) -> tuple[KeySig, float]:
    """Tonalidade pelo perfil de Krumhansl. Retorna (tonalidade, confiança 0..1)."""
    if chroma_sum.sum() < EPS:
        return KeySig(0, "major"), 0.0
    scores = []
    for tonic in range(12):
        for mode, prof in (("major", KK_MAJOR), ("minor", KK_MINOR)):
            scores.append((float(np.corrcoef(np.roll(prof, tonic), chroma_sum)[0, 1]), tonic, mode))
    scores.sort(reverse=True)
    best, second = scores[0], scores[1]
    conf = float(np.clip((best[0] - second[0]) * 6.0 + max(best[0], 0.0) * 0.5, 0.0, 1.0))
    return KeySig(best[1], best[2]), conf


CHORD_TYPES = {
    "": ([0, 4, 7], [1.0, 0.8, 0.9]),
    "m": ([0, 3, 7], [1.0, 0.8, 0.9]),
    "7": ([0, 4, 7, 10], [1.0, 0.8, 0.9, 0.6]),
    "maj7": ([0, 4, 7, 11], [1.0, 0.8, 0.9, 0.6]),
    "m7": ([0, 3, 7, 10], [1.0, 0.8, 0.9, 0.6]),
    "dim": ([0, 3, 6], [1.0, 0.8, 0.8]),
}


def best_chord(vec: np.ndarray, bass: np.ndarray | None = None) -> tuple[int, str, float]:
    """(fundamental 0..11, tipo, pontuação 0..1) ou (-1, '', 0) sem harmonia clara.

    ``bass`` (croma dos graves) dá um pequeno bônus à nota que o baixo está tocando.
    """
    norm = np.linalg.norm(vec)
    if norm < EPS:
        return -1, "", 0.0
    v = vec / norm
    bonus = np.zeros(12) if bass is None or bass.max() < EPS else 0.12 * bass / bass.max()
    best = (-1, "", 0.0)
    for root in range(12):
        for kind, (ivs, ws) in CHORD_TYPES.items():
            t = np.zeros(12)
            for iv, w in zip(ivs, ws, strict=True):
                t[(root + iv) % 12] = w
            others = np.mean([v[(root + iv) % 12] for iv in ivs[1:3]])
            if v[root] < 0.5 * others:
                continue                      # sem a fundamental presente não é este acorde
            if len(ivs) == 4:
                triad = np.mean([v[(root + iv) % 12] for iv in ivs[:3]])
                if v[(root + ivs[3]) % 12] < 0.7 * triad:
                    continue
            score = float(v @ t / np.linalg.norm(t)) - (0.03 if len(ivs) == 4 else 0.0) + float(bonus[root])
            if score > best[2]:
                best = (root, kind, score)
    return best


def detect_chords(chroma: np.ndarray, bass: np.ndarray, cfps: float, beats: np.ndarray, per_bar: int,
                  downbeat: int, key: KeySig) -> list[dict]:
    """Um acorde por compasso (ou dois, quando as metades são claramente diferentes)."""
    bars = beats[downbeat::per_bar]
    if len(bars) < 2:
        return []
    out = []

    def seg(t0, t1):
        a, b = int(t0 * cfps), max(int(t0 * cfps) + 1, int(np.ceil(t1 * cfps)))
        return chroma[a:b].sum(axis=0), bass[a:b].sum(axis=0)

    for i in range(len(bars) - 1):
        t0, t1 = float(bars[i]), float(bars[i + 1])
        root, kind, score = best_chord(*seg(t0, t1))
        entries = [(t0, t1, root, kind, score)]
        if per_bar == 4:
            mid = (t0 + t1) / 2
            a, b = best_chord(*seg(t0, mid)), best_chord(*seg(mid, t1))
            if (a[0], a[1]) != (b[0], b[1]) and min(a[2], b[2]) > score + 0.03 and min(a[2], b[2]) > 0.6:
                entries = [(t0, mid, *a), (mid, t1, *b)]
        for (s, e, r, k, sc) in entries:
            name = chord_name(r, k, key) if r >= 0 and sc >= 0.55 else "N.C."
            out.append({"bar": i + 1, "start": round(s, 3), "end": round(e, 3), "chord": name,
                        "root": int(r), "kind": k, "confidence": round(float(min(sc, 1.0)), 3)})
    return out


def secondary_keys(chroma: np.ndarray, cfps: float, main: KeySig, window_s: float = 20.0) -> list[str]:
    """Tonalidades que aparecem em trechos longos e diferem da principal."""
    step = max(1, int(window_s * cfps))
    counts: dict[str, int] = {}
    total = 0
    for a in range(0, max(1, len(chroma) - step // 2), step):
        k, conf = detect_key(chroma[a:a + step].sum(axis=0))
        total += 1
        if conf > 0.35 and k.name_pt() != main.name_pt():
            counts[k.name_pt()] = counts.get(k.name_pt(), 0) + 1
    return [name for name, c in sorted(counts.items(), key=lambda kv: -kv[1]) if total and c / total >= 0.2]


def musical_analysis(audio: np.ndarray, sr: int) -> dict:
    """Andamento, compasso, tonalidade e acordes de uma gravação."""
    mono = to_mono(audio)
    env, env_low, fps = onset_envelopes(mono, sr)
    bpm, bpm_conf = estimate_bpm(env, fps)
    beats = track_beats(env, fps, bpm)
    if len(beats) > 4:
        d = np.diff(beats)
        d = d[np.abs(d - np.median(d)) < 0.2 * np.median(d)]
        bpm = float(60.0 / d.mean())
    per_bar, downbeat, meter_conf = detect_meter(beats, env_low, fps)
    chroma, bass, cfps = chromagram(mono, sr)
    key, key_conf = detect_key(chroma.sum(axis=0))
    chords = detect_chords(chroma, bass, cfps, beats, per_bar, downbeat, key)
    intervals = np.diff(beats) if len(beats) > 2 else np.array([60.0 / bpm])
    stability = float(np.clip(1.0 - np.std(intervals) / (np.mean(intervals) + EPS) * 5.0, 0.0, 1.0))
    return {
        "bpm": round(bpm, 1), "bpm_confidence": round(bpm_conf, 2),
        "tempo_stability": round(stability, 2),
        "beats": [round(float(b), 4) for b in beats],
        "beats_per_bar": per_bar, "downbeat": downbeat, "meter_confidence": round(meter_conf, 2),
        "time_signature": f"{per_bar}/4",
        "key": key.to_dict(), "key_name": key.name_pt(), "key_confidence": round(key_conf, 2),
        "secondary_keys": secondary_keys(chroma, cfps, key),
        "chords": chords,
    }
