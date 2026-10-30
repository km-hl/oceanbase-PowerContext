const state = { tasks: [], notes: [] };
const $ = (id) => document.getElementById(id);
const titles = { overview: "学习与研究，尽在掌握", tasks: "把目标变成今天的行动", notes: "每一个想法都值得保存", recall: "与你的知识，再次相遇" };
let toastTimer;
let preparedContext = "";

function toast(message) {
  const area = $("toast");
  area.textContent = message;
  area.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => area.classList.remove("show"), 3500);
}

async function api(path, method = "GET", payload) {
  const response = await fetch("/api" + path, {
    method,
    headers: payload === undefined ? {} : { "Content-Type": "application/json" },
    body: payload === undefined ? undefined : JSON.stringify(payload)
  });
  const content = await response.json();
  if (!response.ok) throw new Error(content.error || "请求失败");
  return content;
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function empty(target, message) {
  target.replaceChildren(node("div", "empty", message));
}

function showPage(name) {
  for (const element of document.querySelectorAll(".page")) element.classList.toggle("active", element.id === name);
  for (const element of document.querySelectorAll(".nav-item")) element.classList.toggle("active", element.dataset.page === name);
  $("pageTitle").textContent = titles[name];
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function taskRow(task, compact = false) {
  const row = node("div", "list-row" + (task.done ? " done" : ""));
  const check = node("input", "check");
  check.type = "checkbox";
  check.checked = task.done;
  check.setAttribute("aria-label", "完成：" + task.title);
  check.addEventListener("change", async () => {
    try {
      await api("/tasks/" + task.id, "PATCH", { done: check.checked });
      await refresh();
    } catch (error) { check.checked = !check.checked; toast(error.message); }
  });
  const info = node("div", "list-main");
  info.append(node("strong", "", task.title), node("small", "", task.project + (task.due ? " · 截止 " + task.due : "")));
  const tag = node("span", "tag " + task.priority, ({ high: "重要", medium: "普通", low: "低" })[task.priority] || "普通");
  row.append(check, info, tag);
  if (!compact) {
    const del = node("button", "delete", "删除");
    del.type = "button";
    del.setAttribute("aria-label", "删除任务：" + task.title);
    del.addEventListener("click", async () => {
      if (!confirm("确定删除这项任务吗？")) return;
      try { await api("/tasks/" + task.id, "DELETE"); await refresh(); }
      catch (error) { toast(error.message); }
    });
    row.append(del);
  }
  return row;
}

function noteRow(note, compact = false) {
  const card = node("article", "note-card");
  const head = node("div", "note-top");
  head.append(node("strong", "", note.title), node("small", "", new Date(note.createdAt).toLocaleDateString("zh-CN")));
  card.append(head);
  const paragraph = node("p", "note-content", note.content);
  card.append(paragraph);
  const foot = node("div", "note-footer");
  const meta = node("span", "note-meta" + (note.remembered ? " recalled" : ""),
    note.project + " · " + (note.remembered ? "✓ 已存入 Memory" : note.sourceSynced ? "✓ 已写入 Source" : "○ 尚未同步 PowerContext"));
  foot.append(meta);
  if (!compact) {
    const remember = node("button", "text-link", note.remembered ? "已保存 ✓" : "✧ 存为长期记忆 →");
    remember.type = "button";
    remember.disabled = !!note.remembered;
    remember.addEventListener("click", async () => {
      remember.disabled = true;
      remember.textContent = "正在写入...";
      try {
        await api("/notes/remember", "POST", { id: note.id });
        toast("已通过 PowerContext 保存为长期记忆");
        await refresh();
      } catch (error) {
        remember.disabled = false;
        remember.textContent = "✧ 存为长期记忆 →";
        toast(error.message);
      }
    });
    foot.append(remember);
  }
  card.append(foot);
  return card;
}

function render() {
  const tasks = state.tasks;
  const done = tasks.filter(task => task.done).length;
  $("totalTasks").textContent = String(tasks.length);
  $("doneTasks").textContent = String(done);
  $("totalNotes").textContent = String(state.notes.length);
  const percent = tasks.length ? Math.round(100 * done / tasks.length) : 0;
  $("completion").textContent = percent + "%";
  $("completionBar").style.width = percent + "%";

  const next = $("nextTasks");
  const upcoming = tasks.filter(task => !task.done).sort((a, b) => (a.due || "9999").localeCompare(b.due || "9999")).slice(0, 4);
  next.replaceChildren(...upcoming.map(task => taskRow(task, true)));
  if (!upcoming.length) empty(next, "尚无待办，试着添加一个学习目标。");

  const recent = $("recentNotes");
  recent.replaceChildren(...state.notes.slice(0, 3).map(note => noteRow(note, true)));
  if (!state.notes.length) empty(recent, "你的第一篇研究笔记会出现在这里。");

  const taskFilter = $("taskFilter").value;
  const filteredTasks = tasks.filter(task => taskFilter === "all" || (taskFilter === "done" ? task.done : !task.done));
  const list = $("taskList");
  list.replaceChildren(...filteredTasks.map(task => taskRow(task)));
  if (!filteredTasks.length) empty(list, "没有符合条件的任务。");

  const query = $("noteFilter").value.toLowerCase().trim();
  const filteredNotes = state.notes.filter(note => (note.title + " " + note.content + " " + note.project).toLowerCase().includes(query));
  const notes = $("noteList");
  notes.replaceChildren(...filteredNotes.map(note => noteRow(note)));
  if (!filteredNotes.length) empty(notes, "没有找到笔记，记录一个新发现吧。");
}

async function refresh() {
  const data = await api("/state");
  state.tasks = data.tasks || [];
  state.notes = data.notes || [];
  render();
}

async function checkConnection() {
  try {
    const status = await api("/status");
    const area = $("connection");
    const online = status.powercontext === "online";
    area.classList.toggle("online", online);
    area.replaceChildren(node("span", "dot"), document.createTextNode(online ? "PowerContext 已连接" : "PowerContext 未连接"));
    area.title = status.hint || (status.scopeId ? "Scope: " + status.scopeId : "Scope 将在首次写入时创建");
  } catch { $("connection").textContent = "无法连接应用服务"; }
}

function formData(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function putResult(target, message) {
  target.replaceChildren(node("p", "", message));
}

function showSearch(result) {
  const target = $("searchOutput");
  const items = result.hits ?? result.entries ?? result.results ?? result.matches ?? result.memories ?? (Array.isArray(result) ? result : null);
  if (!Array.isArray(items)) {
    return putResult(target, "PowerContext 返回了无法识别的搜索结构，请检查接口响应。");
  }
  if (!items.length) {
    const reason = result.status === "empty" ? "PowerContext 没有发现相关记忆。" : "当前 Scope 暂无匹配记忆。请检查笔记是否已点击“存为长期记忆”。";
    return putResult(target, reason);
  }
  target.replaceChildren(...items.map(item => {
    const card = node("div", "memory-card");
    card.append(node("div", "", item.text || item.content || item.entry?.text || item.memory?.text || JSON.stringify(item)));
    const citation = typeof item.citation === "string" ? item.citation : item.citation ? JSON.stringify(item.citation) : "";
    const matched = Array.isArray(item.matched_by) ? item.matched_by.join("/") : "";
    if (citation || matched) card.append(node("small", "", [matched, citation].filter(Boolean).join(" · ")));
    return card;
  }));
}

async function submit(form, run) {
  const button = form.querySelector('button[type="submit"]');
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "处理中...";
  try { await run(); }
  catch (error) { toast(error.message); }
  finally { button.disabled = false; button.textContent = label; }
}

function setup() {
  $("today").textContent = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric", weekday: "long" }).format(new Date());
  for (const button of document.querySelectorAll("[data-page]")) button.addEventListener("click", () => showPage(button.dataset.page));
  for (const button of document.querySelectorAll("[data-goto]")) button.addEventListener("click", () => showPage(button.dataset.goto));
  $("heroNew").addEventListener("click", () => { showPage("notes"); $("noteForm").elements.title.focus(); });
  $("taskFilter").addEventListener("change", render);
  $("noteFilter").addEventListener("input", render);
  $("taskForm").addEventListener("submit", event => {
    event.preventDefault();
    const form = event.currentTarget;
    void submit(form, async () => {
      await api("/tasks", "POST", formData(form));
      form.reset();
      await refresh();
      toast("任务已添加");
    });
  });
  $("noteForm").addEventListener("submit", event => {
    event.preventDefault();
    const form = event.currentTarget;
    void submit(form, async () => {
      const note = await api("/notes", "POST", formData(form));
      form.reset();
      await refresh();
      await checkConnection();
      toast(note.sourceSynced ? "笔记已保存并同步为 Source" : "笔记已保存，本次未同步 PowerContext");
    });
  });
  $("searchForm").addEventListener("submit", event => {
    event.preventDefault();
    void submit(event.currentTarget, async () => {
      const result = await api("/search", "POST", { query: $("searchInput").value });
      showSearch(result);
    });
  });
  $("contextForm").addEventListener("submit", event => {
    event.preventDefault();
    void submit(event.currentTarget, async () => {
      const result = await api("/context", "POST", { query: $("contextInput").value });
      preparedContext = result.content || "";
      if (!preparedContext) {
        putResult($("contextOutput"), "尚未召回足够相关的历史上下文。请先保存长期记忆。");
        $("copyContext").classList.add("copy-hidden");
        return;
      }
      const pre = node("pre", "", preparedContext);
      $("contextOutput").replaceChildren(pre);
      $("copyContext").classList.remove("copy-hidden");
    });
  });
  $("copyContext").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(preparedContext); toast("上下文已复制"); }
    catch { toast("复制失败，请手动选中内容"); }
  });
}

setup();
await Promise.allSettled([refresh(), checkConnection()]);
