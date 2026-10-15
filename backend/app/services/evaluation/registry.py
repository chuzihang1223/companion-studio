"""只加载随源码审核的版本化数据集；API 不接受外部数据集路径。"""

import hashlib
import json
from pathlib import Path

from app.schemas.evaluation import BenchmarkCase


class BenchmarkRegistry:
    def __init__(self):
        raw = (Path(__file__).parent / "datasets" / "studio-core.json").read_bytes()
        self.digest = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw)
        self.cases = [BenchmarkCase.model_validate(row) for row in data["cases"]]
        self.by_id = {row.case_id: row for row in self.cases}
        if len(self.by_id) != len(self.cases) or len(self.cases) != 20:
            raise ValueError("内置数据集应包含 20 个唯一测试项")
        self.dataset_id = "studio-core"
        self.version = "1.0.0"

    def public(self, detail=False):
        result = {
            "dataset_id": self.dataset_id,
            "dataset_version": self.version,
            "title": "Studio Core · 20 项",
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
