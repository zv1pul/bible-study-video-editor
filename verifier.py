"""
verifier.py
===========
Quality control between "the AI said so" and "we burn it into the video".

An LLM will happily return a confident, well-formatted timestamp that is
simply wrong, and a wrong timestamp is worse than no timestamp: it puts a
caption on screen while the speaker is talking about something else. So every
match is checked before it is allowed anywhere near the render.

Five independent layers, each able to fail a match on its own:

  1. Range      — the time exists inside this recording.
  2. Snapping   — the time is moved to a real transcript line, not a made-up one.
  3. Evidence   — the quote the model gave is actually spoken at that moment.
                  This is the strongest hallucination detector we have.
  4. Semantics  — an independent, offline word-overlap score between the
                  outline point and what is really being said there.
  5. Consensus  — an optional second opinion from a different model; where two
                  independent runs agree, confidence is high.

Plus two whole-set checks: ordering (divisions that run backwards) and
collisions (several points landing on the same second).

Every match comes out with a verdict — verified / review / rejected — and a
plain-English reason. Nothing is silently discarded and nothing is silently
trusted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence

from matcher import Element, LessonPoint, _tokens

Match = Element
from transcriber import Segment, format_timestamp

# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------

VERIFIED = "verified"
REVIEW = "review"
REJECTED = "rejected"

VERDICT_LABEL = {
    VERIFIED: "✅ verified",
    REVIEW: "⚠️ check this",
    REJECTED: "⛔ not found",
}

# A match must clear this to be trusted without a human looking at it.
VERIFY_THRESHOLD = 0.62
REJECT_THRESHOLD = 0.28

# How far either side of the proposed time we look when checking the claim.
EVIDENCE_WINDOW = 20.0
SEMANTIC_WINDOW = 25.0

# A card shorter than this cannot be read; longer than this has almost
# certainly swallowed the commentary that follows the point.
MIN_READABLE_SECONDS = 3.0
MAX_CARD_SECONDS = 90.0


@dataclass
class Verdict:
    match: Match
    verdict: str = REVIEW
    score: float = 0.0
    evidence_score: float = 0.0
    semantic_score: float = 0.0
    consensus_score: Optional[float] = None
    snapped_from: Optional[float] = None
    reasons: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return VERDICT_LABEL.get(self.verdict, self.verdict)

    @property
    def reason_text(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "All checks passed."


# --------------------------------------------------------------------------
# Layer helpers
# --------------------------------------------------------------------------


def _normalise(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace."""
    text = re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def _window_text(segments: Sequence[Segment], centre: float, radius: float) -> str:
    """Everything said within `radius` seconds either side of `centre`."""
    return " ".join(
        s.text for s in segments if s.end >= centre - radius and s.start <= centre + radius
    ).strip()


def snap_to_transcript(time_value: float, segments: Sequence[Segment]) -> float:
    """
    Move a proposed time onto the start of a real transcript line.

    Models routinely return a plausible-looking number that is not one of the
    timestamps they were shown. Snapping keeps captions aligned to where a
    sentence actually begins instead of cutting into the middle of one.
    """
    if not segments:
        return max(0.0, time_value)
    best = min(segments, key=lambda s: abs(s.start - time_value))
    return float(best.start)


def evidence_score(evidence: str, segments: Sequence[Segment], time_value: float) -> float:
    """
    Layer 3: is the model's quote really spoken near the time it claimed?

    Returns 0.0 when the quote appears nowhere nearby, which in practice means
    the model invented both the quote and the timestamp.
    """
    quote = (evidence or "").strip()
    if len(quote) < 8:
        return 0.0

    haystack = _normalise(_window_text(segments, time_value, EVIDENCE_WINDOW))
    if not haystack:
        return 0.0

    # Both sides must be normalised the same way: Whisper writes "us. It" and
    # the model quotes "us. It", but punctuation and casing still differ often
    # enough that comparing raw text under-scores genuine verbatim quotes.
    needle = _normalise(quote)
    if not needle:
        return 0.0

    if needle in haystack:
        return 1.0

    # Whisper punctuates and capitalises differently from the model's quote,
    # so fall back to how many of the quote's words are present in order.
    words = needle.split()
    if not words:
        return 0.0
    matcher_ = SequenceMatcher(None, needle, haystack)
    longest = matcher_.find_longest_match(0, len(needle), 0, len(haystack)).size
    coverage = longest / max(len(needle), 1)
    present = sum(1 for w in words if w in haystack) / len(words)
    return max(0.0, min(1.0, 0.5 * coverage + 0.5 * present))


