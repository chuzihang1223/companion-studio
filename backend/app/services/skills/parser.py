"""Agent Skills frontmatter validation without YAML objects, aliases or duplicates."""

import re

import yaml

from app.services.mcp_ext.contracts import MCPError


class UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        if len(set(keys)) != len(keys):
            raise MCPError("SKILL_INVALID")
        return super().construct_mapping(node, deep=deep)


def skill_name(name):
    return (
        isinstance(name, str)
        and 1 <= len(name) <= 64
        and name.lower() == name
        and not name.startswith("-")
        and not name.endswith("-")
        and "--" not in name
        and all(c.isalnum() or c == "-" for c in name)
        and name.casefold()
        not in {
            "con",
            "prn",
            "aux",
            "nul",
            *(f"com{i}" for i in range(10)),
            *(f"lpt{i}" for i in range(10)),
        }
    )


def parse_skill(text, dirname):
    if not isinstance(text, str) or len(text.encode("utf-8")) > 32000:
        raise MCPError("SKILL_INVALID")
    match = re.match(r"\A(?:\ufeff)?---\r?\n(.*?)\r?\n---(?:\r?\n|$)(.*)\Z", text, re.S)
    if not match:
        raise MCPError("SKILL_INVALID")
    try:
        if any(
            isinstance(token, (yaml.AliasToken, yaml.AnchorToken)) for token in yaml.scan(match[1])
        ):
            raise MCPError("SKILL_INVALID")
        data = yaml.load(match[1], Loader=UniqueLoader)
    except (yaml.YAMLError, TypeError, ValueError):
        raise MCPError("SKILL_INVALID") from None
    fields = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
    if (
        not isinstance(data, dict)
        or set(data) - fields
        or not skill_name(data.get("name"))
        or data["name"] != dirname
    ):
        raise MCPError("SKILL_INVALID")
    description = data.get("description")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise MCPError("SKILL_INVALID")
    for key, limit in (("license", 500), ("compatibility", 500), ("allowed-tools", 2000)):
        if key in data and (not isinstance(data[key], str) or not 1 <= len(data[key]) <= limit):
            raise MCPError("SKILL_INVALID")
    metadata = data.get("metadata", {})
    if (
        not isinstance(metadata, dict)
        or len(metadata) > 24
        or any(
            not isinstance(k, str) or not isinstance(v, str) or len(k) > 80 or len(v) > 1000
            for k, v in metadata.items()
        )
    ):
        raise MCPError("SKILL_INVALID")
    return {
        **data,
        "metadata": metadata,
        "instructions": match[2].strip(),
        "required_capabilities": [s for s in metadata.get("companion-capabilities", "").split()][
            :16
        ],
        # This experimental field is a dependency hint, never pre-approval.
        "required_tools": data.get("allowed-tools", "").split()[:24],
    }
