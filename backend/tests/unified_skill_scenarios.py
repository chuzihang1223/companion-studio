"""Shared real workflow scenarios and bounded, explicit synthetic approvals."""

import asyncio
from scheduler_fixture import scheduled
from app.services.workflows.contracts import Definition, Node, Edge


def chain(*nodes):
    rows = [Node(node_id="start", type="manual_trigger"), *nodes]
    return Definition(
        nodes=rows,
        edges=[
            Edge(edge_id="edge" + str(i), source_node_id=a.node_id, target_node_id=b.node_id)
            for i, (a, b) in enumerate(zip(rows, rows[1:]))
        ],
    )


def report_graph(collection, *, second_model=False):
    nodes = [
        Node(
            node_id="search",
            type="knowledge_search",
            config={"query": "Orchard", "collection_ids": [collection], "mode": "keyword"},
        ),
        Node(
            node_id="analyze",
            type="llm_agent",
            config={
                "instruction": "Report the Orchard port with actual citations.",
                "value": {"$ref": "nodes.search.output"},
            },
        ),
    ]
    if second_model:
        nodes.append(
            Node(
                node_id="again",
                type="llm_agent",
                config={
                    "instruction": "Summarize again using only verified evidence.",
                    "value": {"$ref": "nodes.analyze.output"},
                },
            )
        )
    else:
        nodes.append(
            Node(
                node_id="write",
                type="tool",
                config={
                    "name": "workspace.create",
                    "coding_mode": "write_workspace",
                    "arguments": {"path": "report.md", "content": {"$ref": "nodes.analyze.output"}},
                },
            )
        )
    nodes.append(
        Node(
            node_id="finish",
            type="output",
            config={
                "value": {"$ref": "nodes.again.output"} if second_model else {"path": "report.md"},
                **({} if second_model else {"format": "file_reference"}),
            },
        )
    )
    return chain(*nodes)


async def scheduled_run(h, graph):
    definition = scheduled(h.app, h.owner, h.clock, graph_definition=graph)
    h.scheduler.save(definition)
    row = h.scheduler.run_now(definition.schedule_id, h.owner)
    await h.scheduler.tick()
    row = h.scheduler.store.occurrence(row["occurrence_id"], h.owner)
    if row["status"] == "WAITING_PERMISSION":
        h.scheduler.grant_once(row["occurrence_id"], h.owner)
        await h.scheduler.tick()
    return row["occurrence_id"]


async def complete_schedule(h, key):
    approvals = set()
    for _ in range(800):
        row = h.scheduler.store.occurrence(key, h.owner)
        run_id = row["workflow_run_id"]
        if run_id:
            run = h.app.state.workflows.repository.run(run_id, h.owner)
            pending = next((n["pending"] for n in run["nodes"] if n.get("pending")), None)
            if (
                run["state"] == "WAITING_PERMISSION"
                and pending
                and pending["approval_id"] not in approvals
            ):
                approvals.add(pending["approval_id"])
                h.app.state.workflows.approve(run_id, h.owner, pending["approval_id"], True)
            if run["state"] in {"COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"}:
                await asyncio.sleep(0.03)  # release the original coordinator task/lease
                h.scheduler.reconcile()
                await h.trace.flush()
                return h.scheduler.store.occurrence(key, h.owner), run, approvals
        await asyncio.sleep(0.01)
    raise AssertionError("Owned scheduled workflow did not settle")
