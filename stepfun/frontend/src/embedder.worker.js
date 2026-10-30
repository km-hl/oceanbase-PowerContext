import { pipeline } from "@huggingface/transformers";

const MODEL_ID = "Xenova/paraphrase-multilingual-MiniLM-L12-v2";
let extractor = null;
let activeKey = "";

self.onmessage = async ({ data }) => {
  const { id, texts, device, dtype } = data;
  try {
    if (device === "webgpu" && !self.navigator.gpu) {
      throw new Error("此浏览器不支持 WebGPU；请选择 WASM 后端。");
    }
    const key = device + "/" + dtype;
    const loadStart = performance.now();
    if (activeKey !== key || !extractor) {
      extractor = null;
      activeKey = "";
      const next = await pipeline("feature-extraction", MODEL_ID, {
        device,
        dtype,
        progress_callback: (event) => {
          if (event.status === "progress" && Number.isFinite(event.progress)) {
            self.postMessage({ id, type: "progress", progress: Math.round(event.progress) });
          }
        },
      });
      extractor = next;
      activeKey = key;
    }
    const loadMs = performance.now() - loadStart;
    const start = performance.now();
    const output = await extractor(texts, { pooling: "mean", normalize: true });
    const vectors = output.tolist();
    if (vectors.length !== texts.length || vectors.some((row) => row.length !== 384)) {
      throw new Error("模型输出维度错误，需要 384 维向量");
    }
    self.postMessage({
      id, type: "done", modelId: MODEL_ID, vectors,
      inferenceMs: Math.round((performance.now() - start) * 100) / 100,
      setupMs: Math.round(loadMs * 100) / 100,
      device, dtype,
    });
  } catch (error) {
    self.postMessage({ id, type: "error", message: String(error?.message || error) });
  }
};
