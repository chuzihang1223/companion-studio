"""Serial DAG scheduling, with explicit inactive-branch propagation and no recursive execution."""

from app.services.workflows.contracts import TERMINAL


def next_node(compiled, records):
    for key in compiled.order:
        row = records[key]
        if row["state"] != "PENDING":
            continue
        edges = compiled.parents[key]
        if any(records[e.source_node_id]["state"] not in TERMINAL for e in edges):
            continue
        active = []
        for edge in edges:
            parent = records[edge.source_node_id]
            port = parent.get("active_port", "out")
            if parent["state"] in {"COMPLETED", "FAILED"} and edge.source_port == port:
                active.append(
                    {
                        "node_id": edge.source_node_id,
                        "output": parent.get("output"),
                        "port": port,
                        "target_port": edge.target_port,
                    }
                )
        node = next(n for n in compiled.definition.nodes if n.node_id == key)
        active.sort(key=lambda item: compiled.order.index(item["node_id"]))
        # Merge waits for terminal activated parents, never for a skipped branch.
        # Other multi-input nodes require all incoming dependencies to activate.
        skip = bool(edges) and (not active or node.type != "merge" and len(active) != len(edges))
        return node, active, skip
    return None
