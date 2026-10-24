"""Read-only public advisory checks and a combined CycloneDX inventory.

No extra scanner is installed. PyPI's version feed is a bounded advisory source,
not a complete vulnerability oracle for CPython, native DLLs or bundled codecs.
Only public package names/versions and the public npm lock are sent to registries.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from uuid import uuid4

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def query(package):
    name, version = package
    url = f"https://pypi.org/pypi/{urllib.parse.quote(name)}/{urllib.parse.quote(version)}/json"
    row = {"name": name, "version": version, "public_url": url}
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("Public metadata response exceeds limit")
        data = json.loads(raw)
        advisories = data.get("vulnerabilities", [])
        row.update(
            status="AVAILABLE",
            response_sha256=hashlib.sha256(raw).hexdigest(),
            advisories=[
                {key: item.get(key) for key in ("id", "aliases", "fixed_in", "link", "withdrawn")}
                for item in advisories
            ],
            active_advisory_count=sum(not item.get("withdrawn") for item in advisories),
        )
    except Exception as error:
        # Never serialize proxy URLs, ambient configuration or arbitrary responses.
        row.update(status="UNAVAILABLE", error_type=type(error).__name__)
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise SystemExit("The existing npm CLI is required; no tool is installed automatically")
    report = {
        "queried_utc": datetime.now(timezone.utc).isoformat(),
        "source_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "lock_hashes": {
            name: digest(root / name)
            for name in (
                "backend/requirements.lock",
                "backend/requirements-dev.txt",
                "desktop/package-lock.json",
            )
        },
        "python_version": sys.version.split()[0],
        "extra_audit_tool_installed": False,
        "private_source_or_profile_uploaded": False,
        "scope_limitations": [
            "PyPI version JSON does not establish absence of all Python vulnerabilities.",
            "npm audit covers the registry lock graph, not all embedded Chromium/native codec advisories.",
            "CPython/native DLLs require the separate version/provenance and exposure review.",
            "Cloud model services, optional user model weights and personal profiles are outside this scan.",
        ],
    }
    requirements = {}
    for filename in ("backend/requirements.lock", "backend/requirements-dev.txt"):
        for line in (root / filename).read_text(encoding="utf-8-sig").splitlines():
            if not line.strip() or line.lstrip().startswith(("#", "-r")):
                continue
            requirement = Requirement(line)
            if requirement.marker and not requirement.marker.evaluate():
                continue
            versions = [item.version for item in requirement.specifier if item.operator == "=="]
            if len(versions) != 1:
                raise ValueError("All scanned requirements must have an exact pin")
            name = canonicalize_name(requirement.name)
            if metadata.version(name) != versions[0]:
                raise ValueError("Installed package differs from the audited lock: " + name)
            requirements[name] = versions[0]
    # pip is supplied by the interpreter; include it even though it isn't an app dependency.
    requirements["pip"] = metadata.version("pip")
    with ThreadPoolExecutor(max_workers=4) as pool:
        report["python"] = list(pool.map(query, sorted(requirements.items())))
    for label, command in (
        ("npm-audit", [npm, "audit", "--json", "--registry=https://registry.npmjs.org"]),
        ("npm-sbom", [npm, "sbom", "--sbom-format=cyclonedx", "--package-lock-only"]),
    ):
        try:
            result = subprocess.run(
                command, cwd=root / "desktop", capture_output=True, timeout=120, check=False
            )
            parsed = json.loads(result.stdout)
            (output / (label + ".json")).write_text(json.dumps(parsed, indent=2), encoding="utf-8")
            report[label] = {
                "exit_code": result.returncode,
                "evidence_sha256": digest(output / (label + ".json")),
            }
        except (subprocess.TimeoutExpired, ValueError) as error:
            report[label] = {"status": "UNAVAILABLE", "error_type": type(error).__name__}
    nodes = (
        json.loads((output / "npm-sbom.json").read_text(encoding="utf-8"))
        if (output / "npm-sbom.json").exists()
        else {}
    )
    components = nodes.get("components", [])
    dependencies = nodes.get("dependencies", [])
    inventory = json.loads((root / "licenses/inventory.json").read_text(encoding="utf-8"))
    licenses = {canonicalize_name(item["name"]): item for item in inventory["components"]}
    for name, version in sorted(requirements.items()):
        purl = f"pkg:pypi/{name}@{version}"
        component = {
            "type": "library",
            "name": name,
            "version": version,
            "purl": purl,
            "bom-ref": purl,
        }
        notice = licenses.get(name)
        if notice:
            component["properties"] = [
                {"name": "companion:notice-path", "value": row["path"]} for row in notice["files"]
            ]
        components.append(component)
        refs = []
        for text in metadata.requires(name) or []:
            requirement = Requirement(text)
            child = canonicalize_name(requirement.name)
            if child in requirements and (not requirement.marker or requirement.marker.evaluate()):
                refs.append(f"pkg:pypi/{child}@{requirements[child]}")
        dependencies.append({"ref": purl, "dependsOn": sorted(set(refs))})
    for item in inventory["components"]:
        if canonicalize_name(item["name"]) in requirements:
            continue
        ref = "companion:vendor:" + canonicalize_name(item["name"]) + ":" + item["version"]
        components.append(
            {
                "type": "framework" if "python" in item["name"].lower() else "library",
                "name": item["name"],
                "version": item["version"],
                "bom-ref": ref,
                "properties": [
                    {"name": "companion:declared-license", "value": item["license"]},
                    {"name": "companion:scope", "value": item["scope"]},
                ],
                "externalReferences": [
                    {"type": "website", "url": url} for url in item.get("upstream", [])
                ],
            }
        )
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "serialNumber": "urn:uuid:" + str(uuid4()),
        "metadata": {
            "timestamp": report["queried_utc"],
            "component": {
                "type": "application",
                "name": "Companion Studio",
                "version": json.loads((root / "desktop/package.json").read_text())["version"],
            },
        },
        "components": components,
        "dependencies": dependencies,
    }
    (output / "sbom.cdx.json").write_text(json.dumps(sbom, indent=2), encoding="utf-8")
    active = sum(row.get("active_advisory_count", 0) for row in report["python"])
    unavailable = sum(row["status"] != "AVAILABLE" for row in report["python"])
    npm_ok = report.get("npm-audit", {}).get("exit_code") == 0
    sbom_ok = report.get("npm-sbom", {}).get("exit_code") == 0
    report.update(
        active_python_advisories=active,
        unavailable_python_components=unavailable,
        registry_scope_status="FAIL"
        if active or report.get("npm-audit", {}).get("exit_code") == 1
        else "PASS"
        if not unavailable and npm_ok and sbom_ok
        else "PARTIAL",
        sbom_sha256=digest(output / "sbom.cdx.json"),
    )
    (output / "dependency-security.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "registry_scope_status",
                    "active_python_advisories",
                    "unavailable_python_components",
                )
            }
        )
    )
    return 1 if report["registry_scope_status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
