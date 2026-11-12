"""Sequência automática "analisar e fazer tudo" e operações avulsas sobre um projeto."""
from __future__ import annotations

import csv
import gc
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import Cancelled, JFDError
from . import analysis, midi, musicxml, remaster as rm, render, separate as sep, transcribe as tr
from .audio_io import SR, WavReader, load, save_wav
from .project import Project, slugify
from .theory import INSTRUMENTS, KeySig
from ..config import log

STEPS = [
    ("import", "Analisando sua música...", 4),
    ("quality", "Verificando a qualidade do som...", 4),
    ("music", "Identificando andamento e tonalidade...", 8),
    ("separate", "Separando os instrumentos...", 50),
    ("remaster", "Melhorando o áudio...", 12),
    ("transcribe", "Reconhecendo as notas...", 12),
    ("scores", "Criando partituras...", 5),
    ("midi", "Gerando arquivos MIDI...", 2),
    ("organize", "Organizando seus arquivos...", 3),
]
MAX_MINUTES = 25
MIN_AUTO_SCORE_CONFIDENCE = 0.45

# faixa -> (nome da linha, fmin, fmax, instrumento padrão, observação)
LINE_SOURCES = {
    "vocals": ("Voz", 70.0, 1100.0, "voz", ""),
    "bass": ("Baixo", 30.0, 400.0, "baixo", ""),
    "other": ("Melodia dos outros instrumentos", 55.0, 1600.0, "melodia",
              "Vem da faixa de grupo: se vários instrumentos tocam juntos, só a linha mais forte é reconhecida."),
    "guitar": ("Guitarra / Violão (melodia)", 70.0, 1300.0, "violao",
               "Só a melodia principal; acordes e várias notas simultâneas ainda não são transcritos."),
    "piano": ("Piano (melodia)", 55.0, 1600.0, "piano",
              "Só a melodia principal; acordes e várias notas simultâneas ainda não são transcritos."),
    "mix": ("Melodia principal", 55.0, 1600.0, "melodia",
            "Reconhecida direto da gravação completa; funciona melhor com um instrumento solo."),
}


@dataclass
class Options:
    separate: bool = True
    remaster: bool = True
    scores: bool = True
    profile: str = "equilibrado"
    remaster_mode: str = "profissional"


class _Ctx:
    def __init__(self, project: Project, progress, cancel_event: threading.Event | None):
        self.project = project
        self._progress = progress
        self._cancel = cancel_event
        self._audio: np.ndarray | None = None
        self.stems: dict[str, np.ndarray] = {}
        self.step = "import"

    def cancel(self) -> None:
        if self._cancel is not None and self._cancel.is_set():
            raise Cancelled()

    def progress(self, frac: float, message: str | None = None) -> None:
        self.cancel()
        if self._progress:
            self._progress(self.step, float(np.clip(frac, 0.0, 1.0)), message)

    def audio(self) -> np.ndarray:
        if self._audio is None:
            self._audio = WavReader(self.project.playback_original).read_all()
        return self._audio

    def stem(self, sid: str) -> np.ndarray | None:
        if sid in self.stems:
            return self.stems[sid]
        meta = self.project.data["stems"].get(sid)
        if meta and self.project.path(meta["file"]).exists():
            return WavReader(self.project.path(meta["file"])).read_all()
        return None


def overall_percent(step: str, frac: float) -> int:
    total = sum(w for _, _, w in STEPS)
    done = 0.0
    for sid, _, w in STEPS:
        if sid == step:
            done += w * frac
            break
        done += w
    return int(round(100 * done / total))


