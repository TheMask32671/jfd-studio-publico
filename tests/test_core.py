"""Testes do núcleo com áudio sintético (não dependem de modelos de IA nem de interface)."""
import struct
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from jfd.core import analysis, midi, musicxml, remaster, transcribe
from jfd.core.audio_io import SR, WavReader, load, peaks, save_wav
from jfd.core.theory import INSTRUMENTS, KeySig, written_key, written_midi


def tone(freq, dur, sr=SR, harmonics=(1.0, 0.5, 0.25, 0.12)):
    t = np.arange(int(dur * sr)) / sr
    x = sum(a * np.sin(2 * np.pi * freq * (i + 1) * t) for i, a in enumerate(harmonics))
    env = np.minimum(1.0, t / 0.01) * np.minimum(1.0, (dur - t) / 0.03)
    return (x * env).astype(np.float32)


def midi_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def make_song(bpm=120.0, bars=8, melody=(60, 62, 64, 65, 67, 65, 64, 62), with_drums=True, seed=0):
    """Melodia em colcheias + bumbo nos tempos + acordes C | Am | F | G."""
    beat = 60.0 / bpm
    n = int(bars * 4 * beat * SR) + SR
    x = np.zeros(n, dtype=np.float32)
    truth = []
    for i in range(bars * 8):
        m = melody[i % len(melody)]
        s = i * beat / 2
        seg = tone(midi_hz(m), beat / 2 * 0.9) * 0.3
        a = int(s * SR)
        x[a:a + len(seg)] += seg
        truth.append((s, m))
    chords = [(48, 52, 55), (45, 48, 52), (41, 45, 48), (43, 47, 50)]
    for b in range(bars):
        for m in chords[b % 4]:
            seg = tone(midi_hz(m), 4 * beat * 0.98, harmonics=(1.0, 0.3)) * 0.12
            a = int(b * 4 * beat * SR)
            x[a:a + len(seg)] += seg
        for k in range(4):                       # baixo tocando a fundamental em semínimas
            seg = tone(midi_hz(chords[b % 4][0] - 12), beat * 0.9, harmonics=(1.0, 0.6, 0.3)) * 0.25
            a = int((b * 4 + k) * beat * SR)
            x[a:a + len(seg)] += seg
    if with_drums:
        t = np.arange(int(0.12 * SR)) / SR
        for i in range(bars * 4):
            kick = np.sin(2 * np.pi * (55 + 60 * np.exp(-t * 40)) * t) * np.exp(-t * 30) * (0.7 if i % 4 == 0 else 0.4)
            a = int(i * beat * SR)
            x[a:a + len(kick)] += kick.astype(np.float32)
    rng = np.random.default_rng(seed)
    x += rng.normal(0, 0.0005, n).astype(np.float32)
    return np.stack([x, x], axis=1) * 0.6, truth


def make_melody(truth, n, dur=0.25):
    """Só a melodia (como uma faixa já separada)."""
    solo = np.zeros((n, 2), dtype=np.float32)
    for s, mnote in truth:
        seg = tone(midi_hz(mnote), dur * 0.9) * 0.3
        a = int(s * SR)
        solo[a:a + len(seg)] += seg[:, None]
    return solo


# ---------------------------------------------------------------- áudio / arquivos
def test_wav_roundtrip(tmp_path):
    audio, _ = make_song(bars=1)
    for bits in (16, 24):
        p = tmp_path / f"t{bits}.wav"
        save_wav(p, audio, SR, bits=bits)
        r = WavReader(p)
        assert (r.sr, r.channels, r.bits, r.frames) == (SR, 2, bits, len(audio))
        back = r.read_all()
        assert np.abs(back - audio).max() < (3e-4 if bits == 16 else 1e-5)
        part = r.read(len(audio) - 10, 50)       # leitura além do fim completa com silêncio
        assert part.shape == (50, 2) and np.all(part[10:] == 0)
    loaded, info = load(tmp_path / "t24.wav")
    assert info["sample_rate"] == SR and info["channels"] == 2
    assert abs(len(loaded) - len(audio)) < 8 and np.abs(loaded[:1000] - audio[:1000]).max() < 1e-4
    assert peaks(audio, 100).shape == (100, 2)


