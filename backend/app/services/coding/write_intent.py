"""Resolve intent from the user's words, never from a provider's tool arguments."""

import re
from typing import Literal

CodingMode = Literal["virtual_only", "preview_patch", "write_workspace"]


class CodingWriteIntentResolver:
    def resolve(self, text: str) -> CodingMode:
        value = text.lower()
        if re.search(
            r"不要(?:修改|改|写|保存)|别(?:改|写)|只(?:读|看|分析)|不(?:要)?改(?:动)?|do not (?:write|edit)|read.only|analy[sz]e.only",
            value,
        ):
            return "preview_patch"
        explicit = (
            r"(?:直接|实际|真正).{0,12}(?:修改|改|写|保存|应用|修复)",
            r"(?:写进|写入|保存到|存到|应用到).{0,24}(?:项目|文件|目录|文件夹|\.\w+|当前)",
            r"(?:修改|改动|编辑|覆盖|删除|重命名).{0,10}(?:本地|磁盘)",
            r"(?:把|将).{0,24}(?:代码|修改|补丁|patch).{0,12}(?:保存下来|保存到|应用|写进|写入)",
            r"(?:应用|apply).{0,10}(?:补丁|patch|diff)",
            r"(?:修复|修改|重构).{0,20}(?:项目|仓库|repo).{0,20}(?:bug|代码|问题|按钮|测试)",
            r"(?:删除|重命名).{0,24}(?:文件|\.\w+)",
            r"(?:edit|fix|modify).{0,20}(?:project|repo).{0,30}(?:code|bug|test)",
            r"(?:save|write|apply).{0,16}(?:to|into|patch).{0,20}(?:file|project|folder|\.\w+)",
        )
        if any(re.search(pattern, value) for pattern in explicit):
            return "write_workspace"
        if re.search(r"看看|看一下|分析|检查|优化建议|哪里有问题|inspect|review|analy[sz]e", value):
            return "preview_patch"
        return "virtual_only"

    @staticmethod
    def execution_mode(mode: CodingMode) -> str:
        return {
            "virtual_only": "draft",
            "preview_patch": "analyze_only",
            "write_workspace": "apply",
        }[mode]
