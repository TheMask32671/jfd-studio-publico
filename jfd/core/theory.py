"""Teoria musical: tonalidades, grafia correta das notas, instrumentos e transposição.

A grafia usa a "linha de quintas" (… Si♭ Fá Dó Sol Ré Lá Mi Si Fá♯ …, com Dó = 0).
Cada tonalidade enxerga 12 posições consecutivas dessa linha, o que define de forma
única se uma nota é escrita como sustenido ou bemol. Transpor uma tonalidade é somar
um deslocamento nessa linha, por isso notas e acidentes saem corretos por construção.
"""
from __future__ import annotations

from dataclasses import dataclass

LETTERS = "FCGDAEB"
STEP_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
PT_NOTE = {"C": "Dó", "D": "Ré", "E": "Mi", "F": "Fá", "G": "Sol", "A": "Lá", "B": "Si"}
ACC_TXT = {-2: "bb", -1: "b", 0: "", 1: "#", 2: "##"}
ACC_PT = {-2: "♭♭", -1: "♭", 0: "", 1: "♯", 2: "♯♯"}


def lof_to_name(pos: int) -> tuple[str, int]:
    """Posição na linha de quintas -> (letra, alteração)."""
    return LETTERS[(pos + 1) % 7], (pos + 1) // 7


def lof_pc(pos: int) -> int:
    return (7 * pos) % 12


@dataclass(frozen=True)
class KeySig:
    tonic_pc: int          # 0 = Dó … 11 = Si
    mode: str = "major"    # "major" ou "minor"

    @property
    def fifths(self) -> int:
        """Número de sustenidos (+) ou bemóis (−) da armadura, entre −6 e +6."""
        rel_major = self.tonic_pc if self.mode == "major" else (self.tonic_pc + 3) % 12
        pos = (rel_major * 7) % 12          # 0..11
        return pos if pos <= 6 else pos - 12

    @classmethod
    def from_fifths(cls, fifths: int, mode: str) -> "KeySig":
        while fifths > 6:
            fifths -= 12
        while fifths < -6:
            fifths += 12
        rel_major = lof_pc(fifths)
        return cls(rel_major if mode == "major" else (rel_major + 9) % 12, mode)

    def _window(self) -> range:
        k = self.fifths
        return range(k - 5, k + 7) if self.mode == "major" else range(k - 2, k + 10)

    def spell_pc(self, pc: int) -> tuple[str, int]:
        for pos in self._window():
            if lof_pc(pos) == pc % 12:
                return lof_to_name(pos)
        raise AssertionError("classe de altura sem grafia")

    def spell_midi(self, midi: int) -> tuple[str, int, int]:
        """Nota MIDI -> (letra, alteração, oitava) na grafia desta tonalidade."""
        step, alter = self.spell_pc(midi % 12)
        octave = (midi - alter - STEP_SEMITONE[step]) // 12 - 1
        return step, alter, octave

    def signature_alter(self, step: str) -> int:
        """Alteração que a armadura impõe a uma letra."""
        k = self.fifths
        if k > 0:
            return 1 if step in "FCGDAEB"[:k] else 0
        if k < 0:
            return -1 if step in "BEADGCF"[:-k] else 0
        return 0

    def transposed(self, fifths_shift: int) -> "KeySig":
        return KeySig.from_fifths(self.fifths + fifths_shift, self.mode)

    def tonic_name(self) -> tuple[str, int]:
        k = self.fifths
        return lof_to_name(k if self.mode == "major" else k + 3)

    def name_pt(self) -> str:
        step, alter = self.tonic_name()
        return f"{PT_NOTE[step]}{ACC_PT[alter]} {'maior' if self.mode == 'major' else 'menor'}"

    def name_short(self) -> str:
        step, alter = self.tonic_name()
        return f"{step}{ACC_TXT[alter]}{'' if self.mode == 'major' else 'm'}"

    def to_dict(self) -> dict:
        return {"tonic_pc": self.tonic_pc, "mode": self.mode}

    @classmethod
    def from_dict(cls, d: dict | None) -> "KeySig":
        d = d or {}
        return cls(int(d.get("tonic_pc", 0)), d.get("mode", "major"))


def chord_name(root_pc: int, kind: str, key: KeySig) -> str:
    step, alter = key.spell_pc(root_pc)
    return f"{step}{ACC_TXT[alter]}{kind}"


def note_name_pt(midi: int, key: KeySig | None = None) -> str:
    step, alter, octave = (key or KeySig(0)).spell_midi(midi)
    return f"{PT_NOTE[step]}{ACC_PT[alter]}{octave}"


# --------------------------------------------------------------------------
# Instrumentos
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Instrument:
    id: str
    name: str            # nome em português, como aparece na partitura
    tuning: str          # "C", "Bb", "Eb" ou "F"
    chromatic: int       # semitons: nota escrita − nota que soa
    diatonic: int        # graus:    nota escrita − nota que soa
    fifths: int          # deslocamento da armadura na linha de quintas
    clef: str            # "G", "F" ou "C" (clave de dó na 3ª linha)
    program: int         # timbre General MIDI (0..127)
    low: int             # extensão aproximada (notas que soam, em MIDI)
    high: int


