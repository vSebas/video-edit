"""Platform-grounded music discovery.

Separates the creative INTENT (mood / energy / tempo, derived from the story and
the measured style) from the platform CATALOG (real tracks from a provider). A
provider proposes candidates; ranking picks; the plan stores a LATE-BOUND
recommendation — an audio id + timing metadata, never a burned copyrighted track.

Design reference:
PHONE_ORCHESTRATION_AND_SOCIAL_MUSIC_INTEGRATION_DESIGN_2026-09-03.md §16-25.

This module is deliberately free of network/provider imports: the model provider
takes an injected `chat` callable, so intent-building, normalization and ranking
are all pure and unit-testable. Real HTTP providers live behind the same
`MusicDiscoveryProvider` protocol and are wired in projects.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Protocol

ENERGIES = ("low", "medium", "high")


# --------------------------------------------------------------------------- #
# Intent — platform-independent creative request                              #
# --------------------------------------------------------------------------- #
@dataclass
class MusicIntent:
    """What the cut wants, before any platform is consulted."""

    concept_id: str | None = None
    title: str = ""
    duration_seconds: float = 0.0
    mood: list[str] = field(default_factory=list)
    energy: str | None = None
    bpm_preferred: int | None = None
    bpm_range: tuple[int, int] | None = None
    niche: list[str] = field(default_factory=list)
    platform_targets: list[str] = field(default_factory=list)


def build_intent(
    concept: dict | None,
    style_application: dict | None,
    duration_seconds: float,
    platform_targets: list[str] | None = None,
) -> MusicIntent:
    """Derive a MusicIntent from the story concept and the MEASURED style.

    Tempo/energy come from the applied style's beat grid and pacing (measured,
    not guessed); mood/niche from the concept's editorial tone. Nothing here
    invents a track — only the request."""
    concept = concept or {}
    editorial = concept.get("editorial") or {}
    tone = [t for t in (editorial.get("tone") or []) if isinstance(t, str)]

    bpm = None
    energy = None
    targets = (style_application or {}).get("targets") or {}
    measured_bpm = targets.get("bpm_estimate")
    if isinstance(measured_bpm, (int, float)) and 30 <= measured_bpm <= 300:
        bpm = int(round(measured_bpm))
    cpm = targets.get("cuts_per_minute")
    if isinstance(cpm, (int, float)):
        energy = "high" if cpm >= 40 else "low" if cpm <= 15 else "medium"
    # tone can override energy (an explicit calm/energetic tone wins)
    if any(t in ("energetic", "upbeat", "chaotic", "intense") for t in tone):
        energy = "high"
    elif any(t in ("calm", "cozy", "nostalgic", "reflective") for t in tone):
        energy = energy if energy == "high" else "low"

    bpm_range = None
    if bpm is not None:
        bpm_range = (max(30, bpm - 12), min(300, bpm + 12))

    return MusicIntent(
        concept_id=concept.get("concept_id"),
        title=str(concept.get("title") or "").strip(),
        duration_seconds=float(duration_seconds or 0.0),
        mood=tone[:4],
        energy=energy,
        bpm_preferred=bpm,
        bpm_range=bpm_range,
        niche=[n for n in (concept.get("niche") or []) if isinstance(n, str)][:4],
        platform_targets=platform_targets or [],
    )


# --------------------------------------------------------------------------- #
# Candidate — a normalized track from any provider                            #
# --------------------------------------------------------------------------- #
@dataclass
class MusicCandidate:
    """One track a provider surfaced, normalized. Unknown fields stay None — a
    provider must never invent data it cannot support (design §19)."""

    provider: str
    title: str | None = None
    artist: str | None = None
    vibe: str | None = None
    platform: str | None = None
    platform_audio_id: str | None = None
    bpm: int | None = None
    energy: str | None = None
    duration_seconds: float | None = None
    source_offset_seconds: float | None = None
    trend_state: str | None = None        # rising | steady | falling | unknown
    trend_rank: int | None = None
    trend_velocity: float | None = None
    availability_region: str | None = None
    account_usable: bool | None = None
    provenance: str = "unknown"

    def to_recommended(self) -> dict:
        """The `recommended` block for a plan music track. Late-bound: it carries
        the platform audio id + timing so the user adds the real track natively;
        no audio is burned."""
        rec: dict = {
            "name": (
                f"{self.title} — {self.artist}"
                if self.title and self.artist else self.title
            ),
            "vibe": self.vibe,
            "bpm": self.bpm if (self.bpm and 30 <= self.bpm <= 300) else None,
            "energy": self.energy if self.energy in ENERGIES else None,
            "apply_in_app": True,
            "platform": self.platform,
            "provider": self.provider,
            "platform_audio_id": self.platform_audio_id,
            "artist": self.artist,
            "source_offset_seconds": self.source_offset_seconds,
            # late_bound is true whenever we point at a platform track rather
            # than a model-invented name with no id
            "late_bound": bool(self.platform_audio_id),
            "trend_state": self.trend_state,
        }
        return {k: v for k, v in rec.items() if v is not None}


def _clamp_energy(value) -> str | None:
    return value if value in ENERGIES else None


def _clamp_bpm(value) -> int | None:
    try:
        bpm = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return bpm if 30 <= bpm <= 300 else None


# --------------------------------------------------------------------------- #
# Provider protocol                                                           #
# --------------------------------------------------------------------------- #
class MusicDiscoveryProvider(Protocol):
    name: str

    def available(self) -> bool:
        """True when the provider can serve requests (e.g. a key is configured)."""
        ...

    def search_audio(self, intent: MusicIntent) -> list[MusicCandidate]:
        ...

    def trending_audio(self, intent: MusicIntent) -> list[MusicCandidate]:
        ...


# --------------------------------------------------------------------------- #
# Ranking (design §20)                                                        #
# --------------------------------------------------------------------------- #
# S = w_b·bpm + w_e·energy + w_t·trend + w_a·availability
_W_BPM = 0.35
_W_ENERGY = 0.30
_W_TREND = 0.20
_W_AVAIL = 0.15


def _bpm_score(intent: MusicIntent, c: MusicCandidate) -> float:
    if intent.bpm_preferred is None or c.bpm is None:
        return 0.5  # unknown → neutral
    diff = abs(c.bpm - intent.bpm_preferred)
    return max(0.0, 1.0 - diff / 40.0)  # within ~40 BPM, linearly


def _energy_score(intent: MusicIntent, c: MusicCandidate) -> float:
    if intent.energy is None or c.energy is None:
        return 0.5
    order = {"low": 0, "medium": 1, "high": 2}
    return 1.0 - abs(order[intent.energy] - order[c.energy]) / 2.0


def _trend_score(c: MusicCandidate) -> float:
    if c.trend_velocity is not None:
        return max(0.0, min(1.0, c.trend_velocity))
    return {"rising": 0.9, "steady": 0.6, "falling": 0.2}.get(
        c.trend_state or "", 0.5
    )


def rank_candidates(
    intent: MusicIntent, candidates: list[MusicCandidate]
) -> list[MusicCandidate]:
    """Score and sort. Availability is a NEAR-HARD constraint: a track that
    cannot be attached to the target account is dropped (design §20) — a perfect
    song you can't use should not rank first."""
    usable = [c for c in candidates if c.account_usable is not False]

    def score(c: MusicCandidate) -> float:
        avail = 1.0 if c.account_usable else (0.5 if c.account_usable is None else 0.0)
        return (
            _W_BPM * _bpm_score(intent, c)
            + _W_ENERGY * _energy_score(intent, c)
            + _W_TREND * _trend_score(c)
            + _W_AVAIL * avail
        )

    return sorted(usable, key=score, reverse=True)