# ---------------------------------------------------------------- teoria
def test_key_signatures_and_spelling():
    assert KeySig(0, "major").fifths == 0
    assert KeySig(7, "major").fifths == 1            # Sol maior
    assert KeySig(5, "major").fifths == -1           # Fá maior
    assert KeySig(9, "minor").fifths == 0            # Lá menor
    assert KeySig(3, "major").fifths == -3           # Mi bemol maior
    assert KeySig(3, "major").name_pt() == "Mi♭ maior"
    assert KeySig(0).spell_midi(61) == ("D", -1, 4)  # em Dó maior, usa Ré bemol
    assert KeySig(0).spell_midi(66) == ("F", 1, 4)   # e Fá sustenido
    assert KeySig(9, "minor").spell_midi(68) == ("G", 1, 4)   # sensível de Lá menor
    assert KeySig(6, "major").spell_midi(65) == ("E", 1, 4)   # Mi# em Fá# maior
    assert KeySig(1, "major").spell_midi(60) == ("C", 0, 4)   # Ré bemol maior: Dó natural
    assert KeySig(1, "major").spell_midi(59) == ("C", -1, 4)  # Dó bemol 4 = Si 3


def test_transposition():
    alto, trumpet, tenor, bari = (INSTRUMENTS[k] for k in ("sax_alto", "trompete_bb", "sax_tenor", "sax_baritono"))
    # Dó de concerto soa quando o sax alto lê Lá uma sexta maior acima
    assert written_midi(60, alto) == 69
    assert written_midi(60, trumpet) == 62
    assert written_midi(60, tenor) == 74
    assert written_midi(60, bari) == 81
    assert written_midi(60, INSTRUMENTS["clarinete_eb"]) == 57
    assert written_midi(60, INSTRUMENTS["trompa"]) == 67
    # tonalidades: Si♭ maior de concerto -> Dó maior para trompete, Sol maior para sax alto
    concert = KeySig(10, "major")
    assert written_key(concert, trumpet).name_pt() == "Dó maior"
    assert written_key(concert, alto).name_pt() == "Sol maior"
    # Mi maior (4#) para sax alto daria 7 sustenidos: vira Ré bemol maior (5 bemóis)
    assert written_key(KeySig(4, "major"), alto).fifths == -5
    # acidentes transpostos corretamente: Fá# de concerto em Dó maior -> Ré# para sax alto (em Lá maior)
    wk = written_key(KeySig(0), alto)
    assert wk.name_pt() == "Lá maior"
    assert wk.spell_midi(written_midi(66, alto)) == ("D", 1, 5)
    # tom de concerto mantém só a oitava do baixo
    assert written_midi(40, INSTRUMENTS["baixo"], concert_pitch=True) == 52
    assert written_midi(60, alto, concert_pitch=True) == 60


# ---------------------------------------------------------------- análise
def test_quality_and_loudness():
    audio, _ = make_song(bars=4)
    rep = analysis.quality_report(audio, SR)
    assert -40 < rep["lufs"] < -5 and rep["hum_hz"] == 0 and rep["clip_ratio"] == 0
    # um seno de 997 Hz a -20 dBFS (pico) em estéreo mede cerca de -20 LUFS
    t = np.arange(SR * 5) / SR
    s = (10 ** (-20 / 20) * np.sin(2 * np.pi * 997 * t)).astype(np.float32)
    assert abs(analysis.loudness_lufs(np.stack([s, s], 1), SR) - (-20.0)) < 0.6
    hum = audio.copy()
    for h in (1, 2, 3):
        hum[:, 0] += 0.03 * np.sin(2 * np.pi * 60 * h * np.arange(len(hum)) / SR).astype(np.float32)
        hum[:, 1] += 0.03 * np.sin(2 * np.pi * 60 * h * np.arange(len(hum)) / SR).astype(np.float32)
    assert analysis.quality_report(hum, SR)["hum_hz"] == 60


