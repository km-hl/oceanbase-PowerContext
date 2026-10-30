import http from "node:http";
import { readFileSync, writeFileSync, renameSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join, resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";

const here = dirname(fileURLToPath(import.meta.url));
const publicDir = join(here, "public");
const mime = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml" };

function json(response, status, data) {
  response.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
  response.end(JSON.stringify(data));
}

function fail(message, status = 400) {
  const error = new Error(message);
  error.status = status;
  return error;
}

function cleanString(value, name, limit = 5000) {
  if (typeof value !== "string" || !value.trim()) throw fail(name + "不能为空");
  const text = value.trim();
  if (text.length > limit) throw fail(name + "太长");
  return text;
}

async function readBody(request) {
  let size = 0;
  const chunks = [];
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 64 * 1024) throw fail("请求体过大", 413);
    chunks.push(chunk);
  }
  try { return JSON.parse(Buffer.concat(chunks).toString("utf8")); }
  catch { throw fail("无效的 JSON"); }
}

export function createAppServer({
  dataDir = process.env.STUDYFLOW_DATA_DIR || join(here, ".data"),
  powerContextURL = process.env.POWERCONTEXT_URL || "http://127.0.0.1:8000",
  token = process.env.POWERCONTEXT_TOKEN || "",
  scopeId = process.env.POWERCONTEXT_SCOPE || ""
} = {}) {
  mkdirSync(dataDir, { recursive: true });
  const path = join(dataDir, "studyflow.json");
  let state = { tasks: [], notes: [], scopeId: scopeId || null };
  if (existsSync(path)) {
    const loaded = JSON.parse(readFileSync(path, "utf8"));
    state = { tasks: loaded.tasks || [], notes: loaded.notes || [], scopeId: scopeId || loaded.scopeId || null };
  }
  function persist() {
    const temp = path + ".tmp";
    writeFileSync(temp, JSON.stringify(state, null, 2), { mode: 0o600 });
    renameSync(temp, path);
  }
  async function power(pathname, payload, method = "POST") {
    const headers = { "Accept": "application/json" };
    if (payload !== undefined) headers["Content-Type"] = "application/json";
    if (token) headers.Authorization = "Bearer " + token;
    let response;
    try {
      response = await fetch(new URL(pathname, powerContextURL), {
        method, headers, body: payload === undefined ? undefined : JSON.stringify(payload), signal: AbortSignal.timeout(6000)
      });
    } catch {
      throw fail("PowerContext 服务无法连接，请先启动服务并检查 POWERCONTEXT_URL", 503);
    }
    const raw = await response.text();
    let result;
    try { result = JSON.parse(raw); } catch { result = { message: raw.slice(0, 300) }; }
    if (!response.ok) throw fail("PowerContext HTTP " + response.status + "：" + (result.error?.message || result.message || "请求失败"), 502);
    return result;
  }
  let creatingScope;
  async function getScope() {
    if (state.scopeId) return state.scopeId;
    if (!creatingScope) {
      creatingScope = (async () => {
        const result = await power("/v1/scopes", {
          title: "StudyFlow 研习台",
          summary: "学习计划、论文阅读、科研笔记和重要决策",
          idempotency_key: "studyflow-personal-v1"
        });
        if (!result.scope_id) throw fail("PowerContext 没有返回 scope_id", 502);
        state.scopeId = result.scope_id;
        persist();
        return state.scopeId;
      })().finally(() => { creatingScope = null; });
    }
    return creatingScope;
  }
  const server = http.createServer(async (req, res) => {
    try {
      const pathName = new URL(req.url, "http://localhost").pathname;
      if (req.method === "GET" && pathName === "/api/status") {
        try {
          await power("/health/live", undefined, "GET");
          return json(res, 200, { powercontext: "online", scopeId: state.scopeId, backend: "online" });
        } catch (error) {
          return json(res, 200, { powercontext: "offline", scopeId: state.scopeId, backend: "online", hint: error.message });
        }
      }
      if (req.method === "GET" && pathName === "/api/state") {
        return json(res, 200, state);
      }
      // Basic same-origin browser protection. Non-browser callers (no Origin) are allowed locally.
      if (pathName.startsWith("/api/") && req.method !== "GET") {
        const origin = req.headers.origin;
        if (origin && new URL(origin).host !== req.headers.host) throw fail("跨域写入被拒绝", 403);
      }
      if (req.method === "POST" && pathName === "/api/tasks") {
        const body = await readBody(req);
        const task = {
          id: randomUUID(), title: cleanString(body.title, "任务标题", 160),
          project: String(body.project || "日常学习").slice(0, 80),
          due: /^\d{4}-\d{2}-\d{2}$/.test(body.due || "") ? body.due : "",
          priority: ["high", "medium", "low"].includes(body.priority) ? body.priority : "medium",
          done: false, createdAt: new Date().toISOString()
        };
        state.tasks.unshift(task); persist();
        return json(res, 201, task);
      }
      const taskMatch = pathName.match(/^\/api\/tasks\/([0-9a-f-]{36})$/);
      if (taskMatch && ["PATCH", "DELETE"].includes(req.method)) {
        const i = state.tasks.findIndex(task => task.id === taskMatch[1]);
        if (i < 0) throw fail("任务不存在", 404);
        if (req.method === "DELETE") {
          state.tasks.splice(i, 1); persist();
          return json(res, 200, { ok: true });
        }
        const body = await readBody(req);
        if (typeof body.done !== "boolean") throw fail("done 必须为布尔值");
        state.tasks[i].done = body.done;
        persist(); return json(res, 200, state.tasks[i]);
      }
      if (req.method === "POST" && pathName === "/api/notes") {
        const body = await readBody(req);
        const note = {
          id: randomUUID(), title: cleanString(body.title, "笔记标题", 160),
          content: cleanString(body.content, "笔记内容", 7500),
          project: String(body.project || "日常学习").slice(0, 80),
          createdAt: new Date().toISOString(), sourceSynced: false, remembered: false
        };
        state.notes.unshift(note); persist();
        // A Source is evidence, not an automatically approved long-term decision.
        try {
          const scope = await getScope();
          await power("/v1/sources/content", {
            scope_id: scope, source_id: "studyflow-" + note.id,
            content: note.title + "\n" + note.content,
            metadata: { origin: "studyflow", project: note.project }
          });
          note.sourceSynced = true; persist();
        } catch (error) {
          note.syncError = error.message; persist();
        }
        return json(res, 201, note);
      }
      if (req.method === "POST" && pathName === "/api/notes/remember") {
        const body = await readBody(req);
        const note = state.notes.find(n => n.id === body.id);
        if (!note) throw fail("笔记不存在", 404);
        if (note.remembered) return json(res, 200, { alreadyRemembered: true, note });
        const scope = await getScope();
        const result = await power("/v1/memory/remember", {
          scope_id: scope, kind: "decision",
          text: (note.title + "：" + note.content).slice(0, 2500),
          reason: "用户在 StudyFlow 中明确选择收藏为长期记忆"
        });
        note.remembered = true;
        note.memoryCitation = result.citation || null;
        persist();
        return json(res, 200, { note, result });
      }
      if (req.method === "POST" && pathName === "/api/search") {
        const body = await readBody(req);
        const query = cleanString(body.query, "检索词", 200);
        const scope = await getScope();
        return json(res, 200, await power("/v1/memory/search", {
          scope_id: scope, query, limit: 8
        }));
      }
      if (req.method === "POST" && pathName === "/api/context") {
        const body = await readBody(req);
        const query = cleanString(body.query, "问题", 200);
        const scope = await getScope();
        return json(res, 200, await power("/v1/context/prepare", {
          scope_id: scope, query, max_bytes: 4000
        }));
      }
      if (req.method === "GET" && !pathName.startsWith("/api/")) {
        const selected = pathName === "/" ? "/index.html" : pathName;
        if (!["/index.html", "/app.js", "/style.css"].includes(selected)) throw fail("页面不存在", 404);
        const filename = resolve(publicDir, "." + selected);
        res.writeHead(200, {
          "Content-Type": (mime[extname(filename)] || "application/octet-stream") + "; charset=utf-8",
          "X-Content-Type-Options": "nosniff",
          "Cache-Control": "no-cache"
        });
        return res.end(readFileSync(filename));
      }
      throw fail("接口不存在", 404);
    } catch (error) {
      return json(res, error.status || 500, { error: error.message || "服务器错误" });
    }
  });
  return server;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const host = process.env.HOST || "127.0.0.1";
  const port = Number(process.env.PORT || 4173);
  createAppServer().listen(port, host, () => {
    console.log("StudyFlow running on http://" + host + ":" + port);
  });
}
