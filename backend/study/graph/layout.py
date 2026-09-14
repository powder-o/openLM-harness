"""Server-side layout for graph payloads: networkx spring layout, seed 42 for stable coordinates."""
from __future__ import annotations

import networkx as nx


def compute_layout(node_ids: list[str], edges: list[tuple[str, str]]) -> dict[str, tuple[float, float]]:
    if not node_ids:
        return {}
    G = nx.Graph()
    G.add_nodes_from(node_ids)
    for a, b in edges:
        if a in G and b in G:
            G.add_edge(a, b)
    pos = nx.spring_layout(G, seed=42)
    return {n: (float(p[0]), float(p[1])) for n, p in pos.items()}
