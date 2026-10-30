# 检索质量检查

本检查覆盖证据选择与完整财报页面检索。公开功能测试只验证接口和部分行为，不能证明正式评测的召回门槛已满足。以下结果来自公开数据上的本地运行，不是平台成绩。

## 方法与数据

产品运行时代码只接收问题、候选原文及真实 Memory 数据，不加载标注、答案、固定页码或题目 ID。标注仅由 `scripts/evaluate_evidence_quality.py` 和 `scripts/evaluate_pdf_quality.py` 读取，用于外部计算指标。下载的语料、数据库与逐例输出放在忽略的 `.pytest_cache/retrieval-quality/`，不作为产品源码提交。

财报来自 [FinanceBench](https://github.com/patronus-ai/financebench/tree/cc39aeb4afdf33909ee1412188bf89035950c2eb)，固定 revision 为 `cc39aeb4afdf33909ee1412188bf89035950c2eb`。标注文件为该 revision 的 `data/financebench_open_source.jsonl`，SHA-256 为 `a5a2aa673e573e55675fc3c0f9aa38c1cf59d2abc91edb077534f71f10a71877`。财报使用同 revision 的完整 `pdfs/*.pdf`。数据集声明 CC BY-NC 4.0，公司财报保留各自权利。

证据检查使用 [SciFact](https://github.com/allenai/scifact) 的[公开数据发布包](https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz)。下载链接可能更新，因此同时记录内容摘要。`corpus.jsonl` 的 SHA-256 为 `b8d6c89624cb2ed74dee8938effc4f5d8bd2086887880af8110d64be4ceade62`；`claims_dev.jsonl` 为 `86f0435d08fdb65d1aa41d1472684f57e6e71930626497bdf4d7a9ec1a632217`；`claims_train.jsonl` 为 `f4c8fa82d8bd0653a9cc8d61a6ea48c25eacea64e90af5dbf390ebb1b74372f0`。claims 与证据采用 CC BY 4.0，论文摘要遵循 S2ORC 的 ODC-By 1.0。

证据检查筛选只有一个证据文档的 claim，以独立的原始词项 TF-IDF 从完整语料中选择两个干扰摘要，通过 claim 和文档身份摘要确定采集顺序。摘要必须满足 Memory 原生的 8,192 bytes 条目限制。`--native` 将三篇摘要采集到真实 Runtime，通过 `Memory.search(mode="fts", limit=16)` 获得实际候选，再交给选择器；输出使用真实 scope、entry、version 身份，并通过 `Memory.get` 回查。被搜索过滤的证据文档仍按未命中计分。无 `--native` 时仅为合成候选的 Builder 检查，不能代表整条路径。

预算为 1,400 或 1,800 bytes；外部检查确认全部标注证据及真实引用可以装入预算后才计入。词项召回使用 `[a-z]+|\d+` 和词频多重集合，完整覆盖按原字符区间计算，重叠及相邻的所选区间先合并。候选负例的构造与正式评测集合不同，不能把本检查数字直接等同于平台指标。

## 实测结果

运行日期为 2026-10-30。扩展财报和额外证据检查使用官方自测镜像：Linux x86_64、Python 3.12.14、pypdf 6.19.0，限制为 1 CPU、2 GiB，无外部网络。财报全部页面真实入库并关闭、重开 SQLite Runtime；检索结果通过公开 Memory API 回查正文及 Source。

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
| 开发集：真实 Memory 路径 | 306 | 0.7027 | 0.6046 |
| 额外检查：训练集跳过前 400 个单证据文档 claim，检查剩余 68 个；真实 Memory 路径 | 120 | 0.7787 | 0.7000 |

两项均在上述官方容器运行。证据文档进入搜索候选的请求比例分别为 0.9575、0.9667。开发集参与过算法选择，不属于未见数据评估；额外检查中的样本未用于本轮算法选择。正式评测仅 48 次请求，数据选择差异可能显著改变结果，须以平台实际验收为准。这里的质量数字不能定位平台未公开的失败项。

本地回归通过真实 Runtime 采集、搜索并回查中文及英文原文，覆盖纯中文问题、中文无空白超长句，以及存在较弱短句候选时仍从相关超长句选择窗口；同时核对完整 UTF-8 预算、原字符区间、scope 和 origins。

## 复现

将 SciFact 的三个 JSONL 文件放入 `.pytest_cache/retrieval-quality/scifact/`，FinanceBench 标注放入 `.pytest_cache/retrieval-quality/financebench.jsonl`；额外四份财报放入 `.pytest_cache/retrieval-quality/pdfs/`。PepsiCo 使用仓库随附完整 PDF。

```powershell
uv run python scripts/evaluate_evidence_quality.py --native --data-dir .pytest_cache/retrieval-quality/scifact --output .pytest_cache/retrieval-quality/evidence-dev.json
uv run python scripts/evaluate_evidence_quality.py --native --data-dir .pytest_cache/retrieval-quality/scifact --claims claims_train.jsonl --skip-claims 400 --output .pytest_cache/retrieval-quality/evidence-extra.json
uv run python scripts/evaluate_pdf_quality.py --pdf-dir competition/challenges/source-03-pdf-retrieval/workload/public/pdfs --pdf-dir .pytest_cache/retrieval-quality/pdfs --annotations .pytest_cache/retrieval-quality/financebench.jsonl --output .pytest_cache/retrieval-quality/pdf.json
uv run python scripts/run_competition_selftests.py --task evidence --task pdf
```

前三条命令在本机环境运行，需要安装 PDF 可选依赖；最后一条使用官方容器。两个质量脚本也可只读挂载进官方容器运行，结果目录须可写。JSON 输出包含数据摘要和逐例指标，便于核对环境及聚合结果。
