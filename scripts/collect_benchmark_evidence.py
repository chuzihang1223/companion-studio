"""Read-only extraction of actual completed Evaluation records from owned pytest databases."""

import json
from pathlib import Path
import sqlite3
from visual_review_evidence import require, sha


def owned_files(root):
    """Do not traverse Windows junctions or links, including parent chains."""
    root = root.absolute()
    require(
        not any(p.is_symlink() or p.is_junction() for p in (root, *root.parents)),
        "QA root contains a filesystem link",
    )
    resolved = root.resolve()
    pending = [root]
    while pending:
        for path in pending.pop().iterdir():
            if path.is_symlink() or path.is_junction():
                continue
            require(path.resolve().is_relative_to(resolved), "QA path escaped scan root")
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                yield path


def collect(source, root, destination, revision):
    require(root.resolve().is_relative_to((source / ".cache").resolve()), "Owned QA cache required")
    cache = (source / ".cache").resolve()
    require(
        destination.resolve().is_relative_to(cache), "Evidence destination must stay in owned cache"
    )
    require(
        not any(p.is_symlink() or p.is_junction() for p in (destination, *destination.parents)),
        "Evidence destination contains a filesystem link",
    )
    destination.mkdir()
    files = list(owned_files(root))
    datasets = {
        p.stem: (p, json.loads(p.read_text(encoding="utf-8")))
        for p in (source / "backend/app/services/evaluation/datasets").glob("*-core.json")
    }
    found = {}
    for database in files:
        if (
            database.suffix not in {".db", ".sqlite", ".sqlite3"}
            or database.is_symlink()
            or not database.is_file()
        ):
            continue
        try:
            with sqlite3.connect(
                database.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2
            ) as connection:
                connection.row_factory = sqlite3.Row
                tables = {
                    r[0]
                    for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                if not {"evaluation_runs", "evaluation_attempts"} <= tables:
                    continue
                for row in connection.execute(
                    "SELECT * FROM evaluation_runs WHERE status='COMPLETED'"
                ):
                    snapshot = json.loads(row["snapshot_json"])
                    key = snapshot.get("dataset_id")
                    if (
                        key not in datasets
                        or snapshot.get("git_commit") != revision
                        or snapshot.get("repetitions") != 1
                    ):
                        continue
                    path, dataset = datasets[key]
                    expected = {case["case_id"] for case in dataset["cases"]}
                    if (
                        snapshot.get("dataset_sha256") != sha(path)
                        or set(snapshot.get("case_ids", [])) != expected
                    ):
                        continue
                    attempts = list(
                        connection.execute(
                            "SELECT * FROM evaluation_attempts WHERE run_id=?", (row["id"],)
                        )
                    )
                    if (
                        len(attempts) != len(expected)
                        or {a["case_id"] for a in attempts} != expected
                    ):
                        continue
                    results = [
                        {
                            **{
                                k: a[k]
                                for k in (
                                    "case_id",
                                    "execution_status",
                                    "grader_status",
                                    "trace_id",
                                    "task_id",
                                    "conversation_id",
                                    "work_session_id",
                                    "duration_ms",
                                    "error_code",
                                )
                            },
                            "result": json.loads(a["result_json"]),
                        }
                        for a in attempts
                    ]
                    value = {
                        "origin": "original_evaluation_database",
                        "run": {
                            "run_id": row["id"],
                            "snapshot": snapshot,
                            "status": row["status"],
                            "mode": row["mode"],
                            "metrics": json.loads(row["metrics_json"]),
                        },
                        "results": results,
                        "database_sha256": sha(database),
                    }
                    require(key not in found, "Ambiguous full benchmark evidence: " + key)
                    require(
                        all(
                            r["execution_status"] != "PASS"
                            or (
                                r["grader_status"] == "GRADED" and r["result"].get("passed") is True
                            )
                            for r in results
                        ),
                        "Contradictory benchmark grader evidence",
                    )
                    found[key] = value
        except sqlite3.DatabaseError:
            continue  # Non-evaluation fixture or deliberately damaged synthetic database.
    # Studio's API contract deliberately deletes its completed DB record. Its
    # unchanged original export is retained before that deletion, then checked
    # with the same source/dataset/case identity as the database-derived reports.
    for path in (p for p in files if p.name == "studio-result.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        snapshot = value["run"]["snapshot"]
        dataset_path, dataset = datasets["studio-core"]
        expected = {case["case_id"] for case in dataset["cases"]}
        if (
            snapshot.get("dataset_id") == "studio-core"
            and snapshot.get("git_commit") == revision
            and snapshot.get("dataset_sha256") == sha(dataset_path)
            and snapshot.get("repetitions") == 1
            and value["run"].get("status") == "COMPLETED"
            and set(snapshot.get("case_ids", [])) == expected
            and len(value["results"]) == len(expected)
            and {r["case_id"] for r in value["results"]} == expected
        ):
            require("studio-core" not in found, "Ambiguous Studio benchmark evidence")
            require(
                all(
                    r["execution_status"] != "PASS"
                    or (r["grader_status"] == "GRADED" and r["result"].get("passed") is True)
                    for r in value["results"]
                ),
                "Contradictory Studio grader evidence",
            )
            found["studio-core"] = {
                **value,
                "origin": "original_evaluation_export_before_API_deletion",
                "export_sha256": sha(path),
            }
    require(
        set(found) == set(datasets),
        "Missing current full benchmark evidence: " + ", ".join(sorted(set(datasets) - set(found))),
    )
    for key, value in found.items():
        (destination / (key + ".json")).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return found
