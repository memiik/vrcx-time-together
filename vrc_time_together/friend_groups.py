from __future__ import annotations

import math

from .models import FriendMapLink, FriendMapNode


def same_instance_strength(link: FriendMapLink) -> float:
    """Return a clustering weight based only on measured same-instance history."""
    hours = max(0, link.milliseconds) / 3_600_000
    encounters = max(0, link.encounters)
    likelihood = min(1.0, max(0.0, link.likelihood))
    return (
        2.4 * math.log1p(encounters)
        + 1.8 * math.sqrt(hours)
        + 3.0 * likelihood
    )


def detect_friend_groups(
    nodes: tuple[FriendMapNode, ...],
    links: tuple[FriendMapLink, ...],
    *,
    resolution: float = 1.25,
) -> dict[str, int]:
    """Detect stable weighted communities; zero means insufficient group evidence."""
    node_ids = {node.user_id for node in nodes}
    if not node_ids:
        return {}

    weights: dict[tuple[str, str], float] = {}
    degree = {user_id: 0.0 for user_id in node_ids}
    for link in links:
        if (
            link.source_user_id not in node_ids
            or link.target_user_id not in node_ids
            or link.source_user_id == link.target_user_id
        ):
            continue
        pair = tuple(sorted((link.source_user_id, link.target_user_id)))
        weight = same_instance_strength(link)
        if weight <= 0:
            continue
        weights[pair] = weights.get(pair, 0.0) + weight
        degree[pair[0]] += weight
        degree[pair[1]] += weight

    total_edge_weight = sum(weights.values())
    if total_edge_weight <= 0:
        return {user_id: 0 for user_id in node_ids}

    communities: dict[str, set[str]] = {
        user_id: {user_id} for user_id in sorted(node_ids)
    }
    community_degree = dict(degree)
    between_weights = dict(weights)

    while True:
        best: tuple[float, str, str] | None = None
        for (first_id, second_id), between in between_weights.items():
            if first_id not in communities or second_id not in communities:
                continue
            gain = (
                between / total_edge_weight
                - resolution
                * community_degree[first_id]
                * community_degree[second_id]
                / (2 * total_edge_weight * total_edge_weight)
            )
            candidate = (gain, first_id, second_id)
            if gain > 1e-12 and (best is None or candidate > best):
                best = candidate
        if best is None:
            break
        _gain, first_id, second_id = best
        keep_id, remove_id = sorted((first_id, second_id))
        communities[keep_id].update(communities.pop(remove_id))
        community_degree[keep_id] += community_degree.pop(remove_id)

        updated_weights: dict[tuple[str, str], float] = {}
        for (source_id, target_id), weight in between_weights.items():
            source_id = keep_id if source_id == remove_id else source_id
            target_id = keep_id if target_id == remove_id else target_id
            if source_id == target_id:
                continue
            pair = tuple(sorted((source_id, target_id)))
            updated_weights[pair] = updated_weights.get(pair, 0.0) + weight
        between_weights = updated_weights

    node_time = {node.user_id: node.milliseconds for node in nodes}
    groups = [members for members in communities.values() if len(members) >= 2]
    groups.sort(
        key=lambda members: (
            -sum(node_time.get(user_id, 0) for user_id in members),
            tuple(sorted(members)),
        )
    )
    result = {user_id: 0 for user_id in node_ids}
    for group_id, members in enumerate(groups, start=1):
        for user_id in members:
            result[user_id] = group_id
    return result
