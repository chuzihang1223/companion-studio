"""Read-only extraction of actual completed Evaluation records from owned pytest databases."""

import json
from pathlib import Path
import sqlite3
from visual_review_evidence import require, sha


def collect(source, root, destination, revision):
    require(root.resolve().is_relative_to((source / ".cache").resolve()), "Owned QA cache required")
    destination.mkdir()
    datasets = {
        p.stem: (p, json.loads(p.read_text(encoding="utf-8")))
        for p in (source / "backend/app/services/evaluation/datasets").glob("*-core.json")
    }
    found = {}
    for database in root.rglob("*"):
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
                    found[key] = value
        except sqlite3.DatabaseError:
            continue  # Non-evaluation fixture or deliberately damaged synthetic database.
    # Studio's API contract deliberately deletes its completed DB record. Its
    # unchanged original export is retained before that deletion, then checked
    # with the same source/dataset/case identity as the database-derived reports.
    for path in root.rglob("studio-result.json"):
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
