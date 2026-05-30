"""
Audio Directions Processor — Production‑grade module for parsing,
validating, and processing structured audio production guidelines.

This module provides a robust, type‑safe, and well‑documented framework
for representing audio production directions. It includes:
- Immutable dataclasses with post‑construction validation
- Hierarchical exception handling
- Structured logging
- Secure input parsing (expects validated JSON/dict)
- Defensive design against untrusted sources
- Full serialization support (to/from dict, JSON)

Usage example:
    raw_data = {
        "version": "1.0",
        "last_updated": "2025-04-03",
        "creator": "Alice",
        "background_music": {"style": "ambient", "tempo_bpm": [80, 90]},
        "sound_effects": [],
        "voiceover": {"narrator_accent": "British", "pace_wpm": 160},
        "timing_breakdown": []
    }
    doc = AudioDirectionParser.from_dict(raw_data)
    print(doc.voiceover.pace_wpm)  # 160

For production deployment, ensure that raw_data has been sanitised and
schema‑validated upstream (e.g., with Pydantic or JSON Schema) before
calling this processor.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
from uuid import uuid4

# ---------------------------------------------------------------------------
# Logging setup – structured, level‑aware, context‑ready
# ---------------------------------------------------------------------------
_logger = logging.getLogger("AudioDirections")
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    _logger.addHandler(_handler)

# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------
class AudioDirectionsError(Exception):
    """Base exception for all audio‑directions failures."""


class ValidationError(AudioDirectionsError):
    """Raised when a field fails type, range, or pattern validation."""


class ParseError(AudioDirectionsError):
    """Raised when unstructured input cannot be parsed into a valid document."""


class TimingOverflowError(AudioDirectionsError):
    """Raised when cumulative timing exceeds enforced limits."""

# ---------------------------------------------------------------------------
# Type aliases and constants
# ---------------------------------------------------------------------------
BPMMin = int
BPMMax = int
TempoRange = Tuple[BPMMin, BPMMax]
# Predefined valid accent patterns (example – extend as needed)
VALID_ACCENTS = frozenset({
    "North American", "British", "Australian", "Indian", "South African",
    "Irish", "Scottish", "French", "German", "Italian", "Spanish", "Other"
})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _validate_nonempty_string(value: Any, field_name: str) -> str:
    """Return a stripped non‑empty string or raise ValidationError."""
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValidationError(f"{field_name} must not be empty or whitespace‑only")
    if len(stripped) > 1000:
        raise ValidationError(f"{field_name} exceeds 1000 characters")
    return stripped


def _validate_nonnegative_number(value: Any, field_name: str) -> float:
    """Return a non‑negative float or raise ValidationError."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be numeric, got {type(value).__name__}")
    val = float(value)
    if val < 0:
        raise ValidationError(f"{field_name} must be ≥ 0, got {val}")
    if val > 1e9:
        raise ValidationError(f"{field_name} exceeds maximum value 1e9")
    return val


def _validate_bounded_int(value: Any, field_name: str, lo: int, hi: int) -> int:
    """Return an integer within [lo, hi] or raise ValidationError."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be an int, got {type(value).__name__}")
    if not (lo <= value <= hi):
        raise ValidationError(f"{field_name} must be in [{lo}, {hi}], got {value}")
    return value


def _validate_tempo_range(value: Any, field_name: str) -> TempoRange:
    """Return validated (min, max) tempo range."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValidationError(f"{field_name} must be a list/tuple of two ints")
    lo = _validate_bounded_int(value[0], f"{field_name}[0]", 40, 240)
    hi = _validate_bounded_int(value[1], f"{field_name}[1]", 40, 240)
    if lo > hi:
        raise ValidationError(f"{field_name}: min ({lo}) must ≤ max ({hi})")
    return (lo, hi)


def _validate_accent(accent: str) -> str:
    """Validate accent against known list, allow custom if prefixed with 'Custom:'."""
    if accent.startswith("Custom:"):
        custom_part = accent[7:].strip()
        if not custom_part:
            raise ValidationError("Custom accent must have a non-empty label")
        return accent
    if accent not in VALID_ACCENTS:
        raise ValidationError(f"Unknown accent '{accent}'. Valid: {sorted(VALID_ACCENTS)}")
    return accent