@pytest.mark.parametrize("bpm", [90.0, 120.0, 140.0])
def test_tempo_key_chords(bpm):
    audio, _ = make_song(bpm=bpm, bars=12)
    m = analysis.musical_analysis(audio, SR)
    assert abs(m["bpm"] - bpm) < 2.0, m["bpm"]
    assert m["key_name"] in ("Dó maior", "Lá menor"), m["key_name"]
    beats = np.array(m["beats"])
    grid = np.round(beats / (60 / bpm)) * (60 / bpm)
    assert np.median(np.abs(beats - grid)) < 0.035
    names = [c["chord"] for c in m["chords"]]
    assert len(names) >= 8
    expected = ["C", "Am", "F", "G"]
    hits = sum(1 for c in m["chords"] if c["chord"].rstrip("7").replace("maj", "").replace("m", "m") == expected[(c["bar"] - 1) % 4])
    assert hits / len(names) > 0.8, names


# ---------------------------------------------------------------- transcrição
def test_transcription_and_quantize():
    bpm = 120.0
    audio, truth = make_song(bpm=bpm, bars=4, with_drums=False)
    solo = make_melody(truth, len(audio))        # só a melodia, como uma faixa separada
    res = transcribe.transcribe_mono(solo, SR)
    notes = res["notes"]
    assert abs(len(notes) - len(truth)) <= 2, (len(notes), len(truth))
    ok = 0
    for s, mnote in truth:
        near = [n for n in notes if abs(n["start"] - s) < 0.06]
        ok += bool(near and near[0]["midi"] == mnote)
    assert ok / len(truth) > 0.9
    beats = [i * 60 / bpm for i in range(17)]
    q = transcribe.quantize(notes, beats, 4, 0)
    assert q["units_per_bar"] == 16
    starts = [n["start"] for n in q["notes"]]
    assert starts[:4] == [0, 2, 4, 6] and all(n["dur"] >= 1 for n in q["notes"])
    low = transcribe.transcribe_mono(np.stack([tone(41.2, 1.0)] * 2, 1), SR, fmin=30, fmax=400)
    assert low["notes"] and low["notes"][0]["midi"] == 28      # Mi grave do baixo


# ---------------------------------------------------------------- partitura / MIDI
def _durations(xml):
    root = ET.fromstring(xml.split("\n", 2)[2])
    return root, [sum(int(n.find("duration").text) for n in m.findall("note")) for m in root.iter("measure")]


def test_musicxml_structure_and_transposition():
    q = [{"start": 0, "dur": 4, "midi": 60}, {"start": 4, "dur": 2, "midi": 62}, {"start": 6, "dur": 1, "midi": 64},
         {"start": 7, "dur": 1, "midi": 66}, {"start": 10, "dur": 12, "midi": 67}, {"start": 24, "dur": 3, "midi": 70},
         {"start": 27, "dur": 5, "midi": 72}]
    xml, info = musicxml.build_score(q, 16, KeySig(0), INSTRUMENTS["melodia"], "Teste", 100)
    root, durs = _durations(xml)
    assert durs == [16, 16] and info["measures"] == 2
    steps = [(n.find("pitch/step").text, n.findtext("pitch/alter")) for n in root.iter("note") if n.find("pitch") is not None]
    assert ("F", "1") in steps and ("B", "-1") in steps
    accs = [n.findtext("accidental") for n in root.iter("note") if n.find("accidental") is not None]
    assert accs == ["sharp", "flat"]
    ties = [t.get("type") for t in root.iter("tie")]
    assert ties.count("start") == ties.count("stop") and ties
    assert root.find("part/measure/attributes/transpose") is None

    xml_a, info_a = musicxml.build_score(q, 16, KeySig(0), INSTRUMENTS["sax_alto"], "Teste", 100, auto_octave=False)
    root_a, durs_a = _durations(xml_a)
    assert durs_a == [16, 16]
    assert root_a.findtext("part/measure/attributes/key/fifths") == "3"
    assert root_a.findtext("part/measure/attributes/transpose/chromatic") == "-9"
    assert root_a.findtext("part/measure/attributes/transpose/diatonic") == "-5"
    first = root_a.find("part/measure/note/pitch")
    assert (first.findtext("step"), first.findtext("octave")) == ("A", "4")
    # Fá# de concerto vira Ré# escrito: precisa de acidente explícito em Lá maior
    assert "sharp" in [n.findtext("accidental") for n in root_a.iter("note") if n.find("accidental") is not None]

    xml_t, _ = musicxml.build_score(q, 16, KeySig(0), INSTRUMENTS["sax_tenor"], "Teste", 100, auto_octave=False)
    root_t, _ = _durations(xml_t)
    tr = root_t.find("part/measure/attributes/transpose")
    assert (tr.findtext("diatonic"), tr.findtext("chromatic"), tr.findtext("octave-change")) == ("-1", "-2", "-1")
    xml_c, info_c = musicxml.build_score(q, 16, KeySig(0), INSTRUMENTS["sax_alto"], "Teste", 100, concert_pitch=True)
    assert info_c["written_key"] == "Dó maior" and ET.fromstring(xml_c.split("\n", 2)[2]).find("part/measure/attributes/transpose") is None
    xml3, _ = musicxml.build_score([{"start": 0, "dur": 12, "midi": 60}, {"start": 12, "dur": 5, "midi": 62}], 12,
                                   KeySig(7), INSTRUMENTS["flauta"], "Valsa", 90)
    assert _durations(xml3)[1] == [12, 12]


