"""Deterministic text similarity and timing checks, without model inference."""

import re
import unicodedata
from datetime import timedelta
from itertools import combinations

from .schemas import PullRequest


SIMILARITY_THRESHOLD = 0.75
CAMPAIGN_WINDOW = timedelta(hours=2)
STOPWORDS = frozenset(
    "a an and are as at be been but by for from had has have i in is it its "
    "of on or our that the their this to was we were will with you your".split()
)


def tokenize(text: str) -> set[str]:
    """Normalize Unicode, case, and punctuation, then drop common stopwords."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return set(re.findall(r"[^\W_]+", normalized)) - STOPWORDS


def jaccard(left: set[str], right: set[str]) -> float:
    """Empty token sets provide no similarity evidence."""
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def connected_groups(neighbors: dict[int, set[int]]) -> list[set[int]]:
    """Collect connected candidate groups in a stable order."""
    remaining = set(neighbors)
    groups = []
    while remaining:
        pending = [min(remaining)]
        group = set()
        while pending:
            current = pending.pop()
            if current in group:
                continue
            group.add(current)
            pending.extend(sorted(neighbors[current] - group, reverse=True))
        remaining -= group
        groups.append(group)
    return groups


def verified_groups(component: set[int], neighbors: dict[int, set[int]]):
    """Grow pairwise-verified groups, so weak similarity chains cannot qualify.

    Try each member as a seed to retain strong subsets of a noisy component.
    This conservative greedy MVP does not exhaustively enumerate all cliques.
    """
    if all(component - {member} <= neighbors[member] for member in component):
        yield frozenset(component)
        return
    for seed in sorted(component):
        group = {seed}
        for candidate in sorted(component - {seed}):
            if group <= neighbors[candidate]:
                group.add(candidate)
        if len(group) >= 3:
            yield frozenset(group)


def describe_campaign(prs: list[PullRequest], score: float, number: int):
    """Report measured evidence, including age as context rather than a rule."""
    first = min(pr.created_at for pr in prs)
    last = max(pr.created_at for pr in prs)
    projects = len({pr.project for pr in prs})
    recent = sum(pr.account_age_days <= 7 for pr in prs)
    ages = [pr.account_age_days for pr in prs]
    return {
        "id": f"campaign-{number}",
        "pr_ids": sorted(pr.id for pr in prs),
        "project_count": projects,
        "similarity_score": round(score, 4),
        "evidence": [
            f"{len(prs)} similar PRs span {projects} distinct projects.",
            f"Minimum pairwise Jaccard similarity is {score:.4f} (threshold 0.75).",
            f"PRs span {(last - first).total_seconds() / 60:g} minutes, "
            f"from {first.isoformat()} to {last.isoformat()}, within a rolling two-hour window.",
            f"{recent} of {len(prs)} PRs report account ages of 7 days or less; "
            f"reported ages range from {min(ages)} to {max(ages)} days. "
            "Account age is supporting context and does not determine detection.",
            "Similarity and timing warrant human review; they do not establish spam or malicious intent.",
        ],
    }


def detect_campaigns(pull_requests: list[PullRequest]):
    """Find verified groups across rolling windows and remove duplicate subsets.

    The score is the minimum pairwise Jaccard similarity, not a probability.
    Pair comparisons are quadratic, suitable for small hackathon scan batches.
    """
    ordered = sorted(pull_requests, key=lambda pr: (pr.created_at, pr.id))
    by_id = {pr.id: pr for pr in ordered}
    tokens = {pr.id: tokenize(pr.title + " " + pr.body) for pr in ordered}
    scores = {}
    neighbors = {pr.id: set() for pr in ordered}
    for left, right in combinations(ordered, 2):
        if right.created_at - left.created_at > CAMPAIGN_WINDOW:
            continue
        score = jaccard(tokens[left.id], tokens[right.id])
        if score >= SIMILARITY_THRESHOLD:
            scores[tuple(sorted((left.id, right.id)))] = score
            neighbors[left.id].add(right.id)
            neighbors[right.id].add(left.id)

    candidates = set()
    for start, first in enumerate(ordered):
        window = set()
        for pr in ordered[start:]:
            if pr.created_at - first.created_at > CAMPAIGN_WINDOW:
                break
            window.add(pr.id)
        if len(window) < 3:
            continue
        window_neighbors = {pr_id: neighbors[pr_id] & window for pr_id in window}
        for component in connected_groups(window_neighbors):
            if len(component) < 3:
                continue
            for group in verified_groups(component, window_neighbors):
                if len({by_id[pr_id].project for pr_id in group}) >= 2:
                    candidates.add(group)

    # Prefer larger groups; overlapping windows must not repeat their subsets.
    maximal = []
    for group in sorted(candidates, key=lambda ids: (-len(ids), tuple(sorted(ids)))):
        if not any(group <= existing for existing in maximal):
            maximal.append(group)
    maximal.sort(key=lambda ids: tuple(sorted(ids)))
    campaigns = []
    for number, group in enumerate(maximal, start=1):
        score = min(scores[pair] for pair in combinations(sorted(group), 2))
        campaigns.append(describe_campaign([by_id[pr_id] for pr_id in sorted(group)], score, number))
    return {"total_prs": len(ordered), "campaigns": campaigns}
