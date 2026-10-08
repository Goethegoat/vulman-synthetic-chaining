"""Seeded, wholly synthetic smoke test for hostname/DC tag adaptation.

The generator does not read the enterprise database. A DC tag influences the
synthetic route-evidence distribution but never proves network connectivity.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import random

from multiscale_chaining import (
    Finding,
    build_graph,
    minimum_separator,
    reachable_pairs,
    relation_scope,
    relation_status,
)


@dataclass(frozen=True)
class SyntheticAssetTags:
    hostname: str
    datacenter: str
    environment: str
    network: str
    identity_domain: str

    @property
    def asset_key(self) -> tuple[str, str]:
        return self.environment, self.hostname


def generate_asset_tags(seed: int) -> list[SyntheticAssetTags]:
    generator = random.Random(seed)
    datacenters = [f"synthetic-dc-{label}" for label in generator.sample("ABCD", 4)]
    layouts = [
        ("prod", "synthetic-segment-1"),
        ("prod", "synthetic-segment-2"),
        ("dev", "synthetic-segment-2"),
        ("test", "synthetic-segment-4"),
        ("stage", "synthetic-segment-5"),
        ("test", "synthetic-segment-6"),
    ]
    return [
        SyntheticAssetTags(
            hostname=f"synthetic-host-{seed:05d}-{index:02d}",
            datacenter=datacenters[index % len(datacenters)],
            environment=environment,
            network=network,
            identity_domain="synthetic-tenant-1",
        )
        for index, (environment, network) in enumerate(layouts)
    ]


def make_route_evidence(
    assets: list[SyntheticAssetTags], seed: int
) -> dict[tuple[tuple[str, str], tuple[str, str]], bool]:
    generator = random.Random(seed ^ 0x5A17C0DE)
    routes: dict[tuple[tuple[str, str], tuple[str, str]], bool] = {}
    for source in assets:
        for target in assets:
            if source == target:
                continue
            same_dc = source.datacenter == target.datacenter
            # These arbitrary generator probabilities stratify probes; a DC tag
            # never enters relation_status and therefore cannot prove a route.
            probability_reachable = 0.62 if same_dc else 0.28
            draw = generator.random()
            if draw < probability_reachable:
                routes[(source.asset_key, target.asset_key)] = True
            elif draw < probability_reachable + 0.22:
                routes[(source.asset_key, target.asset_key)] = False
            # Missing route evidence is intentionally left absent (unknown).

    # Plant the documented fixture paths; the side branch remains unresolved.
    host = {asset.hostname: asset for asset in assets}
    for left, right in (
        (host[assets[0].hostname], host[assets[1].hostname]),
        (host[assets[1].hostname], host[assets[2].hostname]),
        (host[assets[3].hostname], host[assets[2].hostname]),
        (host[assets[2].hostname], host[assets[4].hostname]),
        (host[assets[5].hostname], host[assets[2].hostname]),
    ):
        routes[(left.asset_key, right.asset_key)] = True
    routes.pop((assets[0].asset_key, assets[3].asset_key), None)
    routes[(assets[0].asset_key, assets[5].asset_key)] = False
    return routes


def make_findings(assets: list[SyntheticAssetTags], seed: int) -> list[Finding]:
    generator = random.Random(seed ^ 0x1347A11)
    definitions = [
        ("entry", "initial", {"cap-1", "probe-cap"}),
        ("step-1", "cap-1", {"cap-2"}),
        ("step-2", "cap-2", {"cap-3"}),
        ("side-branch", "cap-2", {"cap-3"}),
        ("blocked-branch", "cap-2", {"cap-3"}),
        ("step-3", "cap-3", {"cap-4"}),
        ("target", "cap-4", {"protected-service"}),
    ]
    locations = [assets[0], assets[0], assets[1], assets[3], assets[5], assets[2], assets[4]]
    for index, asset in enumerate(assets[1:], 1):
        definitions.append((f"route-probe-{index}", "probe-cap", {f"unused-{index}"}))
        locations.append(asset)
    costs = [generator.randint(1, 10) for _ in definitions]
    return [
        Finding(
            identifier=identifier,
            server=asset.hostname,
            environment=asset.environment,
            network=asset.network,
            domain=asset.identity_domain,
            requires=requires,
            grants=frozenset(grants),
            cost=float(costs[index]),
        )
        for index, ((identifier, requires, grants), asset) in enumerate(zip(definitions, locations))
    ]


def run_seed(seed: int) -> dict[str, object]:
    assets = generate_asset_tags(seed)
    findings = make_findings(assets, seed)
    routes = make_route_evidence(assets, seed)
    by_id = {finding.identifier: finding for finding in findings}

    candidate_statuses: Counter[str] = Counter()
    scope_counts: Counter[str] = Counter()
    probe_statuses: Counter[str] = Counter()
    for source in findings:
        for target in findings:
            if source.identifier == target.identifier or target.requires not in source.grants:
                continue
            candidate_statuses[relation_status(source, target, routes)] += 1
            scope_counts[relation_scope(source, target)] += 1

    lower = build_graph(findings, routes, upper=False)
    upper = build_graph(findings, routes, upper=True)
    entries, targets = {"entry"}, {"target"}
    assert lower.has_edge("entry", "step-1")
    assert lower.has_edge("step-1", "step-2")
    assert lower.has_edge("step-2", "step-3")
    assert lower.has_edge("step-3", "target")
    assert not lower.has_edge("step-1", "side-branch")
    assert not upper.has_edge("step-1", "blocked-branch")
    assert upper.has_edge("step-1", "side-branch")
    assert upper.edges["step-1", "side-branch"]["status"] == "unknown"
    assert upper.edges["step-1", "step-2"]["scope"] == "environment"
    assert upper.edges["step-2", "step-3"]["scope"] == "network"
    assert upper.edges["step-3", "target"]["scope"] == "routed"
    for index, asset in enumerate(assets[1:], 1):
        probe_id = f"route-probe-{index}"
        state = relation_status(by_id["entry"], by_id[probe_id], routes)
        same_dc = assets[0].datacenter == asset.datacenter
        probe_statuses[f"{'same_dc' if same_dc else 'cross_dc'}:{state}"] += 1
        if state == "supported":
            assert lower.has_edge("entry", probe_id)
            assert upper.has_edge("entry", probe_id)
        elif state == "unknown":
            assert not lower.has_edge("entry", probe_id)
            assert upper.has_edge("entry", probe_id)
        else:
            assert not upper.has_edge("entry", probe_id)
    assert reachable_pairs(lower, entries, targets) == 1
    assert reachable_pairs(upper, entries, targets) == 1

    costs = {identifier: finding.cost for identifier, finding in by_id.items()}
    selected = minimum_separator(upper, entries, targets, costs)
    residual = upper.copy()
    residual.remove_nodes_from(selected)
    assert reachable_pairs(residual, entries, targets) == 0

    return {
        "candidate_statuses": dict(candidate_statuses),
        "scope_counts": dict(scope_counts),
        "probe_route_statuses": dict(probe_statuses),
        "blocked_branch_excluded": not upper.has_edge("step-1", "blocked-branch"),
        "lower_edges": lower.number_of_edges(),
        "upper_edges": upper.number_of_edges(),
        "unknown_branch_edge_in_upper": upper.has_edge("step-1", "side-branch"),
        "separator_cost": sum(costs[node] for node in selected),
        "distinct_datacenters": len({asset.datacenter for asset in assets}),
        "distinct_networks": len({asset.network for asset in assets}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=50)
    parser.add_argument("--seed", type=int, default=20261008)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "synthetic-tag-validation" / "random_tag_smoke.json",
    )
    args = parser.parse_args()
    if args.seeds < 1:
        parser.error("--seeds must be positive")

    runs = [run_seed(args.seed + index) for index in range(args.seeds)]
    statuses: Counter[str] = Counter()
    scopes: Counter[str] = Counter()
    for run in runs:
        statuses.update(run["candidate_statuses"])
        scopes.update(run["scope_counts"])

    report = {
        "data_origin": "generated_synthetic_only_no_enterprise_database_read",
        "purpose": "software_smoke_test_not_semantic_or_field_validation",
        "seed_start": args.seed,
        "seed_count": args.seeds,
        "synthetic_assets": args.seeds * 6,
        "synthetic_occurrences": args.seeds * 12,
        "candidate_transition_statuses": dict(statuses),
        "candidate_scope_counts": dict(scopes),
        "probe_route_evidence_by_dc_relation": dict(
            sum((Counter(run["probe_route_statuses"]) for run in runs), Counter())
        ),
        "lower_graph_edges_total": sum(int(run["lower_edges"]) for run in runs),
        "upper_graph_edges_total": sum(int(run["upper_edges"]) for run in runs),
        "unknown_branch_retained_in_upper_all_runs": all(
            bool(run["unknown_branch_edge_in_upper"]) for run in runs
        ),
        "known_blocked_branch_excluded_all_runs": all(
            bool(run["blocked_branch_excluded"]) for run in runs
        ),
        "all_upper_separators_disconnect_entry_from_target": True,
        "all_runs_have_four_synthetic_datacenters": all(
            run["distinct_datacenters"] == 4 for run in runs
        ),
        "all_runs_have_five_synthetic_networks": all(
            run["distinct_networks"] == 5 for run in runs
        ),
        "mean_synthetic_separator_cost": round(
            sum(float(run["separator_cost"]) for run in runs) / len(runs), 3
        ),
        "limitations": [
            "Hostname, data-center tags, and connectivity are generated, not observed.",
            "A shared data center changes synthetic route-generation probabilities but never proves a route.",
            "Capabilities are planted to exercise known model transitions.",
            "The run does not evaluate the enterprise snapshot or provide empirical ground truth.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Saved synthetic-only summary: {args.output}")


if __name__ == "__main__":
    main()