def _i(id, name, tuning, chrom, diat, fifths, clef, program, low, high):
    return Instrument(id, name, tuning, chrom, diat, fifths, clef, program, low, high)


INSTRUMENTS: dict[str, Instrument] = {i.id: i for i in [
    # Dó (não transpositores; alguns escrevem uma oitava acima/abaixo)
    _i("melodia", "Melodia (Dó)", "C", 0, 0, 0, "G", 0, 40, 96),
    _i("voz", "Voz", "C", 0, 0, 0, "G", 52, 40, 88),
    _i("flauta", "Flauta", "C", 0, 0, 0, "G", 73, 60, 96),
    _i("flautim", "Flautim", "C", -12, -7, 0, "G", 72, 74, 108),
    _i("oboe", "Oboé", "C", 0, 0, 0, "G", 68, 58, 91),
    _i("fagote", "Fagote", "C", 0, 0, 0, "F", 70, 34, 75),
    _i("violino", "Violino", "C", 0, 0, 0, "G", 40, 55, 103),
    _i("viola", "Viola", "C", 0, 0, 0, "C", 41, 48, 91),
    _i("violoncelo", "Violoncelo", "C", 0, 0, 0, "F", 42, 36, 76),
    _i("piano", "Piano (melodia)", "C", 0, 0, 0, "G", 0, 21, 108),
    _i("violao", "Violão / Guitarra", "C", 12, 7, 0, "G", 24, 40, 88),
    _i("baixo", "Baixo elétrico", "C", 12, 7, 0, "F", 33, 28, 67),
    _i("contrabaixo", "Contrabaixo acústico", "C", 12, 7, 0, "F", 32, 28, 67),
    _i("trombone", "Trombone", "C", 0, 0, 0, "F", 57, 40, 77),
    _i("trombone_baixo", "Trombone baixo", "C", 0, 0, 0, "F", 57, 34, 70),
    _i("tuba", "Tuba em Dó", "C", 0, 0, 0, "F", 58, 28, 58),
    # Si bemol
    _i("trompete_bb", "Trompete em Si♭", "Bb", 2, 1, 2, "G", 56, 52, 84),
    _i("flugelhorn", "Flugelhorn em Si♭", "Bb", 2, 1, 2, "G", 56, 52, 82),
    _i("cornet", "Cornet em Si♭", "Bb", 2, 1, 2, "G", 56, 52, 84),
    _i("clarinete_bb", "Clarinete em Si♭", "Bb", 2, 1, 2, "G", 71, 50, 91),
    _i("sax_soprano", "Saxofone soprano (Si♭)", "Bb", 2, 1, 2, "G", 64, 56, 87),
    _i("sax_tenor", "Saxofone tenor (Si♭)", "Bb", 14, 8, 2, "G", 66, 44, 76),
    # Mi bemol
    _i("sax_alto", "Saxofone alto (Mi♭)", "Eb", 9, 5, 3, "G", 65, 49, 81),
    _i("sax_baritono", "Saxofone barítono (Mi♭)", "Eb", 21, 12, 3, "G", 67, 36, 69),
    _i("clarinete_eb", "Clarinete em Mi♭", "Eb", -3, -2, 3, "G", 71, 55, 94),
    # Fá
    _i("trompa", "Trompa em Fá", "F", 7, 4, 1, "G", 60, 34, 77),
]}

TUNING_LABEL = {"C": "Dó (C)", "Bb": "Si bemol (B♭)", "Eb": "Mi bemol (E♭)", "F": "Fá (F)"}


def instruments_by_tuning() -> dict[str, list[Instrument]]:
    groups: dict[str, list[Instrument]] = {"C": [], "Bb": [], "Eb": [], "F": []}
    for inst in INSTRUMENTS.values():
        groups[inst.tuning].append(inst)
    return groups


def written_key(concert: KeySig, inst: Instrument, concert_pitch: bool = False) -> KeySig:
    """Tonalidade em que a parte é escrita."""
    return concert if concert_pitch else concert.transposed(inst.fifths)


def written_midi(sounding: int, inst: Instrument, concert_pitch: bool = False) -> int:
    """Nota escrita para que o instrumento soe ``sounding``.

    Em "tom de concerto" mantém-se apenas a transposição de oitava (como é praxe
    para baixo, violão e flautim) e anula-se a transposição de tonalidade.
    """
    if concert_pitch:
        return sounding + (inst.chromatic if inst.chromatic % 12 == 0 else 0)
    return sounding + inst.chromatic


def fit_octave(notes: list[int], inst: Instrument) -> int:
    """Quantas oitavas somar para a melodia caber na extensão do instrumento."""
    if not notes:
        return 0
    lo, hi = min(notes), max(notes)
    best, best_out = 0, None
    for shift in (0, 12, -12, 24, -24, 36, -36):
        out = max(0, inst.low - (lo + shift)) + max(0, (hi + shift) - inst.high)
        if best_out is None or out < best_out:
            best, best_out = shift, out
        if out == 0:
            break
    return best
