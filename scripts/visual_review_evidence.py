"""Bound QA evidence to one clean source, build, artifact and protected profile."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from program_inventory import inventory


REQUIRED = {
    "A": {"source-history-audit", "independent-review"},
    "B": {
        "full-check",
        *{
            f"installed-{name}"
            for name in ("trace", "evaluation", "recovery", "extensions", "workflow", "knowledge")
        },
    },
    "C": {"computer-security-tests", "computer-main-security"},
    "D": {"source-gui", "installed-gui"},
    "E": {"source-performance", "installed-performance"},
    "F": {"build", "installer-dryrun", "installer"},
    "G": {"release-audit", "manifest-profile"},
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def source_identity(source, build_id, profile):
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    dirty = bool(
        subprocess.check_output(["git", "status", "--porcelain"], cwd=source, text=True).strip()
    )
    return {
        "source_root": os.path.normcase(str(source.resolve())),
        "source_revision": revision,
        "dirty": dirty,
        "build_id": build_id,
        "version": read(source / "desktop/package.json")["version"],
        "protected_profile": os.path.normcase(str(profile)) if profile else None,
    }


def safe_file(root, relative):
    candidate = root / relative.replace("\\", "/")
    path = candidate.resolve()
    require(path.is_relative_to(root) and path.is_file(), "Manifest path escaped or missing")
    for part in (candidate, *candidate.parents):
        if not part.is_relative_to(root):
            break
        require(not part.is_symlink() and not part.is_junction(), "Manifest filesystem link")
    return path


def artifact_key(source, delivery, identity):
    manifest_path = delivery / "build-manifest.json"
    manifest = read(manifest_path)
    require(not manifest["checks_skipped"], "Build skipped required checks")
    require(
        all(
            manifest["build"].get(key) == identity[key]
            for key in ("source_revision", "build_id", "version")
        ),
        "Build does not belong to current source/BuildId/version",
    )
    observed = {}
    for name, details in manifest["artifacts"].items():
        observed[name] = sha(safe_file(delivery, name))
        require(observed[name] == details["sha256"], "Artifact digest mismatch")
    require(len(observed) >= 4, "Missing core artifacts")
    require(
        bool(manifest.get("program_files"))
        and inventory(delivery / "win-unpacked") == manifest["program_files"],
        "Full program resources differ from build manifest",
    )
    for name, digest in manifest["source_hashes"].items():
        require(sha(safe_file(source, name)) == digest, "Build/source content mismatch")
    return hashlib.sha256(
        json.dumps({"manifest": sha(manifest_path), "artifacts": observed}, sort_keys=True).encode()
    ).hexdigest()


def needs_artifact(label):
    return (
        label == "build"
        or label.startswith("installed-")
        or label in {"installer-dryrun", "installer", "release-audit", "manifest-profile"}
    )


class Evidence:
    def initialize_evidence(self):
        self.path = self.root / "automated-review.json"
        self.report = (
            read(self.path)
            if self.path.exists()
            else {"schema_version": 2, "steps": [], "limitations": []}
        )
        self.identity = source_identity(self.source, self.build_id, self.profile)
        self.save()

    def current_artifact_key(self):
        return artifact_key(self.source, self.delivery, self.identity)

    def save(self):
        artifact_error = None
        try:
            current_artifact = self.current_artifact_key()
        except (OSError, KeyError, ValueError) as error:
            current_artifact = None
            artifact_error = type(error).__name__
        current = []
        excluded = []
        for index, row in enumerate(self.report["steps"]):
            if row.get("identity") != self.identity:
                excluded.append({"step": index, "reason": "SOURCE_BUILD_PROFILE_IDENTITY_MISMATCH"})
            elif needs_artifact(row["label"]) and (
                not current_artifact or row.get("artifact_key") != current_artifact
            ):
                excluded.append({"step": index, "reason": "ARTIFACT_IDENTITY_MISMATCH"})
                # Keep a pending/failed newer attempt in the label order. Simply
                # dropping it could resurrect an older PASS for the same label.
                current.append(
                    {
                        **row,
                        "status": row["status"]
                        if row["status"] in {"FAIL", "BLOCKED", "NOT_RUN"}
                        else "NOT_RUN",
                    }
                )
            else:
                current.append(row)
        gates = []
        for gate, required in REQUIRED.items():
            latest = {row["label"]: row for row in current if row["gate_id"] == gate}
            statuses = {row["status"] for row in latest.values()}
            status = (
                "FAIL"
                if "FAIL" in statuses
                else "BLOCKED"
                if "BLOCKED" in statuses
                else "PASS"
                if required.issubset(latest) and statuses == {"PASS"}
                else "NOT_RUN"
            )
            gates.append({"gate_id": gate, "status": status})
        verdict = (
            "BLOCKED"
            if self.identity["dirty"] or any(g["status"] in {"FAIL", "BLOCKED"} for g in gates)
            else "READY_FOR_RELEASE_REVIEW"
            if all(g["status"] == "PASS" for g in gates)
            else "INCOMPLETE"
        )
        self.report.update(
            schema_version=2,
            current_identity=self.identity,
            artifact_key=current_artifact,
            artifact_error=artifact_error,
            excluded_historical_steps=excluded,
            gates=gates,
            updated_at=stamp(),
            verdict=verdict,
        )
        self.path.write_text(
            json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def step(self, gate, label, command, *, env=None, verify=None):
        log = self.root / f"{len(self.report['steps']) + 1:02d}-{label}.log"
        row = {
            "gate_id": gate,
            "label": label,
            "identity": dict(self.identity),
            "artifact_key": None,
            "status": "NOT_RUN",
            "command": command,
            "started_at": stamp(),
            "completed_at": None,
            "exit_code": None,
            "evidence_path": str(log.relative_to(self.source)),
            "findings": [],
        }
        # Persist the new attempt before launch. A crash or Ctrl+C cannot leave
        # the previous label's PASS as the latest evidence for this identity.
        if needs_artifact(label) and label != "build":
            try:
                row["artifact_key"] = self.current_artifact_key()
            except (OSError, KeyError, ValueError):
                pass
        self.report["steps"].append(row)
        self.save()
        print(f"Starting {gate}: {label}", flush=True)
        started = time.perf_counter()
        try:
            require(not self.identity["dirty"], "Commit reviewed source before candidate QA")
            require(
                source_identity(self.source, self.build_id, self.profile) == self.identity,
                "Source changed before QA command",
            )
            if needs_artifact(label) and label != "build":
                require(bool(row["artifact_key"]), "No current verified artifact")
            with log.open("w", encoding="utf-8") as stream:
                result = subprocess.run(
                    command,
                    cwd=self.source,
                    env={**os.environ, **(env or {}), "PYTHONOPTIMIZE": ""},
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            row["exit_code"] = result.returncode
            require(result.returncode == 0, "Command returned nonzero; inspect log")
            require(
                source_identity(self.source, self.build_id, self.profile) == self.identity,
                "Source changed during QA command",
            )
            if needs_artifact(label):
                observed = self.current_artifact_key()
                require(
                    label == "build" or observed == row["artifact_key"],
                    "Artifacts changed during QA command",
                )
                row["artifact_key"] = observed
            if verify:
                verify()
            row["status"] = "PASS"
        except BaseException as error:
            row["status"] = "FAIL"
            row["findings"].append(type(error).__name__ + ": " + str(error))
            raise
        finally:
            row.update(completed_at=stamp(), duration_ms=(time.perf_counter() - started) * 1000)
            self.save()
            print(f"{row['status']} {gate}: {label}", flush=True)

    def bind_profile(self):
        require(self.profile is not None, "ProtectedProfile is required for full review")
        before = self.root / "profile-before.json"
        binding = self.root / "profile-binding.json"
        profile_id = os.path.normcase(str(self.profile))
        if binding.exists():
            value = read(binding)
            require(
                value["profile_path"] == profile_id and value["baseline_sha256"] == sha(before),
                "Protected profile identity/baseline changed",
            )
        else:
            require(before.exists(), "Profile baseline missing")
            require(read(before) == self.snapshot(), "Cannot bind changed legacy profile baseline")
            binding.write_text(
                json.dumps(
                    {
                        "profile_path": profile_id,
                        "baseline_sha256": sha(before),
                        "bound_at": stamp(),
                    }
                ),
                encoding="utf-8",
            )
        return read(binding)

    def installed_program(self):
        binding = self.report.get("installer_binding", {})
        require(
            binding.get("identity") == self.identity
            and binding.get("artifact_key") == self.current_artifact_key(),
            "Installed snapshot belongs to another source or artifact",
        )
        report = read(self.root / "installer-result.json")
        require(
            report["success"] and report["installed_snapshot"]["verified"],
            "Installer review did not pass",
        )
        program = Path(report["installed_snapshot"]["program"]).resolve()
        require(
            program.is_relative_to(self.source / ".cache/installer-tests"),
            "Installed snapshot path outside QA",
        )
        manifest = read(self.delivery / "build-manifest.json")
        require(
            inventory(program) == manifest["program_files"], "Installed program resources differ"
        )
        for name, details in manifest["artifacts"].items():
            relative = name.replace("\\", "/")
            if relative.startswith("win-unpacked/"):
                require(
                    sha(safe_file(program, relative[len("win-unpacked/") :])) == details["sha256"],
                    "Installed copy/core digest mismatch",
                )
        return program
