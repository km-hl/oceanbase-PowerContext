import "./style.css";
import { chunkText } from "./chunk.js";

const MODEL_ID = "Xenova/paraphrase-multilingual-MiniLM-L12-v2";
const $ = (selector) => document.querySelector(selector);
const worker = new Worker(new URL("./embedder.worker.js", import.meta.url), { type: "module" });
const outstanding = new Map();
const benchmarkHistory = [];
let requestId = 0;
let busy = false;

function setStatus(message, isError = false) {
  $("#status").textContent = message;
  $("#status").classList.toggle("error", isError);
}

function element(tag, className, content) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (content !== undefined) el.textContent = content;
  return el;
}

async function api(path, options = {}) {
  const response = await fetch("/api" + path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail || payload);
    throw new Error("HTTP " + response.status + ": " + message);
  }
  return payload;
}

worker.onmessage = ({ data }) => {
  const entry = outstanding.get(data.id);
  if (!entry) return;
  if (data.type === "progress") {
    setStatus("下载 ONNX 模型中：" + data.progress + "%（文件缓存在本地浏览器）");
    return;
  }
  outstanding.delete(data.id);
  if (data.type === "error") entry.reject(new Error(data.message));
  else {
    $("#inference-state").textContent = data.device.toUpperCase() + " / " + data.dtype.toUpperCase();
    entry.resolve(data);
  }
};

function embed(texts) {
  return new Promise((resolve, reject) => {
    const id = ++requestId;
    outstanding.set(id, { resolve, reject });
    worker.postMessage({ id, texts, device: $("#backend").value, dtype: $("#dtype").value });
  });
}

function setBusy(state) {
  busy = state;
  ["#load-button", "#benchmark-button", "#sample-button", "#search-button", "#file-input"].forEach((sel) => {
    $(sel).disabled = state;
  });
}

async function refresh() {
  try {
    const [health, result] = await Promise.all([api("/health"), api("/documents")]);
    $("#engine-state").textContent = health.engine;
    $("#document-count").textContent = health.chunks;
    const root = $("#documents");
    root.replaceChildren();
    if (!result.documents.length) {
      root.append(element("span", "empty", "当前知识库为空。可导入示例语料快速体验。"));
      return;
    }
    for (const doc of result.documents) {
      const item = element("div", "document");
      const left = element("div", "document-info");
      left.append(element("strong", "", doc.title), element("small", "", doc.chunks + " 个文本块"));
      const remove = element("button", "link-button danger", "删除");
      remove.type = "button";
      remove.addEventListener("click", async () => {
        if (busy || !confirm("确定删除《" + doc.title + "》的全部文本块？")) return;
        try {
          await api("/documents/" + encodeURIComponent(doc.source_id), { method: "DELETE" });
          await refresh();
          setStatus("已删除《" + doc.title + "》");
        } catch (error) {
          setStatus(error.message, true);
        }
      });
      item.append(left, remove);
      root.append(item);
    }
  } catch (error) {
    $("#engine-state").textContent = "不可用";
    $("#documents").textContent = "连接 seekdb 失败：" + error.message;
    setStatus(error.message, true);
  }
}

async function importText(name, contents) {
  const chunks = chunkText(contents);
  if (!chunks.length) throw new Error("文档为空：" + name);
  const sourceId = crypto.randomUUID();
  let inferenceMs = 0;
  let stored = 0;
  for (let i = 0; i < chunks.length; i += 6) {
    const texts = chunks.slice(i, i + 6);
    $("#import-progress").textContent = "端侧向量化 " + Math.min(i + 6, chunks.length) + "/" + chunks.length;
    const result = await embed(texts);
    inferenceMs += result.inferenceMs;
    const records = texts.map((content, index) => ({
      id: crypto.randomUUID(), source_id: sourceId, title: name, content,
      model_id: MODEL_ID, vector: result.vectors[index],
    }));
    const upload = await api("/ingest", { method: "POST", body: JSON.stringify({ chunks: records }) });
    stored += upload.stored;
    $("#import-progress").textContent = "seekdb 已入库 " + stored + "/" + chunks.length;
  }
  setStatus("《" + name + "》导入成功：" + stored + " 块；纯端侧推理 " + inferenceMs.toFixed(1) + " ms");
  await refresh();
}

async function runImport(files) {
  if (busy) return;
  setBusy(true);
  try {
    for (const file of files) {
      if (file.size > 2 * 1024 * 1024) throw new Error("文件超过 2 MiB：" + file.name);
      const contents = await file.text();
      await importText(file.name, contents);
    }
  } catch (error) {
    setStatus("导入失败：" + error.message, true);
  } finally {
    setBusy(false);
    $("#file-input").value = "";
  }
}