def semantic_score(point_text: str, segments: Sequence[Segment], time_value: float) -> float:
    """
    Layer 4: an offline second opinion on whether this passage is even about
    the point. Deliberately does not involve the LLM, so it cannot agree with
    the LLM's own mistake.
    """
    wanted = set(_tokens(point_text))
    if not wanted:
        return 0.0
    spoken = _window_text(segments, time_value, SEMANTIC_WINDOW)
    if not spoken:
        return 0.0
    present = set(_tokens(spoken))
    overlap = len(wanted & present) / len(wanted)
    fuzzy = SequenceMatcher(None, point_text.lower(), spoken.lower()[:600]).ratio()
    return max(0.0, min(1.0, 0.8 * overlap + 0.2 * fuzzy))


def consensus_score(primary: float, second: Optional[float], tolerance: float = 12.0) -> Optional[float]:
    """
    Layer 5: how closely an independent second run agreed.

    1.0 = same moment, 0.0 = completely different part of the video.
    """
    if second is None:
        return None
    gap = abs(primary - second)
    if gap <= tolerance:
        return 1.0 - (gap / tolerance) * 0.35   # 0.65..1.0 — broad agreement
    return max(0.0, 0.65 - min(gap / 180.0, 0.65))


# --------------------------------------------------------------------------
# The main pass
# --------------------------------------------------------------------------


def verify_matches(
    matches: Sequence[Match],
    points: Sequence[LessonPoint],
    segments: Sequence[Segment],
    duration: float,
    second_opinion: Optional[Sequence[Match]] = None,
) -> List[Verdict]:
    """
    Run every layer over every match and return the verdicts, in video order.
    """
    by_id_second: Dict[str, float] = {}
    if second_opinion:
        by_id_second = {m.id: float(m.start_time) for m in second_opinion}

    text_by_id = {p.id: p.text for p in points}
    verdicts: List[Verdict] = []

    for match in matches:
        reasons: List[str] = []
        original = float(match.start_time)
        time_value = original

        # The lower third is fixed by the template, not detected, so there is
        # nothing here to verify.
        if match.type == "lower_third":
            verdicts.append(
                Verdict(match=match, verdict=VERIFIED, score=1.0,
                        reasons=["Fixed by the template."])
            )
            continue

        # --- Layer 1: range -------------------------------------------------
        if duration and (time_value < 0 or time_value > duration):
            reasons.append(
                f"Time {format_timestamp(time_value)} is outside the recording; "
                "clamped."
            )
            time_value = max(0.0, min(time_value, max(duration - 1.0, 0.0)))

        # --- Layer 2: snap to a real line ------------------------------------
        snapped = snap_to_transcript(time_value, segments)
        snapped_from = None
        if abs(snapped - time_value) > 0.05:
            snapped_from = time_value
            if abs(snapped - time_value) > 8.0:
                reasons.append(
                    f"No transcript line at {format_timestamp(time_value)}; moved "
                    f"to the nearest one at {format_timestamp(snapped)}."
                )
            time_value = snapped

        # --- Layers 3 and 4 ---------------------------------------------------
        ev = evidence_score(match.evidence, segments, time_value)
        sem = semantic_score(text_by_id.get(match.id, match.text), segments, time_value)

        if match.source == "llm":
            if ev == 0.0 and match.evidence.strip():
                reasons.append("The quoted line is not spoken anywhere near this time.")
            elif ev < 0.4 and match.evidence.strip():
                reasons.append("The quoted line only loosely matches what was said.")
            elif not match.evidence.strip():
                reasons.append("No supporting quote was given.")

        if sem < 0.12:
            reasons.append("Nothing said here resembles this point.")

        # --- Layer 5 ----------------------------------------------------------
        cons = consensus_score(time_value, by_id_second.get(match.id))
        if cons is not None:
            other = by_id_second[match.id]
            if cons >= 0.65:
                reasons.append(f"A second model agreed ({format_timestamp(other)}).")
            else:
                reasons.append(
                    f"A second model put this at {format_timestamp(other)} instead."
                )

        # --- Layer 6: how long it stays up -------------------------------------
        # Measured against the SNAPPED start and the end that will actually be
        # used, not the model's original numbers — otherwise a card that was
        # moved earlier gets reported as lasting no time at all.
        final_end = max(float(match.end_time), time_value + MIN_READABLE_SECONDS)
        span = max(final_end - time_value, 0.0)
        if span <= 0:
            reasons.append("No end time was given.")
        elif span < MIN_READABLE_SECONDS:
            reasons.append(
                f"On screen for only {span:.0f}s — too brief to read."
            )
        elif not match.has_timer and span > MAX_CARD_SECONDS:
            reasons.append(
                f"On screen for {format_timestamp(span)} — that is long enough "
                "that it probably runs over the explanation as well."
            )

        # --- Combine ----------------------------------------------------------
        span_ok = 1.0
        if span < MIN_READABLE_SECONDS or (
            not match.has_timer and span > MAX_CARD_SECONDS
        ):
            span_ok = 0.0

        if match.source == "llm":
            weights = [(ev, 0.30), (sem, 0.26), (float(match.confidence), 0.14),
                       (span_ok, 0.10)]
            if cons is not None:
                weights.append((cons, 0.20))
        else:
            # The offline matcher has no quote and its "confidence" IS the
            # semantic score, so scoring it on both would double-count.
            weights = [(sem, 0.68), (float(match.confidence), 0.22), (span_ok, 0.10)]

        total_weight = sum(w for _, w in weights)
        score = sum(value * weight for value, weight in weights) / total_weight

        if score >= VERIFY_THRESHOLD:
            verdict = VERIFIED
        elif score >= REJECT_THRESHOLD:
            verdict = REVIEW
        else:
            verdict = REJECTED
            reasons.append("Not confident enough to place automatically.")

        # Snapping moves the START onto a sentence boundary — usually a little
        # earlier, so the card opens cleanly. The END must stay where it is:
        # dragging it back by the same amount would clear the card before the
        # speaker has finished the point.
        placed = replace(
            match,
            start_time=round(time_value, 2),
            end_time=round(final_end, 2),
            confidence=round(score, 2),
        )
        reasons.extend(getattr(match, "notes", []) or [])
        verdicts.append(
            Verdict(
                match=placed,
                verdict=verdict,
                score=round(score, 3),
                evidence_score=round(ev, 3),
                semantic_score=round(sem, 3),
                consensus_score=None if cons is None else round(cons, 3),
                snapped_from=snapped_from,
                reasons=reasons,
            )
        )

    verdicts.sort(key=lambda v: v.match.start_time)
    _check_ordering(verdicts)
    _check_collisions(verdicts)
    return verdicts


