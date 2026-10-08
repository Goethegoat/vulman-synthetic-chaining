"""Reproducible synthetic multiscale campaign, isolated from previous results."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import itertools
import json
from pathlib import Path
import platform
import random
import statistics
import time

import networkx as nx

from multiscale_chaining import (Finding, build_graph, corridor, minimum_separator,
                                reachable_pairs, select_treatments)


FAMILIES = ("server", "environment", "network", "mixed")
POLICIES = ("severity", "signal", "context_cost", "relevant_cost", "separator")


def save(path, value):
    def convert(item):
        if isinstance(item, (set, frozenset)):
            return sorted(item)
        raise TypeError(type(item).__name__)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=convert,
                               allow_nan=False) + "\n", encoding="utf-8")


def make_case(family, width, seed):
    # Derive a local seed so every family/width/seed case is independently repeatable.
    generator = random.Random(100000 * FAMILIES.index(family) + 1000 * width + seed)
    layers = [[f"layer{layer}:{index}" for index in range(count)]
              for layer, count in enumerate((width, width, 2, width, 2))]
    nodes = list(itertools.chain.from_iterable(layers))
    off_target = [f"other:{index}" for index in range(width)]
    desired = set()
    for previous, following in zip(layers, layers[1:]):
        for index, source in enumerate(previous):
            desired.add((source, following[index % len(following)]))
            for target in following:
                if generator.random() < 0.2:
                    desired.add((source, target))
    # Target-specific capability labels encode the generated oracle edges.
    # They test graph construction, not semantic extraction from findings.
    desired.update((layers[0][index], target) for index, target in enumerate(off_target))
    findings = []
    for index, node in enumerate(nodes + off_target):
        if family == "server":
            server, environment, network = "host", "prod", "net"
        elif family == "environment":
            server, environment, network = f"host{index}", "prod", f"net{index % 2}"
        elif family == "network":
            server, environment, network = f"host{index}", f"env{index % 3}", "net"
        else:
            server, environment, network = f"host{index // 2}", f"env{(index // 6) % 3}", f"net{(index // 4) % 2}"
        grants = frozenset("cap:" + target for source, target in desired if source == node)
        findings.append(Finding(node, server, environment, network, "tenant",
                                "cap:" + node, grants, True,
                                round(generator.uniform(4, 10), 3), round(generator.random(), 4),
                                3.0 if node in layers[-1] else 1.0,
                                float(generator.randint(1, 10))))
    assets = sorted({finding.asset for finding in findings})
    routes = {(source, target): generator.random() < 0.8
              for source in assets for target in assets if source != target}
    by_id = {finding.identifier: finding for finding in findings}
    backbone = [(previous[0], following[0]) for previous, following in zip(layers, layers[1:])]
    for source, target in backbone:
        if by_id[source].asset != by_id[target].asset:
            # Keep the intended path feasible after applying synthetic routes.
            routes[by_id[source].asset, by_id[target].asset] = True
    truth_edges = {(source, target) for source, target in desired
                   if by_id[source].asset == by_id[target].asset
                   or routes[by_id[source].asset, by_id[target].asset]}
    truth = nx.DiGraph()
    truth.add_nodes_from(by_id)
    truth.add_edges_from(sorted(truth_edges))
    return findings, routes, set(layers[0]), set(layers[-1]), truth


def residual_graph(graph, selected):
    return graph.subgraph(set(graph) - set(selected))


def evaluate_case(family, width, seed, repeats):
    findings, routes, entries, targets, truth = make_case(family, width, seed)
    graph = build_graph(findings, routes)
    assert set(graph.edges) == set(truth.edges)
    costs = {finding.identifier: finding.cost for finding in findings}
    relevant = corridor(graph, entries, targets)
    identifier = f"{family}-{width}-{seed}"
    records = []
    for policy in POLICIES:
        wall, cpu, choices = [], [], []
        for _ in range(repeats):
            cpu_start, wall_start = time.process_time_ns(), time.perf_counter_ns()
            current = build_graph(findings, routes)
            selected = select_treatments(current, findings, entries, targets, policy)
            # Every policy receives the same modeled endpoint oracle.
            residual = reachable_pairs(residual_graph(truth, selected), entries, targets)
            assert residual == 0
            wall.append((time.perf_counter_ns() - wall_start) / 1e6)
            cpu.append((time.process_time_ns() - cpu_start) / 1e6)
            choices.append(selected)
        assert all(choice == choices[0] for choice in choices)
        budget = 0.25 * sum(costs.values())
        prefix, spent = [], 0
        for node in selected:
            if spent + costs[node] > budget:
                break
            prefix.append(node)
            spent += costs[node]
        records.append(dict(scenario=identifier, family=family, policy=policy, selected=selected,
                            cost=sum(costs[node] for node in selected), actions=len(selected),
                            off_target=len(set(selected) - relevant), residual_pairs=residual,
                            budget=budget, budget_spent=spent,
                            budget_residual_fraction=reachable_pairs(residual_graph(truth, prefix), entries, targets)
                            / reachable_pairs(truth, entries, targets), wall_ms=wall, cpu_ms=cpu))
    ablations = []
    for mode, scopes, evidence in (
        ("server_only", frozenset({"server"}), True),
        ("environment_only", frozenset({"server", "environment"}), True),
        ("no_routed", frozenset({"server", "environment", "network"}), True),
        ("no_evidence", frozenset({"server", "environment", "network", "routed"}), False)):
        partial = build_graph(findings, routes, scopes=scopes, evidence=evidence)
        selected = select_treatments(partial, findings, entries, targets, "separator")
        ablations.append(dict(scenario=identifier, family=family, mode=mode,
                              missing_edges=len(set(truth.edges) - set(partial.edges)),
                              extra_edges=len(set(partial.edges) - set(truth.edges)),
                              corridor_missed=len(relevant - corridor(partial, entries, targets)),
                              selected=selected, cost=sum(costs[node] for node in selected),
                              residual_pairs=reachable_pairs(residual_graph(truth, selected), entries, targets)))
    uncertainty = []
    mask_generator = random.Random(700000 + 10000 * FAMILIES.index(family) + 100 * width + seed)
    mask = {pair: mask_generator.random() for pair in routes}
    for fraction in (0.25, 0.5):
        observed = {pair: None if mask[pair] < fraction else value for pair, value in routes.items()}
        for upper in (False, True):
            uncertain = build_graph(findings, observed, upper=upper)
            selected = select_treatments(uncertain, findings, entries, targets, "separator")
            residual = reachable_pairs(residual_graph(truth, selected), entries, targets)
            if upper:
                assert residual == 0
            uncertainty.append(dict(scenario=identifier, family=family, missing=fraction, upper=upper,
                                    selected=selected, cost=sum(costs[node] for node in selected),
                                    residual_pairs=residual,
                                    observed_routes=[dict(source=source, target=target, value=value)
                                                     for (source, target), value in observed.items()]))
    serialized = dict(identifier=identifier, family=family, width=width, seed=seed,
                      findings=[asdict(finding) for finding in findings], entries=entries, targets=targets,
                      routes=[dict(source=source, target=target, value=value)
                              for (source, target), value in routes.items()],
                      truth_edges=sorted(truth.edges), corridor=sorted(relevant))
    return serialized, records, ablations, uncertainty


def scaling():
    rows = []
    for width in (10, 100, 1000):
        graph = nx.DiGraph()
        layers = [[f"{layer}:{index}" for index in range(width)] for layer in range(6)]
        for previous, following in zip(layers, layers[1:]):
            graph.add_edges_from((source, following[(index + offset) % width])
                                 for index, source in enumerate(previous) for offset in (0, 1, 2))
        costs = {node: 1.0 for node in graph}
        for repetition in range(3):
            started = time.perf_counter_ns()
            nodes = corridor(graph, {layers[0][0]}, set(layers[-1]))
            corridor_ms = (time.perf_counter_ns() - started) / 1e6
            started = time.perf_counter_ns()
            chosen = minimum_separator(graph, set(layers[0]), set(layers[-1]), costs)
            cut_ms = (time.perf_counter_ns() - started) / 1e6
            assert not reachable_pairs(residual_graph(graph, chosen), set(layers[0]), set(layers[-1]))
            rows.append(dict(nodes=len(graph), edges=graph.number_of_edges(), repetition=repetition,
                             corridor_nodes=len(nodes), corridor_ms=corridor_ms, cut_ms=cut_ms,
                             separator_size=len(chosen)))
    return rows


def tables(output, records, ablations, uncertainty, scales):
    lines = [r"\begin{tabular}{llrrrr}", r"\hline",
             r"Scope & Policy & Cost & Nodes & Off-target & Residual at budget \\", r"\hline"]
    summaries = []
    for family, policy in itertools.product(FAMILIES, POLICIES):
        selected = [row for row in records if row["family"] == family and row["policy"] == policy]
        means = {key: statistics.mean(row[key] for row in selected)
                 for key in ("cost", "actions", "off_target", "budget_residual_fraction")}
        summaries.append(dict(family=family, policy=policy, **means))
        label = policy.replace("_", " ")
        lines.append(f"{family} & {label} & {means['cost']:.2f} & {means['actions']:.2f} & "
                     f"{means['off_target']:.2f} & {means['budget_residual_fraction']:.3f} " + r"\\")
    lines.extend([r"\hline", r"\end{tabular}"])
    (output / "policy_table.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")
    lines = [r"\begin{tabular}{llrrr}", r"\hline",
             r"Scope & Ablation & Failed cases & Missing edges & Extra edges \\", r"\hline"]
    for family, mode in itertools.product(FAMILIES, ("server_only", "environment_only", "no_routed", "no_evidence")):
        selected = [row for row in ablations if row["family"] == family and row["mode"] == mode]
        lines.append(f"{family} & {mode.replace('_', ' ')} & {sum(row['residual_pairs'] > 0 for row in selected)}/60 & "
                     f"{statistics.mean(row['missing_edges'] for row in selected):.1f} & "
                     f"{statistics.mean(row['extra_edges'] for row in selected):.1f} " + r"\\")
    lines.extend([r"\hline", r"\end{tabular}"])
    (output / "ablation_table.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")
    lines = [r"\begin{tabular}{lrrrr}", r"\hline",
             r"Hidden routes & Lower failures & Upper failures & Lower cost & Upper cost \\", r"\hline"]
    for fraction in (0.25, 0.5):
        lower = [row for row in uncertainty if row["missing"] == fraction and not row["upper"]]
        upper = [row for row in uncertainty if row["missing"] == fraction and row["upper"]]
        lines.append(f"{int(fraction * 100)}\\% & {sum(row['residual_pairs'] > 0 for row in lower)}/240 & "
                     f"{sum(row['residual_pairs'] > 0 for row in upper)}/240 & "
                     f"{statistics.mean(row['cost'] for row in lower):.2f} & "
                     f"{statistics.mean(row['cost'] for row in upper):.2f} " + r"\\")
    lines.extend([r"\hline", r"\end{tabular}"])
    (output / "uncertainty_table.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")
    lines = [r"\begin{tabular}{rrrr}", r"\hline",
             r"Nodes & Edges & Corridor (ms) & Separator (ms) \\", r"\hline"]
    for size in (60, 600, 6000):
        selected = [row for row in scales if row["nodes"] == size]
        lines.append(f"{size} & {selected[0]['edges']} & "
                     f"{statistics.median(row['corridor_ms'] for row in selected):.2f} & "
                     f"{statistics.median(row['cut_ms'] for row in selected):.2f} " + r"\\")
    lines.extend([r"\hline", r"\end{tabular}"])
    (output / "scaling_table.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summaries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    output = arguments.output
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a new output directory; results are not overwritten")
    output.mkdir(parents=True, exist_ok=True)
    scenarios, records, ablations, uncertainty = [], [], [], []
    for family, width, seed in itertools.product(FAMILIES, (4, 12, 32), range(20)):
        case, decisions, controls, hidden = evaluate_case(family, width, seed, 3)
        scenarios.append(case)
        records.extend(decisions)
        ablations.extend(controls)
        uncertainty.extend(hidden)
    scales = scaling()
    summaries = tables(output, records, ablations, uncertainty, scales)
    for name, value in (("scenarios", scenarios), ("policy_runs", records), ("ablations", ablations),
                        ("uncertainty", uncertainty), ("scaling", scales), ("summary", summaries)):
        save(output / f"{name}.json", value)
    source_files = [Path(__file__), Path(__file__).with_name("multiscale_chaining.py"),
                    Path(__file__).with_name("test_multiscale_chaining.py")]
    manifest = dict(timestamp_utc=datetime.now(timezone.utc).isoformat(), python=platform.python_version(),
                    platform=platform.platform(), networkx=nx.__version__, seeds=20, repeats=3,
                    scenarios=len(scenarios), policy_records=len(records), timed_executions=len(records) * 3,
                    ablations=len(ablations), uncertainty_decisions=len(uncertainty), scaling_runs=len(scales),
                    synthetic_only=True, source_sha256={path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                                       for path in source_files},
                    output_sha256={path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                   for path in sorted(output.iterdir())})
    save(output / "manifest.json", manifest)
    print(json.dumps({key: value for key, value in manifest.items() if "sha256" not in key}, indent=2))


if __name__ == "__main__":
    main()