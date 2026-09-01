from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import FriendIntroduction


EVENT_TOLERANCE = timedelta(minutes=15)
PRIOR_SESSION_GAP = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class IntroductionSession:
    user_id: str
    start: datetime
    end: datetime
    location: str


@dataclass(frozen=True, slots=True)
class _CandidateEvidence:
    parent_id: str
    exact: bool
    distance_seconds: float
    event_overlap_ms: int
    parent_was_present_first: bool
    prior_encounters: int
    prior_milliseconds: int


def _overlap(
    first: IntroductionSession, second: IntroductionSession
) -> tuple[datetime, datetime, int]:
    start = max(first.start, second.start)
    end = min(first.end, second.end)
    milliseconds = max(0, round((end - start).total_seconds() * 1000))
    return start, end, milliseconds


def _prior_evidence(
    child_sessions: list[IntroductionSession],
    parent_sessions: list[IntroductionSession],
    cutoff: datetime,
) -> tuple[int, int]:
    encounters = 0
    milliseconds = 0
    for child_session in child_sessions:
        if child_session.start >= cutoff:
            continue
        for parent_session in parent_sessions:
            if child_session.location != parent_session.location:
                continue
            _start, end, overlap_ms = _overlap(child_session, parent_session)
            if overlap_ms > 0 and end <= cutoff:
                encounters += 1
                milliseconds += overlap_ms
    return encounters, milliseconds


def _candidate_score(candidate: _CandidateEvidence, candidate_count: int) -> float:
    if candidate.exact:
        timing_points = 40.0
    else:
        tolerance_seconds = EVENT_TOLERANCE.total_seconds()
        timing_points = 18.0 + 12.0 * max(
            0.0, 1.0 - candidate.distance_seconds / tolerance_seconds
        )
    overlap_minutes = candidate.event_overlap_ms / 60_000
    overlap_points = 10.0 * min(
        1.0, math.log1p(overlap_minutes) / math.log1p(240)
    )
    arrival_points = 15.0 if candidate.parent_was_present_first else 4.0
    prior_minutes = candidate.prior_milliseconds / 60_000
    prior_points = min(15.0, candidate.prior_encounters * 5.0) + 10.0 * min(
        1.0, math.log1p(prior_minutes) / math.log1p(600)
    )
    specificity_points = 10.0 / math.sqrt(max(1, candidate_count))
    return min(
        0.95,
        (
            timing_points
            + overlap_points
            + arrival_points
            + prior_points
            + specificity_points
        )
        / 100.0,
    )


