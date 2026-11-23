"""Shared music theory, tier profiles, score loader, and self-healing validator."""

from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

OPEN_TUNING_NO_CAPO: Dict[int, int] = {
    6: 40,  # E2
    5: 45,  # A2
    4: 50,  # D3
    3: 55,  # G3
    2: 59,  # B3
    1: 64,  # E4
}

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
NOTE_NAMES_FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
LY_PITCH_NAMES = ["c", "cis", "des", "d", "ees", "e", "f", "fis", "ges", "g", "aes", "a", "bes", "b"]
LY_PC_SHARP = ["c", "cis", "d", "dis", "e", "f", "fis", "g", "gis", "a", "ais", "b"]

VALID_DURS: Set[int] = {1, 2, 3, 4, 6, 8, 12, 16}
GUITAR_SCALE_CM = 65.0


def finger_pos_cm(abs_fret: int) -> float:
    """Physical distance (cm) from guitar nut to finger placement center at absolute fret."""
    if abs_fret <= 0:
        return 0.0
    p0 = GUITAR_SCALE_CM * (1.0 - 2.0 ** (-(abs_fret - 1) / 12.0))
    p1 = GUITAR_SCALE_CM * (1.0 - 2.0 ** (-abs_fret / 12.0))
    return 0.5 * (p0 + p1)

PC_BY_TOKEN: Dict[str, int] = {
    "C": 0, "B#": 0,
    "C#": 1, "DB": 1,
    "D": 2,
    "D#": 3, "EB": 3,
    "E": 4, "FB": 4,
    "F": 5, "E#": 5,
    "F#": 6, "GB": 6,
    "G": 7,
    "G#": 8, "AB": 8,
    "A": 9,
    "A#": 10, "BB": 10,
    "B": 11, "CB": 11,
}

# Major key specs: canonical_name -> (tonic_pc, fifths_signed)
MAJOR_KEYS: Dict[str, Tuple[int, int]] = {
    "C": (0, 0),
    "G": (7, 1),
    "D": (2, 2),
    "A": (9, 3),
    "E": (4, 4),
    "B": (11, 5),
    "F#": (6, 6),
    "Gb": (6, -6),
    "Db": (1, -5),
    "C#": (1, 7),
    "Ab": (8, -4),
    "Eb": (3, -3),
    "Bb": (10, -2),
    "F": (5, -1),
}

MINOR_TO_MAJOR: Dict[str, str] = {
    "AM": "C",
    "EM": "G",
    "BM": "D",
    "F#M": "A",
    "C#M": "E",
    "G#M": "B",
    "D#M": "F#",
    "EBM": "Gb",
    "BBM": "Db",
    "FM": "Ab",
    "CM": "Eb",
    "GM": "Bb",
    "DM": "F",
}

MAJOR_SCALE_INTERVALS = (0, 2, 4, 5, 7, 9, 11)

KEY_FROM_FIFTHS: Dict[Tuple[int, int], str] = {
    (-6, 0): "Gb", (-5, 0): "Db", (-4, 0): "Ab", (-3, 0): "Eb", (-2, 0): "Bb", (-1, 0): "F",
    (0, 0): "C", (1, 0): "G", (2, 0): "D", (3, 0): "A", (4, 0): "E", (5, 0): "B", (6, 0): "F#", (7, 0): "C#",
    (-4, 1): "Fm", (-3, 1): "Cm", (-2, 1): "Gm", (-1, 1): "Dm", (0, 1): "Am",
    (1, 1): "Em", (2, 1): "Bm", (3, 1): "F#m", (4, 1): "C#m", (5, 1): "G#m", (6, 1): "D#m",
}


@dataclass(frozen=True)
class KeySpec:
    name: str
    tonic_pc: int
    major_tonic_pc: int
    is_minor: bool
    fifths: int
    diatonic_pcs: Set[int]
    relative_minor_name: str
    relative_major_name: str


def parse_key_spec(key_str: str) -> KeySpec:
    """Parse key signatures like C, Bb, F#m, A/F#m, Dm into a KeySpec."""
    raw = key_str.strip().split("/")[0].strip()
    upper = raw.upper()
    if upper in MINOR_TO_MAJOR:
        maj_name = MINOR_TO_MAJOR[upper]
        maj_pc, fifths = MAJOR_KEYS[maj_name]
        tonic_pc = PC_BY_TOKEN[upper[:-1]]
        diatonic = {(maj_pc + iv) % 12 for iv in MAJOR_SCALE_INTERVALS}
        canon_minor = raw[0].upper() + raw[1:].lower()
        if len(canon_minor) >= 2 and canon_minor[1] in ("#", "b"):
            canon_minor = canon_minor[0] + canon_minor[1] + "m"
        return KeySpec(
            name=canon_minor,
            tonic_pc=tonic_pc,
            major_tonic_pc=maj_pc,
            is_minor=True,
            fifths=fifths,
            diatonic_pcs=diatonic,
            relative_minor_name=canon_minor,
            relative_major_name=maj_name,
        )

    for k, (pc, fifths) in MAJOR_KEYS.items():
        if k.upper() == upper:
            diatonic = {(pc + iv) % 12 for iv in MAJOR_SCALE_INTERVALS}
            rel_min = next((m for m, mj in MINOR_TO_MAJOR.items() if mj == k), k + "m")
            rel_min_fmt = rel_min[0] + ("#" if "#" in rel_min else ("b" if len(rel_min) == 3 and rel_min[1] == "B" else "")) + "m"
            return KeySpec(
                name=k,
                tonic_pc=pc,
                major_tonic_pc=pc,
                is_minor=False,
                fifths=fifths,
                diatonic_pcs=diatonic,
                relative_minor_name=rel_min_fmt,
                relative_major_name=k,
            )

    raise ValueError(
        f"Unsupported key '{key_str}'. Examples: C, G, D, A, E, F, Bb, Eb, Am, Em, Bm, F#m, Dm, Cm."
    )


def key_name_from_major_pc(pc: int) -> str:
    pref = {0: "C", 1: "Db", 2: "D", 3: "Eb", 4: "E", 5: "F", 6: "F#", 7: "G", 8: "Ab", 9: "A", 10: "Bb", 11: "B"}
    return pref[pc % 12]


def diatonic_chords_for_major_pc(maj_pc: int) -> List[str]:
    """Return Roman diatonic triads (I, ii, iii, IV, V, vi) for a fingered major key center."""
    use_flat = maj_pc in {1, 3, 5, 8, 10}
    names = NOTE_NAMES_FLAT if use_flat else NOTE_NAMES
    steps = [(0, ""), (2, "m"), (4, "m"), (5, ""), (7, ""), (9, "m")]
    return [f"{names[(maj_pc + iv) % 12]}{q}" for iv, q in steps]


@dataclass(frozen=True)
class TierProfile:
    key: str
    label_zh: str
    max_fret: int
    max_span: int
    max_simultaneous: int
    allow_16th: bool
    allowed_techs: Set[str]
    required_techs: Tuple[str, ...]
    description_zh: str