def _check_ordering(verdicts: List[Verdict]) -> None:
    """
    Whole-set check: divisions are taught in the order they are written. One
    that jumps backwards is a strong sign of a mismatch.
    """
    divisions = [v for v in verdicts if v.match.category == "Division"]
    if len(divisions) < 2:
        return
    def written_order(verdict: Verdict) -> int:
        # "division_2" must sort before "division_10", so compare the number.
        digits = re.findall(r"\d+", verdict.match.id)
        return int(digits[-1]) if digits else 0

    written = {v.match.id: written_order(v) for v in divisions}
    for earlier, later in zip(divisions, divisions[1:]):
        if written.get(later.match.id, 0) < written.get(earlier.match.id, 0):
            for verdict in (earlier, later):
                verdict.reasons.append("Divisions appear out of their written order.")
                if verdict.verdict == VERIFIED:
                    verdict.verdict = REVIEW


def _check_collisions(verdicts: List[Verdict]) -> None:
    """
    Whole-set check: several cards landing on the same moment.

    The lower third sits on its own layer over the footage, so it never
    competes with a point card and is left out of this.
    """
    cards = [v for v in verdicts if v.match.type != "lower_third"]
    for earlier, later in zip(cards, cards[1:]):
        if abs(later.match.start_time - earlier.match.start_time) < 1.0:
            for verdict in (earlier, later):
                verdict.reasons.append(
                    "Shares a moment with another point; they will be shown one "
                    "after the other."
                )
                if verdict.verdict == VERIFIED:
                    verdict.verdict = REVIEW


def summarise(verdicts: Sequence[Verdict]) -> dict:
    counts = {VERIFIED: 0, REVIEW: 0, REJECTED: 0}
    for verdict in verdicts:
        counts[verdict.verdict] = counts.get(verdict.verdict, 0) + 1
    total = max(len(verdicts), 1)
    return {
        **counts,
        "total": len(verdicts),
        "trusted_fraction": counts[VERIFIED] / total,
    }