def test_split_span_is_exact():
    for upb in (12, 16):
        for a in range(upb):
            for b in range(a + 1, upb + 1):
                parts = musicxml.split_span(a, b, upb)
                assert parts[0][0] == a and sum(p[1] for p in parts) == b - a


def _parse_midi(data):
    assert data[:4] == b"MThd"
    fmt, ntracks, ppq = struct.unpack(">HHH", data[8:14])
    pos, tracks = 14, []
    for _ in range(ntracks):
        assert data[pos:pos + 4] == b"MTrk"
        size = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        assert body[-3:] == b"\xff\x2f\x00"
        tracks.append(body)
        pos += 8 + size
    assert pos == len(data)
    return fmt, ppq, tracks


def test_midi_file(tmp_path):
    notes = [{"start": 0.0, "end": 1.0, "midi": 60, "velocity": 90}, {"start": 1.0, "end": 1.5, "midi": 64, "velocity": 80}]
    p = tmp_path / "t.mid"
    midi.write_midi(p, [{"name": "Voz", "program": 52, "notes": notes},
                        midi.chord_track([{"start": 0, "end": 2, "root": 0, "kind": "m7", "chord": "Cm7"}], lambda s: s * 2)],
                    120.0, 4, -3, True)
    fmt, ppq, tracks = _parse_midi(p.read_bytes())
    assert (fmt, ppq, len(tracks)) == (1, 480, 3)
    assert b"\xff\x51\x03\x07\xa1\x20" in tracks[0]            # 120 BPM = 500000 µs
    assert tracks[1].count(b"\x90\x3c") == 1 and b"\xc0\x34" in tracks[1]
    assert tracks[2].count(b"\x91") == 4                        # quatro notas do acorde no canal 2


# ---------------------------------------------------------------- remasterização
@pytest.mark.parametrize("mode", ["natural", "profissional", "estudio"])
def test_remaster_modes(mode):
    audio, _ = make_song(bars=6)
    quiet = audio * 0.1
    quiet[:, 1] *= 0.6                                         # lado direito mais baixo
    n = np.arange(len(quiet)) / SR
    quiet += (0.004 * np.sin(2 * np.pi * 60 * n) + 0.003 * np.sin(2 * np.pi * 120 * n) + 0.002 * np.sin(2 * np.pi * 180 * n))[:, None].astype(np.float32)
    quiet[SR * 2 + 100, 0] += 0.5                               # um estalo
    out, rep = remaster.remaster(quiet, SR, mode)
    assert out.shape == quiet.shape and out.dtype == np.float32 and np.isfinite(out).all()
    assert np.abs(out).max() <= remaster.CEILING + 1e-6
    target = remaster.MODES[mode]["target_lufs"]
    assert rep["after"]["lufs"] > rep["before"]["lufs"] + 6
    assert rep["after"]["lufs"] <= target + 1.5
    assert rep["after"]["hum_hz"] == 0
    assert abs(rep["after"]["balance_db"]) < abs(rep["before"]["balance_db"])
    assert any("estalo" in s for s in rep["steps"]) and any("Zumbido" in s for s in rep["steps"])


