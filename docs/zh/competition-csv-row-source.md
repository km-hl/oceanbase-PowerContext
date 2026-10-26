# CSV 行级采集与原始记录回查

## 这一步做了什么

本实现为 PowerContext 增加 `powercontext.sources.csv_rows`，把一个离线 CSV 拆成可独立引用、持久化和回查的行级 Source。不训练模型，不调用网络或模型服务，也不增加项目依赖。

例如，CSV 中的一行：

```csv
id,name,code
001,重庆机场,000123
```

对应一个完整行记录：列名及列顺序不变，`001`、`000123` 仍然是字符串，而不是数字。其他内容即使包含逗号、中文、换行、双引号或空字符串，也沿用标准 CSV 语义保留。

## 数据如何流转

```text
CSVDocumentInput（整个文件）
  → parse_csv_document（完整解析与校验）
  → tuple[CSVRowInput, ...]（每行一项）
  → SourceDefinitionRegistry.resolve
  → CSVRowSource（CAPTURED 原始证据）
  → powercontext.text-evidence 标准投影
  → manifest / observation
  → Runtime ingestion（真实持久化）
  → Memory 检索结果
  → 精确 MemoryCitation 回查完整记录
```

### 解析与原子校验

采用 Python 标准库 `csv.reader(strict=True)`，以严格 UTF-8 解码，接受开头的 UTF-8 BOM。使用 `StringIO(newline="")`，避免破坏带引号字段内部的 CRLF。

表头必须有效且不重复，主键必须精确匹配表头；每行宽度必须正确，主键不能空白或重复。忽略表头后的完全空记录，但不会把包含一个空单元格的记录当作空行。所有校验成功后才返回 tuple；后面一行出错时，不返回前面已经解析出的部分记录。

### 稳定行身份

身份格式为：

```text
csv:{percent-encoded document_id}:{percent-encoded primary_key_value}
```

两个组件分别编码，避免冒号、斜杠和百分号导致身份碰撞；编码后的总长度不得超过项目的 Source ID 上限。

为什么不能用行号？同一行排序后会换位置，但“文档身份 + 主键值”不变。因此重新排序或修改非主键字段，不会把同一记录变成另一条记录。

主键中的有效空格保留。例如 ` key ` 和 `key` 是不同值，不擅自 trim。

### 原始 Source 与标准投影

`CSVRowSource` 保存完整的列和值，`read` 可以还原 `CSVRowInput`。标准 TextEvidence 投影将该行变成保留列顺序的紧凑 JSON，并且只提供题面规定的五项元数据。

Source Definition 复用项目已有的 adapter、projection 和 registry 机制，不另建持久化系统，也没有改动 Memory 或 Runtime 的实现。直接构造不一致的行输入时，resolve 会拒绝，不会悄悄截断字段或覆盖重复列。

## 修改位置

- `src/powercontext/sources/csv_rows.py`：题目实现。
- `tests/test_csv_rows_contract.py`：独立边界及数据保真测试。
- 本文：实现与复现说明。

官方 `competition/` 测试、固定依赖及评分逻辑未修改。未 commit、push 或提交官方评测。

## 已执行的验证

最终联合测试返回：

```text
75 passed in 21.75s
```

包含官方 CSV 公开验收的 17 项测试、新增边界测试以及现有 Source registry、Source observation、持久化和 processing registry 回归测试。JUnit 报告记录失败、错误、跳过均为 0。

官方公开验收实际覆盖了机场数据真实入库、Runtime 重启、持久化记录回读、Memory 检索及精确 citation 回查，并非用 mock 冒充数据库。

Ruff 检查返回 `All checks passed!`，两个新增 Python 文件已格式化。官方隐藏测试和平台正式评分未执行，因此不声称已经获得比赛分数。

## 如何复现

在 Windows 终端运行：

```bash
wsl -d Ubuntu -- bash /mnt/f/实验项目/OceanBase2026/powercontext-selftest.sh csv
```

此命令只运行官方 CSV 公开测试，预期为 17 项通过；联合回归报告包含的其他测试不在此命令范围内。

## 需要真正搞懂的知识

1. **CSV 格式不是按逗号 split。** 引号字段里也可能有逗号和换行。
2. **主键和行号不同。** 主键标识记录，行号只描述当前排列位置。
3. **保真不等于清洗。** 这一题要求保存原始证据，自动转数字或去空格反而会改变证据。
4. **Source 与投影各司其职。** Source 保留原记录；投影提供供检索或下游消费的标准表示。
5. **持久化必须验证重启后还能读。** 函数返回了正确对象，不代表数据已经保存并能被精确引用。
6. **公开测试通过不是正式得分。** 仍需遵守全部契约并接受平台补充验收。

预备知识：Python 函数、tuple/list/dict、异常、字符串与 bytes、UTF-8、基础 JSON、主键概念；阅读 Source adapter 时再了解 async/await 和 Pydantic 即可。
