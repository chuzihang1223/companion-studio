# Codex 进度

## 当前断点（2026-10-27，最终冲刺已完成可执行工程验收）

- Skill：Final Production Release Sprint，承接 Final Production Release。
- 版本仍为 **0.3.0-rc.4**；目标 v0.3.0 未获正式发布批准。
- 分支：`release/0.3.0-final-hardening`；仅本地提交，无推送、Tag、Release 或资产上传。
- 安装包及冻结源码提交：`8d173c0008fcd8ddc58d10b734c2edb53110b21d`。本文件后续提交仅保存交付进度，不冒充该提交重新完成测试。
- **发行状态 BLOCKED**：必需独立 Security 审查未执行，原 code/runtime/security/qa 四域最终合同未闭环。当前工程范围报告不能替代原合同。
- 首先读取本机 `.cache/final-release-sprint-01/candidate-status.json`、`delivery-verification.json` 和候选的 `Reports/FINAL_RELEASE_READINESS.md`，不要重新扫描架构、跑全套或重建安装包。

## 本次关键修改与验证

- `backend/tests/test_trace_persistence.py`：live runtime fixture 原为 DELETE，和正常产品启动的 WAL 不一致。仅增加 WAL 并断言实际返回模式，保留原默认 5000ms 超时，不套用会提高超时的生产 helper。
- `backend/tests/test_agent_trace.py`：保留原四种 outcome ID 和原 5 秒 flush 断言；新增 `cancel_with_reader`，使用真实 BEGIN/read snapshot，在取消和持久化完成后仍保持读快照。未放宽 2 秒、16 次重试或性能门限。
- 旧 fixture 的真实读快照反例在 7.41 秒于实际 commit 失败，错误为 `sqlite3.OperationalError: database is locked`。没有记录数值 SQLite 扩展错误码，不能据此称 SQLITE_LOCKED 枚举。修正后 80 项定向测试通过。
- 当前源码原完整检查 **848 项：846 PASS、2 SKIP、0 FAIL**；包括前端/Electron检查、源码/历史和依赖审计。
- 原构建自身再执行完整回归：**846 PASS、2 SKIP、0 FAIL，1066.77 秒**，无观察插件、未使用 SkipTests。两次取消及真实读快照用例均通过。SKIP 均为 Windows 当前账号不能创建符号链接。
- 当前依赖一致性、许可、SBOM、通告检查及发行资源白名单审计通过；不等同于独立 Security 签署。

## 真实长时证据及限制

- 原一小时实际执行提交仍为 `0ce5cda33348ef4949c7e831584e2b2653d14d2d`，没有改写成 8d 的新执行。
- 真实混合负载 **3600.0011043 秒**，另有 **61.7007484 秒/13 次**空闲采样；360 轮、60 个 MCP 子进程、12 次调度、60 次取消。
- 21774 个事件生成＝接受＝提交；pending、失写、丢弃、BUSY、遗留子进程均为 0；13 项 live counter 在规定静止/空闲/退出边界归零，SQLite 事务 P95 14.032ms。
- 311 个 soak 执行范围、312 个生命周期范围、569 个 GUI 范围文件，以及相关依赖声明和原报告 SHA 与当前一致。改动的两个测试未被原小时 workload 导入，产品运行时代码未改；依据用户允许的未变范围复用，未缩短或伪造一小时。
- 三次服务启停和退出后 7 次采样通过。唯一摘要匹配的闭合**合成**数据库只读核对 integrity=ok、账本/回执/留存一致，前后文件字节未变。
- 负载 private memory 末 30 分钟约 0.3659 MiB/min、末 10 分钟约 0.4464 MiB/min；空闲段斜率 0。余量仍是 **UNATTRIBUTED_GROWTH**，不是任意长期零泄漏证明。
- 旧历史取消失败的唯一原因尚未证明。本次第一次完整检查也曾有 enqueue 3604.5792ms 超原 3000ms 门限；后续源码和构建全套通过不抹除旧失败或证明所有 I/O 抖动消失。checkpoint128 的受控试验更差，已拒绝，产品仍为512。

## 安装与交付

