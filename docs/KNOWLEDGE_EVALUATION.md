# Knowledge Evaluation 与性能

复用原 Evaluation Engine、受控自有 worker、历史/比较与 JSON/CSV/Markdown 导出。
新增 `knowledge-core`，28 项固定原创合成用例：

| 编号 | 实际断言 |
|---|---|
| 001–006 | Markdown、Python、PDF 页、DOCX 段落、损坏文档、超大文件 |
| 007–012 | 新增/修改/删除增量、取消、中断状态恢复、读取时变化 |
| 013–018 | 中文、英文、函数标识、跨文档、混合检索降级、重复内容组去重 |
| 019–024 | 文件引用、行/页引用、删除后旧引用、无证据/伪造标记、未授权目录、实际链接逃逸 |
| 025–028 | 来源注入不授权、手动记忆 CRUD/隔离、Knowledge→模型→输出、来源变化阻止 Workflow 恢复 |

界面位于工作模式 → 模块 → Agent Evaluation。Knowledge 模式区分 KeywordOnly、MockRAG、
HybridLocal、HybridRemote。前两者使用离线资料及固定模型；没有隔离 Embedding 模型时后两者明确阻断，
不继承用户实际模型、密钥、档案或远程传输权限。缺少 Embedding 不阻断 KeywordOnly。

指标：Recall@K（命中的相关来源组 / 标注相关来源组）、MRR（每个已标注查询首个正确结果的倒数排名均值）、
Citation Accuracy（成功重新核验 / 核验次数）、Citation Coverage（有真实引用的固定答案 / 需引用答案）、
Answer Groundedness（固定标注全部受支持 / 固定回答数）、P50/P95、索引吞吐、增量正确率、
越权实际读取数与记忆隔离率。完全相同的重复文档按一个相关内容组评分，任一真实来源均可命中。
拒绝一次越权尝试不是发生一次越权读取。

28 项首轮已通过；最终候选的原始结果保留在本地交付证据，避免把早期快照或动态耗时写成永久成绩。
MockRAG 固定答案不代表真实模型质量；本次未下载模型，也未调用真实聊天/语音/Embedding API。
Windows 安装版 GUI 是独立门槛，不由这 28 项推断。

## 最终安装版实测

2026-10-18，`0.2.0-knowledge.1` 使用实际 NSIS 安装保留的程序、新空白档案与本机固定模型。
知识库 GUI 30 项检查通过，原 Evaluation 冻结 worker 的 28 个用例全部 PASS，
JSON/CSV/Markdown 导出包含真实逐项记录。原生授权选择由仅自有测试进程的夹具控制，
不是一次人工点击验收。

Recall@K / MRR 均为 1.000，限 15 个固定标注查询；Citation Accuracy 为 1.000，
限 16 次实际来源核验。Coverage / Groundedness 为 1.000，但固定回答只有一个，
不能解释为真实模型全部回答正确。检索 P50 14.384 ms、P95 25.538 ms（17 次）。
真实语义模型质量未测量。

实际安装版索引 1,000 个新增合成文档时，renderer 在可见状态采样 10,003.5 ms，
得到 1,650 帧（164.94 FPS），帧间隔 P95 6.10 ms、最大 19.00 ms。
这是该电脑该窗口的一次帧采样，不代表所有硬件、复杂工作台或输入延迟；
下方源码性能的后台心跳与这项实际帧采样分别记录。

## 可复现离线性能入口

```powershell
.\.venv\Scripts\python.exe -B scripts\benchmark-knowledge.py --directory .cache\knowledge-perf-new
```

要求一个尚不存在的目录，自动创建自己的空白档案和原创文档，不读取用户资料。
2026-10-18 最终候选源码在当前电脑实测（关键词，冷建索引；每组 30 次检索）：

| corpus | 文档 / 片段 | 索引秒 | 检索 P50 / P95 ms | 采样峰值 RSS |
|---|---:|---:|---:|---:|
| 100 documents | 100 / 100 | 4.366 | 8.930 / 10.827 | 75.32 MiB |
| 1000 documents | 1000 / 1000 | 41.454 | 9.006 / 9.829 | 77.42 MiB |
| 10000 chunks | 100 / 10000 | 5.923 | 8.452 / 9.290 | 78.77 MiB |

最后一次实际取消约 0.655 秒，状态 CANCELLED。数据库累计（包含前三组）约 17.10 MiB。
后台心跳延迟不是界面 FPS；RSS 不含独立 PDF/DOCX 子进程的峰值。
没有测量真实本地模型、远程模型、真实语义质量或 Embedding 取消延迟。