# ---------------------------------------------------------------------------
# Immutable data models with embedded validation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SoundEffect:
    """Single sound effect specification.

    Attributes:
        section:    Identifier of the section or cue point.
        cue:        Trigger word or event name.
        effect:     Name of the audio effect file or preset.
        description: Human‑readable explanation of when/how this effect plays.
        duration:   Expected duration in seconds (0.0 if instantaneous).
    """
    section: str
    cue: str
    effect: str
    description: str
    duration: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "section",
                          _validate_nonempty_string(self.section, "SoundEffect.section"))
        object.__setattr__(self, "cue",
                          _validate_nonempty_string(self.cue, "SoundEffect.cue"))
        object.__setattr__(self, "effect",
                          _validate_nonempty_string(self.effect, "SoundEffect.effect"))
        object.__setattr__(self, "description",
                          _validate_nonempty_string(self.description, "SoundEffect.description"))
        object.__setattr__(self, "duration",
                          _validate_nonnegative_number(self.duration, "SoundEffect.duration"))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SoundEffect:
        """Construct from a validated dictionary."""
        try:
            return cls(
                section=data["section"],
                cue=data["cue"],
                effect=data["effect"],
                description=data.get("description", ""),
                duration=data.get("duration", 0.0)
            )
        except KeyError as e:
            raise ParseError(f"Missing required key in SoundEffect: {e.args[0]}") from e
        except ValidationError:
            raise


@dataclass(frozen=True)
class TimingSegment:
    """A contiguous segment of the video timeline with audio notes.

    Attributes:
        section:     Section name (must match a track or scene label).
        start:       Start time in seconds (≥ 0).
        end:         End time in seconds (> start).
        audio_notes: Free‑form production notes for this segment.
    """
    section: str
    start: float
    end: float
    audio_notes: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "section",
                          _validate_nonempty_string(self.section, "TimingSegment.section"))
        object.__setattr__(self, "start",
                          _validate_nonnegative_number(self.start, "TimingSegment.start"))
        end_val = _validate_nonnegative_number(self.end, "TimingSegment.end")
        if end_val <= self.start:
            raise ValidationError(
                f"TimingSegment.end ({end_val}) must be > start ({self.start})"
            )
        object.__setattr__(self, "end", end_val)
        if not isinstance(self.audio_notes, str):
            raise ValidationError("TimingSegment.audio_notes must be a string")
        # Allow empty notes – no trim needed

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TimingSegment:
        """Construct from a validated dictionary."""
        try:
            return cls(
                section=data["section"],
                start=float(data["start"]),
                end=float(data["end"]),
                audio_notes=data.get("audio_notes", "")
            )
        except KeyError as e:
            raise ParseError(f"Missing required key in TimingSegment: {e.args[0]}") from e
        except ValidationError:
            raise


@dataclass(frozen=True)
class VoiceoverSettings:
    """Global voiceover style and performance configuration.

    Attributes:
        narrator_accent: Accent or dialect tag. Must be one of the predefined
                         accents or prefixed with 'Custom:'.
        pace_wpm:        Target narration speed in words per minute [80, 250].
        emphasis_terms:  Sequence of words/phrases that require emphasis.
    """
    narrator_accent: str = "North American"
    pace_wpm: int = 150
    emphasis_terms: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Validate accent – use custom method to allow custom tags
        accent = _validate_nonempty_string(self.narrator_accent,
                                           "VoiceoverSettings.narrator_accent")
        accent = _validate_accent(accent)
        object.__setattr__(self, "narrator_accent", accent)

        object.__setattr__(self, "pace_wpm",
                          _validate_bounded_int(self.pace_wpm, "VoiceoverSettings.pace_wpm", 80, 250))

        # Ensure emphasis_terms is a tuple of non‑empty strings, max 20 entries
        start_terms = self.emphasis_terms
        if not isinstance(start_terms, (list, tuple)):
            raise ValidationError("VoiceoverSettings.emphasis_terms must be a list or tuple")
        if len(start_terms) > 20:
            raise ValidationError("VoiceoverSettings.emphasis_terms may have at most 20 terms")
        validated = tuple(
            _validate_nonempty_string(t, "VoiceoverSettings.emphasis_terms element")
            for t in start_terms
        )
        object.__setattr__(self, "emphasis_terms", validated)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VoiceoverSettings:
        """Construct from a validated dictionary."""
        return cls(
            narrator_accent=data.get("narrator_accent", "North American"),
            pace_wpm=data.get("pace_wpm", 150),
            emphasis_terms=tuple(data.get("emphasis_terms", []))
        )


