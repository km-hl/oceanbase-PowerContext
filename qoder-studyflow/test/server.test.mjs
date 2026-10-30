import test, { before, after } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createAppServer } from "../server.mjs";

const dataDir = mkdtempSync(join(tmpdir(), "studyflow-test-"));
const calls = [];
const fakePowerContext = http.createServer(async (req, res) => {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : null;
  calls.push({ path: req.url, body, authorization: req.headers.authorization });
  let result;
  if (req.url === "/health/live") result = { status: "ok" };
  else if (req.url === "/v1/scopes") result = { scope_id: "scope:test-studyflow" };
  else if (req.url === "/v1/sources/content") result = { source: { id: body.source_id } };
  else if (req.url === "/v1/memory/remember") result = { citation: "memory:42@1" };
  else if (req.url === "/v1/memory/search") result = { entries: [{ text: "Remembered HNSW M=16", citation: "memory:42@1" }] };
  else if (req.url === "/v1/context/prepare") result = { status: "ready", content: "Context: HNSW M=16" };
  else { res.writeHead(404); return res.end("{}"); }
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(JSON.stringify(result));
});
let backend;
let appURL;

before(async () => {
  await new Promise(resolve => fakePowerContext.listen(0, "127.0.0.1", resolve));
  const powerURL = "http://127.0.0.1:" + fakePowerContext.address().port;
  backend = createAppServer({ dataDir, powerContextURL: powerURL, token: "unit-test-secret" });
  await new Promise(resolve => backend.listen(0, "127.0.0.1", resolve));
  appURL = "http://127.0.0.1:" + backend.address().port;
});

after(async () => {
  await new Promise(resolve => backend.close(resolve));
  await new Promise(resolve => fakePowerContext.close(resolve));
  rmSync(dataDir, { recursive: true, force: true });
});

async function request(path, method = "GET", body) {
  const res = await fetch(appURL + path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  return { status: res.status, data: await res.json() };
}

test("health checks real backend and does not expose bearer token", async () => {
  const { status, data } = await request("/api/status");
  assert.equal(status, 200);
  assert.equal(data.powercontext, "online");
  assert.equal(JSON.stringify(data).includes("unit-test-secret"), false);
});

test("tasks persist, support completion and deletion", async () => {
  const added = await request("/api/tasks", "POST", { title: "复习数据库", project: "课程", priority: "high" });
  assert.equal(added.status, 201);
  const id = added.data.id;
  assert.equal((await request("/api/state")).data.tasks.length, 1);
  assert.equal((await request("/api/tasks/" + id, "PATCH", { done: true })).data.done, true);
  assert.equal((await request("/api/tasks/" + id, "DELETE")).status, 200);
  assert.equal((await request("/api/state")).data.tasks.length, 0);
});

test("source, authorized memory, search and prepared context work end-to-end with mock API", async () => {
  const created = await request("/api/notes", "POST", {
    title: "HNSW 图索引结论", content: "HNSW M=16, efConstruction=200", project: "向量研究"
  });
  assert.equal(created.status, 201);
  assert.equal(created.data.sourceSynced, true);
  const source = calls.find(call => call.path === "/v1/sources/content");
  assert.equal(source.body.scope_id, "scope:test-studyflow");
  assert.match(source.body.content, /efConstruction/);
  assert.equal(source.authorization, "Bearer unit-test-secret");
  assert.equal(calls.filter(call => call.path === "/v1/scopes").length, 1);

  const saved = await request("/api/notes/remember", "POST", { id: created.data.id });
  assert.equal(saved.status, 200);
  assert.equal(saved.data.note.remembered, true);
  assert.equal(saved.data.note.memoryCitation, "memory:42@1");
  const rememberedBefore = calls.filter(call => call.path === "/v1/memory/remember").length;
  const again = await request("/api/notes/remember", "POST", { id: created.data.id });
  assert.equal(again.data.alreadyRemembered, true);
  assert.equal(calls.filter(call => call.path === "/v1/memory/remember").length, rememberedBefore);

  const search = await request("/api/search", "POST", { query: "HNSW" });
  assert.equal(search.data.entries[0].text, "Remembered HNSW M=16");
  const prepared = await request("/api/context", "POST", { query: "HNSW" });
  assert.equal(prepared.data.status, "ready");
  assert.match(prepared.data.content, /HNSW/);
  assert.equal((await request("/api/state")).data.notes[0].remembered, true);
});

test("invalid payloads are rejected without corrupting data", async () => {
  assert.equal((await request("/api/tasks", "POST", { title: "" })).status, 400);
  assert.equal((await request("/api/search", "POST", { query: "" })).status, 400);
  assert.equal((await request("/api/tasks/non-existent", "PATCH", { done: true })).status, 404);
  assert.equal((await request("/api/state")).data.notes.length, 1);
});
