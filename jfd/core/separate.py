"""Separação de instrumentos com os modelos Demucs (Hybrid Transformer Demucs).

O que os modelos abertos entregam hoje: voz, bateria, baixo e, no modelo de seis
fontes, guitarra/violão e piano. Todo o resto (metais, madeiras, cordas, teclados
eletrônicos…) sai junto na faixa "Outros instrumentos". O JFD mostra isso com
clareza em vez de inventar faixas que o modelo não consegue isolar.
"""
from __future__ import annotations

import numpy as np

from . import JFDError, models
from .hardware import PROFILES, worker_threads

STEMS = {
    "vocals": {"name": "Voz", "icon": "🎤", "reliability": "alta", "instrument": "voz"},
    "drums": {"name": "Bateria e percussão", "icon": "🥁", "reliability": "alta", "instrument": ""},
    "bass": {"name": "Baixo", "icon": "🎸", "reliability": "alta", "instrument": "baixo"},
    "guitar": {"name": "Guitarra / Violão", "icon": "🎸", "reliability": "média", "instrument": "violao"},
    "piano": {"name": "Piano / Teclados", "icon": "🎹", "reliability": "baixa", "instrument": "piano"},
    "other": {"name": "Outros instrumentos (grupo)", "icon": "🎷", "reliability": "grupo", "instrument": "melodia"},
}
RELIABILITY_TEXT = {
    "alta": "Separação normalmente confiável.",
    "média": "Separação razoável; pode haver vazamento de outros instrumentos.",
    "baixa": "Separação experimental; costuma ter vazamentos e falhas.",
    "grupo": "Reúne metais, madeiras, cordas e demais instrumentos que o modelo não isola individualmente.",
}


def available() -> tuple[bool, str]:
    try:
        import torch  # noqa: F401
        import demucs  # noqa: F401
        return True, ""
    except Exception as exc:  # pragma: no cover - depende da instalação
        return False, f"Motor de separação indisponível ({exc.__class__.__name__})."


def separate(audio: np.ndarray, sr: int, profile: str = "equilibrado", progress=None, cancel=None) -> dict:
    """Separa a gravação. Retorna {"stems": {id: áudio [n, 2]}, "report": {id: {...}}, "model": nome}."""
    ok, why = available()
    if not ok:
        raise JFDError(why)
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    cfg = PROFILES.get(profile, PROFILES["equilibrado"])
    if progress:
        progress(0.0, "Preparando a inteligência artificial")
    torch.set_num_threads(worker_threads())
    models.ensure(cfg["model"], progress)
    try:
        model = get_model(cfg["model"])
    except Exception as exc:
        raise JFDError("Não foi possível carregar o modelo de separação de instrumentos. "
                       "Reinstale o JFD e tente novamente.") from exc
    model.eval()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if sr != model.samplerate:
        raise JFDError("Frequência de amostragem inesperada para o modelo de separação.")

    n = len(audio)
    wav = torch.from_numpy(np.ascontiguousarray(audio.T)).float()      # [2, n]
    ref = wav.mean(0)
    mean, std = float(ref.mean()), float(ref.std()) + 1e-8
    wav = (wav - mean) / std
    sources = list(model.sources)
    out = np.zeros((len(sources), n, 2), dtype=np.float32)
    block, ov = int(30 * sr), int(2 * sr)
    fade = np.linspace(0.0, 1.0, ov, dtype=np.float32)[None, :, None]
    start = 0
    while start < n:
        if cancel is not None:
            cancel()
        end = min(n, start + block)
        if progress:
            progress(0.05 + 0.9 * start / n, "Separando os instrumentos")
        chunk = wav[:, start:end]
        try:
            with torch.no_grad():
                est = apply_model(model, chunk[None], device=device, shifts=cfg["shifts"], split=True,
                                  overlap=cfg["overlap"], progress=False)[0]
        except RuntimeError as exc:
            if "out of memory" in str(exc).lower() or "alloc" in str(exc).lower():
                raise JFDError("Memória insuficiente para separar esta música. Feche outros programas "
                               "ou escolha o perfil Leve.") from exc
            raise
        est = (est * std + mean).cpu().numpy().transpose(0, 2, 1)       # [fontes, amostras, 2]
        if start == 0:
            out[:, start:end] = est
        else:
            m = min(ov, end - start)
            out[:, start:start + m] = out[:, start:start + m] * (1 - fade[:, :m]) + est[:, :m] * fade[:, :m]
            out[:, start + m:end] = est[:, m:]
        if end == n:
            break
        start = end - ov
    if progress:
        progress(0.97, "Avaliando o resultado da separação")
    mix_rms = float(np.sqrt(np.mean(audio ** 2)) + 1e-12)
    report, stems = {}, {}
    for i, sid in enumerate(sources):
        rms = float(np.sqrt(np.mean(out[i] ** 2)) + 1e-12)
        presence_db = 20 * np.log10(rms / mix_rms)
        meta = STEMS.get(sid, {"name": sid, "icon": "🎵", "reliability": "média", "instrument": "melodia"})
        present = presence_db > -32.0
        report[sid] = {"id": sid, "name": meta["name"], "icon": meta["icon"], "present": bool(present),
                       "presence_db": round(float(presence_db), 1), "reliability": meta["reliability"],
                       "note": RELIABILITY_TEXT[meta["reliability"]] if present else
                       "Quase nada foi encontrado nesta faixa; o instrumento provavelmente não está na gravação.",
                       "instrument": meta["instrument"]}
        stems[sid] = out[i]
    return {"stems": stems, "report": report, "model": cfg["model"], "device": device}
