"""Public dataset and mode validation, independent of model credentials."""

import pytest
from pydantic import ValidationError

from app.schemas.evaluation import BenchmarkCase, RunRequest
from app.services.evaluation.registry import BenchmarkRegistry


def test_registered_dataset_is_versioned_and_has_twenty_unique_cases():
    registry = BenchmarkRegistry()
    assert len(registry.by_id) == 20
    assert registry.public(True)["cases"][0]["case_id"] == "EVAL-001"
    assert len(registry.digest) == 64
    with pytest.raises(ValueError):
        registry.select(["EVAL-999"])


@pytest.mark.parametrize("path", ["../secret", "C:/private", "/root/file", "..\\secret"])
def test_case_cannot_escape_or_inject_executable_fields(path):
    row = BenchmarkRegistry().cases[0].model_dump()
    row["allowed_paths"] = [path]
    with pytest.raises(ValidationError):
        BenchmarkCase.model_validate(row)
    row["allowed_paths"] = ["fixture.txt"]
    row["shell"] = "anything"
    with pytest.raises(ValidationError):
        BenchmarkCase.model_validate(row)


@pytest.mark.parametrize(
    "patch",
    [
        {"mode": "live_model"},
        {"case_ids": ["EVAL-001", "EVAL-001"]},
        {"concurrency": 3},
        {"mode": "windows_gui", "concurrency": 2},
        {"max_requests": 1},
        {"dataset_id": "../data"},
    ],
)
def test_invalid_run_and_implicit_paid_requests_are_rejected(patch):
    with pytest.raises(ValidationError):
        RunRequest.model_validate(patch)