def run_all(project: Project, options: Options, progress=None, cancel_event=None) -> Project:
    """Executa todas as etapas. Etapas já concluídas são reaproveitadas (retomada de projeto)."""
    ctx = _Ctx(project, progress, cancel_event)
    data = project.data
    base = slugify(project.name)

    # 1. importar e conferir o arquivo ------------------------------------------
    ctx.step = "import"
    ctx.progress(0.0, "Lendo o arquivo")
    if not (project.is_done("import") and project.playback_original.exists()):
        audio, info = load(project.original_path)
        if info["duration"] < 1.0:
            raise JFDError("O áudio é curto demais para ser analisado (menos de 1 segundo).")
        if info["duration"] > MAX_MINUTES * 60:
            raise JFDError(f"Por enquanto o JFD trabalha com músicas de até {MAX_MINUTES} minutos.")
        save_wav(project.playback_original, audio, SR, bits=24)
        data["info"] = info
        ctx._audio = audio
        project.mark("import")

    # 2. qualidade ------------------------------------------------------------------
    ctx.step = "quality"
    ctx.progress(0.0)
    if not project.is_done("quality"):
        data["quality"] = analysis.quality_report(ctx.audio(), SR)
        project.mark("quality")

    # 3. andamento, tonalidade e acordes --------------------------------------------
    ctx.step = "music"
    ctx.progress(0.0)
    if not project.is_done("music"):
        data["music"] = analysis.musical_analysis(ctx.audio(), SR)
        project.mark("music")

    # 4. separação -------------------------------------------------------------------
    ctx.step = "separate"
    ctx.progress(0.0)
    if options.separate and not project.is_done("separate"):
        ok, why = sep.available()
        if not ok:
            project.notice("A separação de instrumentos não está disponível nesta instalação. " + why)
        else:
            result = sep.separate(ctx.audio(), SR, options.profile, progress=ctx.progress, cancel=ctx.cancel)
            data["stems"] = {}
            for sid, arr in result["stems"].items():
                rel = f"instrumentos/{base}_{sid}.wav"
                save_wav(project.path(rel), arr, SR, bits=24)
                data["stems"][sid] = dict(result["report"][sid], file=rel)
            ctx.stems = result["stems"]
            data["separation_model"] = result["model"]
            project.mark("separate")

    # 5. remasterização --------------------------------------------------------------
    ctx.step = "remaster"
    ctx.progress(0.0)
    if options.remaster and not (project.is_done("remaster") and data["remaster"].get("mode") == options.remaster_mode):
        do_remaster(project, options.remaster_mode, audio=ctx.audio(), progress=ctx.progress, cancel=ctx.cancel)

    # 6. transcrição -----------------------------------------------------------------
    ctx.step = "transcribe"
    ctx.progress(0.0)
    if options.scores and not project.is_done("transcribe"):
        present = [s for s in ("vocals", "bass", "other", "guitar", "piano")
                   if data["stems"].get(s, {}).get("present")]
        sources = present or ["mix"]
        music = data["music"]
        data["lines"] = {}
        for i, sid in enumerate(sources):
            name, fmin, fmax, inst, note = LINE_SOURCES[sid]
            ctx.progress(i / len(sources), f"Reconhecendo as notas: {name}")
            src = ctx.audio() if sid == "mix" else ctx.stem(sid)
            if src is None:
                continue
            res = tr.transcribe_mono(src, SR, fmin=fmin, fmax=fmax, cancel=ctx.cancel)
            if len(res["notes"]) < 4:
                continue
            quant = tr.quantize(res["notes"], music["beats"], music["beats_per_bar"], music["downbeat"])
            data["lines"][sid] = {"id": sid, "name": name, "notes": res["notes"], "quant": quant,
                                  "confidence": res["confidence"], "tuning_cents": res["tuning_cents"],
                                  "instrument": inst, "note": note}
        if not data["lines"]:
            project.notice("Não foi possível reconhecer uma melodia clara nesta gravação.")
        project.mark("transcribe")
    ctx.stems = {}
    gc.collect()

    # 7. partituras ------------------------------------------------------------------
    ctx.step = "scores"
    ctx.progress(0.0)
    if options.scores and not project.is_done("scores"):
        data["scores"] = []
        lines = [ln for ln in data["lines"].values() if ln["confidence"] >= MIN_AUTO_SCORE_CONFIDENCE]
        if len(lines) < len(data["lines"]):
            project.notice("Algumas melodias foram reconhecidas com pouca confiança e não viraram partitura "
                           "automaticamente; elas podem ser geradas na aba Partituras.")
        for i, line in enumerate(lines):
            ctx.progress(i / max(1, len(lines)), f"Criando partitura: {line['name']}")
            try:
                make_score(project, line["id"], line["instrument"], concert_pitch=False, save=False)
            except Exception:
                log.exception("Falha ao criar partitura de %s", line["id"])
                project.notice(f"Não foi possível criar a partitura de {line['name']}.")
        project.mark("scores")

    # 8. MIDI ------------------------------------------------------------------------
    ctx.step = "midi"
    ctx.progress(0.0)
    if options.scores and not project.is_done("midi"):
        make_midi(project)
        project.mark("midi")

    # 9. organizar -------------------------------------------------------------------
    ctx.step = "organize"
    ctx.progress(0.2)
    write_reports(project)
    ctx.progress(1.0, "Pronto")
    project.mark("pipeline")
    return project


