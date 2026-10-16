# 变量、条件与输出

公开数据路径只使用 `inputs` 和 `nodes.<node_id>.output`。`variables` 是实现内部保留容器，
本版不提供任意全局变量赋值。JSON 文本不会被当作 Python、JavaScript 或 Shell 执行。

```json
{
  "path": {"$ref": "inputs.path"},
  "report": "文件内容：{{nodes.read.output.content}}"
}
```

完整的 `{"$ref":"nodes.read.output"}` 或 `"{{nodes.read.output}}"` 保留原类型；
句中插值只接受字符串、数字或布尔值。不会静默丢字段或把对象拼成 `[object Object]`。
节点引用只能指向拓扑祖先，不能读取自身、未来节点或另一个 Run。
如果一个祖先分支被跳过，其结果不存在；需要通过 Merge 明确汇合，不能假定所有分支都有值。

外部返回的字符串只作为数据传递，不会递归求值其中的 `{{...}}`，也不能替换图、预算、
模型配置或审批范围。敏感字段、常见凭据文本、`__proto__` 等特殊字段和过大结果被拒绝。
这是防护限额，不代表任意用户文本的完整敏感信息识别。

## 条件 DSL

```json
{
  "expression": {
    "op": "and",
    "items": [
      {"op": "exists", "left": {"$ref": "inputs.count"}},
      {"op": "greater_than", "left": {"$ref": "inputs.count"}, "right": 0}
    ]
  }
}
```

支持 equals / not_equals / greater_than / less_than / contains / exists / is_empty / and / or / not。
逻辑项最多 20 个、最多 8 层；not 只能包含一个项。数字比较不把 true 转成 1，跨类型不静默相等。
`exists` 对未定义路径返回 false，其余缺失变量会定位到节点并失败。

## Transform

| operation | 实际行为 |
| --- | --- |
| select | 从对象选 fields 中明确存在的字段。 |
| concat | 用 separator 拼接由基本类型组成的数组。 |
| default | value 为 null 时使用 default。 |
| number | 明确转为有限数字；非数字失败。 |
| length | 字符串、数组或对象的长度。 |
| map | 按 path 提取每个数组对象的字段。 |
| extract | 从对象或 JSON 字符串按 path 提取；支持数组下标。 |

## 有界引用

Input 的 file_reference 形式为 `{"kind":"selected_workspace_file","path":"docs/readme.md"}`。
必须属于本 Run 已选择的项目，存在且不超过 100 KB，不扫描整个目录。
Output 的 file_reference 再检查实际项目文件（最多 1 MB），记录相对路径、大小、SHA256 和
Run ID，不把内容复制进引用。它不是可任意读取绝对路径的下载链接。

trace_reference 只生成当前 Run 的真实 Trace 引用；evaluation_reference 只引用当前档案中
已经存在的评测记录；不会启动评测。task_summary 明确不声称语义目标已经验证。
本版不自动上传、下载或缓存大文件、网页图片和音频。过大中间值会失败；既有用户文件不会被清理。
