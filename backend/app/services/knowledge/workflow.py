"""Schema-1 additive knowledge node; it still executes as an original Agent tool."""


def tool_node(node):
    if node.type != "knowledge_search":
        from app.services.computer_use.workflow import tool_node as computer_node

        return computer_node(node)
    return node.model_copy(
        update={
            "type": "tool",
            "config": {"name": "knowledge.search", "arguments": dict(node.config)},
        }
    )
