"""Original MIT deterministic graphs for the existing isolated Evaluation worker."""

from app.services.workflows.contracts import Definition, Node, Edge


TITLES = [
    "空草稿可保存",
    "合法线性流程",
    "循环拒绝",
    "非法连线拒绝",
    "非法变量拒绝",
    "原工具读取文件",
    "Mock 模型节点",
    "已启用 Skill 节点",
    "授权 MCP 节点",
    "断开 MCP 阻断",
    "条件 true 分支",
    "条件 false 分支",
    "Merge 跳过未激活分支",
    "人工批准",
    "人工拒绝",
    "节点超时",
    "运行中取消",
    "中断后检查点续接",
    "未知副作用不重复",
    "Trace 与结果一致",
]


def linear(kind="transform", config=None):
    return Definition(
        nodes=[
            Node(node_id="start", type="manual_trigger"),
            Node(
                node_id="step",
                type=kind,
                config=config
                if config is not None
                else {"operation": "concat", "value": ["workflow", "fixture"], "separator": " "},
            ),
            Node(node_id="finish", type="output", config={"value": {"$ref": "nodes.step.output"}}),
        ],
        edges=[
            Edge(edge_id="a", source_node_id="start", target_node_id="step"),
            Edge(edge_id="b", source_node_id="step", target_node_id="finish"),
        ],
    )


def branch(choice):
    graph = linear("condition", {"expression": {"op": "equals", "left": choice, "right": True}})
    graph.nodes += [
        Node(node_id="yes", type="transform", config={"operation": "concat", "value": ["yes"]}),
        Node(node_id="no", type="transform", config={"operation": "concat", "value": ["no"]}),
        Node(node_id="join", type="merge", config={"mode": "first_available"}),
    ]
    graph.nodes[2].config = {"value": {"$ref": "nodes.join.output"}}
    graph.edges = [
        graph.edges[0],
        Edge(edge_id="y", source_node_id="step", source_port="true", target_node_id="yes"),
        Edge(edge_id="n", source_node_id="step", source_port="false", target_node_id="no"),
        Edge(edge_id="jy", source_node_id="yes", target_node_id="join"),
        Edge(edge_id="jn", source_node_id="no", target_node_id="join"),
        Edge(edge_id="end", source_node_id="join", target_node_id="finish"),
    ]
    return graph


class WorkflowMockModel:
    async def chat(self, messages):
        return "Synthetic workflow report; no real AI quality claim."
