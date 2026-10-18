"""Schema-1 additive knowledge node; it still executes as an original Agent tool."""


def tool_node(node):
    if node.type != "knowledge_search":
        return node
    return node.model_copy(
        update={
            "type": "tool",
            "config": {"name": "knowledge.search", "arguments": dict(node.config)},
        }
    )
