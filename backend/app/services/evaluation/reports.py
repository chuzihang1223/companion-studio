"""只导出固定测试摘要和 allowlist 证据；不合并用户聊天或原始 Trace。"""

import csv
import io
import json


def csv_cell(value):
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


class EvaluationReportService:
    def __init__(self, repository):
        self.repository = repository

    def export(self, run_id, format):
        run = self.repository.detail(run_id)
        rows = self.repository.results(run_id, 0, 100)["items"]
        # All report text comes from vetted built-in labels and controlled DTOs.
        # Config snapshots have no keys/URLs; results contain booleans/counts only.
        report = {
            "schema_version": 1,
            "title": "Mock Evaluation" if run["mode"] == "mock" else "Agent Evaluation",
            "run": run,
            "results": rows,
            "limitations": [
                "Mock 是模拟边界回归，不能视为真实 AI 任务成功率。",
                "GUI/Live 未执行项不计入有效测试数。",
                "费用未知显示 N/A；报告不含私人聊天/原始 Trace。",
                "Token 预算按返回 Usage 停止后续调用，服务端输入 Token 无法在请求前精确计量。",
            ],
        }
        if format == "json":
            content = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
        elif format == "csv":
            stream = io.StringIO(newline="")
            writer = csv.writer(stream)
            writer.writerow(
                [
                    "source",
                    "case_id",
                    "attempt_id",
                    "status",
                    "duration_ms",
                    "tool_calls",
                    "retries",
                    "error_code",
                    "trace_id",
                    "recovery_attempts",
                    "recovery_duration_ms",
                    "replanning_count",
                    "duplicate_execution_count",
                ]
            )
            for row in rows:
                result = row["result"]
                writer.writerow(
                    [
                        csv_cell(value)
                        for value in (
                            report["title"],
                            row["case_id"],
                            row["attempt_id"],
                            row["execution_status"],
                            row["duration_ms"],
                            result.get("tool_calls"),
                            result.get("retry_count"),
                            row["error_code"],
                            row["trace_id"],
                            *(
                                result.get("recovery_metrics", {}).get(key)
                                for key in (
                                    "attempt_count",
                                    "duration_ms",
                                    "replanning_count",
                                    "duplicate_execution_count",
                                )
                            ),
                        )
                    ]
                )
            content = stream.getvalue()
        elif format == "markdown":
            snapshot, metrics = run["snapshot"], run["metrics"]

            def value(key):
                return "N/A" if metrics.get(key) is None else str(round(metrics[key], 3))

            lines = [
                "# " + report["title"],
                "",
                "本报告来自内置合成 Fixture，不包含真实用户资料。",
                "",
                f"- Run: {run_id}",
                f"- App: {snapshot['app_version']} / Commit: {snapshot['git_commit']}",
                f"- Dataset: {snapshot['dataset_id']} {snapshot['dataset_version']}",
                f"- Mode: {run['mode']} / Date: {run['created_at']}",
                "- Model: " + json.dumps(snapshot["model"], ensure_ascii=False),
                "- Environment: " + json.dumps(snapshot["environment"], ensure_ascii=False),
                f"- Valid: {metrics.get('valid_count', 0)} / Success rate: {value('success_rate')} / Failure rate: {value('failure_rate')}",
                f"- Average/P50/P95 (ms): {value('average_duration_ms')} / {value('p50_ms')} / {value('p95_ms')}",
                f"- Tool calls: {metrics.get('tool_calls', 0)} / Retries: {metrics.get('retry_count', 0)}",
                "- Usage: " + json.dumps(metrics.get("model_usage", {}), ensure_ascii=False),
                "- API cost: N/A (no confirmed pricing)",
                "",
                "|Case|Result|Duration ms|Error|Trace|",
                "|---|---|---:|---|---|",
            ]
            lines += [
                f"|{row['case_id']}|{row['execution_status']}|{round(row['duration_ms'], 3) if row['duration_ms'] is not None else 'N/A'}|{row['error_code'] or ''}|{row['trace_id'] or 'N/A'}|"
                for row in rows
            ]
            if metrics.get("recovery"):
                lines += [
                    "",
                    "## Recovery metrics",
                    "",
                    json.dumps(metrics["recovery"], ensure_ascii=False),
                    "",
                    "安全故障断言通过不等于原目标完成；进程退出前的未知耗时不补造。",
                ]
            lines += [
                "",
                "## Failure categories",
                "",
                json.dumps(metrics.get("failure_categories", {}), ensure_ascii=False),
                "",
                "## Limits",
                "",
                *["- " + text for text in report["limitations"]],
            ]
            content = "\n".join(lines) + "\n"
        else:
            raise ValueError("只支持 JSON、CSV 和 Markdown")
        return {
            "format": format,
            "content": content,
            "partial": run["status"] not in {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"},
        }
