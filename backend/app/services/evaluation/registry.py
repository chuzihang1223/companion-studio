"""Load only audited, bundled dataset files; counts and versions come from their content."""

import hashlib
import json
from pathlib import Path
import re

from app.schemas.evaluation import BenchmarkCase

DIRECTORY = Path(__file__).parent / "datasets"


class BenchmarkRegistry:
    @staticmethod
    def available():
        return tuple(
            sorted(
                path.stem
                for path in DIRECTORY.glob("*-core.json")
                if re.fullmatch(r"[a-z]+(?:-[a-z]+)*-core", path.stem)
            )
        )

    def __init__(self, dataset_id="studio-core"):
        if dataset_id not in self.available():
            raise ValueError("未知内置数据集")
        raw = (DIRECTORY / (dataset_id + ".json")).read_bytes()
        self.digest = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw)
        if data["dataset_id"] != dataset_id or not re.fullmatch(r"1\.0\.0", data["version"]):
            raise ValueError("内置数据集身份或版本不符")
        self.cases = [BenchmarkCase.model_validate(row) for row in data["cases"]]
        self.by_id = {row.case_id: row for row in self.cases}
        if not self.cases or len(self.by_id) != len(self.cases):
            raise ValueError("内置数据集测试项为空或编号重复")
        if any(row.dataset_version != data["version"] for row in self.cases):
            raise ValueError("内置数据集测试项版本不符")
        self.dataset_id, self.version = dataset_id, data["version"]
        self.title = data.get("title", dataset_id)

    def public(self, detail=False):
        result = {
            "dataset_id": self.dataset_id,
            "dataset_version": self.version,
            "title": f"{self.title} · {len(self.cases)} 项",
            "case_count": len(self.cases),
            "sha256": self.digest,
        }
        if detail:
            result["cases"] = [row.model_dump(mode="json") for row in self.cases]
        return result

    def select(self, ids):
        if set(ids) - self.by_id.keys():
            raise ValueError("测试项不在当前数据集中")
        return [self.by_id[key] for key in ids] if ids else self.cases
