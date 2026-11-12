"""Escrita de arquivos MIDI padrão (formato 1), sem dependências externas."""
from __future__ import annotations

import struct
from pathlib import Path

PPQ = 480


def _varlen(value: int) -> bytes:
    value = max(0, int(value))
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def _meta(kind: int, data: bytes) -> bytes:
    return bytes([0xFF, kind]) + _varlen(len(data)) + data


def _chunk(events: list[tuple[int, int, bytes]]) -> bytes:
    """events: (tick absoluto, prioridade, bytes). Converte para deltas e fecha a trilha."""
    events = sorted(events, key=lambda e: (e[0], e[1]))
    body, last = b"", 0
    for tick, _, data in events:
        body += _varlen(tick - last) + data
        last = tick
    body += _varlen(0) + _meta(0x2F, b"")
    return b"MTrk" + struct.pack(">I", len(body)) + body


def write_midi(path: str | Path, tracks: list[dict], bpm: float, beats_per_bar: int = 4,
               key_fifths: int = 0, minor: bool = False) -> None:
    """Grava um MIDI com uma trilha por instrumento.

    Cada trilha: {"name", "program", "channel" (opcional), "notes": [{start, end, midi, velocity}]}
    com ``start``/``end`` em tempos musicais (semínimas) contados do início.
    """
    bpm = max(20.0, min(300.0, float(bpm)))
    tempo = int(round(60_000_000 / bpm))
    conductor = [
        (0, 0, _meta(0x51, struct.pack(">I", tempo)[1:])),
        (0, 0, _meta(0x58, bytes([beats_per_bar, 2, 24, 8]))),
        (0, 0, _meta(0x59, struct.pack("bB", max(-7, min(7, key_fifths)), 1 if minor else 0))),
    ]
    chunks = [_chunk(conductor)]
    melodic = 0
    for tr in tracks:
        ch = tr.get("channel")
        if ch is None:
            ch = melodic if melodic < 9 else melodic + 1   # pula o canal 10 (bateria)
            melodic += 1
        ch = int(ch) % 16
        ev = [(0, 0, _meta(0x03, tr.get("name", "Trilha").encode("utf-8"))),
              (0, 1, bytes([0xC0 | ch, int(tr.get("program", 0)) & 0x7F]))]
        for n in tr.get("notes", []):
            on = int(round(max(0.0, n["start"]) * PPQ))
            off = max(on + 1, int(round(max(0.0, n["end"]) * PPQ)))
            pitch = max(0, min(127, int(n["midi"])))
            vel = max(1, min(127, int(n.get("velocity", 90))))
            ev.append((on, 3, bytes([0x90 | ch, pitch, vel])))
            ev.append((off, 2, bytes([0x80 | ch, pitch, 0])))
        chunks.append(_chunk(ev))
    data = b"MThd" + struct.pack(">IHHH", 6, 1, len(chunks), PPQ) + b"".join(chunks)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


CHORD_INTERVALS = {"": [0, 4, 7], "m": [0, 3, 7], "7": [0, 4, 7, 10], "maj7": [0, 4, 7, 11],
                   "m7": [0, 3, 7, 10], "dim": [0, 3, 6]}


def chord_track(chords: list[dict], seconds_to_beats) -> dict:
    """Trilha de acordes em bloco a partir da análise harmônica."""
    notes = []
    for c in chords:
        if c.get("root", -1) < 0 or c.get("chord") == "N.C.":
            continue
        s, e = seconds_to_beats(c["start"]), seconds_to_beats(c["end"])
        root = 48 + c["root"]
        for iv in CHORD_INTERVALS.get(c.get("kind", ""), [0, 4, 7]):
            notes.append({"start": s, "end": e, "midi": root + iv, "velocity": 70})
    return {"name": "Acordes", "program": 0, "notes": notes}