$("#file-input").addEventListener("change", (event) => runImport([...event.target.files]));

$("#sample-button").addEventListener("click", () => {
  const sample = [
    "向量检索把文本转成语义向量，通过距离近似度寻找不同措辞但含义相似的内容。",
    "全文检索使用倒排索引，擅长搜索准确关键词、专业术语和特定名称。",
    "混合检索把向量通道和全文通道的结果使用 Reciprocal Rank Fusion 合并排序。",
    "WebGPU 把 ONNX 推理放在 GPU 上运行，WASM 是通用的 CPU 回退方案。",
    "模型量化使用更低位宽减少加载体积和内存占用，例如 INT8 相比 FP32 模型通常更小。",
    "OceanBase seekdb 支持 VECTOR 向量字段、HNSW 索引和 FULLTEXT 全文索引。",
  ].join("\n\n");
  runImport([{ name: "StepFun 示例知识.md", size: new Blob([sample]).size, text: async () => sample }]);
});

$("#refresh-button").addEventListener("click", refresh);

$("#load-button").addEventListener("click", async () => {
  if (busy) return;
  setBusy(true);
  try {
    const result = await embed(["端侧推理模型预热"]);
    setStatus("模型已预热：" + result.device + "/" + result.dtype +
      "；初始化 " + result.setupMs.toFixed(1) + " ms，推理 " + result.inferenceMs.toFixed(1) + " ms");
  } catch (error) {
    setStatus("模型加载失败：" + error.message, true);
  } finally {
    setBusy(false);
  }
});

$("#benchmark-button").addEventListener("click", async () => {
  if (busy) return;
  setBusy(true);
  try {
    const sentences = [
      "如何使用 seekdb 混合检索个人知识库？",
      "WebGPU 浏览器端机器学习性能测试。",
      "Vector embeddings are computed on the client.",
      "量化模型减少传输体积与内存使用。",
    ];
    await embed(sentences);
    const readings = [];
    for (let iteration = 0; iteration < 3; iteration++) {
      const result = await embed(sentences);
      readings.push(result.inferenceMs);
    }
    readings.sort((a, b) => a - b);
    const medianMs = readings[1];
    const item = {
      config: $("#backend").value + "/" + $("#dtype").value,
      medianMs,
      docsPerSecond: Math.round(sentences.length / (medianMs / 1000)),
    };
    const old = benchmarkHistory.findIndex((entry) => entry.config === item.config);
    if (old !== -1) benchmarkHistory.splice(old, 1);
    benchmarkHistory.unshift(item);
    const root = $("#benchmarks");
    root.replaceChildren();
    for (const row of benchmarkHistory) {
      root.append(element("div", "bench-row", row.config + "　P50 " +
        row.medianMs.toFixed(1) + " ms / 4 条　≈ " + row.docsPerSecond + " 条/s"));
    }
    setStatus("基准完成：4 段短文本，预热后重复 3 次取中位数；模型未重新下载。");
  } catch (error) {
    setStatus("基准失败：" + error.message, true);
  } finally {
    setBusy(false);
  }
});

$("#search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  setBusy(true);
  const query = $("#query").value.trim();
  const mode = $("#search-mode").value;
  try {
    const started = performance.now();
    const vector = mode === "text" ? null : (await embed([query])).vectors[0];
    const result = await api("/search", {
      method: "POST",
      body: JSON.stringify({ query, vector, mode, top_k: 8, model_id: MODEL_ID }),
    });
    const totalMs = performance.now() - started;
    const root = $("#results");
    root.replaceChildren();
    if (!result.results.length) {
      root.append(element("div", "placeholder", "没有找到匹配内容，可以换一个查询或导入更多文档。"));
    }
    for (const hit of result.results) {
      const card = element("article", "result");
      const heading = element("div", "result-heading");
      heading.append(
        element("h3", "", hit.title),
        element("span", "result-score", "RRF " + hit.score.toFixed(4)),
      );
      card.append(
        heading,
        element("p", "", hit.content),
        element("div", "result-foot", "命中通道：" + hit.matched_by.join(" + ") + " · 文本块 " + hit.id.slice(0, 8)),
      );
      root.append(card);
    }
    $("#search-stats").textContent = result.results.length + " 条 · " +
      totalMs.toFixed(0) + " ms 端到端 · seekdb " + result.search_ms + " ms · " +
      "Vector " + result.candidate_counts.vector + " / FTS " + result.candidate_counts.fts;
    setStatus("检索完成：实际结果由 seekdb SQL 查询返回，模型推理仅在本地运行。");
  } catch (error) {
    setStatus("检索失败：" + error.message, true);
  } finally {
    setBusy(false);
  }
});

refresh();
