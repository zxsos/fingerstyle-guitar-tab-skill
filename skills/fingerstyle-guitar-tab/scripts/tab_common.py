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
    diatonic_pcs: Set[int] = set(getattr(mod, "DIATONIC_PCS", {0, 2, 4, 5, 7, 9, 11}))
    allow_chromatic = bool(getattr(mod, "ALLOW_CHROMATIC", False))
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

    if not measures:
        return (["build_measures() returned empty list -> 至少添加 1 个 Measure (bar=1)。"], tech_counts, {})

    has_pickup = bool(getattr(measures[0], "is_pickup", False) or measures[0].bar == 0)
    expected_bar_start = 0 if has_pickup else 1
    last_bar = measures[-1].bar

    active_melody_midi: Optional[int] = None
    max_fret_seen = 0
    total_notes = 0
    bar_by_idx: Dict[int, Any] = {}

    for i, meas in enumerate(measures):
        expected_bar = expected_bar_start + i
        if meas.bar != expected_bar:
            errors.append(
                f"M{meas.bar:02d}: 小节序号错位，期望 bar={expected_bar} "
                f"-> 修复建议: 按顺序从 bar={expected_bar_start} 连续递增。"
            )
        bar_by_idx[meas.bar] = meas

        if not meas.events:
            errors.append(f"M{meas.bar:02d}: events 为空 -> 修复建议: 至少添加填满小节的 BeatEvent (可用 rest=True)。")
            continue

        for sl, lbl in getattr(meas, "chords", []):
            try:
                chord_to_lilypond(lbl, "4")
            except ValueError as exc:
                errors.append(f"M{meas.bar:02d} slot {sl}: {exc}")

        if i == 0 and getattr(meas, "is_pickup", False):
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

            if e.rest:
                active_melody_midi = None
                if e.notes:
                    errors.append(f"M{meas.bar:02d} slot {e.slot}: 休止符事件 (rest=True) 不能带 notes -> 删除 notes。")
                if e.arpeggio or e.link_tech:
                    errors.append(f"M{meas.bar:02d} slot {e.slot}: 休止符不能带 arpeggio/link_tech -> 设为 False/None。")
                continue

            if not e.notes:
                errors.append(f"M{meas.bar:02d} slot {e.slot}: 非休止事件缺少音符 -> 添加 n(...) 或改 rest=True。")
                continue

            total_notes += len(e.notes)
            if len(e.notes) > tier.max_simultaneous:
                errors.append(
                    f"M{meas.bar:02d} slot {e.slot}: 同时拨弦数 {len(e.notes)} 超过档位 TIER='{tier.key}' 上限 ({tier.max_simultaneous}) "
                    f"-> 修复建议: 删减内声部 inner 音至 <={tier.max_simultaneous} 音，或升级 TIER='standard'。"
                )

            if e.arpeggio:
                tech_counts["arpeggio"] += 1
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
            mel_notes = [nt for nt in pitched_notes if nt.role == "melody"]
            if mel_notes:
                if any(nt.harmonic for nt in mel_notes):
                    active_melody_midi = None
                else:
                    active_melody_midi = max(note_concert_midi(nt, capo) for nt in mel_notes)

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

                if not allow_chromatic and (cm % 12) not in diatonic_pcs:
                    fixes = []
                    for df in (-1, 1):
                        nf = nt.fret + df
                        if 0 <= nf <= tier.max_fret and ((cm + df) % 12) in diatonic_pcs:
                            fixes.append(f"n({nt.string}, {nf}, \"{nt.role}\") [{midi_to_name(cm + df)}]")
                    tip = " 或 ".join(fixes) if fixes else "确认简谱/调性是否错音，若为刻意调外经过音请设 ALLOW_CHROMATIC = True"
                    errors.append(
                        f"M{meas.bar:02d} slot {e.slot}: 调外音 n({nt.string},{nt.fret}) = {midi_to_name(cm)} "
                        f"-> 修复建议: 邻近调内音 {tip}。"
                    )

                if nt.harmonic:
                    tech_counts["harmonic"] += 1
                    if "harmonic" not in tier.allowed_techs:
                        errors.append(
                            f"M{meas.bar:02d} slot {e.slot}: 档位 '{tier.key}' 禁用泛音 harmonic -> 改为 harmonic=False 或升级 TIER='standard'。"
                        )
                    if nt.fret != 12:
                        errors.append(f"M{meas.bar:02d} slot {e.slot}: 自然泛音需设为 fret=12，当前为 {nt.fret}。")

                if nt.palm_mute:
                    tech_counts["palm_mute"] += 1
                    if "palm_mute" not in tier.allowed_techs:
                        errors.append(
                            f"M{meas.bar:02d} slot {e.slot}: 档位 '{tier.key}' 禁用闷音 palm_mute -> 改为 palm_mute=False 或升级 TIER='standard'。"
                        )

                if nt.grace_from is not None:
                    tech_counts["grace"] += 1
                    tech_counts["S"] += 1
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
                    # Try relocating extreme fretted note to another string inside the comfortable window
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

            if e.link_tech is not None:
                if e.link_tech not in ("H", "P", "S"):
                    errors.append(f"M{meas.bar:02d} slot {e.slot}: 非法 link_tech='{e.link_tech}'，仅允许 'H'/'P'/'S'。")
                else:
                    tech_counts[e.link_tech] += 1
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

        if cursor != expected_end or dur_sum != expected_sum:
            diff = expected_sum - dur_sum
            hint = f"少了 {diff} 个 16 分时值" if diff > 0 else f"多了 {-diff} 个 16 分时值"
            errors.append(
                f"M{meas.bar:02d}: 小节时值之和={dur_sum}，期望 [{expected_start}..{expected_end}) 总和={expected_sum} ({hint}) "
                f"-> 修复建议: 补齐或缩短本小节最后几个事件的 dur。"
            )

    for req_t in required_techs:
        if tech_counts.get(req_t, 0) < 1:
            errors.append(
                f"技巧校验: 档位 '{tier.key}' 要求至少出现 1 次 '{req_t}'，当前为 0 "
                f"-> 修复建议: 添加 1 处 '{req_t}' 或在 score_data.py 中调整 TIER/REQUIRED_TECHS。"
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

    summary = {
        "tier": tier.key,
        "tier_label": tier.label_zh,
        "measures": len(measures),
        "first_bar": measures[0].bar,
        "last_bar": measures[-1].bar,
        "total_notes": total_notes,
        "max_fret": max_fret_seen,
        "landmarks": len(landmarks),
    }
    return errors, tech_counts, summary