def infer_introductions(
    user_ids: tuple[str, ...],
    friendship_dates: dict[str, datetime],
    sessions: tuple[IntroductionSession, ...],
) -> tuple[FriendIntroduction, ...]:
    """Build a conservative, acyclic tree from temporal co-presence evidence.

    The result ranks possible introduction paths. Its score summarizes evidence
    quality and is deliberately not presented as a probability.
    """

    by_user: dict[str, list[IntroductionSession]] = {}
    for session in sessions:
        if session.location and session.start < session.end:
            by_user.setdefault(session.user_id, []).append(session)
    for user_sessions in by_user.values():
        user_sessions.sort(key=lambda item: item.start)

    results: list[FriendIntroduction] = []
    for child_id in user_ids:
        befriended_at = friendship_dates.get(child_id)
        if befriended_at is None:
            results.append(
                FriendIntroduction(
                    child_user_id=child_id,
                    parent_user_id=None,
                    befriended_at=None,
                    evidence_score=0.0,
                    evidence="No friendship timestamp is available in the recorded VRCX history.",
                    timestamp_source="unavailable",
                )
            )
            continue

        all_child_sessions = by_user.get(child_id, [])
        event_sessions = [
            item
            for item in all_child_sessions
            if item.start - EVENT_TOLERANCE
            <= befriended_at
            <= item.end + EVENT_TOLERANCE
        ]
        candidates: list[_CandidateEvidence] = []
        for parent_id in user_ids:
            parent_date = friendship_dates.get(parent_id)
            if (
                parent_id == child_id
                or parent_date is None
                or parent_date >= befriended_at
            ):
                continue
            parent_sessions = by_user.get(parent_id, [])
            best_event: tuple[
                tuple[int, float, int, int],
                IntroductionSession,
                bool,
                float,
                int,
                bool,
            ] | None = None
            for child_session in event_sessions:
                for parent_session in parent_sessions:
                    if child_session.location != parent_session.location:
                        continue
                    overlap_start, overlap_end, overlap_ms = _overlap(
                        child_session, parent_session
                    )
                    if overlap_ms <= 0:
                        continue
                    exact = overlap_start <= befriended_at <= overlap_end
                    distance_seconds = (
                        0.0
                        if exact
                        else min(
                            abs((befriended_at - overlap_start).total_seconds()),
                            abs((befriended_at - overlap_end).total_seconds()),
                        )
                    )
                    if distance_seconds > EVENT_TOLERANCE.total_seconds():
                        continue
                    present_first = (
                        parent_session.start
                        <= child_session.start + timedelta(minutes=2)
                    )
                    rank = (
                        1 if exact else 0,
                        -distance_seconds,
                        1 if present_first else 0,
                        overlap_ms,
                    )
                    if best_event is None or rank > best_event[0]:
                        best_event = (
                            rank,
                            child_session,
                            exact,
                            distance_seconds,
                            overlap_ms,
                            present_first,
                        )
            if best_event is None:
                continue
            _, child_event_session, exact, distance_seconds, overlap_ms, present_first = (
                best_event
            )
            prior_encounters, prior_milliseconds = _prior_evidence(
                all_child_sessions,
                parent_sessions,
                child_event_session.start - PRIOR_SESSION_GAP,
            )
            candidates.append(
                _CandidateEvidence(
                    parent_id=parent_id,
                    exact=exact,
                    distance_seconds=distance_seconds,
                    event_overlap_ms=overlap_ms,
                    parent_was_present_first=present_first,
                    prior_encounters=prior_encounters,
                    prior_milliseconds=prior_milliseconds,
                )
            )

        candidate_count = len(candidates)
        scored = sorted(
            (
                (_candidate_score(candidate, candidate_count), candidate)
                for candidate in candidates
            ),
            key=lambda item: (
                -item[0],
                -item[1].prior_encounters,
                -item[1].prior_milliseconds,
                item[1].parent_id,
            ),
        )
        if not scored:
            results.append(
                FriendIntroduction(
                    child_user_id=child_id,
                    parent_user_id=None,
                    befriended_at=befriended_at,
                    evidence_score=0.0,
                    evidence=(
                        "No earlier friend was recorded in the same known instance within "
                        "15 minutes of this friendship event."
                    ),
                )
            )
            continue

        evidence_score, best = scored[0]
        alternatives = tuple(candidate.parent_id for _score, candidate in scored[1:4])
        overlap_minutes = max(1, round(best.event_overlap_ms / 60_000))
        if best.exact:
            timing = "Present in the same known instance at the friendship timestamp"
        else:
            timing = (
                "Present in the same known instance "
                f"{max(1, round(best.distance_seconds / 60))} min from the friendship event"
            )
        factors = [f"{timing}; {overlap_minutes} min of overlapping session time"]
        if best.parent_was_present_first:
            factors.append("already present when that session began")
        if best.prior_encounters:
            factors.append(
                f"{best.prior_encounters} earlier shared-instance encounter"
                f"{'s' if best.prior_encounters != 1 else ''}"
            )
        if candidate_count > 1:
            factors.append(f"chosen from {candidate_count} plausible people present")
        results.append(
            FriendIntroduction(
                child_user_id=child_id,
                parent_user_id=best.parent_id,
                befriended_at=befriended_at,
                evidence_score=evidence_score,
                evidence="; ".join(factors) + ".",
                alternative_parent_ids=alternatives,
                candidate_count=candidate_count,
                exact_event_overlap=best.exact,
                prior_encounters=best.prior_encounters,
                prior_milliseconds=best.prior_milliseconds,
                parent_was_present_first=best.parent_was_present_first,
            )
        )

    return tuple(results)
