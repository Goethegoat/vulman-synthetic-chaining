"""Replay stored decisions independently and run post-main topology controls."""
import argparse
from collections import defaultdict
import hashlib
import itertools
import json
from pathlib import Path
import random
import statistics

import networkx as nx

from multiscale_chaining import Finding, build_graph, select_treatments


def pairs_by_search(edges, entries, targets, removed):
    # Recompute residual reachability with adjacency lists, independently of the
    # NetworkX path used by the experiment runner.
    adjacency = defaultdict(set)
    for source, target in edges:
        if source not in removed and target not in removed:
            adjacency[source].add(target)
    count = 0
    for entry in set(entries) - removed:
        reached, pending = {entry}, [entry]
        while pending:
            current = pending.pop()
            for successor in adjacency[current] - reached:
                reached.add(successor)
                pending.append(successor)
        count += len(reached & (set(targets) - removed))
    return count


def replay(root):
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["source_sha256"].items():
        assert hashlib.sha256((root.parent / name).read_bytes()).hexdigest() == expected
    for name, expected in manifest["output_sha256"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected
    cases = {case["identifier"]: case for case in json.loads((root / "scenarios.json").read_text(encoding="utf-8"))}
    checked = {}
    for name in ("policy_runs", "ablations", "uncertainty"):
        rows = json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
        for row in rows:
            case = cases[row["scenario"]]
            selected = set(row["selected"])
            costs = {finding["identifier"]: finding["cost"] for finding in case["findings"]}
            assert selected <= costs.keys()
            assert sum(costs[node] for node in selected) == row["cost"]
            actual = pairs_by_search(case["truth_edges"], case["entries"], case["targets"], selected)
            assert actual == row["residual_pairs"]
        checked[name] = len(rows)
    return checked


def topology_controls():
    records = []
    # Disjoint chains test the no-shared-bottleneck case; random DAG/cyclic
    # controls probe dependence on topology, not enterprise prevalence.
    for family, seed in itertools.product(("disjoint", "random_dag", "random_cyclic"), range(60)):
        generator = random.Random(190000 + seed)
        graph = nx.DiGraph()
        if family == "disjoint":
            for index in range(8):
                graph.add_edges_from([(f"a{index}", f"b{index}"), (f"b{index}", f"c{index}")])
            entries, targets = {f"a{index}" for index in range(8)}, {f"c{index}" for index in range(8)}
        else:
            nodes = [f"node{index:02}" for index in range(24)]
            graph.add_nodes_from(nodes)
            graph.add_edges_from(zip(nodes, nodes[1:]))
            graph.add_edges_from((source, target) for source in nodes for target in nodes
                                 if source != target and (family == "random_cyclic" or source < target)
                                 and generator.random() < 0.08)
            entries, targets = {nodes[0], nodes[1]}, {nodes[-1], nodes[-2]}
        findings = [Finding(node, "server", "env", "net", "domain", "cap:" + node,
                            frozenset("cap:" + target for target in graph.successors(node)),
                            severity=generator.uniform(4, 10), signal=generator.random(),
                            criticality=3 if node in targets else 1,
                            cost=1 if family == "disjoint" else generator.randint(1, 10))
                    for node in sorted(graph)]
        modeled = build_graph(findings, {})
        assert set(modeled.edges) == set(graph.edges)
        costs = {finding.identifier: finding.cost for finding in findings}
        for policy in ("context_cost", "relevant_cost", "separator"):
            selected = select_treatments(modeled, findings, entries, targets, policy)
            assert pairs_by_search(graph.edges, entries, targets, set(selected)) == 0
            records.append(dict(family=family, seed=seed, policy=policy, selected=selected,
                                cost=sum(costs[node] for node in selected), actions=len(selected),
                                edges=sorted(graph.edges), entries=sorted(entries), targets=sorted(targets), costs=costs))
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    output = arguments.output
    if output.exists():
        raise ValueError("Choose a new validation output directory")
    checked = replay(arguments.results)
    controls = topology_controls()
    output.mkdir(parents=True)
    summary = []
    for family, policy in itertools.product(("disjoint", "random_dag", "random_cyclic"),
                                            ("context_cost", "relevant_cost", "separator")):
        rows = [row for row in controls if row["family"] == family and row["policy"] == policy]
        summary.append(dict(family=family, policy=policy, mean_cost=statistics.mean(row["cost"] for row in rows)))
    for name, value in (("verification", checked), ("controls", controls), ("summary", summary)):
        (output / f"{name}.json").write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = dict(source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    outputs={path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in output.iterdir()})
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(dict(replayed=checked, control_scenarios=180, control_decisions=len(controls), summary=summary), indent=2))


if __name__ == "__main__":
    main()