"use strict";

(() => {
  const $ = (id) => document.getElementById(id);

  const ICONS = {
    search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/></svg>',
    logo: '<svg viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/></svg>',
    edit: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="m13.5 6.5 4 4"/></svg>',
    trash: '<svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
    copy: '<svg viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="m5 12 5 5L20 7"/></svg>',
  };

  const EXAMPLES = [
    "What are recent methods for fault detection in electric motor drives?",
    "How is deep learning used for medical image segmentation?",
    "Which factors affect patient experience with remote care?",
    "What are the main mechanisms of catalyst degradation?",
  ];

  const state = {
    conversations: [],
    currentId: null,
    conversation: null,   // {id, title, messages, pending}
    sending: false,
    streamingId: null,    // conversation answered through this tab's open stream
    pollTimer: null,
    searchTimer: null,
    status: null,
  };

  // ------------------------------------------------------------ helpers

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const child of children.flat()) {
      if (child == null || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  const icon = (name) => el("span", { html: ICONS[name], style: "display:contents" });

  async function api(method, url, body) {
    const res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw new Error(await errorText(res));
    return res.json();
  }

  async function errorText(res) {
    try { return (await res.json()).detail || res.statusText; } catch (_) { return res.statusText; }
  }

  const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function inline(text, refPrefix) {
    let s = text;
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\w)/g, "$1<em>$2</em>");
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
    if (refPrefix) {
      s = s.replace(/\[(\d+(?:\s*,\s*\d+)*)\](?!\()/g, (_, nums) => nums.split(/\s*,\s*/)
        .map((n) => `<a class="cite" href="#" data-ref="${refPrefix}-${n}">[${n}]</a>`).join(""));
    }
    return s;
  }

  /** Small, safe Markdown renderer for answers (HTML is escaped first). */
  function renderMarkdown(src, refPrefix) {
    const lines = escapeHtml(src || "").split(/\r?\n/);
    let html = "";
    let para = [];
    let list = null;
    const flushPara = () => {
      if (para.length) html += `<p>${inline(para.join(" "), refPrefix)}</p>`;
      para = [];
    };
    const flushList = () => {
      if (list) {
        const start = list.type === "ol" && list.start !== 1 ? ` start="${list.start}"` : "";
        html += `<${list.type}${start}>${list.items.map((i) => `<li>${inline(i, refPrefix)}</li>`).join("")}</${list.type}>`;
      }
      list = null;
    };
    for (const raw of lines) {
      const line = raw.trim();
      let m;
      if (!line) { flushPara(); flushList(); continue; }
      if ((m = line.match(/^#{1,4}\s+(.*)$/))) { flushPara(); flushList(); html += `<h4>${inline(m[1], refPrefix)}</h4>`; continue; }
      if (/^(-{3,}|\*{3,})$/.test(line)) { flushPara(); flushList(); html += "<hr>"; continue; }
      if ((m = line.match(/^[-*•]\s+(.*)$/)) || (m = line.match(/^(\d+)[.)]\s+(.*)$/))) {
        const type = m.length === 3 ? "ol" : "ul";
        flushPara();
        // Keep the model's numbering when bullet points interrupt a numbered list.
        if (!list || list.type !== type) { flushList(); list = { type, items: [], start: type === "ol" ? Number(m[1]) : 1 }; }
        list.items.push(type === "ol" ? m[2] : m[1]);
        continue;
      }
      flushList();
      para.push(line);
    }
    flushPara();
    flushList();
    return html;
  }

  const md = (text, refPrefix, extraClass) =>
    el("div", { class: `md ${extraClass || ""}`, html: renderMarkdown(text, refPrefix) });

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (_) {
      // Not a secure context (e.g. opened via a LAN IP): fall back.
      const area = el("textarea", { style: "position:fixed;opacity:0" });
      area.value = text;
      document.body.append(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
  }

  function groupLabel(ts) {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
    if (ts >= today) return "Today";
    if (ts >= today - 86400) return "Yesterday";
    if (ts >= today - 7 * 86400) return "Previous 7 days";
    if (ts >= today - 30 * 86400) return "Previous 30 days";
    return new Date(ts * 1000).toLocaleDateString(undefined, { year: "numeric", month: "long" });
  }

  // ------------------------------------------------------------ sidebar

  async function loadConversations() {
    const q = $("search").value.trim();
    try {
      state.conversations = await api("GET", `/api/conversations${q ? `?q=${encodeURIComponent(q)}` : ""}`);
    } catch (_) {
      return;
    }
    renderConversations();
  }

  function renderConversations() {
    const list = $("conv-list");
    list.replaceChildren();
    if (!state.conversations.length) {
      list.append(el("div", { class: "conv-empty" }, $("search").value.trim() ? "No matching chats." : "No chats yet."));
      return;
    }
    let lastGroup = null;
    for (const conv of state.conversations) {
      const group = groupLabel(conv.updated_at);
      if (group !== lastGroup) {
        list.append(el("div", { class: "conv-group" }, group));
        lastGroup = group;
      }
      const item = el("a", {
        class: `conv-item${conv.id === state.currentId ? " active" : ""}`,
        href: `#/c/${conv.id}`,
        title: conv.title,
      },
        el("span", { class: "conv-title" }, conv.title),
        el("span", { class: "conv-actions" },
          el("button", { type: "button", title: "Rename", "aria-label": "Rename", onclick: (e) => { e.preventDefault(); e.stopPropagation(); startRename(item, conv); } }, icon("edit")),
          el("button", { type: "button", title: "Delete", "aria-label": "Delete", onclick: (e) => { e.preventDefault(); e.stopPropagation(); deleteConversation(conv); } }, icon("trash"))));
      item.addEventListener("click", () => closeSidebar());
      list.append(item);
    }
  }

  function startRename(item, conv) {
    const input = el("input", { class: "conv-rename", value: conv.title, maxlength: "200" });
    let done = false;
    const finish = async (save) => {
      if (done) return;
      done = true;
      const title = input.value.trim();
      if (save && title && title !== conv.title) {
        try { await api("PATCH", `/api/conversations/${conv.id}`, { title }); } catch (err) { alert(err.message); }
        if (state.conversation && state.conversation.id === conv.id) {
          state.conversation.title = title;
          $("chat-title").textContent = title;
        }
      }
      loadConversations();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); finish(true); }
      if (e.key === "Escape") { e.preventDefault(); finish(false); }
    });
    input.addEventListener("blur", () => finish(true));
    input.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); });
    item.replaceChildren(input);
    input.focus();
    input.select();
  }

  async function deleteConversation(conv) {
    if (!confirm(`Delete the chat "${conv.title}"?`)) return;
    try {
      await api("DELETE", `/api/conversations/${conv.id}`);
    } catch (err) {
      alert(err.message);
      return;
    }
    if (conv.id === state.currentId) location.hash = "";
    loadConversations();
  }

  function openSidebar() { document.querySelector(".app").classList.add("sidebar-open"); $("scrim").hidden = false; }
  function closeSidebar() { document.querySelector(".app").classList.remove("sidebar-open"); $("scrim").hidden = true; }

  // ------------------------------------------------------------ conversation view

  function routeFromHash() {
    const m = location.hash.match(/^#\/c\/([0-9a-f]+)$/);
    return m ? m[1] : null;
  }

  async function showRoute() {
    clearTimeout(state.pollTimer);
    const id = routeFromHash();
    state.currentId = id;
    if (!id) {
      state.conversation = null;
      renderThread();
      renderConversations();
      $("input").focus();
      return;
    }
    try {
      state.conversation = await api("GET", `/api/conversations/${id}`);
    } catch (_) {
      location.hash = "";
      return;
    }
    renderThread();
    renderConversations();
    scrollToBottom(true);
    schedulePoll();
  }

  function schedulePoll() {
    clearTimeout(state.pollTimer);
    const conv = state.conversation;
    if (!conv || !conv.pending || state.streamingId === conv.id) return;
    // Answer running from another tab or before a reload: follow it.
    state.pollTimer = setTimeout(async () => {
      if (!state.conversation || state.conversation.id !== conv.id) return;
      try {
        state.conversation = await api("GET", `/api/conversations/${conv.id}`);
      } catch (_) { return; }
      if (state.conversation.pending) updatePending(); else { renderThread(); scrollToBottom(); }
      schedulePoll();
    }, 1200);
  }

  function renderThread() {
    const conv = state.conversation;
    const box = $("messages");
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    box.replaceChildren();
    $("chat-title").textContent = conv ? conv.title : "New chat";
    $("export-btn").hidden = !(conv && conv.messages.length);

    if (!conv || (!conv.messages.length && !conv.pending)) {
      box.append(renderWelcome());
      updateComposer();
      return;
    }
    const thread = el("div", { class: "thread" });
    for (const msg of conv.messages) thread.append(msg.role === "user" ? renderUser(msg) : renderAssistant(msg));
    if (conv.pending) thread.append(renderPending(conv.pending));
    box.append(thread);
    if (atBottom) scrollToBottom(true);
    updateComposer();
  }

  function renderWelcome() {
    const info = state.status && state.status.index;
    let sub = "Answers are grounded in the papers of the local index, with citations.";
    if (info && info.papers) sub = `Ask about anything covered by the ${Number(info.papers).toLocaleString()} indexed papers. Answers come with citations.`;
    return el("div", { class: "welcome" },
      el("h2", {}, "What would you like to research?"),
      el("p", {}, sub),
      el("div", { class: "examples" },
        EXAMPLES.map((q) => el("button", { class: "example", type: "button", onclick: () => ask(q) }, q))));
  }

  const renderUser = (msg) => el("div", { class: "msg-user" }, msg.content);

  function sectionLabel(text) { return el("div", { class: "section-label" }, text); }

  function renderAssistant(msg) {
    const data = msg.data || {};
    const prefix = `m${msg.id}`;
    const body = el("div", { class: "msg-body" });

    if (data.status === "answered") {
      if (data.standalone_question) {
        body.append(el("div", { class: "searched", title: "Your follow-up question was searched as" },
          icon("search"), `Searched for: ${data.standalone_question}`));
      }
      if (data.notice) body.append(el("div", { class: "notice no_results compact" }, data.notice));
      body.append(sectionLabel("Summary"), md(data.summary, null, "summary"));
      body.append(sectionLabel("Detailed answer"), md(data.answer, prefix));
      if (data.references && data.references.length) {
        body.append(sectionLabel("References"), renderReferences(data.references, prefix));
      }
      if (data.followups && data.followups.length) {
        body.append(sectionLabel("Related questions"), el("div", { class: "followups" },
          data.followups.map((q) => el("button", { class: "followup", type: "button", disabled: state.sending, onclick: () => ask(q) }, q))));
      }
    } else if (data.status) {
      body.append(el("div", { class: `notice ${data.status}` }, md(data.message || msg.content)));
    } else {
      body.append(md(msg.content));
    }

    const copyBtn = el("button", { class: "icon-btn", type: "button", title: "Copy answer", "aria-label": "Copy answer" }, icon("copy"));
    copyBtn.addEventListener("click", async () => {
      await copyText(msg.content);
      copyBtn.replaceChildren(icon("check"));
      setTimeout(() => copyBtn.replaceChildren(icon("copy")), 1500);
    });
    body.append(el("div", { class: "msg-actions" }, copyBtn,
      data.seconds ? `Answered in ${data.seconds} s` : null));

    return el("div", { class: "msg-assistant" }, el("div", { class: "avatar", html: ICONS.logo }), body);
  }

  function renderReferences(refs, prefix) {
    return el("ol", { class: "refs" }, refs.map((r) => {
      const title = r.doi
        ? el("a", { class: "ref-title", href: `https://doi.org/${r.doi}`, target: "_blank", rel: "noopener" }, r.title)
        : el("span", { class: "ref-title" }, r.title);
      const meta = [r.journal, r.year].filter(Boolean).join(" · ");
      const pct = Math.max(0, Math.min(100, Math.round((r.score || 0) * 100)));
      return el("li", { class: "ref", id: `ref-${prefix}-${r.n}` },
        el("span", { class: "ref-n" }, `[${r.n}]`),
        el("div", { class: "ref-main" },
          title,
          meta ? el("div", { class: "ref-meta" }, meta) : null,
          el("div", { class: "ref-score", title: "Relevance to the question (reranker score)" },
            el("span", { class: "ref-bar" }, el("span", { style: `width:${pct}%` })), `relevance ${pct}%`)));
    }));
  }

  function renderPending(p) {
    const body = el("div", { class: "msg-body", id: "pending" },
      el("div", { class: "pending-stage" }, el("span", { class: "spinner" }), el("span", { id: "pending-text" }, p.text || "Working…")),
      el("div", { class: "md streaming", id: "pending-partial", hidden: !p.partial, html: renderMarkdown(p.partial || "") }));
    return el("div", { class: "msg-assistant" }, el("div", { class: "avatar", html: ICONS.logo }), body);
  }

  function updatePending() {
    const p = state.conversation && state.conversation.pending;
    if (!p) return;
    if (!$("pending")) { renderThread(); return; }
    const box = $("messages");
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    $("pending-text").textContent = p.text || "Working…";
    const partial = $("pending-partial");
    partial.hidden = !p.partial;
    if (p.partial) partial.innerHTML = renderMarkdown(p.partial);
    if (atBottom) scrollToBottom(true);
  }

  function scrollToBottom(instant) {
    const box = $("messages");
    box.scrollTo({ top: box.scrollHeight, behavior: instant ? "auto" : "smooth" });
  }

  // ------------------------------------------------------------ asking

  async function ask(text) {
    text = (text || "").trim();
    if (!text || state.sending) return;
    if (state.conversation && state.conversation.pending) return;

    state.sending = true;
    $("input").value = "";
    autosize();
    let id = state.currentId;

    try {
      if (!id) {
        const conv = await api("POST", "/api/conversations");
        id = conv.id;
        state.currentId = id;
        state.conversation = { ...conv, messages: [], pending: null };
        history.replaceState(null, "", `#/c/${id}`);
      }
      const conv = state.conversation;
      conv.messages.push({ id: "pending-user", role: "user", content: text });
      conv.pending = { stage: "queued", text: "Starting…", partial: "" };
      state.streamingId = id;
      renderThread();
      scrollToBottom(true);

      const res = await fetch(`/api/conversations/${id}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text }),
      });
      if (!res.ok) throw new Error(await errorText(res));
      await readEvents(res, (event, data) => onEvent(id, event, data));
    } catch (err) {
      if (state.currentId === id && state.conversation) {
        const conv = state.conversation;
        conv.pending = null;
        conv.messages = conv.messages.filter((m) => m.id !== "pending-user");
        const text = `⚠️ ${err.message}`;
        conv.messages.push({ id: `err${Date.now()}`, role: "assistant", content: text, data: { status: "error", message: text } });
        renderThread();
      }
    } finally {
      state.sending = false;
      state.streamingId = null;
      updateComposer();
      loadConversations();
      if (state.currentId === id) schedulePoll();
    }
  }

  function onEvent(id, event, data) {
    const conv = state.conversation;
    if (!conv || conv.id !== id) return;  // user switched to another chat meanwhile
    if (event === "user") {
      const tmp = conv.messages.find((m) => m.id === "pending-user");
      if (tmp) Object.assign(tmp, data.message);
      loadConversations();
    } else if (event === "stage") {
      conv.pending = { ...(conv.pending || {}), stage: data.stage, text: data.text };
      updatePending();
    } else if (event === "delta") {
      conv.pending = { ...(conv.pending || {}), partial: data.text };
      updatePending();
    } else if (event === "done" || event === "error") {
      conv.pending = null;
      if (!conv.messages.some((m) => m.id === data.message.id)) conv.messages.push(data.message);
      renderThread();
    }
  }

  /** Read a Server-Sent Events response (EventSource cannot POST). */
  async function readEvents(res, onEvent) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        let event = "message";
        let data = "";
        for (const line of chunk.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (data) onEvent(event, JSON.parse(data));
      }
    }
  }

  function updateComposer() {
    const busy = state.sending || !!(state.conversation && state.conversation.pending);
    $("send").disabled = busy || !$("input").value.trim();
    $("input").placeholder = busy ? "Waiting for the answer…" : "Ask a research question…";
    document.querySelectorAll(".followup").forEach((b) => { b.disabled = busy; });
  }

  function autosize() {
    const input = $("input");
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
    updateComposer();
  }

  // ------------------------------------------------------------ status

  async function loadStatus() {
    try {
      state.status = await api("GET", "/api/status");
    } catch (_) {
      $("index-info").textContent = "Search service unreachable";
      return;
    }
    const info = state.status.index || {};
    if (info.papers) {
      $("index-info").textContent = `${Number(info.papers).toLocaleString()} papers · built ${String(info.built_at || "").slice(0, 10)}`;
    } else if (info.ready) {
      $("index-info").textContent = "Index ready";
    } else {
      $("index-info").textContent = "No index yet – use the embedding service";
    }
    $("embed-link").href = `${location.protocol}//${location.hostname}:${state.status.embed_ui_port}/`;
    if (!state.conversation) renderThread();
  }

  // ------------------------------------------------------------ wiring

  $("composer").addEventListener("submit", (e) => { e.preventDefault(); ask($("input").value); });
  $("input").addEventListener("input", autosize);
  $("input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      ask($("input").value);
    }
  });
  $("new-chat").addEventListener("click", () => { location.hash = ""; closeSidebar(); $("input").focus(); });
  $("search").addEventListener("input", () => {
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(loadConversations, 250);
  });
  $("clear-all").addEventListener("click", async () => {
    if (!confirm("Delete all chats? This cannot be undone.")) return;
    try { await api("DELETE", "/api/conversations"); } catch (err) { alert(err.message); return; }
    location.hash = "";
    loadConversations();
  });
  $("export-btn").addEventListener("click", () => {
    if (state.currentId) location.href = `/api/conversations/${state.currentId}/export`;
  });
  $("open-sidebar").addEventListener("click", openSidebar);
  $("close-sidebar").addEventListener("click", closeSidebar);
  $("scrim").addEventListener("click", closeSidebar);
  $("messages").addEventListener("click", (e) => {
    const cite = e.target.closest(".cite");
    if (!cite) return;
    e.preventDefault();
    const target = document.getElementById(`ref-${cite.dataset.ref}`);
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "nearest" });
    target.classList.add("flash");
    setTimeout(() => target.classList.remove("flash"), 1400);
  });
  window.addEventListener("hashchange", showRoute);

  loadStatus();
  setInterval(loadStatus, 30000);
  loadConversations();
  showRoute();
})();