def test_noise_reduction_and_limiter():
    audio, _ = make_song(bars=4)
    rng = np.random.default_rng(1)
    noisy = audio * 0.5 + rng.normal(0, 0.01, audio.shape).astype(np.float32)
    den = remaster.reduce_noise(noisy, SR, -12.0)
    assert den.shape == noisy.shape
    gap = slice(int(SR * 8.3), int(SR * 8.9))                   # trecho só com ruído (fim da música)
    assert np.sqrt((den[gap] ** 2).mean()) < 0.6 * np.sqrt((noisy[gap] ** 2).mean())
    hot = audio * 4.0
    lim = remaster.limit(hot, SR)
    assert np.abs(lim).max() <= remaster.CEILING + 1e-6
    long = np.tile(audio, (4, 1))
    chunked = remaster.process_in_chunks(long, lambda b: b * 0.5, SR, seconds=5.0)
    assert np.allclose(chunked, long * 0.5, atol=1e-6)


# ---------------------------------------------------------------- pipeline completo
def test_pipeline_end_to_end(tmp_path, monkeypatch):
    import zipfile

    from jfd.core import pipeline, project as prj

    monkeypatch.setattr(prj, "projects_dir", lambda: tmp_path)
    audio, _ = make_song(bars=8)
    src = tmp_path / "Minha Música.wav"
    save_wav(src, audio, SR, bits=16)
    proj = prj.Project.create(src)
    seen = []
    opts = dict(separate=False, profile="leve", remaster_mode="natural")   # a separação real é coberta pelo autoteste
    pipeline.run_all(proj, pipeline.Options(**opts), progress=lambda step, frac, msg: seen.append(step))
    d = prj.Project.load(proj.folder).data
    assert d["done"]["pipeline"] and d["music"]["key_name"] and d["remaster"]["file"]
    assert seen[0] == "import" and seen[-1] == "organize"
    assert proj.path(d["remaster"]["file"]).exists() and proj.playback_original.exists()
    assert d["lines"], "deveria reconhecer ao menos uma linha melódica"
    line = next(iter(d["lines"].values()))
    assert d["scores"] and proj.path(d["scores"][0]["musicxml"]).exists()
    assert any(f.endswith("_todos.mid") for f in d["midi"]) and all(proj.path(f).exists() for f in d["midi"])
    assert "Tonalidade" in proj.path("analise/relatorio.txt").read_text(encoding="utf-8")
    # nova partitura transposta para sax alto, a partir das notas já reconhecidas
    entry = pipeline.make_score(proj, line["id"], "sax_alto")
    assert entry["instrument_name"].startswith("Saxofone alto") and proj.path(entry["musicxml"]).exists()
    assert len(prj.Project.load(proj.folder).data["scores"]) == len(d["scores"]) + 1
    # retomada: rodar de novo não refaz as etapas concluídas
    before = proj.path(d["remaster"]["file"]).stat().st_mtime_ns
    pipeline.run_all(proj, pipeline.Options(**opts))
    assert proj.path(d["remaster"]["file"]).stat().st_mtime_ns == before
    z = pipeline.export_zip(proj, tmp_path / "tudo.zip")
    names = zipfile.ZipFile(z).namelist()
    assert any(n.startswith("original/") for n in names) and any(n.endswith(".musicxml") for n in names)
    assert prj.recent_projects()[0]["name"] == "Minha Música"
    # cancelamento
    import threading

    from jfd.core import Cancelled
    proj2 = prj.Project.create(src, "Outra")
    ev = threading.Event()
    ev.set()
    with pytest.raises(Cancelled):
        pipeline.run_all(proj2, pipeline.Options(), cancel_event=ev)
