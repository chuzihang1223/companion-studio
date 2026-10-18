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

## 可复现离线性能入口

```powershell
.\.venv\Scripts\python.exe -B scripts\benchmark-knowledge.py --directory .cache\knowledge-perf-new
```

要求一个尚不存在的目录，自动创建自己的空白档案和原创文档，不读取用户资料。
当前电脑首轮实测（关键词，冷建索引；每组 30 次检索）：

| corpus | 文档 / 片段 | 索引秒 | 检索 P50 / P95 ms | 采样峰值 RSS |
|---|---:|---:|---:|---:|
| 100 documents | 100 / 100 | 4.080 | 8.569 / 10.320 | 74.72 MiB |
| 1000 documents | 1000 / 1000 | 40.553 | 8.651 / 10.086 | 77.20 MiB |
| 10000 chunks | 100 / 10000 | 6.035 | 9.158 / 10.612 | 79.45 MiB |

最后一次实际取消约 0.081 秒，状态 CANCELLED。数据库累计（包含前三组）约 16.54 MiB。
后台心跳延迟不是界面 FPS；RSS 不含独立 PDF/DOCX 子进程的峰值。
没有测量真实本地模型、远程模型、真实语义质量或 Embedding 取消延迟。
