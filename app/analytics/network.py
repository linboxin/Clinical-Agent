"""network: entities linked by shared trials. Two different dimensions give a bipartite graph
(e.g. lead sponsor ↔ drug); the same dimension twice gives a co-occurrence graph (drug ↔ drug
co-listed in one trial record — not proof of combined administration). Edge weight = number
of distinct trials that link the two nodes."""

from collections import Counter
from itertools import combinations, product

from app.analytics.types import Bucket, CohortTrials, Edge, NetworkResult, Node, TruncationInfo
from app.analytics.values import ValueView
from app.contracts.plan import QueryPlan
from app.registry import REGISTRY

MAX_NODES = 40
MAX_EDGES = 150


def network(plan: QueryPlan, cohorts: list[CohortTrials], view: ValueView) -> NetworkResult:
    op = plan.operation
    assert op.dimension is not None and op.second_dimension is not None
    assert len(cohorts) == 1, "validated upstream: networks use a single cohort"
    a, b = REGISTRY[op.dimension], REGISTRY[op.second_dimension]
    same = a.name == b.name
    relation = (
        f"{a.name.value} co-listed in the same trial"
        if same
        else f"{a.name.value}–{b.name.value} in the same trial"
    )

    nodes: dict[str, Node] = {}
    edges: dict[tuple[str, str], Edge] = {}
    miss: Counter[str] = Counter()

    for trial in cohorts[0].trials:
        filters = cohorts[0].cohort.filters
        va = view.values(a.name, trial, filters)
        vb = va if same else view.values(b.name, trial, filters)
        if same and len(va) < 2:
            miss[f"fewer_than_two_{a.name.value}_values"] += 1
            continue
        if not same and (not va or not vb):
            miss[a.name.value if not va else b.name.value] += 1
            continue
        for v, dim in [(v, a) for v in va] + ([] if same else [(v, b) for v in vb]):
            node_id = f"{dim.name.value}:{v.key}"
            node = nodes.setdefault(node_id, Node(node_id, dim.name.value, Bucket(v.key)))
            node.bucket.add(trial, v.label, [(v.path, v.raw)])
        pairs = combinations(sorted(va, key=lambda v: v.key), 2) if same else product(va, vb)
        for x, y in pairs:
            source = f"{a.name.value}:{x.key}"
            target = f"{b.name.value}:{y.key}"
            edge = edges.setdefault((source, target), Edge(source, target, relation, Bucket("")))
            edge.bucket.add(trial, "", [(x.path, x.raw), (y.path, y.raw)])

    max_nodes = plan.top_n or MAX_NODES
    ranked = sorted(
        edges.values(),
        key=lambda e: (-e.bucket.count, nodes[e.source].bucket.label, nodes[e.target].bucket.label),
    )
    kept_edges: list[Edge] = []
    kept_nodes: set[str] = set()
    for edge in ranked:
        if len(kept_edges) >= MAX_EDGES:
            break
        new = {edge.source, edge.target} - kept_nodes
        if len(kept_nodes) + len(new) > max_nodes:
            continue  # strongest edges first; skip edges that would exceed the node cap
        kept_edges.append(edge)
        kept_nodes |= new

    truncation = None
    if len(kept_edges) < len(edges):
        truncation = TruncationInfo(
            shown=len(kept_edges),
            total=len(edges),
            rule=f"Strongest edges (most shared trials) kept, up to {max_nodes} nodes "
            f"and {MAX_EDGES} edges",
        )
    kept_node_list = sorted(
        (nodes[n] for n in kept_nodes), key=lambda n: (-n.bucket.count, n.bucket.label)
    )
    return NetworkResult(
        nodes=kept_node_list,
        edges=kept_edges,
        bipartite=not same,
        missing={cohorts[0].cohort.label: dict(miss)},
        truncation=truncation,
    )
