"""Offline, explicit-capability research extension; no scanner or deployment I/O."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math

import networkx as nx


@dataclass(frozen=True)
class Finding:
    identifier: str
    server: str
    environment: str
    network: str
    domain: str | None
    requires: str
    grants: frozenset[str]
    remote: bool = True
    severity: float = 5.0
    signal: float = 0.5
    criticality: float = 1.0
    cost: float = 1.0

    @property
    def asset(self) -> tuple[str, str]:
        return self.environment, self.server


def relation_scope(source: Finding, target: Finding) -> str:
    # This precedence makes scope labels exclusive; none of them implies access.
    if source.asset == target.asset:
        return "server"
    if source.environment == target.environment:
        return "environment"
    if source.network == target.network:
        return "network"
    return "routed"


def relation_status(source: Finding, target: Finding, routes: dict) -> str:
    if source.identifier == target.identifier or target.requires not in source.grants:
        return "incompatible"
    if not target.remote and source.asset != target.asset:
        return "incompatible"
    identity = (None if source.domain is None or target.domain is None
                else source.domain == target.domain)
    # Same-server reachability is assumed here; cross-server routes require evidence.
    network = True if source.asset == target.asset else routes.get((source.asset, target.asset))
    # Any known negative condition overrides missing evidence; missing alone is unknown.
    if identity is False or network is False:
        return "incompatible"
    return "supported" if identity is True and network is True else "unknown"


def build_graph(findings: list[Finding], routes: dict, *, upper: bool = True,
                scopes: frozenset[str] = frozenset({"server", "environment", "network", "routed"}),
                evidence: bool = True) -> nx.DiGraph:
    identifiers = [finding.identifier for finding in findings]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Finding occurrence identifiers must be unique")
    graph = nx.DiGraph()
    graph.add_nodes_from(identifiers)
    by_requirement = defaultdict(list)
    for finding in findings:
        if not math.isfinite(finding.cost) or finding.cost <= 0:
            raise ValueError("Costs must be finite and positive")
        by_requirement[finding.requires].append(finding)
    # Capabilities index candidates but do not merge occurrence nodes.
    for source in findings:
        for capability in sorted(source.grants):
            for target in by_requirement[capability]:
                scope = relation_scope(source, target)
                if source.identifier == target.identifier or scope not in scopes:
                    continue
                status = relation_status(source, target, routes)
                # The upper graph retains unresolved candidates, never known-blocked ones.
                if (not evidence or status == "supported" or (upper and status == "unknown")):
                    graph.add_edge(source.identifier, target.identifier, scope=scope, status=status)
    return graph


def corridor(graph: nx.DiGraph, entries: set[str], targets: set[str]) -> set[str]:
    """Nodes on entry-target walks; exact simple-path union on a DAG."""
    forward = set(entries) & set(graph)
    backward = set(targets) & set(graph)
    for entry in entries & set(graph):
        forward.update(nx.descendants(graph, entry))
    for target in targets & set(graph):
        backward.update(nx.ancestors(graph, target))
    return forward & backward


def reachable_pairs(graph: nx.DiGraph, entries: set[str], targets: set[str]) -> int:
    return sum(target in ({entry} | nx.descendants(graph, entry))
               for entry in entries & set(graph) for target in targets & set(graph))


def minimum_separator(graph: nx.DiGraph, entries: set[str], targets: set[str],
                      costs: dict[str, float]) -> set[str]:
    active = corridor(graph, entries, targets)
    if not active:
        return set()
    if any(not math.isfinite(costs[node]) or costs[node] <= 0 for node in active):
        raise ValueError("Costs must be finite and positive")
    capacity_bound = sum(costs[node] for node in active) + 1
    split = nx.DiGraph()
    start, finish = ("terminal", "start"), ("terminal", "finish")
    # Node splitting assigns treatment costs to vertices; a larger capacity prevents
    # the minimum cut from severing a transition or terminal connection instead.
    for node in sorted(active):
        split.add_edge((node, "in"), (node, "out"), capacity=costs[node])
    for source, target in sorted(graph.subgraph(active).edges):
        split.add_edge((source, "out"), (target, "in"), capacity=capacity_bound)
    for entry in sorted(entries & active):
        split.add_edge(start, (entry, "in"), capacity=capacity_bound)
    for target in sorted(targets & active):
        split.add_edge((target, "out"), finish, capacity=capacity_bound)
    _, partition = nx.minimum_cut(split, start, finish)
    reachable, other = partition
    return {node for node in active if (node, "in") in reachable and (node, "out") in other}


def select_treatments(graph: nx.DiGraph, findings: list[Finding], entries: set[str],
                      targets: set[str], policy: str) -> list[str]:
    by_id = {finding.identifier: finding for finding in findings}
    costs = {node: finding.cost for node, finding in by_id.items()}
    relevant = corridor(graph, entries, targets)
    if policy == "separator":
        candidates = minimum_separator(graph, entries, targets, costs)
        order = sorted(candidates, key=lambda node: (costs[node], node))
    elif policy == "relevant_cost":
        order = sorted(relevant, key=lambda node: (costs[node], node))
    else:
        def score(node):
            finding = by_id[node]
            if policy == "severity":
                return finding.severity
            if policy == "signal":
                return finding.signal
            if policy == "context_cost":
                return (finding.severity * (1 + finding.signal) * finding.criticality / finding.cost)
            raise ValueError(f"Unknown policy: {policy}")
        order = sorted(by_id, key=lambda node: (-score(node), node))
    residual = graph.copy()
    selected = []
    for node in order:
        if not reachable_pairs(residual, entries, targets):
            break
        selected.append(node)
        residual.remove_node(node)
    if reachable_pairs(residual, entries, targets):
        raise ValueError("Policy did not disconnect all represented entry-target pairs")
    return selected