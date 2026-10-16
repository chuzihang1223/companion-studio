# 为 Companion Studio 编写 Agent Skill

遵循 [Agent Skills 规范](https://agentskills.io/specification)。目录名必须与 frontmatter name
一致，1–64 字符、小写字母/数字/连字符、不以连字符开头结尾、无连续连字符；拒绝 Windows 保留名。
description 必填、非空、最多 1024 字；compatibility 可选、最多 500 字。
metadata 是字符串到字符串的映射。禁止 YAML 对象、别名、锚点、重复键和未知顶层字段。

```text
my-skill/
  SKILL.md
  references/
    guide.md
```

```yaml
---
name: my-skill
description: Describe the concrete task and when to use it.
license: MIT
metadata:
  version: "1.0.0"
  companion-capabilities: read_project_files search_code
allowed-tools: workspace.read workspace.search
---
```

正文写明目标、步骤、输入/输出、失败处理和安全边界。把长篇资料放在 references/，只在需要
时读取，避免每次发送完整资料。不要写“忽略系统规则”、永久权限、泄露凭据或自动安装程序。

`allowed-tools` 在本应用中只用于依赖检查，不按标准实验字段名称自动授予权限。
当前能力映射：read_project_files → 项目读取工具，search_code → workspace.search，
write_report → workspace.create/write，run_tests → code.run。未知能力标为缺失。
权限和所选项目约束仍由本地执行层决定。

开发验证：

```powershell
.\.venv\Scripts\python.exe -B -m pytest backend\tests\test_skill_security.py -q
```

源码示例由公开 MIT 工程自建，不含角色素材、账号、模型文件或第三方受限内容。