def do_remaster(project: Project, mode: str, audio: np.ndarray | None = None, progress=None, cancel=None) -> dict:
    """Cria (ou recria em outro modo) a cópia remasterizada. O original nunca é alterado."""
    if audio is None:
        audio = WavReader(project.playback_original).read_all()
    quality = project.data.get("quality") or analysis.quality_report(audio, SR)
    out, rep = rm.remaster(audio, SR, mode, report=quality, progress=progress, cancel=cancel)
    rel = f"remasterizado/{slugify(project.name)}_remasterizado.wav"
    save_wav(project.path(rel), out, SR, bits=24)
    project.data["remaster"] = {"file": rel, "mode": mode, "mode_label": rep.get("mode_label", mode),
                                "steps": rep["steps"],
                                "after": {k: rep["after"][k] for k in ("lufs", "peak_db", "noise_floor_db")},
                                "before": {k: rep["before"][k] for k in ("lufs", "peak_db", "noise_floor_db")}}
    project.mark("remaster")
    return project.data["remaster"]


def make_score(project: Project, line_id: str, instrument_id: str, concert_pitch: bool = False,
               save: bool = True) -> dict:
    """Gera MusicXML + PDF de uma linha melódica para o instrumento escolhido."""
    data = project.data
    line = data["lines"].get(line_id)
    if not line or not line["quant"]["notes"]:
        raise JFDError("Não há notas reconhecidas para esta faixa.")
    inst = INSTRUMENTS.get(instrument_id) or INSTRUMENTS["melodia"]
    music = data["music"]
    key = KeySig.from_dict(music.get("key"))
    title = project.name
    xml, info = musicxml.build_score(line["quant"]["notes"], line["quant"]["units_per_bar"], key, inst,
                                     title, music.get("bpm", 120.0), concert_pitch=concert_pitch)
    stem = f"{slugify(project.name)}_{line_id}_{inst.id}{'_concerto' if concert_pitch else ''}"
    xml_rel, pdf_rel = f"partituras/{stem}.musicxml", f"partituras/{stem}.pdf"
    musicxml.save_score(project.path(xml_rel), xml)
    ok, engine = render.render_pdf(project.path(xml_rel), project.path(pdf_rel))
    entry = {"line": line_id, "line_name": line["name"], "instrument": inst.id, "instrument_name": inst.name,
             "concert_pitch": concert_pitch, "musicxml": xml_rel, "pdf": pdf_rel if ok else "",
             "engine": engine if ok else "", "info": info}
    data["scores"] = [s for s in data["scores"] if s["musicxml"] != xml_rel] + [entry]
    if not ok:
        project.notice("O PDF das partituras não pôde ser gerado neste computador; os arquivos MusicXML "
                       "estão disponíveis e abrem no MuseScore.")
    if save:
        project.save()
    return entry


def make_midi(project: Project) -> list[str]:
    """Gera os MIDI: tempo real (alinhado ao áudio) e quantizado (igual à partitura)."""
    data = project.data
    music = data["music"]
    bpm = float(music.get("bpm", 120.0))
    key = KeySig.from_dict(music.get("key"))
    per_bar = int(music.get("beats_per_bar", 4))
    base = slugify(project.name)
    to_beats = lambda s: s * bpm / 60.0  # noqa: E731
    real, quant, files = [], [], []
    for line in data["lines"].values():
        inst = INSTRUMENTS.get(line["instrument"]) or INSTRUMENTS["melodia"]
        r = {"name": line["name"], "program": inst.program,
             "notes": [{"start": to_beats(n["start"]), "end": to_beats(n["end"]), "midi": n["midi"],
                        "velocity": n["velocity"]} for n in line["notes"]]}
        q = {"name": line["name"], "program": inst.program,
             "notes": [{"start": n["start"] / 4.0, "end": (n["start"] + n["dur"]) / 4.0, "midi": n["midi"],
                        "velocity": n["velocity"]} for n in line["quant"]["notes"]]}
        real.append(r)
        quant.append(q)
        for suffix, track in (("", r), ("_quantizado", q)):
            rel = f"midi/{base}_{line['id']}{suffix}.mid"
            midi.write_midi(project.path(rel), [track], bpm, per_bar, key.fifths, key.mode == "minor")
            files.append(rel)
    chords = midi.chord_track(music.get("chords", []), to_beats)
    if chords["notes"]:
        rel = f"midi/{base}_acordes.mid"
        midi.write_midi(project.path(rel), [chords], bpm, per_bar, key.fifths, key.mode == "minor")
        files.append(rel)
        real.append(chords)
    if real:
        rel = f"midi/{base}_todos.mid"
        midi.write_midi(project.path(rel), real, bpm, per_bar, key.fifths, key.mode == "minor")
        files.insert(0, rel)
    data["midi"] = files
    return files