TIER_PROFILES: Dict[str, TierProfile] = {
    "beginner": TierProfile(
        key="beginner",
        label_zh="新手入门档",
        max_fret=7,
        max_span=2,
        max_simultaneous=3,
        allow_16th=False,
        allowed_techs={"arpeggio"},
        required_techs=(),
        description_zh="0-7品(偏好0-3品), 指法跨度<=2品, 禁16分音符, 主旋律+低音两声部为主, 仅基础拨弦/简单琶音",
    ),
    "standard": TierProfile(
        key="standard",
        label_zh="标准指弹档",
        max_fret=12,
        max_span=3,
        max_simultaneous=6,
        allow_16th=True,
        allowed_techs={"H", "P", "S", "grace", "arpeggio", "harmonic", "palm_mute", "slap"},
        required_techs=("arpeggio",),
        description_zh="0-12品, 指法跨度<=3品, 支持三声部/切分延音/常用指弹技巧",
    ),
    "advanced": TierProfile(
        key="advanced",
        label_zh="进阶演奏档",
        max_fret=15,
        max_span=4,
        max_simultaneous=6,
        allow_16th=True,
        allowed_techs={"H", "P", "S", "grace", "arpeggio", "harmonic", "palm_mute", "slap"},
        required_techs=("H", "P", "S", "arpeggio", "harmonic"),
        description_zh="0-15品, 跨度放宽至<=4品, 完整多声部与常用指弹技巧",
    ),
}


def get_tier_profile(tier_name: Optional[str]) -> TierProfile:
    key = (tier_name or "standard").strip().lower()
    if key not in TIER_PROFILES:
        raise ValueError(f"Unknown TIER '{tier_name}'. Must be one of: {', '.join(TIER_PROFILES.keys())}")
    return TIER_PROFILES[key]


@dataclass(frozen=True)
class NoteEvent:
    string: int
    fret: int
    role: str = "melody"
    harmonic: bool = False
    grace_from: Optional[int] = None
    palm_mute: bool = False
    capo: int = 0

    @property
    def concert_midi(self) -> int:
        return OPEN_TUNING_NO_CAPO[self.string] + self.capo + self.fret

    @property
    def written_midi(self) -> int:
        return OPEN_TUNING_NO_CAPO[self.string] + self.fret

    @property
    def pitch_name(self) -> str:
        m = self.concert_midi
        return f"{NOTE_NAMES[m % 12]}{m // 12 - 1}"


@dataclass
class BeatEvent:
    slot: int
    dur: int
    notes: Tuple[NoteEvent, ...] = ()
    rest: bool = False
    lyric: Optional[str] = None
    fermata: bool = False
    arpeggio: bool = False
    link_tech: Optional[str] = None


@dataclass
class Measure:
    bar: int
    events: List[BeatEvent]
    chords: List[Tuple[int, str]] = field(default_factory=list)
    section: Optional[str] = None
    is_pickup: bool = False
    dynamic: float = 1.0


def n(
    string: int,
    fret: int,
    role: str = "melody",
    *,
    harmonic: bool = False,
    grace_from: Optional[int] = None,
    palm_mute: bool = False,
    capo: int = 0,
) -> NoteEvent:
    return NoteEvent(
        string=string,
        fret=fret,
        role=role,
        harmonic=harmonic,
        grace_from=grace_from,
        palm_mute=palm_mute,
        capo=capo,
    )


def ev(
    slot: int,
    dur: int,
    *notes: NoteEvent,
    rest: bool = False,
    lyric: Optional[str] = None,
    fermata: bool = False,
    arpeggio: bool = False,
    link_tech: Optional[str] = None,
) -> BeatEvent:
    return BeatEvent(
        slot=slot,
        dur=dur,
        notes=notes,
        rest=rest,
        lyric=lyric,
        fermata=fermata,
        arpeggio=arpeggio,
        link_tech=link_tech,
    )


def note_written_midi(nt: Any) -> int:
    return OPEN_TUNING_NO_CAPO[nt.string] + nt.fret


def note_concert_midi(nt: Any, capo: int) -> int:
    return OPEN_TUNING_NO_CAPO[nt.string] + capo + nt.fret


def midi_to_name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def note_name_to_ly(root: str) -> str:
    m = re.fullmatch(r"([A-Ga-g])([#b]?)", root.strip())
    if not m:
        raise ValueError(f"Invalid chord root '{root}'")
    letter = m.group(1).lower()
    acc = m.group(2)
    if acc == "#":
        return f"{letter}is"
    if acc == "b":
        # LilyPond nederlands accepts aes/as, ees/es, bes, des, ges
        return f"{letter}es"
    return letter


CHORD_QUALITIES: Dict[str, Tuple[str, Tuple[int, ...]]] = {
    "": ("", (0, 4, 7)),
    "M": ("", (0, 4, 7)),
    "m": (":m", (0, 3, 7)),
    "maj7": (":maj7", (0, 4, 7, 11)),
    "M7": (":maj7", (0, 4, 7, 11)),
    "m7": (":m7", (0, 3, 7, 10)),
    "7": (":7", (0, 4, 7, 10)),
    "6": (":6", (0, 4, 7, 9)),
    "m6": (":m6", (0, 3, 7, 9)),
    "9": (":9", (0, 4, 7, 10, 2)),
    "m9": (":m9", (0, 3, 7, 10, 2)),
    "maj9": (":maj9", (0, 4, 7, 11, 2)),
    "add9": (":5.9", (0, 4, 7, 2)),
    "sus2": (":sus2", (0, 2, 7)),
    "sus4": (":sus4", (0, 5, 7)),
    "sus": (":sus4", (0, 5, 7)),
    "7sus4": (":7.5sus4", (0, 5, 7, 10)),
    "dim": (":dim", (0, 3, 6)),
    "dim7": (":dim7", (0, 3, 6, 9)),
    "m7b5": (":m7.5-", (0, 3, 6, 10)),
    "aug": (":aug", (0, 4, 8)),
    "+": (":aug", (0, 4, 8)),
    "5": (":5", (0, 7)),
}

CHORD_RE = re.compile(r"^([A-G][#b]?)(.*?)(?:/([A-G][#b]?))?$")


def chord_to_lilypond(label: str, dur_str: str) -> str:
    """Convert any standard chord symbol (e.g. Cmaj7, D/F#, Bm7, Gadd9) to LilyPond chordmode."""
    clean = label.strip()
    m = CHORD_RE.match(clean)
    if not m:
        raise ValueError(
            f"Unsupported chord label '{label}'. Use standard format like C, Am7, Fmaj7, D/F#, Bb, Gsus4."
        )
    root, qual, bass = m.group(1), m.group(2), m.group(3)
    if qual not in CHORD_QUALITIES:
        raise ValueError(
            f"Unsupported chord quality '{qual}' in '{label}'. Supported: {', '.join(q or '<maj>' for q in CHORD_QUALITIES)}"
        )
    ly_root = note_name_to_ly(root)
    ly_qual = CHORD_QUALITIES[qual][0]
    ly_bass = f"/{note_name_to_ly(bass)}" if bass else ""
    return f"{ly_root}{dur_str}{ly_qual}{ly_bass}"


def chord_pitch_classes(label: str, capo: int = 0) -> Set[int]:
    """Return concert pitch classes for a fingered chord label (shifted by +capo)."""
    m = CHORD_RE.match(label.strip())
    if not m:
        return set()
    root, qual, bass = m.group(1), m.group(2), m.group(3)
    root_pc = PC_BY_TOKEN.get(root.upper())
    if root_pc is None or qual not in CHORD_QUALITIES:
        return set()
    pcs = {(root_pc + iv + capo) % 12 for iv in CHORD_QUALITIES[qual][1]}
    if bass:
        b_pc = PC_BY_TOKEN.get(bass.upper())
        if b_pc is not None:
            pcs.add((b_pc + capo) % 12)
    return pcs


