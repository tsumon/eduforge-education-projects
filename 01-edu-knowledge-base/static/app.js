/* EduForge 知识库前端：原生 JS，hash 路由，无构建步骤。 */
(() => {
  "use strict";
  const API = "/api/knowledge";
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const el = (tag, attrs = {}, children = []) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k === "html") n.innerHTML = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v === true ? "" : v);
    }
    for (const c of [].concat(children)) if (c !== null && c !== undefined) n.append(c);
    return n;
  };
  const fmtTime = iso => {
    const d = new Date(iso);
    if (isNaN(d)) return iso || "";
    const p = n => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  };
  const sourceLabel = c => (c.title.startsWith("《") ? c.title : `《${c.title}》`) + (c.heading && c.heading !== c.title ? ` · ${c.heading}` : "");
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  // ---------- 提示 ----------
  let toastTimer = null;
  function toast(msg, isError = false) {
    const t = $("#toast");
    t.textContent = msg;
    t.className = "toast" + (isError ? " error" : "");
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, isError ? 7000 : 3000);
  }

  // ---------- API ----------
  const tokenKey = "eduforge.kb.token";
  const getToken = () => localStorage.getItem(tokenKey) || "";
  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(API + path, Object.assign({}, opts, { headers }));
    let body = null;
    try { body = await res.json(); } catch (_) { /* 非 JSON 响应 */ }
    if (res.status === 401) {
      setStatus("err", "需要访问令牌");
      throw new Error("需要访问令牌：请到「设置」填写");
    }
    if (!res.ok) {
      const d = body && body.detail;
      const msg = typeof d === "string" ? d
        : d && d.message ? `${d.message}：${(d.failed || []).map(f => `${f.file}（${f.error}）`).join("；")}`
        : Array.isArray(d) ? d.map(x => x.msg).join("；")
        : `请求失败（${res.status}）`;
      throw new Error(msg);
    }
    return body;
  }
  const postJSON = (path, data) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
  const initialAnswer = $("#answer-empty").cloneNode(true);
  let asking = false;

  // Downloads use the same authorization as JSON requests; the token never enters a URL.
  document.addEventListener("click", async ev => {
    const link = ev.target.closest("a[download]");
    if (!link || !link.href.startsWith(location.origin + API + "/")) return;
    ev.preventDefault();
    if (link.dataset.busy) return;
    link.dataset.busy = "true";
    try {
      const headers = getToken() ? { Authorization: `Bearer ${getToken()}` } : {};
      const res = await fetch(link.href, { headers });
      if (!res.ok) throw new Error(res.status === 401 ? "导出需要访问令牌，请在设置中填写" : `导出失败（${res.status}）`);
      const disposition = res.headers.get("Content-Disposition") || "";
      const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
      const simple = disposition.match(/filename="?([^";]+)"?/i);
      const filename = encoded ? decodeURIComponent(encoded[1]) : simple ? simple[1] : link.href.endsWith(".jsonl") ? "knowledge-history.jsonl" : "knowledge-answer.md";
      const url = URL.createObjectURL(await res.blob());
      const a = el("a", { href: url, download: filename });
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { toast(e.message, true); }
    finally { delete link.dataset.busy; }
  });

  // ---------- 顶栏状态 ----------
  function setStatus(kind, text) {
    $("#status-dot").className = "dot " + (kind === "ok" ? "ok" : kind === "err" ? "err" : "");
    $("#status-text").textContent = text;
  }
  let stats = null;
  async function loadStats() {
    try {
      stats = await api("/stats");
      const retrieval = stats.retrieval || { mode: "lexical", ready: true };
      setStatus(retrieval.ready ? "ok" : "err", retrieval.ready ? `${stats.document_count} 份资料 · ${stats.chunk_count} 个分片` : "索引异常，请到设置重建");
      const llm = stats.llm && stats.llm.configured;
      $("#status-mode").textContent = `${retrieval.mode === "hybrid" ? "混合检索" : "词法检索"} · ${stats.agent?.enabled ? "知识库 Agent" : llm ? "API 生成" : "基础检索预览"}`;
      $("#status-mode").title = llm ? stats.llm.model : "未启用回答模型";
      if ($("#answer-empty-docs")) $("#answer-empty-docs").hidden = stats.document_count > 0;
      $("#library-doc-count").textContent = stats.document_count.toLocaleString("zh-CN");
      $("#library-chunk-count").textContent = stats.chunk_count.toLocaleString("zh-CN");
      $("#workspace-categories").replaceChildren(...Object.entries(stats.categories || {}).map(([name, count]) =>
        el("div", { class: "category-row" }, [el("span", { text: name }), el("span", { text: `${count} 份` })])));
      $("#ask-hint").textContent = stats.document_count > 0
        ? "回答只引用已导入资料；资料不足时会明确拒答"
        : "知识库为空，先导入资料";
      renderSettings();
      loadSidebar();
    } catch (e) {
      setStatus("err", e.message.includes("访问令牌") ? "需要访问令牌" : "服务不可用");
      toast(e.message, true);
    }
  }

  // ---------- 路由 ----------
  const views = ["ask", "docs", "history", "settings"];
  function route() {
    const name = (location.hash || "#ask").slice(1).split("?")[0];
    if (name.startsWith("cite-")) return;
    const view = views.includes(name) ? name : "ask";
    $$(".view").forEach(v => {
      v.hidden = v.dataset.view !== view;
      // A routed document has one page heading; inactive views retain section headings.
      const heading = v.querySelector(".page-head > div > h1, .page-head > div > h2");
      const tag = v.hidden ? "h2" : "h1";
      if (heading && heading.tagName.toLowerCase() !== tag) heading.replaceWith(el(tag, { id: heading.id, text: heading.textContent }));
    });
    $$(".tabs a[data-view]").forEach(a => {
      a.classList.toggle("on", a.dataset.view === view);
      if (a.dataset.view === view) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    if (view === "docs") loadDocuments();
    if (view === "history") loadHistory();
    if (view === "settings") renderSettings();
  }
  window.addEventListener("hashchange", route);

  async function loadSidebar() {
    try {
      const [documents, history] = await Promise.all([api("/documents"), api("/history?limit=4")]);
      $("#source-count").textContent = `${documents.documents.length} 份`;
      $("#workspace-source-list").replaceChildren(...(documents.documents.length
        ? documents.documents.slice(0, 4).map(d => {
          const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
          const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
          use.setAttribute("href", "#i-file"); icon.append(use);
          return el("button", { type: "button", class: "source-item", title: d.title, onclick: () => openDrawer(d.id) }, [icon,
            el("span", {}, [el("span", { class: "source-title", text: d.title }), el("span", { class: "source-caption", text: `${d.category} · ${d.chunk_count} 个分片` })])]);
        }) : [el("p", { class: "muted", text: "导入课程文档后，来源会显示在这里。" })]));
      $("#recent-questions").replaceChildren(...(history.history.length
        ? history.history.map(h => el("button", { type: "button", class: "recent-question", title: h.question, text: h.question, onclick: () => showRecord(h) }))
        : [el("p", { class: "muted", text: "还没有问答记录" })]));
    } catch (e) {
      $("#workspace-source-list").replaceChildren(el("button", { type: "button", class: "link-btn", text: "来源加载失败，重试", onclick: loadSidebar }));
    }
  }

  // ---------- 示例问题 ----------
  async function loadSampleQuestions() {
    try {
      const data = await api("/sample");
      if (!data.questions.length) return;
      $("#samples").hidden = false;
      $("#sample-chips").replaceChildren(...data.questions.map(q => el("button", {
        type: "button", class: "chip" + (q.expect === "refuse" ? " refuse" : ""),
        title: q.expect === "refuse" ? "资料中没有相关内容，预期拒答" : `知识点：${q.topic}`,
        text: q.question, onclick: () => { $("#question").value = q.question; ask(q.question); },
      })));
    } catch (_) { /* 无示例时静默 */ }
  }

  // ---------- 问答 ----------
  function highlightExcerpt(text, excerpt) {
    const frag = document.createDocumentFragment();
    const sentences = (excerpt || "").split(/\n|(?<=[。！？；])\s*/u).map(s => s.trim()).filter(Boolean);
    const ranges = sentences.map(s => ({ start: text.indexOf(s), text: s })).filter(r => r.start >= 0).sort((a, b) => a.start - b.start);
    let offset = 0;
    for (const range of ranges) {
      if (range.start < offset) continue;
      frag.append(text.slice(offset, range.start), el("mark", { text: range.text }));
      offset = range.start + range.text.length;
    }
    frag.append(text.slice(offset));
    return frag;
  }
  function renderCiteLinks(container, text, citations) {
    container.replaceChildren();
    for (const part of text.split(/(\[\d+\])/g)) {
      const m = part.match(/^\[(\d+)\]$/);
      if (m && citations.some(c => String(c.n) === m[1])) container.append(el("a", { class: "cite", href: `#cite-${m[1]}`, text: part }));
      else container.append(part);
    }
  }
  function renderAnswer(rec) {
    const tpl = $("#tpl-answer").content.cloneNode(true);
    const art = $(".answer", tpl);
    const cites = rec.citations || [];
    $(".answer-q", tpl).textContent = rec.question;
    const run = rec.diagnostics?.agent;
    const runFailed = run && run.status !== "completed";
    $(".pill-mode", tpl).textContent = run ? (runFailed ? "Agent 未完成" : rec.refused ? "Agent · 资料不足" : "Agent · 已核验引用") : rec.refused ? "资料不足" : rec.mode === "llm" ? "模型生成 · 基于引用" : rec.diagnostics?.degraded ? "生成未通过 · 原文抽取" : "原文抽取";
    $(".result-title", tpl).textContent = rec.refused ? "当前资料无法支持回答" : "根据课程资料";
    if (runFailed) $(".result-title", tpl).textContent = "Agent 未完成本次任务";
    if (rec.refused) $(".pill-mode", tpl).style.color = "var(--red)";
    const time = $(".answer-time", tpl);
    time.textContent = fmtTime(rec.created_at);
    time.setAttribute("datetime", rec.created_at);
    $(".export", tpl).href = `${API}/export/${encodeURIComponent(rec.id)}`;

    if (rec.refused) {
      art.classList.add("refused");
      $(".stamp", tpl).hidden = false;
      if (runFailed) $(".stamp", tpl).textContent = "查看下方工具记录后重试";
      const p = $(".refusal", tpl);
      p.hidden = false;
      p.textContent = rec.answer;
    } else if (rec.mode === "agent" || rec.mode === "llm" || !cites.every(c => c.excerpt)) {
      const p = $(".answer-text", tpl);
      p.hidden = false;
      renderCiteLinks(p, rec.answer, cites);
    } else {
      $(".points", tpl).replaceChildren(...cites.map(c => el("li", {}, [
        el("p", { class: "point-text", text: c.excerpt }),
        el("p", { class: "point-src" }, [el("a", { href: `#cite-${c.n}`, text: `[${c.n}] ${sourceLabel(c)}` })]),
      ])));
    }
    if (cites.length) {
      $(".citations", tpl).hidden = false;
      $(".citation-list", tpl).replaceChildren(...cites.map(c => el("li", { id: `cite-${c.n}`, value: c.n, tabindex: -1 }, [
        el("span", { class: "citation-src", text: sourceLabel(c) }),
        el("span", { class: "citation-score", text: c.semantic_score != null ? `语义相似度 ${c.semantic_score.toFixed(3)} · 词法匹配 ${c.lexical_score}` : `词法匹配 ${c.score} · 内容词覆盖 ${Math.round(c.coverage * 100)}%` }),
        el("p", { class: "citation-text" }, highlightExcerpt(c.text, c.excerpt)),
      ])));
    }
    // 反馈
    const fb = $(".feedback", tpl);
    const state = $(".feedback-state", tpl);
    const paint = feedback => {
      $$("button", fb).forEach(b => b.classList.toggle("on", !!feedback && String(feedback.helpful) === b.dataset.helpful));
      state.textContent = feedback ? `已记录 · ${fmtTime(feedback.created_at)}` : "";
    };
    paint(rec.feedback);
    $$("button", fb).forEach(b => b.addEventListener("click", async () => {
      try {
        const upd = await postJSON(`/history/${encodeURIComponent(rec.id)}/feedback`, { helpful: b.dataset.helpful === "true" });
        paint(upd.feedback);
      } catch (e) { toast(e.message, true); }
    }));
    if (run) {
      const names = { search_knowledge: "检索资料", read_evidence: "读取原文", check_answer: "检查草稿" };
      const statuses = { completed: "已完成", limited: "达到运行上限", failed: "运行失败", not_configured: "尚未配置 API" };
      $(".diag", tpl).before(el("details", { class: "agent-trace" }, [
        el("summary", { text: `Agent ${statuses[run.status] || run.status} · ${run.steps} 次模型决策 · ${run.tool_calls} 次工具调用` }),
        el("p", { class: "muted", text: "这里展示实际工具调用和核验结果，可展开查看依据。" }),
        ...run.events.map(event => el("details", {}, [
          el("summary", { text: event.kind === "tool" ? `${names[event.tool] || event.tool} · ${event.status === "ok" ? "成功" : "未成功"}` : `最终核验 · ${event.status === "ok" ? "通过" : "需修正"}` }),
          el("pre", { text: JSON.stringify(event.kind === "tool" ? { arguments: event.arguments, observation: event.observation } : { reason: event.reason || "passed" }, null, 2) })
        ])), ...(run.error ? [el("p", { class: "muted", text: "停止原因：" + run.error })] : [])
      ]));
    }
    $(".diag pre", tpl).textContent = JSON.stringify(rec.diagnostics || {}, null, 2);
    $("#answer-area").replaceChildren(tpl);
  }
  async function ask(question) {
    const q = (question || "").trim();
    if (!q || asking) return;
    if (q.length > 500) { toast("问题不能超过 500 字", true); return; }
    asking = true;
    $("#question").value = q;
    const btn = $("#btn-ask");
    btn.disabled = true;
    $("#btn-new-question").disabled = true;
    $("span", btn).textContent = "检索中…";
    $$(".chip").forEach(b => { b.disabled = true; });
      $("#answer-area").replaceChildren(el("p", { class: "loading", text: stats?.agent?.enabled ? "Agent 正在检索并核对原文…" : "正在检索资料…" }));
    try {
      const rec = await postJSON("/ask", { question: q });
      renderAnswer(rec);
      loadStats();
    } catch (e) {
      $("#answer-area").replaceChildren(el("div", { class: "empty-state" }, [el("p", { class: "empty-title", text: "提问失败" }), el("p", { text: e.message })]));
    } finally {
      asking = false; btn.disabled = false;
      $("span", btn).textContent = "发送问题";
      $("#btn-new-question").disabled = false;
      $$(".chip").forEach(b => { b.disabled = false; });
    }
  }
  $("#answer-area").addEventListener("click", ev => {
    const link = ev.target.closest('a[href^="#cite-"]');
    if (!link) return;
    ev.preventDefault();
    const target = document.getElementById(link.getAttribute("href").slice(1));
    if (!target) return;
    $$(".citation-list li").forEach(li => li.classList.toggle("is-focused", li === target));
    target.focus({ preventScroll: true });
    target.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "nearest" });
  });
  $("#btn-new-question").addEventListener("click", () => {
    if (asking) return;
    $("#answer-area").replaceChildren(initialAnswer.cloneNode(true));
    if (stats) $("#answer-empty-docs").hidden = stats.document_count > 0;
    $("#question").value = ""; $("#question").focus();
  });
  $("#form-ask").addEventListener("submit", ev => { ev.preventDefault(); ask($("#question").value); });
  $("#question").addEventListener("keydown", ev => {
    if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); ask($("#question").value); }
  });

  // ---------- 资料 ----------
  let docsCache = [];
  let categories = ["课程资料", "课程文档", "项目文档", "其他"];
  function fillCategorySelects() {
    $$(".sel-category").forEach(sel => sel.replaceChildren(...categories.map(c => el("option", { value: c, text: c }))));
    const f = $("#doc-filter");
    const cur = f.value;
    f.replaceChildren(el("option", { value: "", text: "全部分类" }), ...categories.map(c => el("option", { value: c, text: c })));
    f.value = cur;
  }
  function renderDocuments() {
    const kw = $("#doc-search").value.trim().toLowerCase();
    const cat = $("#doc-filter").value;
    const rows = docsCache.filter(d => (!cat || d.category === cat) && (!kw || d.title.toLowerCase().includes(kw) || d.source_name.toLowerCase().includes(kw)));
    $("#doc-rows").replaceChildren(...rows.map(d => el("tr", { class: "clickable", tabindex: 0, onclick: () => openDrawer(d.id), onkeydown: e => { if (e.key === "Enter") openDrawer(d.id); } }, [
      el("td", { class: "title" }, [d.title, /^\d\d_.*\.md$/.test(d.source_name) ? el("span", { class: "tag sample", text: "示例" }) : null]),
      el("td", {}, [el("span", { class: "tag", text: d.category })]),
      el("td", { class: "muted", text: d.source_name }),
      el("td", { class: "num", text: d.chunk_count }),
      el("td", { class: "muted date-col", text: fmtTime(d.created_at) }),
      el("td", { class: "actions" }, [el("button", { type: "button", class: "btn btn-sm", text: "详情", onclick: e => { e.stopPropagation(); openDrawer(d.id); } })]),
    ])));
    $("#doc-table").hidden = docsCache.length === 0;
    $("#doc-empty").hidden = docsCache.length > 0;
    $("#doc-foot").textContent = docsCache.length ? (rows.length === docsCache.length ? `共 ${docsCache.length} 份资料` : `筛选出 ${rows.length} / ${docsCache.length} 份`) : "";
    $("#doc-summary").textContent = `共 ${docsCache.length} 份资料`;
    if (docsCache.length && !rows.length) {
      $("#doc-rows").append(el("tr", {}, [el("td", { colspan: 6, class: "muted", text: "没有符合条件的资料，请调整关键词或分类。" })]));
    }
  }
  async function loadDocuments() {
    try {
      const data = await api("/documents");
      docsCache = data.documents;
      if (data.categories) { categories = data.categories; fillCategorySelects(); }
      renderDocuments();
    } catch (e) { toast(e.message, true); }
  }
  $("#doc-search").addEventListener("input", debounce(renderDocuments, 150));
  $("#doc-filter").addEventListener("change", renderDocuments);

  $("#btn-load-samples").addEventListener("click", async ev => {
    ev.currentTarget.disabled = true;
    try {
      const r = await api("/sample/load", { method: "POST" });
      toast(r.imported.length ? `已导入 ${r.imported.length} 份示例资料` : "示例资料已存在");
      await Promise.all([loadDocuments(), loadStats()]);
    } catch (e) { toast(e.message, true); } finally { ev.target.disabled = false; }
  });

  // 对话框
  const openDialog = id => { const d = $(id); if (typeof d.showModal === "function") d.showModal(); else d.setAttribute("open", ""); };
  const closeDialog = id => { const d = $(id); if (d.open) d.close(); else d.removeAttribute("open"); };
  $("#btn-import").addEventListener("click", () => openDialog("#dlg-import"));
  $("#btn-new-text").addEventListener("click", () => openDialog("#dlg-text"));
  $$("[data-close]").forEach(b => b.addEventListener("click", () => closeDialog("#" + b.closest("dialog").id)));

  $("#form-upload").addEventListener("submit", async ev => {
    ev.preventDefault();
    const form = ev.currentTarget;
    const fd = new FormData(form);
    if (!fd.getAll("files").some(f => f && f.size)) { toast("请选择至少一个文件", true); return; }
    const btn = $("button[type=submit]", form);
    btn.disabled = true;
    try {
      const r = await api("/import", { method: "POST", body: fd });
      const ok = r.imported.length, bad = r.failed.length;
      toast(bad ? `导入 ${ok} 份，失败 ${bad} 份：${r.failed.map(f => `${f.file}（${f.error}）`).join("；")}` : `已导入 ${ok} 份资料`, bad > 0);
      form.reset();
      closeDialog("#dlg-import");
      await Promise.all([loadDocuments(), loadStats()]);
    } catch (e) { toast(e.message, true); } finally { btn.disabled = false; }
  });
  $("#form-text").addEventListener("submit", async ev => {
    ev.preventDefault();
    const form = ev.currentTarget;
    const fd = new FormData(form);
    const btn = $("button[type=submit]", form);
    btn.disabled = true;
    try {
      const d = await postJSON("/documents", { title: fd.get("title"), content: fd.get("content"), category: fd.get("category") });
      toast(`已保存《${d.title}》，共 ${d.chunk_count} 个分片`);
      form.reset();
      closeDialog("#dlg-text");
      await Promise.all([loadDocuments(), loadStats()]);
    } catch (e) { toast(e.message, true); } finally { btn.disabled = false; }
  });

  // 抽屉
  let drawerDoc = null;
  let drawerReturnFocus = null;
  let previousOverflow = "";
  async function openDrawer(id) {
    try {
      const d = await api(`/documents/${encodeURIComponent(id)}`);
      drawerDoc = d;
      $("#drawer-title").textContent = d.title;
      $("#drawer-meta").replaceChildren(
        el("dt", { text: "分类" }), el("dd", { text: d.category }),
        el("dt", { text: "来源" }), el("dd", { text: d.source_name }),
        el("dt", { text: "导入时间" }), el("dd", { text: fmtTime(d.created_at) }),
        el("dt", { text: "正文" }), el("dd", { text: `${d.char_count} 字` }),
      );
      $("#drawer-chunk-count").textContent = `${d.chunks.length} 个`;
      $("#drawer-chunks").replaceChildren(...d.chunks.map(c => el("li", {}, [c.heading ? el("span", { class: "heading", text: c.heading }) : null, c.text])));
      $("#drawer").hidden = false;
      $("#scrim").hidden = false;
      drawerReturnFocus = document.activeElement;
      previousOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      $(".app-shell").inert = true; $(".rail").inert = true;
      $("#drawer-close").focus();
    } catch (e) { toast(e.message, true); }
  }
  function closeDrawer() {
    $("#drawer").hidden = true; $("#scrim").hidden = true; drawerDoc = null;
    $(".app-shell").inert = false; $(".rail").inert = false;
    document.body.style.overflow = previousOverflow;
    if (drawerReturnFocus && drawerReturnFocus.isConnected) drawerReturnFocus.focus();
  }
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("#drawer").hidden) closeDrawer(); });
  document.addEventListener("keydown", e => {
    if (e.key !== "Tab" || $("#drawer").hidden) return;
    const buttons = $$("button:not(:disabled)", $("#drawer"));
    const first = buttons[0], last = buttons[buttons.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  $("#drawer-delete").addEventListener("click", async () => {
    if (!drawerDoc) return;
    if (!confirm(`删除《${drawerDoc.title}》及其 ${drawerDoc.chunks.length} 个分片？已有问答记录保留。`)) return;
    try {
      await api(`/documents/${encodeURIComponent(drawerDoc.id)}`, { method: "DELETE" });
      toast(`已删除《${drawerDoc.title}》`);
      closeDrawer();
      await Promise.all([loadDocuments(), loadStats()]);
    } catch (e) { toast(e.message, true); }
  });

  // ---------- 记录 ----------
  const hist = { offset: 0, limit: 20, refused: "", q: "", total: 0 };
  async function loadHistory() {
    const params = new URLSearchParams({ limit: hist.limit, offset: hist.offset });
    if (hist.refused !== "") params.set("refused", hist.refused);
    if (hist.q) params.set("q", hist.q);
    try {
      const data = await api(`/history?${params}`);
      hist.total = data.total;
      $("#history-rows").replaceChildren(...data.history.map(h => el("tr", { class: "clickable", tabindex: 0, onclick: () => showRecord(h), onkeydown: e => { if (e.key === "Enter") showRecord(h); } }, [
        el("td", { class: "q", text: h.question }),
        el("td", {}, [h.refused ? el("span", { class: "tag red", text: "资料不足" }) : el("span", { class: "tag green", text: h.mode === "llm" ? "模型生成" : "已回答" })]),
        el("td", { class: "num", text: (h.citations || []).length }),
        el("td", { class: "muted", text: h.feedback ? (h.feedback.helpful ? "有帮助" : "没帮助") : "—" }),
        el("td", { class: "muted date-col", text: fmtTime(h.created_at) }),
      ])));
      $("#history-table").hidden = data.history.length === 0;
      $("#history-empty").hidden = data.history.length > 0;
      const from = data.total ? hist.offset + 1 : 0;
      const to = Math.min(hist.offset + hist.limit, data.total);
      $("#history-count").textContent = data.total ? `第 ${from}–${to} 条，共 ${data.total} 条` : "";
      $("#history-prev").disabled = hist.offset === 0;
      $("#history-next").disabled = to >= data.total;
    } catch (e) { toast(e.message, true); }
  }
  function showRecord(h) { location.hash = "#ask"; renderAnswer(h); window.scrollTo({ top: 0 }); }
  $$(".segmented button").forEach(b => b.addEventListener("click", () => {
    $$(".segmented button").forEach(x => x.classList.toggle("on", x === b));
    hist.refused = b.dataset.refused; hist.offset = 0; loadHistory();
  }));
  $("#history-search").addEventListener("input", debounce(() => { hist.q = $("#history-search").value.trim(); hist.offset = 0; loadHistory(); }, 250));
  $("#history-prev").addEventListener("click", () => { hist.offset = Math.max(0, hist.offset - hist.limit); loadHistory(); });
  $("#history-next").addEventListener("click", () => { hist.offset += hist.limit; loadHistory(); });

  // ---------- 设置 ----------
  const policyLabels = {
    min_segment_coverage: ["词组覆盖率下限", "问题里至少这一比例的词组要在证据分片中出现"],
    segment_hit_fraction: ["词组命中比例", "词组内有这一比例的字对出现即算该词组被覆盖"],
    min_coverage: ["内容词覆盖率下限", "按 idf 加权；资料从未出现的词也计入分母"],
    min_score: ["BM25 最低分", "绝对阈值"],
    relative_floor: ["候选相对下限", "得分低于最佳分 × 该比例的分片不进入重排"],
    max_citations: ["最多引用数", ""],
    max_sentences_per_chunk: ["每片摘录句数", ""],
    min_question_terms: ["问题最少内容词", ""],
    semantic_min_score: ["语义召回门槛", "余弦相似度，数值不是答案正确概率"],
  };
  function renderSettings() {
    if (!stats) return;
    $("#policy-table").replaceChildren(...Object.entries(stats.policy || {}).map(([k, v]) => {
      const [label, help] = policyLabels[k] || [k, ""];
      return el("tr", {}, [el("th", { text: label, title: help }), el("td", { text: k === "semantic_min_score" ? v.toFixed(2) : typeof v === "number" && v < 1 ? `${Math.round(v * 100)}%` : String(v) })]);
    }));
    const llm = stats.llm || {};
    $("#llm-info").replaceChildren(llm.configured
        ? el("p", {}, [el("span", { class: "tag green", text: "兼容 API" }), ` ${llm.model}；${stats.agent?.enabled ? `LangGraph Agent，最多 ${stats.agent.max_steps} 次决策，需支持 tools/tool_calls` : "基础生成模式 llm"}`])
        : el("p", {}, [el("span", { class: "tag", text: "未配置" }), " 未启用回答模型，当前为基础检索预览"]));
    const retrieval = stats.retrieval || {};
    const vector = retrieval.vector_store;
    $("#retrieval-info").replaceChildren(vector
      ? el("div", {}, [el("p", {}, [el("span", { class: `tag ${retrieval.ready ? "green" : "red"}`, text: retrieval.ready ? "已就绪" : "索引异常" }), " BM25 + 语义混合检索"]), el("p", { class: "connection-help", text: `${vector.embedding.model} · ${vector.embedding.dimension} 维` }), el("p", { class: "connection-help", text: `${vector.engine} 本地持久化 · ${vector.chunks} 个向量分片` }), el("p", { class: "model-revision muted", text: `版本 ${vector.embedding.revision.slice(0, 12)}` })])
      : el("p", { text: "当前为词法检索模式；未启用 Embedding 和向量数据库。" }));
    $("#runtime-table").replaceChildren(
      el("tr", {}, [el("th", { text: "数据目录" }), el("td", { text: stats.data_dir || "" })]),
      el("tr", {}, [el("th", { text: "索引" }), el("td", { text: `${stats.index ? stats.index.chunks : 0} 个分片 · 重建于 ${stats.index ? fmtTime(stats.index.rebuilt_at) : "—"}` })]),
      el("tr", {}, [el("th", { text: "问答" }), el("td", { text: `${stats.question_count} 次，其中拒答 ${stats.refused_count} 次` })]),
      el("tr", {}, [el("th", { text: "反馈" }), el("td", { text: stats.feedback_count ? `${stats.helpful_count} / ${stats.feedback_count} 评价为有帮助` : "暂无" })]),
      el("tr", {}, [el("th", { text: "最近提问" }), el("td", { text: stats.last_question_at ? fmtTime(stats.last_question_at) : "—" })]),
    );
  }
  $("#form-token").addEventListener("submit", ev => {
    ev.preventDefault();
    const v = $("#token-input").value.trim();
    if (v) localStorage.setItem(tokenKey, v); else localStorage.removeItem(tokenKey);
    toast(v ? "令牌已保存" : "令牌已清除");
    loadStats();
  });
  $("#token-clear").addEventListener("click", () => { localStorage.removeItem(tokenKey); $("#token-input").value = ""; toast("令牌已清除"); loadStats(); });
  $("#refresh-settings").addEventListener("click", loadStats);
  $("#rebuild-index").addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    $("#rebuild-state").textContent = "正在重建，请稍候…";
    try {
      await postJSON("/index/rebuild", {});
      await loadStats();
      $("#rebuild-state").textContent = "索引已重建，资料与向量已同步。";
    } catch (error) {
      $("#rebuild-state").textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });

  // ---------- 启动 ----------
  $("#token-input").value = getToken();
  fillCategorySelects();
  route();
  loadStats();
  loadSampleQuestions();
})();
