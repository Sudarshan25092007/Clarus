"""Constrained graph clustering for patient record linkage.

Implements Score-Ordered Constrained Union-Find with Lazy Cross-Pair Verification.
Guarantees partition completeness, deterministic ordering, and anti-transitivity:
no cluster will ever merge records that contradict each other (NON_MATCH).

Algorithm:
  1. Filter active candidate edges (decision == 'MATCH').
  2. Sort active edges deterministically by descending confidence: (-score, min(i, j), max(i, j)).
  3. Pre-populate edge decision cache from candidate edges.
  4. For each active edge (u, v):
     a. If already in the same cluster (find(u) == find(v)), continue.
     b. Check all cross-pairs (x, y) between member sets C_u and C_v.
     c. Look up (x, y) in edge cache, or lazily compute decide_pairwise_match(compare_records(x, y)).
     d. If ANY cross-pair is NON_MATCH, abort the merge and log to blocked_merges.
     e. If all cross-pairs are compatible, merge C_u and C_v.
  5. Return canonical, deterministically sorted clusters: list[list[int]].
"""

from __future__ import annotations

from typing import Any

from identity_features import compare_records
from identity_decision import decide_pairwise_match


class ConstrainedUnionFind:
    """Disjoint-set structure tracking member sets and enforcing pairwise constraints."""

    def __init__(self, n: int) -> None:
        self.parent: list[int] = list(range(n))
        self.members: dict[int, set[int]] = {i: {i} for i in range(n)}

    def find(self, i: int) -> int:
        root = i
        while root != self.parent[root]:
            root = self.parent[root]
        # Path compression
        curr = i
        while curr != root:
            nxt = self.parent[curr]
            self.parent[curr] = root
            curr = nxt
        return root

    def can_merge(
        self,
        root_u: int,
        root_v: int,
        records: list[dict],
        edge_cache: dict[tuple[int, int], str],
    ) -> tuple[bool, tuple[int, int] | None]:
        """Verify all cross-pairs in C_u x C_v are compatible (none is NON_MATCH).

        Returns (True, None) if compatible, or (False, (x, y)) on first contradiction.
        """
        members_u = self.members[root_u]
        members_v = self.members[root_v]

        for x in members_u:
            for y in members_v:
                pair = (min(x, y), max(x, y))
                decision = edge_cache.get(pair)
                if decision is None:
                    # Lazy cross-pair evaluation
                    feats = compare_records(records[x], records[y])
                    res = decide_pairwise_match(feats)
                    decision = res["decision"]
                    edge_cache[pair] = decision

                if decision == "NON_MATCH":
                    return False, pair

        return True, None

    def union(self, root_u: int, root_v: int) -> int:
        """Merge root_v into root_u."""
        self.parent[root_v] = root_u
        self.members[root_u].update(self.members[root_v])
        del self.members[root_v]
        return root_u


def cluster_records(
    records: list[dict],
    candidate_edges: list[dict],
) -> dict[str, Any]:
    """Partition records into clusters using Score-Ordered Constrained Union-Find.

    Args:
      records: List of canonical patient record dicts (0 .. N-1).
      candidate_edges: List of edge dicts from identity_decision (with keys 'i', 'j',
                       'decision', 'score', 'is_active_edge').

    Returns:
      {
          "clusters": list[list[int]],      # Complete partition of 0..N-1
          "blocked_merges": list[dict],     # Audit trail of prevented contradictions
      }
    """
    n = len(records)
    if n == 0:
        return {"clusters": [], "blocked_merges": []}

    # 1. Build edge cache from candidate edges
    edge_cache: dict[tuple[int, int], str] = {}
    active_edges: list[dict] = []

    for edge in candidate_edges:
        u = min(edge["i"], edge["j"])
        v = max(edge["i"], edge["j"])
        edge_cache[(u, v)] = edge["decision"]
        if edge.get("is_active_edge") or edge.get("decision") == "MATCH":
            active_edges.append(edge)

    # 2. Sort active edges deterministically: descending score, then ascending indices
    active_edges.sort(
        key=lambda e: (-float(e.get("score", 0.0)), min(e["i"], e["j"]), max(e["i"], e["j"]))
    )

    # 3. Constrained Union-Find
    uf = ConstrainedUnionFind(n)
    blocked_merges: list[dict] = []

    for edge in active_edges:
        u = min(edge["i"], edge["j"])
        v = max(edge["i"], edge["j"])

        root_u = uf.find(u)
        root_v = uf.find(v)

        if root_u == root_v:
            continue

        # Lazy cross-pair verification across all pairs between the two candidate clusters
        allowed, conflict_pair = uf.can_merge(root_u, root_v, records, edge_cache)
        if allowed:
            uf.union(root_u, root_v)
        else:
            blocked_merges.append({
                "attempted_edge": (u, v),
                "conflicting_pair": conflict_pair,
                "score": edge.get("score", 0.0),
                "reason": "transitive_non_match_conflict",
            })

    # 4. Canonical sorting: sort each cluster ascending, sort cluster list by first index
    raw_clusters = [sorted(members) for members in uf.members.values()]
    raw_clusters.sort(key=lambda c: c[0])

    return {
        "clusters": raw_clusters,
        "blocked_merges": blocked_merges,
    }
