# 语音 Provider 的实际能力

当前本地路径使用 Windows 已安装的 `.NET Framework System.Speech`；
验证构建使用 Python 3.13.17 和锁定的 `audioop-lts` 执行实际重采样。
Python 3.13 不再自带 audioop，必须安装项目锁定依赖，不能只更换解释器。
ASR/TTS 不共用其他产品的环境、模型或声音素材。

| 路径 | 真实接口能力 | 网络 / 配置 | 验收边界 |
|---|---|---|---|
| Windows System.Speech ASR | 终句后的 Batch dictation；无 Partial、无原生 streaming | 本地；需要已安装对应语言识别器 | 公共合成 WAV 的中文、英文实测；混读存在误识别，必须校对 |
| Windows System.Speech TTS | 系统音色分段合成；16k mono PCM16；支持取消 | 本地、无需密钥 | 真实系统合成和实际 Windows PCM 输出；无角色克隆、情绪或 reference 能力 |
| 已配置 Fish Audio | 保留原 HTTP / WebSocket 和方向提示；本轮快照配置 | 需用户自己的密钥和声音 ID；另需回复文字传输允许 | 本轮没有外部调用授权，不计为真实 Fish 验收 |
| 已配置 IndexTTS | 原本地 worker；以语义段为单位合成，不称为原生 streaming | 需用户自己的环境、模型和授权参考音频 | 本轮无权重或私人素材，使用模拟 worker 验证协议、顺序与取消；真实模型 NOT_RUN |
| 远程 ASR | 未配置，不能发送录音 | 没有远程音频授权入口 | NOT_CONFIGURED |

Fish 的可用性、模型选择和计费沿用原设置，不自动开通账号、付费模型或 API 测试。
系统语音模式不会因之后更改全局引擎而切到 Fish。已配置引擎发生变化时需要重开语音会话。
角色 ID 与轮次固定，但系统音色只能选本机提供的声音；不能假称任意角色音色已加载。

TTS Router 保留原配置的回退政策：只有尚未交付任何 PCM 的允许故障可以退到本地 Index。
开始播放后不切换引擎、不从头重放。回退仍受本轮取消和原语音信号量约束。
语速内部协议限制在 0.9–1.1；当前面板默认 1.0，沿用主界面的播放音量。
显式选择输出设备需要浏览器 setSinkId；默认输出复用 Windows 连续 PCM 播放器。

不能将「文字按段到达」「WAV 已生成」当成原生 streaming、麦克风准确率或扬声器可听性。
小样本软件延迟不是声学端到端延迟；真实麦克风、噪声、回声、多人环境需单独验收。