- Build：`studio-0.3.0-rc.4-sprint01`，manifest/source/ASAR/冻结模块绑定当前 8d。
- 原脚本实际 DryRun、隔离安装、28 项界面检查、现代记事本实际启动并就绪聚焦、退出/重开/卸载通过；原同产品注册与快捷方式恢复。1324 个程序资源逐文件匹配。
- 已安装快照的原生 GUI：21 PASS、3 个 DPI/显示器硬件 BLOCKED；性能通过；十个功能模块均通过，共260项模块断言。源码 GUI 沿用相同字节范围的原执行身份。
- 原120功能映射：**105 PASS、15 PARTIAL**，15项均保留原环境/能力限制；16项INT、10项E2E通过。合成模型/ASR/TTS不冒充真实云模型、物理麦克风或声学质量。
- 正式 UserData 只作文件摘要比较：**96→96，变化0**；没有读取SQL记录、复制、导入或迁移正式数据库。仅本候选路径范围的最终CIM观测无遗留应用/后端进程；用户记事本和其他版本不在清理范围。
- 当前 Setup 的 Windows Defender 扫描无威胁；签名 **NotSigned**，按 Skill 披露，不冒充已签名。
- 993 个公开源码文件/ZIP与冻结源逐字节相等。候选含安装器、同版目录程序、源码树/ZIP、九份必需报告及校验值；本机原始日志、私有档案、截图、密钥和测试音频不进入公开发行材料。
- 本机位置见 `candidate-status.json`；Setup SHA256：`83c633c6fed02aa1c753703225df5630cc633bb2a7fd1478d63171b6a7a13425`。
- Code功能正确性、Runtime、Database、QA分别完成范围限定的独立工程复审，无确认的未解决 P0/P1/P2；报告及 SHA 在候选中，脱敏副本与本机原件摘要分别记录。**不构成Security或整体发布签署**。
- 现用正式版本和个人版没有替换、合并档案或修改。

## 必需审核阻断与下一步

1. 原 `Independent security reviewer continuation` 被自动审批以“可能存在网络安全风险”拒绝，未给具体规则。原 pip-audit 安装操作也被拒绝。原始记录在 `.cache/final-hardening-01/security-independent-block.json` 和 `policy-blocks.json`。
2. 不重试、改名、拆分或换入口绕过这些拒绝；普通只读审计和工程复审不替代该签署。**GATE-07/GATE-13/最终签署保持 BLOCKED，不公开 GitHub。**
3. 只有出现获准的实际 Security 审查条件后，补齐原四域合同所需的实际审查与当前源码/构建绑定，再执行原最终验收合同。不能把本机范围报告或旧 inventory 清单自动写成四域 PASS。
4. 不要盲重跑当前 frozen runner：它和发行件绑定 8d，后续本进度提交只是记录。先读本机状态与候选最终报告；若有真实产品/依赖变更，重新判断受影响检查和真实一小时要求，不能套用旧结果。

## 证据入口与保留的失败

- `.cache/final-release-sprint-01/SPRINT_STATE.json`：本轮续接索引。
- `review02/source-check/pytest.xml`、`build-pytest.xml`：两份完整回归。
- `FINAL_ENGINEERING_EVIDENCE.json`：精确120功能/INT/E2E/GUI/原始门与未变范围复用，原NOT_RUN未改成PASS。
- `unchanged-scope-reuse.json`、`stress-analysis.json`：原一小时和生命周期复用、关闭后合成数据库与内存统计。
- `phase-installer.json`、`phase-installed-gui.json`、`phase-consistency.json`、`phase-supplemental.json`：实际阶段收据。
- `runtime-final-review`、`database-final-review`、`qa-final-review`：独立工程报告、原始SHA与输入快照。
- `failed-full-check-01`：本轮3604ms性能失败。此前 `.cache/final-production-01/failed-full-check-06` 等原日志未删。
- 首次构建参数因带空格的JUnit路径被拆开，零测试即停止；原XML与日志保留，改为相对路径后才完成真正构建回归。
- 最终一致性门曾在安装前因缺少installed snapshot正确拒绝；`10-manifest-profile.log` FAIL保留，安装后原门 `26-manifest-profile.log` PASS。没有绕过检查。
- 本机源码导出首次误指定Source内缓存被原guard拒绝，随后使用Source外独立目录；汇总脚本的外部路径根、PowerShell引号/编码及URL误判已纠正，只影响本机交付辅助，不涉及产品或测试门。
