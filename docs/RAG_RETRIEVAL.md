# 关键词、向量与 RAG

默认关键词完全在本地 SQLite FTS5 运行。本机实测 FTS5/trigram 可用；产品索引使用 unicode61
加显式 CJK 单字/双字、英文/代码标识符分词，中文两字查询也有测试。
这不是中文语义模型；同义改写和复杂跨语言问题的召回仍有限。

查询先经过词法拆分及引号构造，FTS/SQL 参数绑定，不执行用户输入的 MATCH/SQL 运算符。
默认 top_k=5、上限 20；按已授权库、格式和相对路径筛选，最多 100 个候选。
检索结果保留 BM25、余弦、RRF 和词汇覆盖度的独立含义。
混合使用 RRF `sum(1/(60+rank))`，不直接相加 BM25 与 cosine。
按内容摘要去重、每文档最多三个片段，再进行确定性词汇覆盖/融合排名。

引用返回前重新核验来源。RAGContextBuilder 只提供最多 8,000 字符的证据，
相邻片段合并分组并删除完全相同的重叠，每部分仍保留自己的真实 chunk ID 和位置。
Chat 复用原 LLMService，流式事件顺序与语音流程保持原协议。
Agent/Workflow 原始工具观察留在本地结果，模型只收到单独构建的有界证据上下文。
当前 Workflow 的知识后续模型节点按后端祖先引用构建证据，不把任意转换后的长文本再次作为证据上传。

没有证据时返回「在当前授权知识库中没有找到可验证的资料，无法据此确认」，不调用模型补造。
有资料也不代表模型的全部回答已经事实核查；界面明确区分来源核验与回答质量。

## 可选 Embedding

抽象 `EmbeddingProvider`，当前实现用户配置的 OpenAI-compatible `/embeddings` endpoint：
DISABLED（默认）、LOCAL（只允许回环 HTTP/HTTPS）、REMOTE（HTTPS、另行原生确认）。
不安装模型、不增加大型依赖；本地权重由用户自行部署的服务持有。
远程密钥只从后端 `.env` 的 `KNOWLEDGE_EMBEDDING_API_KEY` 读取，不进 renderer。

配置包含地址、模型、修订号与维度（1–2,048）；指纹隔离不同模型/修订/维度。
更换配置清除旧向量并提示重建。每批最多八段，向量按规范化 JSON 保存在 SQLite，
每库最多 10,000 条，查询按批读取，不使用可执行 pickle。
网络、响应大小、维度、有限数值、零向量、失效片段均检查；失败明确回退关键词。
授权只在当前进程生效，重启需重新确认，不把旧授权自动应用到服务。
外部服务取消需等待当前有界请求退出；本次只实测关键词取消，没有测真实 Embedding 的取消延迟。

## 可选模型调研（未下载、未运行）

2026-10-18 阅读发布者资料后，后续可优先比较以下选项。实际 CPU 延迟、内存和项目召回须在用户授权后测量，
不能用模型卡分数替代本项目成绩；安装包不分发这些权重或推理代码。

| 候选 | 适用考虑 | 约束 |
|---|---|---|
| multilingual-e5-small | 多语言、384 维、MIT；适合先比较中英文资料 | 512 token；服务端需正确区分 query/passage 前缀，代码质量另测 |
| bge-small-zh-v1.5 | 中文为主、512 维、MIT | 英文/代码效果不可由中文指标推断 |
| jina-embeddings-v2-base-code | 英文与代码、161M 参数、Apache-2.0 | 资源成本更高；远程自定义代码须另行审计，不默认载入 |

来源：[E5 发布者模型卡](https://huggingface.co/intfloat/multilingual-e5-small/raw/main/README.md)、
[BGE 发布者模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5)、
[Jina 发布者模型卡](https://huggingface.co/jinaai/jina-embeddings-v2-base-code/raw/main/README.md)。
