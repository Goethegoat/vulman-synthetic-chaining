import itertools
import random
import unittest
from dataclasses import replace

import networkx as nx

from multiscale_chaining import (Finding, build_graph, corridor, minimum_separator,
                                reachable_pairs, relation_status, select_treatments)


class MultiscaleTests(unittest.TestCase):
    def setUp(self):
        self.source = Finding("first", "server1", "prod", "net1", "tenant1", "entry", frozenset({"admin"}))
        self.target = Finding("second", "server1", "prod", "net1", "tenant1", "admin", frozenset({"data"}))

    def test_same_server(self):
        self.assertEqual(relation_status(self.source, self.target, {}), "supported")

    def test_environment_network_and_routed(self):
        for environment, network, expected in [("prod", "net2", "environment"),
                                                ("test", "net1", "network"),
                                                ("test", "net2", "routed")]:
            target = replace(self.target, server="server2", environment=environment, network=network)
            graph = build_graph([self.source, target], {(self.source.asset, target.asset): True})
            self.assertEqual(graph.edges["first", "second"]["scope"], expected)

    def test_segmentation_blocks(self):
        target = replace(self.target, server="server2")
        self.assertEqual(build_graph([self.source, target], {(self.source.asset, target.asset): False}).number_of_edges(), 0)

    def test_same_environment_does_not_prove_access(self):
        target = replace(self.target, server="server2")
        self.assertEqual(relation_status(self.source, target, {}), "unknown")
        self.assertEqual(build_graph([self.source, target], {}, upper=False).number_of_edges(), 0)
        self.assertEqual(build_graph([self.source, target], {}).number_of_edges(), 1)

    def test_local_privilege_not_transferred(self):
        target = replace(self.target, server="server2", remote=False)
        self.assertEqual(relation_status(self.source, target, {(self.source.asset, target.asset): True}), "incompatible")

    def test_identity_boundary(self):
        self.assertEqual(relation_status(self.source, replace(self.target, domain="tenant2"), {}), "incompatible")

    def test_unknown_identity(self):
        self.assertEqual(relation_status(self.source, replace(self.target, domain=None), {}), "unknown")

    def test_scoped_host_identity(self):
        self.assertEqual(relation_status(self.source, replace(self.target, environment="test"), {}), "unknown")

    def test_capability_mismatch(self):
        self.assertEqual(relation_status(self.source, replace(self.target, requires="credential"), {}), "incompatible")

    def test_duplicate_and_self(self):
        with self.assertRaises(ValueError):
            build_graph([self.source, self.source], {})
        source = replace(self.source, requires="admin")
        self.assertEqual(build_graph([source], {}).number_of_edges(), 0)

    def test_dag_corridor_against_enumeration(self):
        for seed in range(40):
            generator = random.Random(seed)
            graph = nx.DiGraph()
            graph.add_nodes_from(str(index) for index in range(8))
            graph.add_edges_from((str(source), str(target)) for source in range(8)
                                 for target in range(source + 1, 8) if generator.random() < 0.3)
            explicit = set(itertools.chain.from_iterable(nx.all_simple_paths(graph, "0", "7")))
            self.assertEqual(corridor(graph, {"0"}, {"7"}), explicit)

    def test_cycle_is_walk_superset_not_simple_path_union(self):
        graph = nx.DiGraph([("start", "pivot"), ("pivot", "target"), ("pivot", "spur"), ("spur", "pivot")])
        explicit = set(itertools.chain.from_iterable(nx.all_simple_paths(graph, "start", "target")))
        self.assertEqual(corridor(graph, {"start"}, {"target"}) - explicit, {"spur"})

    def test_separator_against_exhaustive_subsets(self):
        for seed in range(40):
            generator = random.Random(seed)
            nodes = [str(index) for index in range(7)]
            graph = nx.DiGraph()
            graph.add_nodes_from(nodes)
            graph.add_edges_from((source, target) for source in nodes for target in nodes
                                 if source != target and generator.random() < 0.18)
            costs = {node: generator.randint(1, 9) for node in nodes}
            selected = minimum_separator(graph, {"0", "1"}, {"5", "6"}, costs)
            optimum = float("inf")
            for size in range(len(nodes) + 1):
                for subset in itertools.combinations(nodes, size):
                    residual = graph.subgraph(set(nodes) - set(subset))
                    if not reachable_pairs(residual, {"0", "1"}, {"5", "6"}):
                        optimum = min(optimum, sum(costs[node] for node in subset))
            self.assertEqual(sum(costs[node] for node in selected), optimum)

    def test_empty_and_overlapping_endpoints(self):
        graph = nx.DiGraph()
        graph.add_node("first")
        self.assertEqual(minimum_separator(graph, set(), {"first"}, {"first": 2}), set())
        self.assertEqual(minimum_separator(graph, {"first"}, {"first"}, {"first": 2}), {"first"})

    def test_policy_endpoint(self):
        findings = [self.source, self.target]
        graph = build_graph(findings, {})
        for policy in ("severity", "signal", "context_cost", "relevant_cost", "separator"):
            selected = select_treatments(graph, findings, {"first"}, {"second"}, policy)
            self.assertEqual(len(selected), 1)

    def test_invalid_cost(self):
        for value in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                build_graph([replace(self.source, cost=value)], {})


if __name__ == "__main__":
    unittest.main()