# Realtime Voice Benchmark v1

原 Evaluation 新增内置 `voice-core`，36 个固定版本用例，仍使用隔离 worker、原数据库、
Runtime、权限、Trace 和报告。面板 →「语音评测」直接选择本数据集；没有外部请求预算。
Mock / Sandbox 是合成边界测试，不等于真实语音成功率。当前原生设备项目在 worker 中
明确 BLOCKED，由独立 GUI / 本地引擎报告补充，不把它们静默记为通过。

| 范围 | 用例 |
|---|---|
| 001–006 | 权限拒绝、设备查询/切换、格式与重采样、断连、取消清理 |
| 007–012 | VAD 开始、终句候选、停顿、Partial/Final、中文、英文和混读 |
| 013–018 | 会话、角色、模型增量、分段、工具状态、旧回调隔离 |
| 019–024 | Provider、音频格式、顺序、边生成边播、背压、实际播放完成 |
| 025–030 | 手动/语音打断、旧队列、禁止重放、ASR 超时、TTS 回退 |
| 031–036 | 禁止上传、密钥/原音、工具权限、Workflow、Scheduler、Trace/Recovery |

## 分层证据

1. **Layer A**：合成正弦 PCM / 模拟 ASR，实际生产状态机、重采样、队列和原执行器。
2. **Layer B**：公共文字经真实本地系统 TTS 生成 WAV，再交真实本地 ASR。不是麦克风准确率。
3. **Layer C**：真实 Electron / AudioWorklet / 固定 IPC / 本地 ASR / 延迟本机模拟 LLM / 系统 TTS /
   原 Windows PCM 播放器，验证原生播放位置推进、打断、下一轮和 Trace。
4. **Layer D**：物理麦克风、声学回环、噪声、AEC、免提。未经实际测试不得标 PASS。
5. **Layer E**：外部 ASR/TTS/模型质量，需新的授权；本轮 NOT_RUN。

本轮通过 Layer C 的输入是 Chromium 的公共 WAV 测试设备；不是用户真实讲话。
另一受控 Web Audio MediaStream 测试仅验证 Worklet 之后的路径，不替代 getUserMedia 验收。
失败尝试、权限修复、源码/安装版报告分别保留，不覆盖旧失败记录。

## 本地复现（项目根目录 PowerShell）

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_realtime_voice_core.py backend/tests/test_realtime_voice_api.py backend/tests/test_realtime_voice_evaluation.py backend/tests/test_realtime_voice_safety.py backend/tests/test_voice_agent_orchestration.py -q
node tests/ui/realtime-voice-unit.cjs
.\.venv\Scripts\python.exe scripts/voice_native_probe.py --output .cache/voice-probe-new
```

probe 只接受本项目 `.cache` 内的新目录，不用私人音频、不读取真实模型配置。
会输出 5 个公共合成 WAV 及真实时间、原样识别结果和精确匹配结果，保留混读误识别。
不会开始录音、自动播放或调用云服务。安装 GUI 测试使用 `tests/ui/realtime-voice-e2e.cjs`：
设置 `COMPANION_TEST_EXE`、全新的 `COMPANION_TEST_HOME`、`COMPANION_VOICE_FIXTURE`；
源码测试另设 `COMPANION_TEST_SOURCE=1`。不得传入正式档案。测试会实际播放合成回复。

性能报告必须标输入设备、采样率、Provider、模型边界、实际播放、样本量、P50/P95/最大值。
ASR helper、LLM 首字、TTS 首 PCM、PCM 到播放器、结束输入到播放和打断分别计时。
P95 小样本只是观察值，不是生产 SLA；软件播放器位置不是声学回环。
系统语音 helper 按请求创建，常驻模型速度、显存、真实外部质量缺测时写 N/A。
