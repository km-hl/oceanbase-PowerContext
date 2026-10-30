# StepFun Knowledge Lab

**端侧 AI 推理性能优化与 seekdb 混合检索** — an independent, runnable competition project on the **StepFun** branch. The original PowerContext product and competition acceptance tests remain untouched. The competition evaluator expects this project at repository-root `stepfun/`.

## What it actually does

1. Import local TXT, Markdown, CSV, JSON or LOG text in the browser (2 MiB/file).
2. Split text into 380-character chunks with 48-character overlaps **in the browser**.
3. Run the real 384-dimensional multilingual MiniLM ONNX embedding model **in a Web Worker on the user's device** with Hugging Face Transformers.js. Supports WASM and WebGPU; select q8 or fp32 according to the device. No fake/hash-based embeddings or backend embedding API.
4. Upload text chunks and their actual computed vectors to a self-hosted FastAPI endpoint, which persists them in **OceanBase seekdb** using native `VECTOR(384)`, HNSW and FULLTEXT indexes.
5. Embed each query in the browser, then retrieve from **both** seekdb vector and FTS channels and merge by deterministic reciprocal rank fusion (RRF k=60), exposing `matched_by`.
6. Compare measured warm inference latency for device/precision combinations with a repeated, fixed four-sentence benchmark.

The frontend can load publicly hosted model weights on first use; their download is cached by the browser. The document text and query go only to the self-hosted backend for storage and retrieval. The model inference itself does **not** go through an external embedding service.

## Architecture

```text
Browser (Web Worker)                       Self-hosted Docker Compose
TXT/MD → chunkText() → ONNX → vector        ┌──────────────────┐
                                     ──────→│ FastAPI /api     │
Query → ONNX → query vector            ─────→│ ingest/search    │
                                            └────────┬─────────┘
                                                     │ MySQL protocol
                                            ┌────────▼─────────┐
                                            │ seekdb 2881      │
                                            │ VECTOR + HNSW    │
                                            │ FULLTEXT + IK    │
                                            └──────────────────┘
```

The two seekdb candidate SQL statements are executed only for `hybrid` mode: one HNSW approximate vector query, one FULLTEXT `MATCH ... AGAINST` query. Scores are fused in the API. `vector` and `text` modes run only their respective channel. This is **not** a claim of a single-SQL hybrid plan.

## Start (Docker, recommended)

Use Docker Compose on Linux x86_64 or a supported machine with **at least 2 GiB available for seekdb** (4 GiB total recommended). From the repository root:

```bash
cd stepfun
cp .env.example .env
# Edit .env to use your own random SEEKDB_PASSWORD
docker compose up -d --build
docker compose logs -f app
```

Open **http://127.0.0.1:8000**. The container starts seekdb, creates its `stepfun_kb` database, waits until it is ready, then creates the table with native indexes. First startup and initial ONNX download may take longer. The app binds to **localhost only**. To expose it on the Internet, add HTTPS **and access control** to your reverse proxy: the demo API has no login and should not be published openly.

The persistent Docker volume is `seekdb_data`. `docker compose down` preserves data; `down -v` permanently deletes data.

### Development without Compose

Start an existing seekdb server with a database named `stepfun_kb` and set `SEEKDB_*` environment variables from `backend/.env.example`. Then:

```bash
python -m pip install -r backend/requirements.txt pytest
cd backend
uvicorn app:app --reload --port 8000
# separate shell:
cd frontend
npm install
npm run dev
```

The Vite dev server proxies `/api` to localhost:8000. The production image builds Vite and serves its assets from FastAPI on port 8000.

## Evaluation and tests

```bash
# Pure algorithm and validation checks, without database/network:
cd frontend && npm test
python -m pip install -r ../backend/requirements.txt pytest
python -m pytest -q ../backend/tests/test_app.py

# After starting the real seekdb + application (tests actual SQL):
python backend/tests/smoke_seekdb.py
```

In the UI, choose `WASM/q8` (reliable CPU baseline) or `WebGPU/fp32` (GPU acceleration), press **运行推理基准** after model prewarm, and compare the actual recorded three-run median. Rerun with fixed corpus/query and compare search modes to evaluate quality. Backend responses report measured query latency and candidate counts; the UI reports user-perceived end-to-end time.

**Validation scope:** local unit tests cover fusion, vector validation and chunk segmentation; the optional smoke script requires a real seekdb installation. Browser inference and database performance depend on actual hardware and network and must be measured on the target machine. No performance uplift, Recall@K or competition grade is claimed without real measurements.

## API overview

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Check the **real** seekdb connection and chunk count |
| GET | `/api/documents` | List document sources |
| POST | `/api/ingest` | Store chunks and 384-D embeddings, up to 32/request |
| POST | `/api/search` | `hybrid`, `vector` or `text`; return RRF scores and channel labels |
| DELETE | `/api/documents/{source_id}` | Remove a source and its chunks |

`model_id` must match the pinned multilingual MiniLM model for both ingest and query; this guards against mixing incompatible embeddings. All SQL values use driver parameters rather than string concatenation. Incoming floats must be finite and nonzero, and ingestion is transactional per batch.

## Technical details / tradeoffs

- **Model:** [Xenova/paraphrase-multilingual-MiniLM-L12-v2](https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/tree/main/onnx). Publishes FP32 and quantized ONNX model files; 384-dimensional embeddings.
- **Inference:** [Transformers.js v3 WebGPU guide](https://huggingface.co/docs/transformers.js/v3.8.1/guides/webgpu) and [quantization](https://huggingface.co/blog/transformersjs-v3). ONNX Runtime executes in a dedicated Worker; batches contain six chunks; pooling = mean and normalization = true. Models are cached per browser session and are not reloaded for each chunk.
- **Storage and native indexes:** [seekdb documentation](https://github.com/oceanbase/seekdb) shows `VECTOR`, `FULLTEXT`, `MATCH` and `L2_DISTANCE`. The index schema targets the seekdb server image; generic MySQL is **not** a substitute.
- **Fusion:** RRF combines independent ranks instead of incomparable L2 distance and text relevance scales. Results use stable id tie-breaking and retain source ids for traceability.
- **Limits:** text-only import (no PDF/OCR extraction), no multi-user authentication, no offline bundled ONNX weights. q8/WebGPU compatibility depends on the device and ONNX backend; switch to WASM/q8 if the browser cannot run the selected combination. Character chunking is deterministic but token-aware splitting is a future enhancement.

## Submission

Repository: https://github.com/km-hl/oceanbase-PowerContext

Branch: `StepFun` (independent from `2026-competition`).

Project root: `stepfun/`.

The full 40-character commit SHA is the GitHub commit used for submission; branch names alone do not identify an immutable version.

License: repository Apache-2.0. The model has its own published licensing and attribution requirements; consult the model card before redistribution.
