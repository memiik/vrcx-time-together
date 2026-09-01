from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import FriendIntroduction


@dataclass(frozen=True, slots=True)
class IntroductionSession:
    user_id: str
    start: datetime
    end: datetime
    location: str


def infer_introductions(
    user_ids: tuple[str, ...],
    friendship_dates: dict[str, datetime],
    sessions: tuple[IntroductionSession, ...],
) -> tuple[FriendIntroduction, ...]:
    """Build a conservative, acyclic tree from friendship and co-presence evidence.

    This cannot establish who actually introduced two people. A parent is selected only
    when they were already a friend and were recorded in the same known instance close
    to the child's friendship event.
    """

    by_user: dict[str, list[IntroductionSession]] = {}
    for session in sessions:
        if session.location:
            by_user.setdefault(session.user_id, []).append(session)

    results: list[FriendIntroduction] = []
    for child_id in user_ids:
        befriended_at = friendship_dates.get(child_id)
        if befriended_at is None:
            results.append(
                FriendIntroduction(
                    child_user_id=child_id,
                    parent_user_id=None,
                    befriended_at=None,
                    confidence=0.0,
                    evidence="No friendship timestamp is available in the recorded VRCX history.",
                    timestamp_source="unavailable",
                )
            )
            continue

        child_sessions = [
            item
            for item in by_user.get(child_id, ())
            if item.start - timedelta(minutes=15)
            <= befriended_at
            <= item.end + timedelta(minutes=15)
        ]
        candidates: list[tuple[float, int, str, bool]] = []
        for parent_id in user_ids:
            parent_date = friendship_dates.get(parent_id)
            if parent_id == child_id or parent_date is None or parent_date >= befriended_at:
                continue
            best_overlap = 0
            exact = False
            for child_session in child_sessions:
                for parent_session in by_user.get(parent_id, ()):
                    if child_session.location != parent_session.location:
                        continue
                    overlap_start = max(child_session.start, parent_session.start)
                    overlap_end = min(child_session.end, parent_session.end)
                    overlap_ms = max(
                        0,
                        round((overlap_end - overlap_start).total_seconds() * 1000),
                    )
                    if overlap_ms <= 0:
                        continue
                    contains_event = overlap_start <= befriended_at <= overlap_end
                    if contains_event or overlap_ms > best_overlap:
                        exact = exact or contains_event
                        best_overlap = max(best_overlap, overlap_ms)
            if best_overlap:
                score = (1.0 if exact else 0.72) + min(best_overlap / 14_400_000, 0.18)
                candidates.append((score, best_overlap, parent_id, exact))

        candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
        if not candidates:
            results.append(
                FriendIntroduction(
                    child_user_id=child_id,
                    parent_user_id=None,
                    befriended_at=befriended_at,
                    confidence=0.0,
                    evidence=(
                        "No earlier friend was recorded in the same known instance when "
                        "this friendship began."
                    ),
                )
            )
            continue

        best_score, overlap_ms, parent_id, exact = candidates[0]
        alternatives = tuple(item[2] for item in candidates[1:4])
        ambiguity_penalty = min(0.24, len(candidates[1:]) * 0.08)
        confidence = max(0.35, min(0.88, 0.82 if exact else 0.58) - ambiguity_penalty)
        overlap_minutes = max(1, round(overlap_ms / 60_000))
        timing = "at the friendship timestamp" if exact else "around the friendship event"
        evidence = (
            f"Recorded together in the same known instance {timing} "
            f"({overlap_minutes} min overlapping session evidence)."
        )
        if alternatives:
            evidence += f" {len(alternatives)} other plausible earlier friend(s) were present."
        results.append(
            FriendIntroduction(
                child_user_id=child_id,
                parent_user_id=parent_id,
                befriended_at=befriended_at,
                confidence=confidence,
                evidence=evidence,
                alternative_parent_ids=alternatives,
            )
        )

    return tuple(results)
