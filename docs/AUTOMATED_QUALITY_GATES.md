# Automated QA Gates

统一入口 `scripts/review-visual-computer-use.ps1` 复用原 check/build/installer/audit；不替代已有发布系统。所有证据保存在本机隔离目录，不提交运行日志、截图或用户档案。

| Gate | 实际执行内容 |
| --- | --- |
| A Source Audit | Ruff/格式/源码与可达历史审计、依赖和授权路径复审 |
| B Unit & Integration | 原完整 pytest、TS、ESLint、Prettier、Electron、Runtime/MCP/Skills/Workflow/Knowledge 回归 |
| C Security | 窗口/会话归属、取消、过期、隐私、未知副作用、Vision Schema/Mock 及传输确认检查 |
| D GUI | 自有 Windows Fixture，真实产品 UI/IPC/Runtime/worker，24 项原生 Evaluation 与 Trace |
| E Performance | GUI 测量中 UIA/捕获/OCR/验证用时、主进程与 Fixture 内存、rAF 间隔 |
| F Installer | 新 BuildId 完整构建、DryRun、隔离安装、安装版 GUI、卸载与注册/快捷方式恢复 |
| G Consistency | Git 与 manifest、逐文件摘要、ASAR/frozen 后端、安全审计与正式档案摘要保护 |

每次命令写入 gate_id、status、command、started_at、completed_at、exit_code、evidence_path、findings。汇总读取实际报告，不凭文件存在或文案声称 PASS；FAIL、BLOCKED、NOT_RUN 都保留。

完整执行必须明确提供要保护的本产品正式档案，入口在运行耗时检查前验证参数：

```powershell
.\scripts\review-visual-computer-use.ps1 -Phase all -ProtectedProfile '<本产品正式目录>\UserData'
```

每条证据绑定干净的 Git 修订、源码目录、版本、BuildId、正式档案规范路径；安装相关检查还绑定 manifest 与真实发行文件摘要。旧修订、旧构建的结果保留为历史，不计入当前门。每次尝试在启动前保存 NOT_RUN，失败或中断覆盖该标签的旧 PASS；只有所有必需项均为 PASS 才通过。禁止以优化模式 Python 执行审核，校验不依赖可被优化删除的 assert。

档案保护只计算文件摘要，基线摘要与规范路径一同绑定，不能切换到内容相同的另一目录。`source-gui` 使用本仓库锁定 Electron；安装测试先运行真实安装后的程序，随后从卸载前保留、核对发行核心摘要的安装副本执行 Computer Use 及 Skill 01–06 GUI 回归。安装阶段不会回退到源码 Electron。

独立 QA 复审报告同时记录被审查入口、证据聚合、校验器及边界测试的源码摘要；最终门会重新核对，修改这些实现后必须更新复审证据。

安装与保留副本必须与发行目录逐文件核对，包含依赖的无扩展名资源及许可证；不能只核对 EXE / ASAR。构建清单包含完整程序摘要。Windows PowerShell 5 的深路径可能导致不完整枚举，测试采用短目录名，并在修改安装信息前拒绝过深的测试路径。单独安装和保留副本分别保存资源对照结果。MCP Schema 元数据、参数与输出只使用本地 Registry，缺资源立即拒绝，禁止隐式远程补取。

默认执行不使用付费模型，不发送私人图片，不写正式聊天库，不改变 DPI 或系统安全设置。安装测试会按原隔离脚本备份/恢复必要安装注册信息，只操作自有测试进程。代码签名、缺少的物理显示条件和外部视觉模型不能被模拟测试替代。

`READY_FOR_RELEASE_REVIEW` 只是适用门通过的受限候选，不代表获得公开发布许可；关键安全或安装问题则 BLOCKED，缺少尚可执行证据则 INCOMPLETE。独立 review 与复测的结论一同保存。默认不推送、打标签、合并 main 或发布 Release。
