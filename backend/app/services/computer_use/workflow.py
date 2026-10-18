"""Schema-1 visual nodes are normalized into original registry tools."""


def tool_node(node):
    if node.type != "computer_use":
        return node
    return node.model_copy(
        update={
            "type": "tool",
            "config": {
                "name": "computer." + node.config.get("action", "observe"),
                "arguments": node.config.get("arguments", {}),
            },
        }
    )