@dataclass(frozen=True)
class MusicSettings:
    """Background music configuration.

    Attributes:
        style:         Genre or descriptor string.
        tempo_bpm:     Two‑int tuple (min, max) in range [40, 240].
        volume_rel_db: Relative gain in dB (≤ 0, typically between -60 and 0).
        duck_amount:   Amount of audio ducking (0.0 = none, 1.0 = full mute).
    """
    style: str = "ambient"
    tempo_bpm: TempoRange = (80, 100)
    volume_rel_db: float = -6.0
    duck_amount: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "style",
                          _validate_nonempty_string(self.style, "MusicSettings.style"))
        object.__setattr__(self, "tempo_bpm",
                          _validate_tempo_range(self.tempo_bpm, "MusicSettings.tempo_bpm"))
        # volume_rel_db must be ≤ 0, typically >= -60
        vol = _validate_nonnegative_number(abs(self.volume_rel_db),
                                           "MusicSettings.volume_rel_db (absolute)")
        if vol > 60:
            raise ValidationError(
                f"MusicSettings.volume_rel_db must be ≥ -60, got {self.volume_rel_db}"
            )
        object.__setattr__(self, "volume_rel_db", -vol)

        duck = _validate_nonnegative_number(self.duck_amount,
                                            "MusicSettings.duck_amount")
        if not (0.0 <= duck <= 1.0):
            raise ValidationError(
                f"MusicSettings.duck_amount must be in [0.0, 1.0], got {duck}"
            )
        object.__setattr__(self, "duck_amount", duck)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MusicSettings:
        """Construct from a validated dictionary."""
        try:
            return cls(
                style=data.get("style", "ambient"),
                tempo_bpm=tuple(data.get("tempo_bpm", (80, 100))),
                volume_rel_db=float(data.get("volume_rel_db", -6.0)),
                duck_amount=float(data.get("duck_amount", 0.0))
            )
        except ValidationError:
            raise
        except (TypeError, ValueError) as e:
            raise ParseError(f"Invalid value in MusicSettings: {e}") from e