# --------------------------------------------------------------------------- #
# Model provider — the working default (no external catalog)                  #
# --------------------------------------------------------------------------- #
class ModelMusicProvider:
    """Falls back to the language model's own knowledge when no real platform
    provider is configured. It NAMES plausible tracks fitting the intent — it
    does not know current popularity or platform availability, so those fields
    stay unknown (provenance = model_knowledge). This is the honest default the
    design keeps as a fallback (§4 of the phases)."""

    name = "model"

    def __init__(self, chat: Callable[[list[dict]], str]):
        self._chat = chat

    def available(self) -> bool:
        return True

    def _ask(self, intent: MusicIntent, want: int) -> list[MusicCandidate]:
        bpm = intent.bpm_preferred or "desconocido"
        mood = ", ".join(intent.mood) or "sin tono marcado"
        messages = [
            {"role": "system", "content": (
                "Sugieres MÚSICA DE FONDO para un vlog corto que el creador "
                "agregará como audio nativo al publicar en Instagram/TikTok (no "
                "se incrusta audio, no hay licencias). Propón pistas concretas y "
                "buscables: nombre de canción + artista real, ajustadas al tempo "
                "y energía. Responde SOLO JSON: "
                '{"candidates":[{"title":"...","artist":"...","vibe":"<breve, es>",'
                '"bpm":<30-300 o null>,"energy":"low|medium|high"}]}'
            )},
            {"role": "user", "content": (
                f"Vlog: «{intent.title or 'sin título'}» · "
                f"{intent.duration_seconds:.0f}s · tono: {mood}. "
                f"Ritmo medido: BPM {bpm}, energía {intent.energy or 'medium'}. "
                f"Sugiere {want} pistas distintas que encajen."
            )},
        ]
        parsed = json.loads(_extract_json(self._chat(messages)))
        rows = parsed.get("candidates") if isinstance(parsed, dict) else None
        if not isinstance(rows, list):
            rows = [parsed] if isinstance(parsed, dict) else []
        out: list[MusicCandidate] = []
        for row in rows[:want]:
            if not isinstance(row, dict):
                continue
            out.append(MusicCandidate(
                provider=self.name,
                title=(str(row.get("title")).strip() or None) if row.get("title") else None,
                artist=(str(row.get("artist")).strip() or None) if row.get("artist") else None,
                vibe=(str(row.get("vibe")).strip()[:120] or None) if row.get("vibe") else None,
                bpm=_clamp_bpm(row.get("bpm")),
                energy=_clamp_energy(row.get("energy")) or intent.energy,
                provenance="model_knowledge",
            ))
        return out

    def search_audio(self, intent: MusicIntent) -> list[MusicCandidate]:
        return self._ask(intent, want=3)

    # the model has no real trend data; treat "trending" as "give a few options"
    def trending_audio(self, intent: MusicIntent) -> list[MusicCandidate]:
        return self._ask(intent, want=3)


def _extract_json(content: str) -> str:
    """Tolerate ```json fences / surrounding prose around a JSON object."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    return text[start:end + 1] if start != -1 and end != -1 else text


# --------------------------------------------------------------------------- #
# Discovery entry point                                                       #
# --------------------------------------------------------------------------- #
def discover(
    intent: MusicIntent,
    providers: list[MusicDiscoveryProvider],
    prefer_trending: bool = True,
) -> tuple[list[MusicCandidate], str]:
    """Query the first AVAILABLE provider (real platform providers are listed
    before the model fallback), rank, and return (candidates, provider_name).
    A provider that errors or returns nothing falls through to the next."""
    last_error: Exception | None = None
    for provider in providers:
        try:
            if not provider.available():
                continue
            found = (
                provider.trending_audio(intent) if prefer_trending
                else provider.search_audio(intent)
            )
            if not found:
                found = provider.search_audio(intent)
            ranked = rank_candidates(intent, found)
            if ranked:
                return ranked, provider.name
        except Exception as exc:  # noqa: BLE001 — fall through to next provider
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    return [], ""
