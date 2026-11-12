"""Gera partituras em MusicXML a partir das notas já encaixadas na grade rítmica."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .theory import Instrument, KeySig, fit_octave, written_key, written_midi

DIVISIONS = 4  # unidades por semínima (semicolcheia = 1)
# (duração em unidades, figura, pontos)
FIGURES = [(16, "whole", 0), (12, "half", 1), (8, "half", 0), (6, "quarter", 1),
           (4, "quarter", 0), (3, "eighth", 1), (2, "eighth", 0), (1, "16th", 0)]
ACCIDENTAL = {-2: "flat-flat", -1: "flat", 0: "natural", 1: "sharp", 2: "double-sharp"}
CLEFS = {"G": ("G", 2), "F": ("F", 4), "C": ("C", 3)}


def _can_start(d: int, a: int, upb: int) -> bool:
    if d == 16:
        return a == 0 and upb == 16
    if d == 12:
        return a == 0 and upb >= 12
    if d in (8, 6):
        return a % 8 == 0 if upb == 16 else a % 4 == 0
    if d in (4, 3):
        return a % 4 == 0
    if d == 2:
        return a % 2 == 0
    return True


def split_span(a: int, b: int, upb: int) -> list[tuple[int, int, str, int]]:
    """Divide [a, b) (dentro de um compasso) em figuras legíveis: (início, duração, figura, pontos)."""
    out = []
    while a < b:
        for d, fig, dots in FIGURES:
            if a + d <= b and _can_start(d, a, upb):
                out.append((a, d, fig, dots))
                a += d
                break
    return out


def _sub(parent, tag, text=None, **attrs):
    el = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        el.text = str(text)
    return el


def build_score(qnotes: list[dict], units_per_bar: int, key: KeySig, inst: Instrument, title: str,
                bpm: float, concert_pitch: bool = False, auto_octave: bool = True) -> tuple[str, dict]:
    """Monta o MusicXML de uma parte. Retorna (texto XML, informações)."""
    upb = int(units_per_bar)
    beats = upb // DIVISIONS
    notes = sorted(({"start": int(n["start"]), "dur": int(n["dur"]), "midi": int(n["midi"])}
                    for n in qnotes if n["dur"] > 0), key=lambda n: n["start"])
    shift = fit_octave([n["midi"] for n in notes], inst) if auto_octave else 0
    wkey = written_key(key, inst, concert_pitch)
    for n in notes:
        n["written"] = written_midi(n["midi"] + shift, inst, concert_pitch)

    clef = inst.clef
    if concert_pitch and inst.chromatic % 12 != 0 and notes:
        ordered = sorted(n["written"] for n in notes)
        clef = "F" if ordered[len(ordered) // 2] < 55 else "G"

    end = max((n["start"] + n["dur"] for n in notes), default=upb)
    n_measures = max(1, -(-end // upb))
    # segmentos por compasso: (início, fim, nota ou None, liga_de_antes, liga_para_depois)
    measures: list[list[tuple]] = [[] for _ in range(n_measures)]
    cursor = 0
    for n in notes:
        s, e = max(n["start"], cursor), n["start"] + n["dur"]
        if e <= s:
            continue
        if s > cursor:
            _fill(measures, cursor, s, None, upb)
        _fill(measures, s, e, n["written"], upb)
        cursor = e
    if cursor < n_measures * upb:
        _fill(measures, cursor, n_measures * upb, None, upb)

    root = ET.Element("score-partwise", version="3.1")
    _sub(_sub(root, "work"), "work-title", title)
    ident = _sub(root, "identification")
    _sub(_sub(ident, "encoding"), "software", "JFD – Intelligent Audio Studio")
    plist = _sub(root, "part-list")
    sp = _sub(plist, "score-part", id="P1")
    _sub(sp, "part-name", inst.name + (" – tom de concerto" if concert_pitch and inst.chromatic % 12 else ""))
    _sub(_sub(sp, "score-instrument", id="P1-I1"), "instrument-name", inst.name)
    mi = _sub(sp, "midi-instrument", id="P1-I1")
    _sub(mi, "midi-channel", 1)
    _sub(mi, "midi-program", inst.program + 1)
    part = _sub(root, "part", id="P1")

    for mi_idx, segs in enumerate(measures):
        m = _sub(part, "measure", number=mi_idx + 1)
        if mi_idx == 0:
            at = _sub(m, "attributes")
            _sub(at, "divisions", DIVISIONS)
            k = _sub(at, "key")
            _sub(k, "fifths", wkey.fifths)
            _sub(k, "mode", wkey.mode)
            t = _sub(at, "time")
            _sub(t, "beats", beats)
            _sub(t, "beat-type", 4)
            c = _sub(at, "clef")
            _sub(c, "sign", CLEFS[clef][0])
            _sub(c, "line", CLEFS[clef][1])
            chrom = 0 if concert_pitch and inst.chromatic % 12 else -inst.chromatic
            diat = 0 if concert_pitch and inst.chromatic % 12 else -inst.diatonic
            if chrom:
                sign = -1 if chrom < 0 else 1
                octs = (abs(chrom) // 12) * sign
                tr = _sub(at, "transpose")
                _sub(tr, "diatonic", diat - 7 * octs)
                _sub(tr, "chromatic", chrom - 12 * octs)
                if octs:
                    _sub(tr, "octave-change", octs)
            d = _sub(m, "direction", placement="above")
            met = _sub(_sub(d, "direction-type"), "metronome")
            _sub(met, "beat-unit", "quarter")
            _sub(met, "per-minute", int(round(bpm)))
            _sub(d, "sound", tempo=int(round(bpm)))
        _write_measure(m, segs, upb, wkey)
        if mi_idx == n_measures - 1:
            _sub(_sub(m, "barline", location="right"), "bar-style", "light-heavy")

    ET.indent(root, space="  ")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN" '
           '"http://www.musicxml.org/dtds/partwise.dtd">\n' + ET.tostring(root, encoding="unicode") + "\n")
    out_of_range = sum(1 for n in notes if not (inst.low <= n["midi"] + shift <= inst.high))
    info = {"measures": n_measures, "notes": len(notes), "octave_shift": shift // 12,
            "written_key": wkey.name_pt(), "concert_key": key.name_pt(),
            "out_of_range": out_of_range, "clef": clef}
    return xml, info


def _fill(measures: list[list[tuple]], s: int, e: int, pitch, upb: int) -> None:
    """Distribui [s, e) pelos compassos, marcando as ligaduras entre os pedaços."""
    first = True
    while s < e:
        mi = s // upb
        stop = min(e, (mi + 1) * upb)
        if mi < len(measures):
            measures[mi].append((s - mi * upb, stop - mi * upb, pitch, not first, stop < e))
        s = stop
        first = False


def _write_measure(m, segs: list[tuple], upb: int, wkey: KeySig) -> None:
    if len(segs) == 1 and segs[0][2] is None and segs[0][0] == 0 and segs[0][1] == upb:
        note = _sub(m, "note")
        _sub(note, "rest", measure="yes")
        _sub(note, "duration", upb)
        _sub(note, "voice", 1)
        return
    # 1) decompõe em figuras
    pieces = []  # dict(start, dur, fig, dots, pitch, tie_in, tie_out)
    for (a, b, pitch, tied_in, tied_out) in segs:
        parts = split_span(a, b, upb)
        for i, (s, d, fig, dots) in enumerate(parts):
            pieces.append({"start": s, "dur": d, "fig": fig, "dots": dots, "pitch": pitch,
                           "tie_in": pitch is not None and (tied_in or i > 0),
                           "tie_out": pitch is not None and (tied_out or i < len(parts) - 1)})
    # 2) barras de ligação (colcheias e semicolcheias dentro do mesmo tempo)
    groups, cur = [], []
    for p in pieces:
        beamable = p["pitch"] is not None and p["dur"] <= 3
        if beamable and cur and cur[-1]["start"] // 4 == p["start"] // 4 and cur[-1]["start"] + cur[-1]["dur"] == p["start"]:
            cur.append(p)
        else:
            if len(cur) > 1:
                groups.append(cur)
            cur = [p] if beamable else []
    if len(cur) > 1:
        groups.append(cur)
    for g in groups:
        for i, p in enumerate(g):
            p["beam1"] = "begin" if i == 0 else ("end" if i == len(g) - 1 else "continue")
            if p["dur"] == 1:
                prev16 = i > 0 and g[i - 1]["dur"] == 1
                next16 = i < len(g) - 1 and g[i + 1]["dur"] == 1
                if prev16 and next16:
                    p["beam2"] = "continue"
                elif next16:
                    p["beam2"] = "begin"
                elif prev16:
                    p["beam2"] = "end"
                else:
                    p["beam2"] = "forward hook" if i == 0 else "backward hook"
    # 3) escreve as notas, controlando os acidentes do compasso
    state: dict[tuple[str, int], int] = {}
    for p in pieces:
        note = _sub(m, "note")
        if p["pitch"] is None:
            _sub(note, "rest")
            _sub(note, "duration", p["dur"])
            _sub(note, "voice", 1)
            _sub(note, "type", p["fig"])
            for _ in range(p["dots"]):
                _sub(note, "dot")
            continue
        step, alter, octave = wkey.spell_midi(p["pitch"])
        pe = _sub(note, "pitch")
        _sub(pe, "step", step)
        if alter:
            _sub(pe, "alter", alter)
        _sub(pe, "octave", octave)
        _sub(note, "duration", p["dur"])
        if p["tie_in"]:
            _sub(note, "tie", type="stop")
        if p["tie_out"]:
            _sub(note, "tie", type="start")
        _sub(note, "voice", 1)
        _sub(note, "type", p["fig"])
        for _ in range(p["dots"]):
            _sub(note, "dot")
        if not p["tie_in"]:
            current = state.get((step, octave), wkey.signature_alter(step))
            if alter != current:
                _sub(note, "accidental", ACCIDENTAL[alter])
            state[(step, octave)] = alter
        if "beam1" in p:
            _sub(note, "beam", p["beam1"], number=1)
        if "beam2" in p:
            _sub(note, "beam", p["beam2"], number=2)
        if p["tie_in"] or p["tie_out"]:
            nt = _sub(note, "notations")
            if p["tie_in"]:
                _sub(nt, "tied", type="stop")
            if p["tie_out"]:
                _sub(nt, "tied", type="start")


def save_score(path: str | Path, xml: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml, encoding="utf-8")
