---
name: markdown-report
description: Produce a concise Markdown report from inspected evidence. 用于整理 Markdown 报告、结论、证据和局限。
license: MIT
compatibility: Companion Studio; saving requires a selected project and normal write approval.
metadata:
  version: "1.0.0"
  companion-capabilities: write_report
allowed-tools: workspace.create
---

# Markdown report

Organize the report into a summary, observed facts, actions and limits. Do not invent
measurements or task success. See [the outline](references/outline.md) if needed.

Show the report in the response unless the user requested a file. For a new file,
use the selected workspace and normal permissions; never overwrite an existing file
silently. Verify a saved artifact using the existing file tools. Do not execute scripts.
