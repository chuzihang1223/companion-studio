"""Check the public lock graph and native notices, not just a nonempty SBOM."""

from visual_review_evidence import read, require


def validate_sbom(source, home, combined, python_pins):
    lock = read(source / "desktop/package-lock.json")["packages"]
    packages = {path: row for path, row in lock.items() if path and "version" in row}

    def identity(path):
        row = packages[path]
        return row.get("name") or path.rsplit("node_modules/", 1)[-1], row["version"]

    def resolve(path, name):
        # Node resolves the nearest installed dependency, walking out through
        # nested node_modules. Missing optional/peer dependencies stay optional.
        while True:
            candidate = (path + "/" if path else "") + "node_modules/" + name
            if candidate in packages:
                return identity(candidate)
            if not path:
                return None
            path = path.rsplit("/node_modules/", 1)[0] if "/node_modules/" in path else ""

    npm = read(home / "npm-sbom.json")
    components = npm.get("components", [])
    expected = {identity(path) for path in packages}
    require(
        expected and {(c["name"], c.get("version")) for c in components} == expected,
        "npm SBOM omits or invents locked components",
    )
    refs = {c["bom-ref"]: (c["name"], c.get("version")) for c in components}
    require(len(refs) == len(components), "npm component references are duplicated")
    root_ref = npm["metadata"]["component"]["bom-ref"]
    all_refs = set(refs) | {root_ref}
    graph = {d["ref"]: set(d.get("dependsOn", [])) for d in npm.get("dependencies", [])}
    require(
        set(graph) == all_refs and len(graph) == len(npm["dependencies"]),
        "npm dependency graph does not cover every component",
    )
    require(all(children <= all_refs for children in graph.values()), "npm graph has unknown refs")
    reverse = {value: key for key, value in refs.items()}
    for path, row in lock.items():
        if path and path not in packages:
            continue
        parent_ref = reverse[identity(path)] if path else root_ref
        names = set(row.get("dependencies", {}))
        if not path:
            names.update(row.get("devDependencies", {}))
        for name in names - set(row.get("optionalDependencies", {})):
            child = resolve(path, name)
            require(
                child is not None and reverse[child] in graph[parent_ref],
                "npm graph omits a mandatory locked edge",
            )
    merged = {c["bom-ref"]: c for c in combined["components"]}
    require(len(merged) == len(combined["components"]), "Combined SBOM has duplicate refs")
    require(
        all(merged.get(c["bom-ref"]) == c for c in components),
        "Combined SBOM omits or changes npm components",
    )
    merged_graph = {d["ref"]: set(d.get("dependsOn", [])) for d in combined["dependencies"]}
    require(
        all(
            merged_graph.get(ref, set()) >= children
            if ref == root_ref
            else merged_graph.get(ref) == children
            for ref, children in graph.items()
        ),
        "Combined SBOM omits or changes the npm graph",
    )
    require(
        combined.get("metadata", {}).get("component", {}).get("bom-ref") == root_ref,
        "Combined SBOM lost the application root",
    )
    require(
        set(merged_graph) == set(merged) | {root_ref}
        and all(children <= set(merged_graph) for children in merged_graph.values()),
        "Combined dependency graph omits components or contains unknown refs",
    )
    from packaging.utils import canonicalize_name

    inventory = read(source / "licenses/inventory.json")["components"]
    for item in inventory:
        name = canonicalize_name(item["name"])
        if name in python_pins:
            continue
        ref = "companion:vendor:" + name + ":" + item["version"]
        component = merged.get(ref, {})
        props = {p["name"]: p["value"] for p in component.get("properties", [])}
        require(
            component.get("version") == item["version"]
            and props.get("companion:declared-license") == item["license"]
            and component.get("externalReferences"),
            "SBOM omits native license/provenance",
        )