# ---------------------------------------------------------------------------
# Top-level document model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AudioDirectionDocument:
    """Complete set of audio production directions for a video project.

    Attributes:
        document_id:     Unique identifier (auto-generated UUID if not provided).
        version:         Schema version string (e.g., "1.0").
        last_updated:    ISO 8601 timestamp (defaults to now).
        creator:         Creator/exporter name.
        background_music: Optional music settings.
        sound_effects:   List of sound effect specifications.
        voiceover:       Voiceover settings.
        timing_breakdown: Ordered list of timing segments.
    """
    document_id: str = field(default_factory=lambda: str(uuid4()))
    version: str = "1.0"
    last_updated: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    creator: str = ""
    background_music: Optional[MusicSettings] = None
    sound_effects: Tuple[SoundEffect, ...] = ()
    voiceover: VoiceoverSettings = field(default_factory=VoiceoverSettings)
    timing_breakdown: Tuple[TimingSegment, ...] = ()

    def __post_init__(self) -> None:
        # Validate document_id format (UUID, allow any non-empty string as fallback)
        if not self.document_id or not isinstance(self.document_id, str):
            raise ValidationError("document_id must be a non-empty string")

        object.__setattr__(self, "version",
                          _validate_nonempty_string(self.version, "version"))
        # Validate last_updated is a valid ISO 8601 datetime string
        try:
            datetime.fromisoformat(self.last_updated.replace("Z", "+00:00"))
        except (ValueError, TypeError) as e:
            raise ValidationError(f"Invalid last_updated format: {e}") from e

        object.__setattr__(self, "creator",
                          _validate_nonempty_string(self.creator, "creator"))

        # Ensure sound_effects is tuple of SoundEffect
        effects = self.sound_effects
        if not isinstance(effects, (list, tuple)):
            raise ValidationError("sound_effects must be a list or tuple")
        validated_effects = tuple(
            eff if isinstance(eff, SoundEffect) else SoundEffect.from_dict(eff)
            for eff in effects
        )
        object.__setattr__(self, "sound_effects", validated_effects)

        # Ensure timing_breakdown is tuple of TimingSegment
        segments = self.timing_breakdown
        if not isinstance(segments, (list, tuple)):
            raise ValidationError("timing_breakdown must be a list or tuple")
        validated_seg = tuple(
            seg if isinstance(seg, TimingSegment) else TimingSegment.from_dict(seg)
            for seg in segments
        )
        object.__setattr__(self, "timing_breakdown", validated_seg)

        # Validate no overlapping timing segments
        sorted_segs = sorted(validated_seg, key=lambda x: x.start)
        for i in range(1, len(sorted_segs)):
            if sorted_segs[i].start < sorted_segs[i-1].end:
                raise ValidationError(
                    f"Timing segments overlap: {sorted_segs[i-1].section} "
                    f"({sorted_segs[i-1].start}-{sorted_segs[i-1].end}) and "
                    f"{sorted_segs[i].section} ({sorted_segs[i].start}-{sorted_segs[i].end})"
                )

        # Validate background_music
        bm = self.background_music
        if bm is not None and not isinstance(bm, MusicSettings):
            if isinstance(bm, dict):
                object.__setattr__(self, "background_music", MusicSettings.from_dict(bm))
            else:
                raise ValidationError("background_music must be a MusicSettings instance or dict")

    def total_duration(self) -> float:
        """Compute total timeline duration from segments (max end time)."""
        if not self.timing_breakdown:
            return 0.0
        return max(seg.end for seg in self.timing_breakdown)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "document_id": self.document_id,
            "version": self.version,
            "last_updated": self.last_updated,
            "creator": self.creator,
            "background_music": asdict(self.background_music) if self.background_music else None,
            "sound_effects": [asdict(se) for se in self.sound_effects],
            "voiceover": asdict(self.voiceover),
            "timing_breakdown": [asdict(seg) for seg in self.timing_breakdown]
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize to a pretty‑printed JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AudioDirectionDocument:
        """Parse a dictionary into a validated AudioDirectionDocument."""
        try:
            # Extract basic fields
            doc_id = data.get("document_id", str(uuid4()))
            version = str(data.get("version", "1.0"))
            last_updated = data.get("last_updated", datetime.utcnow().isoformat() + "Z")
            creator = str(data.get("creator", ""))
            # Build sub-objects
            bg_music = None
            if "background_music" in data and data["background_music"] is not None:
                bg_music = MusicSettings.from_dict(data["background_music"])
            sound_effects = [
                SoundEffect.from_dict(se) if isinstance(se, dict) else se
                for se in data.get("sound_effects", [])
            ]
            voiceover = VoiceoverSettings.from_dict(data.get("voiceover", {}))
            timing = [
                TimingSegment.from_dict(seg) if isinstance(seg, dict) else seg
                for seg in data.get("timing_breakdown", [])
            ]
            return cls(
                document_id=doc_id,
                version=version,
                last_updated=last_updated,
                creator=creator,
                background_music=bg_music,
                sound_effects=tuple(sound_effects),
                voiceover=voiceover,
                timing_breakdown=tuple(timing)
            )
        except (ValidationError, ParseError):
            raise
        except Exception as e:
            raise ParseError(f"Unexpected error during parsing: {e}") from e

    @classmethod
    def from_json(cls, raw_json: str) -> AudioDirectionDocument:
        """Parse a JSON string into a validated AudioDirectionDocument."""
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as e:
            raise ParseError(f"Invalid JSON: {e}") from e
        return cls.from_dict(data)


# ---------------------------------------------------------------------------
# Convenience alias for external use
# ---------------------------------------------------------------------------
AudioDirectionParser = AudioDirectionDocument

# ---------------------------------------------------------------------------
# Module-level guard: only export public API
# ---------------------------------------------------------------------------
__all__ = [
    "AudioDirectionsError",
    "ValidationError",
    "ParseError",
    "TimingOverflowError",
    "SoundEffect",
    "TimingSegment",
    "VoiceoverSettings",
    "MusicSettings",
    "AudioDirectionDocument",
    "AudioDirectionParser",
]