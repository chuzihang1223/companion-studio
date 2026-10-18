"""只加载随源码审核的版本化数据集；API 不接受外部数据集路径。"""

import hashlib
import json
from pathlib import Path

from app.schemas.evaluation import BenchmarkCase


class BenchmarkRegistry:
    def __init__(self, dataset_id="studio-core"):
        if dataset_id not in {
            "studio-core",
            "recovery-core",
            "mcp-skills-core",
            "workflow-core",
            "knowledge-core",
            "computer-use-core",
        }:
            raise ValueError("未知内置数据集")
        raw = (Path(__file__).parent / "datasets" / (dataset_id + ".json")).read_bytes()
        self.digest = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw)
        self.cases = [BenchmarkCase.model_validate(row) for row in data["cases"]]
        self.by_id = {row.case_id: row for row in self.cases}
        expected = (
            24
            if dataset_id == "computer-use-core"
            else 28
            if dataset_id == "knowledge-core"
            else 16
            if dataset_id == "recovery-core"
            else 20
        )
        if len(self.by_id) != len(self.cases) or len(self.cases) != expected:
            raise ValueError("内置数据集测试项数量不符")
        self.dataset_id = dataset_id
        self.version = "1.0.0"

    def public(self, detail=False):
        result = {
            "dataset_id": self.dataset_id,
            "dataset_version": self.version,
            "title": "Visual Computer Use · 24 项（隔离 Windows GUI）"
            if self.dataset_id == "computer-use-core"
            else "Studio Core · 20 项"
            if self.dataset_id == "studio-core"
            else "Knowledge Core · 28 项"
            if self.dataset_id == "knowledge-core"
            else "Recovery Core · 16 项"
            if self.dataset_id == "recovery-core"
            else "Workflow Core · 20 项"
            if self.dataset_id == "workflow-core"
            else "MCP & Skills Core · 20 项",
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