def chord_root_concert_pc(label: str, capo: int = 0) -> Optional[int]:
    m = CHORD_RE.match(label.strip())
    if not m:
        return None
    root, _qual, bass = m.group(1), m.group(2), m.group(3)
    tok = (bass or root).upper()
    pc = PC_BY_TOKEN.get(tok)
    return ((pc + capo) % 12) if pc is not None else None


def fret_candidates_for_midi(
    concert_midi: int,
    capo: int,
    max_fret: int,
    *,
    preferred_strings: Tuple[int, ...] = (1, 2, 3, 4, 5, 6),
) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for s in preferred_strings:
        fret = concert_midi - OPEN_TUNING_NO_CAPO[s] - capo
        if 0 <= fret <= max_fret:
            out.append((s, fret))
    return out


def safe_file_prefix(mod: Any) -> str:
    explicit = getattr(mod, "FILE_PREFIX", None)
    if explicit:
        return str(explicit).strip()
    title = str(getattr(mod, "TITLE", "Untitled_Score")).strip()
    prefix = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", title).strip("_")
    return prefix or "Fingerstyle_Score"


def load_score_module(project_dir: Path) -> Any:
    """Dynamically load <project_dir>/src/score_data.py."""
    score_path = project_dir / "src" / "score_data.py"
    if not score_path.is_file():
        raise FileNotFoundError(f"Missing score file: {score_path}")

    mod_name = f"score_data_{abs(hash(str(score_path.resolve())))}"
    src_dir_str = str(score_path.parent)
    added = src_dir_str not in sys.path
    if added:
        sys.path.insert(0, src_dir_str)
    try:
        spec = importlib.util.spec_from_file_location(mod_name, str(score_path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create module spec for {score_path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        if added and src_dir_str in sys.path:
            sys.path.remove(src_dir_str)


def _suggest_accompaniment_candidates(
    max_midi: int,
    capo: int,
    diatonic_pcs: Set[int],
    chord_pcs: Set[int],
    root_pc: Optional[int],
    tier: TierProfile,
    role: str,
) -> str:
    strings = (6, 5, 4) if role == "bass" else (4, 3, 5, 2)
    cands: List[Tuple[int, int, int, int]] = []
    for s in strings:
        max_f = min(tier.max_fret, 5 if tier.key == "beginner" else tier.max_fret)
        for f in range(0, max_f + 1):
            cm = OPEN_TUNING_NO_CAPO[s] + capo + f
            if cm > max_midi:
                continue
            pc = cm % 12
            if chord_pcs and pc not in chord_pcs:
                continue
            if not chord_pcs and pc not in diatonic_pcs:
                continue
            is_root = 0 if (root_pc is not None and pc == root_pc) else 1
            open_bonus = 0 if f == 0 else 1
            cands.append((is_root if role == "bass" else open_bonus, f, -cm, s))
    cands.sort()
    if not cands and chord_pcs:
        return _suggest_accompaniment_candidates(max_midi, capo, diatonic_pcs, set(), None, tier, role)
    picks = [f"n({s}, {f}, \"{role}\") [midi={-neg_cm} {midi_to_name(-neg_cm)}]" for _r, f, neg_cm, s in cands[:3]]
    return " 或 ".join(picks) if picks else "省略该内声部音或抬高当前旋律八度"


def validate_project_score(
    mod: Any, measures: Optional[List[Any]] = None
) -> Tuple[List[str], Dict[str, int], Dict[str, Any]]:
    """Run tier-aware, self-healing validation on any project's score_data.py."""
    if measures is None:
        measures = mod.build_measures()

    capo = int(getattr(mod, "CAPO", 0))
    bpm = int(getattr(mod, "TEMPO_BPM", 72) or 72)
    diatonic_pcs: Set[int] = set(getattr(mod, "DIATONIC_PCS", {0, 2, 4, 5, 7, 9, 11}))
    allow_chromatic = bool(getattr(mod, "ALLOW_CHROMATIC", False))
    allow_short_demo = bool(getattr(mod, "ALLOW_SHORT_DEMO", False))
    is_starter = bool(getattr(mod, "IS_STARTER_TEMPLATE", False))
    tier = get_tier_profile(getattr(mod, "TIER", "standard"))
    required_override = getattr(mod, "REQUIRED_TECHS", None)
    required_techs: Tuple[str, ...] = (
        tuple(required_override) if required_override is not None else tier.required_techs
    )
    landmarks: Dict[int, List[int]] = dict(getattr(mod, "LANDMARKS", {}) or {})

    errors: List[str] = []
    tech_counts: Dict[str, int] = {
        "H": 0,
        "P": 0,
        "S": 0,
        "grace": 0,
        "arpeggio": 0,
        "harmonic": 0,
        "palm_mute": 0,
        "slap": 0,
    }

    if is_starter:
        errors.append(
            "模板拦截: 仍带有 IS_STARTER_TEMPLATE = True（未替换 init_project.py 生成的占位小节）"
            " -> 修复建议: 写入真实完整曲谱后删除 IS_STARTER_TEMPLATE（或设为 False）。"
        )

    if not measures:
        return (["build_measures() returned empty list -> 至少添加 16 个完整小节 Measure (bar=1..N)。"], tech_counts, {})

    has_pickup = bool(getattr(measures[0], "is_pickup", False) or measures[0].bar == 0)
    expected_bar_start = 0 if has_pickup else 1
    last_bar = measures[-1].bar

    active_melody_midi: Optional[int] = None
    max_fret_seen = 0
    total_notes = 0
    bar_by_idx: Dict[int, Any] = {}

    # Anti-slop, harmony, physical ergonomics & difficulty accumulators
    non_pickup_bars = 0
    mel_bars = 0
    bass_bars = 0
    chord_bars = 0
    empty_bars = 0
    mel_total = 0
    mel_on_bass_strings = 0
    pitched_total = 0
    out_of_key_pitched = 0
    non_rest_events = 0
    poly_events = 0
    max_simul = 0
    sixteenth_events = 0
    tech_events = 0
    crossings_count = 0
    chord_bass_checks = 0
    chord_bass_hits = 0
    chord_bass_mismatches: List[str] = []
    distinct_bass: Set[int] = set()
    sections: Set[str] = set()
    dynamics: Set[float] = set()
    bar_slot_sums: List[int] = []
    bar_nps_list: List[float] = []
    mel_bar_fps: List[Tuple[Any, ...]] = []
    max_consec_same = 1
    cur_consec_same = 1
    prev_bar_fp: Optional[Tuple[Any, ...]] = None
    consec_repeat_bar: Optional[int] = None

    spans_cm: List[float] = []
    impossible_chords: List[Tuple[int, int, int]] = []
    extreme_stretches: List[Tuple[int, int, float, int, int]] = []
    hard_stretches = 0
    barre_events = 0
    awkward_shifts: List[Tuple[int, int, int, float]] = []
    large_shifts = 0
    shift_transitions = 0
    shift_velocities: List[float] = []
    prev_anchor_cm: Optional[float] = None
    prev_mean_fret: Optional[float] = None
    elapsed_since_fretted_slots = 0
    total_slots = 0
    sec_per_slot = (60.0 / max(30, bpm)) / 4.0

    for i, meas in enumerate(measures):
        expected_bar = expected_bar_start + i
        if meas.bar != expected_bar:
            errors.append(
                f"M{meas.bar:02d}: 小节序号错位，期望 bar={expected_bar} "
                f"-> 修复建议: 按顺序从 bar={expected_bar_start} 连续递增。"
            )
        bar_by_idx[meas.bar] = meas

        is_pickup_bar = bool(i == 0 and getattr(meas, "is_pickup", False))
        sec_lbl = getattr(meas, "section", None)
        if sec_lbl:
            sections.add(str(sec_lbl).strip())
        dynamics.add(round(float(getattr(meas, "dynamic", 1.0) or 1.0), 2))
        if not is_pickup_bar:
            non_pickup_bars += 1
            if getattr(meas, "chords", None):
                chord_bars += 1
            if not meas.events:
                empty_bars += 1

        if not meas.events:
            errors.append(f"M{meas.bar:02d}: events 为空 -> 修复建议: 至少添加填满小节的 BeatEvent (可用 rest=True)。")
            continue

        for sl, lbl in getattr(meas, "chords", []):
            try:
                chord_to_lilypond(lbl, "4")
            except ValueError as exc:
                errors.append(f"M{meas.bar:02d} slot {sl}: {exc}")

        if is_pickup_bar:
            expected_start = meas.events[0].slot
            if expected_start not in (4, 8, 12):
                errors.append(
                    f"M00: 弱起小节起始 slot={expected_start} 不标准 -> 修复建议: 建议设为 slot 12(1拍), 8(2拍) 或 4(3拍)，并覆盖到 16。"
                )
            expected_end = 16
            expected_sum = 16 - expected_start
        else:
            expected_start, expected_end, expected_sum = 0, 16, 16

        cursor = expected_start
        dur_sum = 0
        sorted_chords = sorted(getattr(meas, "chords", []), key=lambda x: x[0])
        chords_by_slot = {sl: lbl for sl, lbl in sorted_chords}
        bar_has_mel = False
        bar_has_bass = False
        bar_pitched = 0
        bar_fp_items: List[Any] = []

        for ev_idx, e in enumerate(meas.events):
            if e.slot != cursor:
                errors.append(
                    f"M{meas.bar:02d}: 事件顺序断层，期望 slot={cursor}，实际为 slot={e.slot} "
                    f"-> 修复建议: 修改前一个事件 dur={e.slot - (cursor - meas.events[ev_idx - 1].dur) if ev_idx > 0 else e.slot} 或把当前事件改为 ev({cursor}, ...) 。"
                )
            if e.dur not in VALID_DURS:
                errors.append(
                    f"M{meas.bar:02d} slot {e.slot}: dur={e.dur} 不是合法 LilyPond 时值 "
                    f"-> 修复建议: 只能使用 {{1, 2, 3, 4, 6, 8, 12, 16}}，例如把 dur={e.dur} 拆成合法组合。"
                )
            elif not tier.allow_16th and e.dur == 1:
                errors.append(
                    f"M{meas.bar:02d} slot {e.slot}: 当前档位 TIER='{tier.key}' ({tier.label_zh}) 禁止 16 分音符 (dur=1) "
                    f"-> 修复建议: 合并为 8 分音符 (dur=2) 或把 TIER 升级为 'standard'。"
                )

            cursor += e.dur
            dur_sum += e.dur
            total_slots += max(1, e.dur)
            if e.dur == 1:
                sixteenth_events += 1
            ev_has_tech = False

            if e.rest:
                active_melody_midi = None
                elapsed_since_fretted_slots += max(1, e.dur)
                if e.notes:
                    errors.append(f"M{meas.bar:02d} slot {e.slot}: 休止符事件 (rest=True) 不能带 notes -> 删除 notes。")
                if e.arpeggio or e.link_tech:
                    errors.append(f"M{meas.bar:02d} slot {e.slot}: 休止符不能带 arpeggio/link_tech -> 设为 False/None。")
                continue

            if not e.notes:
                elapsed_since_fretted_slots += max(1, e.dur)
                errors.append(f"M{meas.bar:02d} slot {e.slot}: 非休止事件缺少音符 -> 添加 n(...) 或改 rest=True。")
                continue

            non_rest_events += 1
            total_notes += len(e.notes)
            if len(e.notes) > tier.max_simultaneous:
                errors.append(
                    f"M{meas.bar:02d} slot {e.slot}: 同时拨弦数 {len(e.notes)} 超过档位 TIER='{tier.key}' 上限 ({tier.max_simultaneous}) "
                    f"-> 修复建议: 删减内声部 inner 音至 <={tier.max_simultaneous} 音，或升级 TIER='standard'。"
                )

            if e.arpeggio:
                tech_counts["arpeggio"] += 1
                ev_has_tech = True
                if "arpeggio" not in tier.allowed_techs:
                    errors.append(f"M{meas.bar:02d} slot {e.slot}: 档位 '{tier.key}' 禁用 arpeggio -> 删掉 arpeggio=True。")
                if len(e.notes) < 2:
                    errors.append(f"M{meas.bar:02d} slot {e.slot}: arpeggio=True 至少需要 2 个音符 -> 添加低音或去掉 arpeggio=True。")

            strings = [nt.string for nt in e.notes]
            if len(strings) != len(set(strings)):
                errors.append(
                    f"M{meas.bar:02d} slot {e.slot}: 同一时刻在同一根弦 {strings} 上拨了两个音 -> 把其中一个音挪到相邻空弦。"
                )

            pitched_notes = [nt for nt in e.notes if nt.role != "slap"]
            pitched_total += len(pitched_notes)
            bar_pitched += len(pitched_notes)
            if len(pitched_notes) >= 2:
                poly_events += 1
            if len(pitched_notes) > max_simul:
                max_simul = len(pitched_notes)

            mel_notes = [nt for nt in pitched_notes if nt.role == "melody"]
            bass_notes = [nt for nt in pitched_notes if nt.role == "bass"]
            if mel_notes:
                bar_has_mel = True
                for nt in mel_notes:
                    mel_total += 1
                    if nt.string in (5, 6):
                        mel_on_bass_strings += 1
                bar_fp_items.append((e.slot, e.dur, tuple(sorted(note_concert_midi(nt, capo) for nt in mel_notes))))
                if any(nt.harmonic for nt in mel_notes):
                    active_melody_midi = None
                else:
                    active_melody_midi = max(note_concert_midi(nt, capo) for nt in mel_notes)
            if bass_notes:
                bar_has_bass = True
                for nt in bass_notes:
                    distinct_bass.add(note_concert_midi(nt, capo))
                if e.slot in chords_by_slot:
                    slot_lbl = chords_by_slot[e.slot]
                    slot_pcs = chord_pitch_classes(slot_lbl, capo)
                    if slot_pcs:
                        chord_bass_checks += 1
                        if any((note_concert_midi(nt, capo) % 12) in slot_pcs for nt in bass_notes):
                            chord_bass_hits += 1
                        else:
                            b0 = bass_notes[0]
                            bm = note_concert_midi(b0, capo)
                            chord_bass_mismatches.append(
                                f"M{meas.bar:02d} slot {e.slot} 和弦 '{slot_lbl}' 却配了低音 n({b0.string},{b0.fret})={midi_to_name(bm)}"
                            )

            # Active chord for self-healing voice-crossing tips
            cur_chord = ""
            for c_slot, c_lbl in sorted_chords:
                if c_slot <= e.slot:
                    cur_chord = c_lbl
            cur_chord_pcs = chord_pitch_classes(cur_chord, capo) if cur_chord else set()
            cur_root_pc = chord_root_concert_pc(cur_chord, capo) if cur_chord else None

            is_closing_final_event = (meas.bar == last_bar and ev_idx == len(meas.events) - 1)
            if not is_closing_final_event and active_melody_midi is not None:
                for nt in pitched_notes:
                    cm = note_concert_midi(nt, capo)
                    if nt.role in ("bass", "inner") and not nt.harmonic and cm > active_melody_midi:
                        crossings_count += 1
                        tip = _suggest_accompaniment_candidates(
                            active_melody_midi, capo, diatonic_pcs, cur_chord_pcs, cur_root_pc, tier, nt.role
                        )
                        errors.append(
                            f"M{meas.bar:02d} slot {e.slot}: 声部交叉 — {nt.role} n({nt.string},{nt.fret}) "
                            f"{midi_to_name(cm)}({cm}) 高于当前主旋律 ({active_melody_midi}) "
                            f"-> 修复建议: 替换为 {tip}"
                        )

            for nt in e.notes:
                if nt.string not in OPEN_TUNING_NO_CAPO:
                    errors.append(f"M{meas.bar:02d} slot {e.slot}: 非法弦号 {nt.string} (仅允许 1..6)。")
                    continue
                if nt.role == "slap":
                    tech_counts["slap"] += 1
                    ev_has_tech = True
                    if "slap" not in tier.allowed_techs:
                        errors.append(
                            f"M{meas.bar:02d} slot {e.slot}: 档位 TIER='{tier.key}' ({tier.label_zh}) 禁用拍弦 slap "
                            f"-> 修复建议: 删除该拍弦音符或改用 TIER='standard'。"
                        )
                    continue

                cm = note_concert_midi(nt, capo)
                if not nt.harmonic:
                    max_fret_seen = max(max_fret_seen, nt.fret)
                    if not (0 <= nt.fret <= tier.max_fret):
                        cands = fret_candidates_for_midi(cm, capo, tier.max_fret)
                        tip = (
                            " 或 ".join(f"n({s},{f},\"{nt.role}\")" for s, f in cands[:3])
                            if cands
                            else f"八度/移调调整或将 TIER 改为 'standard'/'advanced'"
                        )
                        errors.append(
                            f"M{meas.bar:02d} slot {e.slot}: 品位 fret={nt.fret} 超过档位 '{tier.key}' 上限 ({tier.max_fret}) "
                            f"-> 修复建议: 换弦保持同音高 [{tip}]。"
                        )

                if (cm % 12) not in diatonic_pcs:
                    out_of_key_pitched += 1
                    if not allow_chromatic:
                        fixes = []
                        for df in (-1, 1):
                            nf = nt.fret + df
                            if 0 <= nf <= tier.max_fret and ((cm + df) % 12) in diatonic_pcs:
                                fixes.append(f"n({nt.string}, {nf}, \"{nt.role}\") [{midi_to_name(cm + df)}]")
                        tip = " 或 ".join(fixes) if fixes else "用 scripts/fret_helper.py jianpu 重查调内品格（禁止盲改 ALLOW_CHROMATIC 绕过错音）"
                        errors.append(
                            f"M{meas.bar:02d} slot {e.slot}: 调外音 n({nt.string},{nt.fret}) = {midi_to_name(cm)} "
                            f"-> 修复建议: 邻近调内音 {tip}。"
                        )

                if nt.harmonic:
                    tech_counts["harmonic"] += 1
                    ev_has_tech = True
                    if "harmonic" not in tier.allowed_techs:
                        errors.append(
                            f"M{meas.bar:02d} slot {e.slot}: 档位 '{tier.key}' 禁用泛音 harmonic -> 改为 harmonic=False 或升级 TIER='standard'。"
                        )
                    if nt.fret != 12:
                        errors.append(f"M{meas.bar:02d} slot {e.slot}: 自然泛音需设为 fret=12，当前为 {nt.fret}。")

                if nt.palm_mute:
                    tech_counts["palm_mute"] += 1
                    ev_has_tech = True
                    if "palm_mute" not in tier.allowed_techs:
                        errors.append(
                            f"M{meas.bar:02d} slot {e.slot}: 档位 '{tier.key}' 禁用闷音 palm_mute -> 改为 palm_mute=False 或升级 TIER='standard'。"
                        )

                if nt.grace_from is not None:
                    tech_counts["grace"] += 1
                    tech_counts["S"] += 1
                    ev_has_tech = True
                    if "grace" not in tier.allowed_techs and "S" not in tier.allowed_techs:
                        errors.append(
                            f"M{meas.bar:02d} slot {e.slot}: 档位 '{tier.key}' 禁用倚音滑音 grace_from -> 删除 grace_from 或升级 TIER='standard'。"
                        )
                    if not (1 <= nt.grace_from <= 15):
                        errors.append(f"M{meas.bar:02d} slot {e.slot}: grace_from={nt.grace_from} 需在 1..15。")
                    g_midi = OPEN_TUNING_NO_CAPO[nt.string] + capo + nt.grace_from
                    if not allow_chromatic and (g_midi % 12) not in diatonic_pcs:
                        errors.append(f"M{meas.bar:02d} slot {e.slot}: grace_from={nt.grace_from} 为调外音，请改用邻近调内品格。")
                    if abs(nt.fret - nt.grace_from) > 3 or nt.fret == nt.grace_from:
                        errors.append(f"M{meas.bar:02d} slot {e.slot}: 倚音滑音跨度需在 1..3 品内，当前 {nt.grace_from}->{nt.fret}。")

            fretted_notes = [nt for nt in pitched_notes if nt.fret > 0 and not nt.harmonic]
            if fretted_notes:
                f_vals = [nt.fret for nt in fretted_notes]
                span = max(f_vals) - min(f_vals)
                if span > tier.max_span:
                    min_f, max_f = min(f_vals), max(f_vals)
                    outliers = [nt for nt in fretted_notes if nt.fret in (min_f, max_f)]
                    used_s = {nt.string for nt in pitched_notes}
                    alts: List[str] = []
                    for ol in outliers:
                        ol_midi = note_concert_midi(ol, capo)
                        rem_f = [nt.fret for nt in fretted_notes if nt is not ol]
                        for s, f in fret_candidates_for_midi(ol_midi, capo, tier.max_fret):
                            if s in used_s:
                                continue
                            test_f = rem_f + ([f] if f > 0 else [])
                            if not test_f or (max(test_f) - min(test_f)) <= tier.max_span:
                                alts.append(f"把 ({ol.string}弦{ol.fret}品) 换成 n({s},{f},\"{ol.role}\")")
                    tip = " 或 ".join(alts[:2]) if alts else f"利用空弦或降低/删去多余内声部音 (跨度 {span} > 上限 {tier.max_span})"
                    errors.append(
                        f"M{meas.bar:02d} slot {e.slot}: 品格跨度 {span} 超过档位 '{tier.key}' 上限 ({tier.max_span})，按品 {f_vals} "
                        f"-> 修复建议: {tip}。"
                    )

                # Physical left-hand ergonomics (65cm scale model)
                uniq_fretted = sorted({(nt.string, nt.fret) for nt in fretted_notes})
                frets_only = [f for _, f in uniq_fretted]
                min_f_u, max_f_u = min(frets_only), max(frets_only)
                at_min = [s for s, f in uniq_fretted if f == min_f_u]
                k_f = len(uniq_fretted)
                fingers_needed = (k_f - len(at_min) + 1) if len(at_min) >= 2 else k_f
                if fingers_needed > 4:
                    impossible_chords.append((meas.bar, e.slot, fingers_needed))
                    errors.append(
                        f"M{meas.bar:02d} slot {e.slot}: 不可弹指法 — 同时按弦需 {fingers_needed} 根手指（无法用横按覆盖）"
                        f" -> 修复建议: 删减多余内声部或改用空弦。"
                    )
                if (k_f >= 4 and len(at_min) >= 2) or (
                    k_f >= 3 and len(at_min) >= 2 and (max(at_min) - min(at_min)) >= 2
                ):
                    barre_events += 1
                if max_f_u > min_f_u:
                    span_cm = finger_pos_cm(capo + max_f_u) - finger_pos_cm(capo + min_f_u)
                    spans_cm.append(span_cm)
                    if span_cm > 14.5:
                        extreme_stretches.append((meas.bar, e.slot, round(span_cm, 1), min_f_u, max_f_u))
                        errors.append(
                            f"M{meas.bar:02d} slot {e.slot}: 物理跨度超限 ({span_cm:.1f}cm > 14.5cm，{min_f_u}–{max_f_u}品)"
                            f" -> 修复建议: 换弦缩短左手跨度或省略外延伴奏音。"
                        )
                    elif span_cm > 12.5:
                        hard_stretches += 1
                anchor = sum(finger_pos_cm(capo + f) for f in frets_only) / len(frets_only)
                mean_f = sum(frets_only) / len(frets_only)
                if prev_anchor_cm is not None and prev_mean_fret is not None:
                    dt = max(0.06, elapsed_since_fretted_slots * sec_per_slot)
                    shift_cm = abs(anchor - prev_anchor_cm)
                    fjump = abs(mean_f - prev_mean_fret)
                    shift_transitions += 1
                    shift_velocities.append(shift_cm / dt)
                    if fjump >= 4:
                        large_shifts += 1
                    if fjump >= 5 and shift_cm >= 12.0 and dt <= 0.28:
                        awkward_shifts.append((meas.bar, int(round(prev_mean_fret)), int(round(mean_f)), round(dt, 2)))
                prev_anchor_cm = anchor
                prev_mean_fret = mean_f
                elapsed_since_fretted_slots = max(1, e.dur)
            else:
                elapsed_since_fretted_slots += max(1, e.dur)

            if e.link_tech is not None:
                if e.link_tech not in ("H", "P", "S"):
                    errors.append(f"M{meas.bar:02d} slot {e.slot}: 非法 link_tech='{e.link_tech}'，仅允许 'H'/'P'/'S'。")
                else:
                    tech_counts[e.link_tech] += 1
                    ev_has_tech = True
                    if e.link_tech not in tier.allowed_techs:
                        errors.append(
                            f"M{meas.bar:02d} slot {e.slot}: 档位 '{tier.key}' 禁用连奏技巧 '{e.link_tech}' "
                            f"-> 修复建议: 去掉 link_tech='{e.link_tech}' 或升级 TIER='standard'。"
                        )
                    if ev_idx + 1 >= len(meas.events):
                        errors.append(f"M{meas.bar:02d} slot {e.slot}: link_tech='{e.link_tech}' 不能放在小节最后一个事件上。")
                    else:
                        nxt = meas.events[ev_idx + 1]
                        mel_cur = [nt for nt in pitched_notes if nt.role == "melody"]
                        if not mel_cur:
                            errors.append(f"M{meas.bar:02d} slot {e.slot}: link_tech='{e.link_tech}' 需要当前事件包含 role='melody' 音符。")
                        else:
                            s0, f0 = mel_cur[0].string, mel_cur[0].fret
                            nxt_match = [nt for nt in nxt.notes if nt.string == s0 and nt.role != "slap"]
                            if not nxt_match:
                                errors.append(
                                    f"M{meas.bar:02d} slot {e.slot}: link_tech='{e.link_tech}' 下一事件缺少同弦 (string={s0}) 目标音 "
                                    f"-> 修复建议: 把下一音换到 {s0} 弦或取消 link_tech。"
                                )
                            else:
                                df = nxt_match[0].fret - f0
                                if e.link_tech == "H" and not (1 <= df <= 3):
                                    errors.append(f"M{meas.bar:02d} slot {e.slot}: 击弦 H 品格差 df={df} 需在 1..3 (低品->高品)。")
                                elif e.link_tech == "P" and not (-3 <= df <= -1):
                                    errors.append(f"M{meas.bar:02d} slot {e.slot}: 勾弦 P 品格差 df={df} 需在 -3..-1 (高品->低品)。")
                                elif e.link_tech == "S" and df == 0:
                                    errors.append(f"M{meas.bar:02d} slot {e.slot}: 滑音 S 目标品格不能与起始相同 (df=0)。")

            if ev_has_tech:
                tech_events += 1

        if cursor != expected_end or dur_sum != expected_sum:
            diff = expected_sum - dur_sum
            hint = f"少了 {diff} 个 16 分时值" if diff > 0 else f"多了 {-diff} 个 16 分时值"
            errors.append(
                f"M{meas.bar:02d}: 小节时值之和={dur_sum}，期望 [{expected_start}..{expected_end}) 总和={expected_sum} ({hint}) "
                f"-> 修复建议: 补齐或缩短本小节最后几个事件的 dur。"
            )

        if not is_pickup_bar:
            if bar_has_mel:
                mel_bars += 1
            if bar_has_bass:
                bass_bars += 1
            if dur_sum > 0:
                bar_slot_sums.append(dur_sum)
            bar_sec = max(0.25, (dur_sum or 16) * sec_per_slot)
            bar_nps_list.append(bar_pitched / bar_sec)
            t_fp = tuple(bar_fp_items)
            mel_bar_fps.append(t_fp)
            if t_fp and t_fp == prev_bar_fp:
                cur_consec_same += 1
                if cur_consec_same > max_consec_same:
                    max_consec_same = cur_consec_same
                    consec_repeat_bar = meas.bar
            else:
                cur_consec_same = 1
            prev_bar_fp = t_fp

    for req_t in required_techs:
        if tech_counts.get(req_t, 0) < 1:
            errors.append(
                f"技巧校验: 档位 '{tier.key}' 要求至少出现 1 次 '{req_t}'，当前为 0 "
                f"-> 修复建议: 在合适乐句处添加真实指弹 '{req_t}' 技巧（禁止通过清空 REQUIRED_TECHS 绕过）。"
            )

    for bar_idx, expected_pitches in landmarks.items():
        if bar_idx not in bar_by_idx:
            errors.append(f"LANDMARKS M{bar_idx:02d}: 小节号不存在。")
            continue
        actual_pitches = [
            note_concert_midi(nt, capo)
            for e in bar_by_idx[bar_idx].events
            for nt in e.notes
            if nt.role == "melody" and not nt.harmonic
        ]
        if actual_pitches != list(expected_pitches):
            exp_names = [midi_to_name(p) for p in expected_pitches]
            act_names = [midi_to_name(p) for p in actual_pitches]
            errors.append(
                f"LANDMARKS M{bar_idx:02d} 主旋律偏航: 期望 {list(expected_pitches)} ({exp_names})，实际 {actual_pitches} ({act_names}) "
                f"-> 修复建议: 用 scripts/fret_helper.py jianpu 重查 M{bar_idx:02d} 主旋律弦位。"
            )

    # =========================================================================
    # Anti-Slop, Harmony & Physical Quality Gates (Block Half-Baked "四不像")
    # =========================================================================
    np_denom = max(1, non_pickup_bars)
    mel_ratio = mel_bars / np_denom
    bass_ratio = bass_bars / np_denom
    chord_ratio = chord_bars / np_denom
    avg_poly = pitched_total / max(1, non_rest_events)
    poly_ratio = poly_events / max(1, non_rest_events)
    mel_56_ratio = mel_on_bass_strings / max(1, mel_total)
    out_key_ratio = out_of_key_pitched / max(1, pitched_total)
    uniq_mel_ratio = len({fp for fp in mel_bar_fps if fp}) / np_denom

    if not allow_short_demo and non_pickup_bars < 16:
        errors.append(
            f"完整性拦截: 曲目过短（仅 {non_pickup_bars} 个完整小节，至少需 16 小节）"
            f" -> 修复建议: 完整编配主歌/副歌/尾奏段落，严禁交付 4-8 小节敷衍半成品（仅当用户明确要求短片段时设 ALLOW_SHORT_DEMO = True）。"
        )

    if max_consec_same >= 4:
        errors.append(
            f"反敷衍拦截: 截至 M{consec_repeat_bar or 0:02d} 连续 {max_consec_same} 个小节主旋律完全一模一样"
            f" -> 修复建议: 按原曲真实旋律展开乐句，禁止复制粘贴同一小节循环凑长度。"
        )
    if non_pickup_bars >= 16 and uniq_mel_ratio < 0.25:
        errors.append(
            f"反敷衍拦截: 小节旋律重复率过高（唯一旋律小节仅占 {uniq_mel_ratio * 100:.0f}% < 25%）"
            f" -> 修复建议: 补全真实主歌/副歌变化，严禁拿 2-3 个小节循环凑整首歌。"
        )

    if mel_ratio < 0.75:
        errors.append(
            f"指弹结构拦截: 主旋律小节覆盖率仅 {mel_ratio * 100:.0f}% (< 75%)"
            f" -> 修复建议: 独奏指弹必须保持清晰主旋律 (role='melody')，不能只写纯和弦伴奏。"
        )

    if bass_ratio < 0.65 or (non_pickup_bars >= 8 and len(distinct_bass) < 3):
        errors.append(
            f"指弹结构拦截: 低音声部严重不足（覆盖 {bass_ratio * 100:.0f}%，仅 {len(distinct_bass)} 种低音音高）"
            f" -> 修复建议: 用 scripts/fret_helper.py chord 为每小节和弦重拍配好 role='bass' 根音，禁止交单音干瘪旋律谱。"
        )

    min_avg_poly = 1.06 if tier.key == "beginner" else 1.08
    if avg_poly < min_avg_poly or poly_ratio < 0.08:
        errors.append(
            f"指弹质感拦截: 复调饱满度过低（平均同时发声 {avg_poly:.2f} 音，多音拍占比 {poly_ratio * 100:.0f}%）"
            f" -> 修复建议: 在每小节强拍同时拨响主旋律 + 低音，并在长音间隙填充低于主旋律的内声部 (role='inner')。"
        )

    if mel_total >= 8 and mel_56_ratio > 0.25:
        errors.append(
            f"声部倒置拦截: 有 {mel_56_ratio * 100:.0f}% 主旋律落在 5/6 低音弦上"
            f" -> 修复建议: 用 scripts/fret_helper.py jianpu 把主旋律放到 1-3 弦高声部，低音留给 4-6 弦。"
        )

    if chord_ratio < 0.75:
        errors.append(
            f"谱面规范拦截: 和弦标记覆盖率仅 {chord_ratio * 100:.0f}% (< 75%)"
            f" -> 修复建议: 为每个完整小节填写准确的 chords=[(0, '...')] 和弦标记。"
        )

    if non_pickup_bars >= 16 and len(sections) < 2:
        errors.append(
            f"段落结构拦截: 缺少段落划分（仅 {len(sections)} 个 section 标记，至少需 2 个）"
            f" -> 修复建议: 标注清晰的 section='Intro'/'Verse'/'Chorus'/'Outro' 并配合 dynamic 强弱层次。"
        )

    if chord_bass_checks >= 4 and (chord_bass_hits / chord_bass_checks) < 0.65:
        sample = "；".join(chord_bass_mismatches[:3])
        errors.append(
            f"和声失配拦截: 和弦与低音严重不符（匹配率 {chord_bass_hits}/{chord_bass_checks} < 65%：{sample}）"
            f" -> 修复建议: 用 scripts/fret_helper.py chord 按标注和弦重查正确根音/低音，避免标 G 却乱弹外音。"
        )

    # Anti-cheat: reject forged non-chord-tone slash chords (e.g., G/A, Em/A, C/A, D/E) & half-speed split bugs
    total_chord_labels = 0
    non_tone_slash_chords = 0
    for meas in measures:
        for _sl, lbl in getattr(meas, "chords", []) or []:
            total_chord_labels += 1
            if "/" in lbl:
                base_lbl, bass_tok = lbl.rsplit("/", 1)
                base_pcs = chord_pitch_classes(base_lbl, 0)
                b_pc = PC_BY_TOKEN.get(bass_tok.strip().upper())
                if base_pcs and b_pc is not None and b_pc not in base_pcs:
                    non_tone_slash_chords += 1
    if total_chord_labels >= 8 and (non_tone_slash_chords / total_chord_labels) > 0.30:
        errors.append(
            f"防造假拦截: 非和弦音斜杠和弦过多（{non_tone_slash_chords}/{total_chord_labels} > 30%，如 G/A、Em/A、C/A）"
            f" -> 修复建议: 检查是否把 AlphaTab 弦号上下颠倒（AlphaTab string=1 是 6 弦低音 E，必须做 7-string 翻转！）或直接改用 scripts/import_score.py 导入，严禁伪造斜杠和弦凑低音。"
        )

    if non_pickup_bars >= 16 and bpm < 55 and sixteenth_events == 0:
        errors.append(
            f"半速异常拦截: TEMPO_BPM={bpm} 过慢且全曲 0 个 16 分音符"
            f" -> 修复建议: 检查是否误把每小节拆成两倍小节数却未翻倍 BPM（导致 0.5x 慢放），应保持原拍格或同步翻倍 BPM。"
        )

    expr_tech_total = (
        tech_counts["H"] + tech_counts["P"] + tech_counts["S"]
        + tech_counts["grace"] + tech_counts["harmonic"]
        + tech_counts["palm_mute"] + tech_counts["slap"]
    )
    if tier.key == "standard" and non_pickup_bars >= 16 and required_override is None and expr_tech_total == 0:
        errors.append(
            "表现力拦截: 标准指弹档 (TIER='standard') 完整曲目缺少任何指弹表情技巧（H/P/S/倚音/泛音/闷音/拍弦全为 0）"
            " -> 修复建议: 保留原谱击勾滑/倚音/泛音或用 scripts/import_score.py 导入，避免剥离技巧变成呆板机械谱。"
        )

    if allow_chromatic and pitched_total >= 16 and out_key_ratio > 0.20:
        errors.append(
            f"防作弊拦截: ALLOW_CHROMATIC=True 但调外音高达 {out_key_ratio * 100:.0f}% (> 20%)"
            f" -> 修复建议: 检查 KEY / CAPO / DIATONIC_PCS 是否设错，严禁靠开 ALLOW_CHROMATIC 掩盖大量错音。"
        )

    max_awkward = max(6, len(measures) // 6)
    if len(awkward_shifts) > max_awkward:
        b0, pf0, nf0, dt0 = awkward_shifts[0]
        errors.append(
            f"把位跳动拦截: 存在 {len(awkward_shifts)} 处高速大跳把位（如 M{b0:02d} {pf0}品→{nf0}品仅 {dt0:.2f}s，上限 {max_awkward}）"
            f" -> 修复建议: 用相邻把位换弦优化指法连贯性，避免反复瞬间大跳把位。"
        )

    # Physical quality score (0..100) + difficulty (Lv. 1.0..10.0) — aligned with guitar-tab-library
    tech_variety = sum(
        1
        for flag in (
            (tech_counts["H"] + tech_counts["P"] + tech_counts["S"]) > 0,
            tech_counts["arpeggio"] > 0,
            tech_counts["harmonic"] > 0,
            tech_counts["palm_mute"] > 0,
            tech_counts["slap"] > 0,
            tech_counts["grace"] > 0,
        )
        if flag
    )
    fretted_events_est = max(1, shift_transitions + 1)
    excess_hard = max(0, hard_stretches - int(fretted_events_est * 0.05))
    playability_f = 40.0
    playability_f -= min(18.0, len(impossible_chords) * 6.0)
    playability_f -= min(14.0, len(extreme_stretches) * 3.5)
    playability_f -= min(6.0, excess_hard * 0.8)
    playability_f -= min(8.0, len(awkward_shifts) * 1.5)
    playability_f -= min(10.0, crossings_count * 1.2)
    if max_fret_seen > 17:
        playability_f -= 2.0
    playability_pts = max(0, min(40, int(round(playability_f))))

    pts_bass = min(9.0, bass_ratio * 10.5) + min(2.0, len(distinct_bass) * 0.4)
    pts_poly = min(6.0, max(0.0, (avg_poly - 1.0) * 8.0)) + min(4.0, poly_ratio * 10.0)
    pts_tech = min(6.0, tech_variety * 1.6) + min(2.0, (tech_events / max(1, non_rest_events)) * 18.0)
    pts_struct = (
        min(3.0, len(sections) * 1.0)
        + min(2.0, max(0, len(dynamics) - 1) * 0.8)
        + (1.0 if non_pickup_bars >= 16 else (0.5 if non_pickup_bars >= 8 else 0.0))
    )
    arrangement_pts = max(0, min(35, int(round(pts_bass + pts_poly + pts_tech + pts_struct))))

    if bar_slot_sums:
        dom_slots = max(set(bar_slot_sums), key=bar_slot_sums.count)
        aligned_ratio = sum(1 for s in bar_slot_sums if s == dom_slots) / np_denom
    else:
        aligned_ratio = 0.0
    title_str = str(getattr(mod, "TITLE", "") or "").strip()
    artist_str = str(getattr(mod, "ARTIST", "") or getattr(mod, "COMPOSER", "") or "").strip()
    has_title = bool(title_str and title_str.lower() not in ("untitled", "my song", "test"))
    has_artist = bool(artist_str and artist_str.lower() != "unknown")
    has_setup = hasattr(mod, "CAPO") or bool(getattr(mod, "ARRANGER_INFO", ""))
    pts_meta = (
        (1.5 if has_title else 0.0)
        + (1.5 if has_artist else 0.0)
        + (1.0 if bpm > 0 else 0.0)
        + (1.0 if has_setup else 0.0)
    )
    pts_clean = max(0.0, 3.0 - empty_bars * 1.0 - (1.5 if non_pickup_bars < 4 else 0.0))
    notation_pts = max(0, min(25, int(round(aligned_ratio * 10.0 + min(7.0, chord_ratio * 7.8) + pts_meta + pts_clean))))
    quality_score = max(0, min(100, playability_pts + arrangement_pts + notation_pts))

    min_quality = 60 if allow_short_demo else (68 if tier.key == "beginner" else 75)
    if quality_score < min_quality:
        errors.append(
            f"综合质量门禁未达标: 当前 {quality_score}分 (手型{playability_pts}/40 · 编配{arrangement_pts}/35 · 规范{notation_pts}/25)，"
            f"档位 '{tier.key}' 最低要求 {min_quality}分"
            f" -> 修复建议: 补强低音/复调织体、和弦与段落层次或优化左手把位跨度。"
        )

    dur_sec = max(1.0, total_slots * sec_per_slot)
    nps = pitched_total / dur_sec
    sorted_bnps = sorted(bar_nps_list) if bar_nps_list else [nps]
    p90_nps = sorted_bnps[min(len(sorted_bnps) - 1, int(len(sorted_bnps) * 0.9))]
    eff_nps = 0.55 * nps + 0.45 * p90_nps
    ratio_16th = sixteenth_events / max(1, non_rest_events)
    d_speed = min(
        3.0,
        max(0.0, (eff_nps - 1.0) * 0.42)
        + (0.35 if ratio_16th >= 0.08 else (0.15 if sixteenth_events > 0 else 0.0)),
    )
    sorted_spans = sorted(spans_cm) if spans_cm else [0.0]
    p95_span = sorted_spans[min(len(sorted_spans) - 1, int(len(sorted_spans) * 0.95))]
    barre_ratio = barre_events / max(1, non_rest_events)
    d_hand = min(2.2, min(1.3, max(0.0, (p95_span - 6.0) * 0.16)) + min(0.9, barre_ratio * 4.5))
    avg_shift_vel = sum(shift_velocities) / max(1, len(shift_velocities))
    large_shift_ratio = large_shifts / max(1, shift_transitions)
    d_neck = min(
        1.8,
        min(0.9, max(0.0, (max_fret_seen - 3) * 0.075))
        + min(0.9, large_shift_ratio * 3.0 + min(0.45, avg_shift_vel / 25.0)),
    )
    d_poly = min(1.4, max(0.0, (avg_poly - 1.0) * 1.1) + (0.3 if max_simul >= 4 else 0.0))
    d_tech = min(1.2, tech_variety * 0.18 + min(0.5, (tech_events / max(1, non_rest_events)) * 2.5))
    difficulty = round(min(10.0, max(1.0, 1.0 + d_speed + d_hand + d_neck + d_poly + d_tech)), 1)

    summary = {
        "tier": tier.key,
        "tier_label": tier.label_zh,
        "measures": len(measures),
        "first_bar": measures[0].bar,
        "last_bar": measures[-1].bar,
        "total_notes": total_notes,
        "max_fret": max_fret_seen,
        "landmarks": len(landmarks),
        "quality_score": quality_score,
        "score_breakdown": {
            "playability": playability_pts,
            "arrangement": arrangement_pts,
            "notation": notation_pts,
        },
        "difficulty": difficulty,
    }
    return errors, tech_counts, summary