def write_reports(project: Project) -> None:
    data = project.data
    info, q, m = data.get("info", {}), data.get("quality", {}), data.get("music", {})
    lines = ["JFD – Intelligent Audio Studio — Relatório da análise", f"Projeto: {project.name}",
             f"Criado em: {data.get('created', '')}", "",
             "ARQUIVO ORIGINAL",
             f"  Arquivo: {info.get('file', '')}",
             f"  Formato: {info.get('codec', '')}, {info.get('sample_rate', 0)} Hz, {info.get('channels', 0)} canal(is)",
             f"  Duração: {int(info.get('duration', 0) // 60)} min {int(info.get('duration', 0) % 60)} s", ""]
    if q:
        lines += ["QUALIDADE SONORA",
                  f"  Volume percebido: {q['lufs']:.1f} LUFS   Pico: {q['peak_db']:.1f} dBFS",
                  f"  Ruído de fundo estimado: {q['noise_floor_db']:.1f} dBFS"]
        lines += [f"  Problema: {p}" for p in q.get("problems", [])] or ["  Nenhum problema evidente."]
        lines.append("")
    if m:
        lines += ["ANÁLISE MUSICAL",
                  f"  Andamento: {m['bpm']} BPM (confiança {int(m['bpm_confidence'] * 100)}%)",
                  f"  Compasso provável: {m['time_signature']}",
                  f"  Tonalidade: {m['key_name']} (confiança {int(m['key_confidence'] * 100)}%)"]
        if m.get("secondary_keys"):
            lines.append("  Tonalidades secundárias: " + ", ".join(m["secondary_keys"]))
        lines.append("")
    if data.get("stems"):
        lines.append("INSTRUMENTOS SEPARADOS")
        for s in data["stems"].values():
            state = "presente" if s["present"] else "não encontrado"
            lines.append(f"  {s['name']}: {state} ({s['presence_db']} dB em relação à mistura) — {s['note']}")
        lines.append("")
    if data.get("remaster", {}).get("steps"):
        lines.append(f"REMASTERIZAÇÃO (modo {data['remaster'].get('mode_label', '')})")
        lines += [f"  {s}" for s in data["remaster"]["steps"]]
        lines.append("")
    if data.get("lines"):
        lines.append("TRANSCRIÇÃO")
        for ln in data["lines"].values():
            lines.append(f"  {ln['name']}: {len(ln['notes'])} notas (confiança {int(ln['confidence'] * 100)}%)")
        lines.append("")
    if data.get("notices"):
        lines.append("AVISOS")
        lines += [f"  {n}" for n in data["notices"]]
        lines.append("")
    lines.append("A separação e a transcrição são automáticas e podem conter erros; confira as partituras antes de usar.")
    project.path("analise/relatorio.txt").write_text("\n".join(lines), encoding="utf-8")
    with open(project.path("analise/harmonia.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Compasso", "Início (s)", "Fim (s)", "Acorde", "Confiança"])
        for c in m.get("chords", []):
            w.writerow([c["bar"], f"{c['start']:.2f}", f"{c['end']:.2f}", c["chord"], f"{c['confidence']:.2f}"])


def export_zip(project: Project, dest: str | Path, progress=None) -> Path:
    """BAIXAR TUDO: empacota os resultados do projeto em um ZIP."""
    dest = Path(dest)
    files = [f for sub in ("original", "remasterizado", "instrumentos", "partituras", "midi", "analise")
             for f in sorted((project.folder / sub).rglob("*")) if f.is_file()]
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as z:
        for i, f in enumerate(files):
            if progress:
                progress(i / max(1, len(files)), f.name)
            z.write(f, f.relative_to(project.folder))
    return dest
