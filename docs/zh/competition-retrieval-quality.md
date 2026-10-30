# 检索质量检查

本检查覆盖证据选择与完整财报页面检索。公开功能测试只验证接口和部分行为，不能证明正式评测的召回门槛已满足。以下结果来自公开数据上的本地运行，不是平台成绩。

## 方法与数据

产品运行时代码只接收问题、候选原文及真实 Memory 数据，不加载标注、答案、固定页码或题目 ID。标注仅由 `scripts/evaluate_evidence_quality.py` 和 `scripts/evaluate_pdf_quality.py` 读取，用于外部计算指标。下载的语料、数据库与逐例输出放在忽略的 `.pytest_cache/retrieval-quality/`，不作为产品源码提交。

财报来自 [FinanceBench](https://github.com/patronus-ai/financebench/tree/cc39aeb4afdf33909ee1412188bf89035950c2eb)，固定 revision 为 `cc39aeb4afdf33909ee1412188bf89035950c2eb`。标注文件为该 revision 的 `data/financebench_open_source.jsonl`，SHA-256 为 `a5a2aa673e573e55675fc3c0f9aa38c1cf59d2abc91edb077534f71f10a71877`。财报使用同 revision 的完整 `pdfs/*.pdf`。数据集声明 CC BY-NC 4.0，公司财报保留各自权利。

证据检查使用 [SciFact](https://github.com/allenai/scifact) 的[公开数据发布包](https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz)。下载链接可能更新，因此同时记录内容摘要。`corpus.jsonl` 的 SHA-256 为 `b8d6c89624cb2ed74dee8938effc4f5d8bd2086887880af8110d64be4ceade62`；`claims_dev.jsonl` 为 `86f0435d08fdb65d1aa41d1472684f57e6e71930626497bdf4d7a9ec1a632217`；`claims_train.jsonl` 为 `f4c8fa82d8bd0653a9cc8d61a6ea48c25eacea64e90af5dbf390ebb1b74372f0`。claims 与证据采用 CC BY 4.0，论文摘要遵循 S2ORC 的 ODC-By 1.0。

证据检查筛选只有一个证据文档的 claim，以独立的原始词项 TF-IDF 从完整语料中选择两个干扰摘要，通过 claim 和文档身份摘要确定采集顺序。摘要必须满足 Memory 原生的 8,192 bytes 条目限制。`--native --all-candidates` 将三篇摘要采集到真实 Runtime，再通过公开 `Memory.list()` 将全部三篇正文和真实引用提供给选择器，确保每次输入恰为三篇；输出通过 `Memory.get` 回查。

单独使用 `--native` 时，输入取自 `Memory.search(mode="fts", limit=16)` 的实际结果，可能少于三篇，被搜索过滤的证据文档按未命中计分。此模式用于检索至选择的整条路径诊断，不把 FTS 的候选漏召归因于选择器。无 `--native` 时仅为合成候选的 Builder 检查。三种模式均不会向产品提供标注。

预算为 1,400 或 1,800 bytes；外部检查确认全部标注证据及真实引用可以装入预算后才计入。词项召回使用 `[a-z]+|\d+` 和词频多重集合，完整覆盖按原字符区间计算，重叠及相邻的所选区间先合并。候选负例的构造与正式评测集合不同，不能把本检查数字直接等同于平台指标。

## 实测结果

财报检查运行于 2026-10-30，以下证据检查运行于 2026-10-31。环境使用官方自测镜像：Linux x86_64、Python 3.12.14、pypdf 6.19.0，限制为 1 CPU、2 GiB，无外部网络。财报全部页面真实入库并关闭、重开 SQLite Runtime；检索结果通过公开 Memory API 回查正文及 Source。

| 完整财报 | 物理页 | 问题 | 证据页 Recall@20 |
| --- | ---: | ---: | ---: |
| 3M 2022 10-K | 252 | 3 | 0.8000 |
| AMD 2022 10-K | 121 | 7 | 0.8571 |
| American Express 2022 10-K | 260 | 7 | 0.7143 |
| Boeing 2022 10-K | 190 | 7 | 0.7778 |
| PepsiCo 2022 10-K | 503 | 5 | 1.0000 |
| 合计 | 1,326 | 29 | 0.8333（30/36） |

所有标注证据中的最低词项召回率为 0.9825，最低标注整页 F1 为 0.9748。平台冻结的全页词项计数未随公开包提供，本脚本不宣称验证了该项。五份财报不等同于正式评测的 1,151 页、24 个问题集合。

一次顺序运行中，首个查询约 2.42 秒，后续查询的中位数约 1.11 秒；这是上述语料与资源配置下的观测值，不是延迟保证。首次查询包括全量 Memory 读取及派生索引构建。

| SciFact 检查 | 可行请求 | 平均证据词项召回 | 完整覆盖比例 |
| --- | ---: | ---: | ---: |
| 开发集：真实入库、完整三篇候选 | 306 | 0.7221 | 0.6209 |
| 训练集额外检查：真实入库、完整三篇候选 | 796 | 0.7355 | 0.6030 |

两项均在上述官方容器运行，每次输入恰为三篇，证据文档均在输入候选中。开发集参与过算法选择；两者均为公开数据检查，不是独立正式评测。

为了避免总体平均值掩盖波动，脚本从两个预算均可行的 claim 中，按原始顺序每 24 个形成固定分组，每组 48 次请求；不足 24 个的尾组单独计数，不参与完整分组统计。结果如下：

| 数据划分 | 完整分组数 | 同时达到 0.65 召回与 0.40 完整覆盖的分组 | 最低组召回 | 最低组完整覆盖 |
| --- | ---: | ---: | ---: | ---: |
| 开发集 | 5 | 4 | 0.5997 | 0.5000 |
| 训练集额外检查 | 14 | 10 | 0.5029 | 0.3542 |

**公开功能验收通过，扩展分组质量尚未全部达标。** 正式评测也仅有 48 次请求，不能用总体平均值推定平台通过。构造的分组与干扰摘要不等同于正式集合，这些数字也不能定位平台未公开的具体失败项。完整 JSON 输出保留每组及逐例指标。

另以 FTS 结果子集检查完整路径，开发集 306 次请求的召回和完整覆盖分别为 0.7134、0.6176，训练集 796 次请求分别为 0.7328、0.6043；证据文档进入输入候选的比例分别为 0.9575、0.9648。

本地回归通过真实 Runtime 采集、搜索并回查中文及英文原文，覆盖纯中文问题、中文无空白超长句，以及存在较弱短句候选时仍从相关超长句选择窗口；同时核对完整 UTF-8 预算、原字符区间、scope 和 origins。

准备模式的公共接口回归还覆盖缺失或不匹配的 Memory revision、空引用、版本去重、相同正文的 scope 隔离、数字标识符写法、共享数字的多个标识符，以及同一变量的支持与反驳原文。相关回归及 API 契约检查共 147 项通过，官方赛题十公开验收 3 项通过；这些数量不包含正式评测。

## 复现

将 SciFact 的三个 JSONL 文件放入 `.pytest_cache/retrieval-quality/scifact/`，FinanceBench 标注放入 `.pytest_cache/retrieval-quality/financebench.jsonl`；额外四份财报放入 `.pytest_cache/retrieval-quality/pdfs/`。PepsiCo 使用仓库随附完整 PDF。

```powershell
uv run python scripts/evaluate_evidence_quality.py --native --all-candidates --data-dir .pytest_cache/retrieval-quality/scifact --output .pytest_cache/retrieval-quality/evidence-dev.json
uv run python scripts/evaluate_evidence_quality.py --native --all-candidates --data-dir .pytest_cache/retrieval-quality/scifact --claims claims_train.jsonl --output .pytest_cache/retrieval-quality/evidence-extra.json
uv run python scripts/evaluate_pdf_quality.py --pdf-dir competition/challenges/source-03-pdf-retrieval/workload/public/pdfs --pdf-dir .pytest_cache/retrieval-quality/pdfs --annotations .pytest_cache/retrieval-quality/financebench.jsonl --output .pytest_cache/retrieval-quality/pdf.json
uv run python scripts/run_competition_selftests.py --task evidence --task pdf
```

前三条命令在本机环境运行，需要安装 PDF 可选依赖；最后一条使用官方容器。两个质量脚本也可只读挂载进官方容器运行，结果目录须可写。JSON 输出包含数据摘要和逐例指标，便于核对环境及聚合结果。
