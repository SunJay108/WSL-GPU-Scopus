"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const { el, api, md, fmtDateTime, logout } = window.UI;
  const { t } = window.I18N;

  const state = { me: null, users: [], actions: {}, lastId: null, chatId: null, bundlesDir: "", timers: {} };

  // Answer status of an "ask" entry: [text key, badge class].
  const STATUS_BADGE = {
    answered: ["status.answered", "ok"],
    no_results: ["status.no_results", "warn"],
    no_index: ["status.no_index", "warn"],
    blocked: ["status.blocked", "err"],
    unavailable: ["status.unavailable", "err"],
    error: ["status.error", "err"],
  };

  function badge(text, cls = "") { return el("span", { class: `badge ${cls}` }, text); }

  const debounce = (key, fn) => {
    clearTimeout(state.timers[key]);
    state.timers[key] = setTimeout(fn, 300);
  };

  // Activity labels come from the server in English; zh.js translates them by action key.
  const actionLabel = (key, fallback) => (I18N.has(`action.${key}`) ? t(`action.${key}`) : fallback || key);
  const roleName = (role) => (I18N.has(`role.${role}`) ? t(`role.${role}`) : role);

  // ------------------------------------------------------------ tabs

  function currentTab() {
    const selected = document.querySelector('.admin-tabs button[aria-selected="true"]');
    return selected ? selected.dataset.tab : "activity";
  }

  function showTab(name) {
    document.querySelectorAll(".admin-tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    for (const tab of ["activity", "chats", "users", "index"]) $(`tab-${tab}`).hidden = tab !== name;
    if (name === "chats") loadChats();
    if (name === "users") loadUsers();
    if (name === "index") loadIndex();
    else clearTimeout(state.timers.index);
  }

  // ------------------------------------------------------------ overview

  async function loadStats() {
    const s = await api("GET", "/api/admin/stats");
    const cards = [
      [s.users, t("stats.users")],
      [s.active_today, t("stats.activeToday")],
      [s.chats, t("stats.chats", { deleted: s.deleted_chats })],
      [s.questions, t("stats.questions", { today: s.questions_today })],
      [s.blocked, t("stats.blocked")],
      [s.failed_logins_today, t("stats.failedLogins")],
    ];
    $("stats").replaceChildren(...cards.map(([value, label]) =>
      el("div", { class: "stat" }, el("strong", {}, Number(value).toLocaleString()), el("span", {}, label))));
  }

  async function loadUserOptions() {
    state.users = await api("GET", "/api/admin/users");
    for (const id of ["act-user", "chat-user"]) {
      const select = $(id);
      const current = select.value;
      select.replaceChildren(el("option", { value: "" }, t("f.allUsers")),
        ...state.users.map((u) => el("option", { value: u.id }, u.username)));
      select.value = current;
    }
  }

  function renderActionOptions() {
    const select = $("act-action");
    const current = select.value;
    select.replaceChildren(el("option", { value: "" }, t("f.allActions")),
      ...Object.entries(state.actions).map(([k, label]) => el("option", { value: k }, actionLabel(k, label))));
    select.value = current;
  }

  // ------------------------------------------------------------ activity

  function describe(item) {
    const d = item.detail || {};
    switch (item.action) {
      case "ask": {
        const [key, cls] = STATUS_BADGE[d.status] || [null, ""];
        const label = key ? t(key) : d.status || "?";
        const note = d.status === "blocked" && d.category ? `${label}: ${d.category.toLowerCase().replace("_", "-")}` : label;
        return [
          el("div", { class: "detail-main" }, d.question || ""),
          el("div", { class: "detail-sub" }, badge(note, cls),
            d.searched_as ? t("act.searchedAs", { q: d.searched_as }) : "",
            d.reason && d.status === "blocked" ? ` · ${d.reason}` : "",
            d.removed_sentences ? t("act.removed", { n: d.removed_sentences }) : "",
            d.seconds ? ` · ${t("dur.s", { s: d.seconds })}` : ""),
        ];
      }
      case "rename_chat":
        return [el("div", { class: "detail-main" }, `“${d.old}” → “${d.new}”`)];
      case "delete_chat":
        return [el("div", { class: "detail-main" }, `“${d.title}”`), el("div", { class: "detail-sub" }, t("act.questions", { n: d.questions || 0 }))];
      case "clear_history":
        return [el("div", { class: "detail-main" }, t("act.chatsDeleted", { n: d.chats || 0 }))];
      case "export_chat":
        return [el("div", { class: "detail-main" }, `“${d.title}”`)];
      case "login_failed":
        return [el("div", { class: "detail-sub" }, [serviceName(d), d.reason].filter(Boolean).join(" · "))];
      case "signup":
        return [el("div", { class: "detail-sub" }, t("act.role", { service: serviceName(d), role: roleName(d.role) }))];
      case "login":
      case "logout":
      case "password_change":
        return [el("div", { class: "detail-sub" }, serviceName(d))];
      case "upload_folder":
        return [el("div", { class: "detail-main" }, d.folder || "")];
      case "ingest_start": {
        const what = d.tables && d.tables.length ? t("act.tables", { n: d.tables.length }) : t("act.allTables");
        return [el("div", { class: "detail-main" }, d.folder || ""),
          el("div", { class: "detail-sub" }, `${t(d.mode === "append" ? "act.addToIndex" : "act.replaceIndex")} · ${what}${d.include_ris ? " + RIS" : ""}`)];
      }
      case "ingest_completed": {
        const s = d.summary || {};
        return [el("div", { class: "detail-main" }, d.folder || ""),
          el("div", { class: "detail-sub" },
            t("act.ingestDone", { added: Number(s.papers_added || 0), total: Number(s.papers_total || 0), s: Math.round(s.seconds || 0) }))];
      }
      case "ingest_failed":
        return [el("div", { class: "detail-main" }, d.folder || ""), el("div", { class: "detail-sub" }, d.error || "")];
      case "ingest_cancel":
      case "ingest_cancelled":
        return [el("div", { class: "detail-main" }, d.folder || (d.job ? t("act.job", { id: String(d.job) }) : ""))];
      case "index_import_start":
        return [el("div", { class: "detail-main" }, d.bundle || ""),
          el("div", { class: "detail-sub" }, [d.papers ? t("act.papers", { n: Number(d.papers) }) : "",
            d.add ? t("act.added") : "",
            d.low_memory ? t("act.lowMemory") : ""].filter(Boolean).join(" · "))];
      case "index_import_completed":
        return [el("div", { class: "detail-main" }, d.bundle || ""),
          el("div", { class: "detail-sub" }, d.mode === "add"
            ? t("act.importAdded", { new: Number(d.papers_new || 0), replaced: Number(d.papers_replaced || 0), s: Math.round(d.seconds || 0) })
            : t("act.imported", { papers: Number(d.papers || 0), chunks: Number(d.chunks || 0), s: Math.round(d.seconds || 0) }))];
      case "index_import_failed":
        return [el("div", { class: "detail-main" }, d.bundle || ""), el("div", { class: "detail-sub" }, d.error || "")];
      case "bundle_deleted":
        return [el("div", { class: "detail-main" }, d.bundle || "")];
      default:
        if (d.target) {
          const extra = d.role ? ` → ${roleName(d.role)}${d.previous ? t("act.was", { role: roleName(d.previous) }) : ""}` : "";
          return [el("div", { class: "detail-main" }, `${d.target}${extra}`)];
        }
        return [];
    }
  }

  const serviceName = (d) => (d.service === "embedding" ? t("act.serviceEmbedding") : d.service === "search" ? t("act.serviceSearch") : "");

  function actionBadge(item) {
    const a = item.action;
    const cls = a === "login_failed" || a === "ingest_failed" || a === "index_import_failed" ? "err"
      : a === "ingest_completed" || a === "index_import_completed" ? "ok"
        : a.startsWith("admin_") || a.startsWith("ingest_") || a.startsWith("index_") || a === "upload_folder" ? "accent"
          : a.startsWith("delete") || a === "clear_history" ? "warn" : "";
    return badge(actionLabel(a, item.action_label), cls);
  }

  async function loadActivity(more = false) {
    if (!more) state.lastId = null;
    const params = new URLSearchParams({ limit: "100" });
    if ($("act-user").value) params.set("user_id", $("act-user").value);
    if ($("act-action").value) params.set("action", $("act-action").value);
    if ($("act-q").value.trim()) params.set("q", $("act-q").value.trim());
    if (more && state.lastId) params.set("before", state.lastId);

    const data = await api("GET", `/api/admin/activity?${params}`);
    if (!Object.keys(state.actions).length) {
      state.actions = data.actions;
      renderActionOptions();
    }

    const rows = data.items.map((item) => el("tr", {},
      el("td", { class: "nowrap" }, fmtDateTime(item.created_at)),
      el("td", { class: "nowrap" }, item.username || "–"),
      el("td", {}, actionBadge(item)),
      el("td", {}, describe(item)),
      el("td", { class: "nowrap" }, item.conversation_id
        ? [el("button", { class: "link-btn", type: "button", onclick: () => openChat(item.conversation_id, true) }, t("act.open")),
          item.conversation_deleted_at ? [" ", badge(t("act.deleted"), "warn")] : null]
        : "")));

    if (more) $("act-rows").append(...rows); else $("act-rows").replaceChildren(...rows);
    if (!more && !rows.length) $("act-rows").append(el("tr", {}, el("td", { colspan: "5", class: "muted" }, t("act.none"))));
    if (data.items.length) state.lastId = data.items[data.items.length - 1].id;
    $("act-more").hidden = data.items.length < 100;
    $("act-count").textContent = t("act.count", { n: $("act-rows").querySelectorAll("tr").length });
  }

  // ------------------------------------------------------------ chats

  async function loadChats() {
    const params = new URLSearchParams({ status: $("chat-status").value });
    if ($("chat-user").value) params.set("user_id", $("chat-user").value);
    if ($("chat-q").value.trim()) params.set("q", $("chat-q").value.trim());
    const chats = await api("GET", `/api/admin/conversations?${params}`);
    $("chat-rows").replaceChildren(...(chats.length ? chats.map((c) => el("tr", {
      class: `clickable${c.id === state.chatId ? " selected" : ""}`,
      "data-id": c.id,
      onclick: () => openChat(c.id),
    },
      el("td", {}, c.title, c.deleted_at ? [" ", badge(t("act.deleted"), "warn")] : null),
      el("td", { class: "nowrap" }, c.username || "–"),
      el("td", { class: "num" }, c.questions),
      el("td", { class: "nowrap" }, fmtDateTime(c.updated_at)))) : [el("tr", {}, el("td", { colspan: "4", class: "muted" }, t("chats.none")))]));
  }

  async function openChat(id, switchTab = false) {
    state.chatId = id;
    if (switchTab) showTab("chats");
    let conv;
    try {
      conv = await api("GET", `/api/admin/conversations/${id}`);
    } catch (err) {
      $("viewer").replaceChildren(el("p", { class: "muted viewer-empty" }, err.message));
      return;
    }
    document.querySelectorAll("#chat-rows tr").forEach((tr) => tr.classList.toggle("selected", tr.dataset.id === id));
    // The messages are shown as written; only the frame around them is translated.
    const thread = el("div", { class: "thread" }, conv.messages.map((m) => m.role === "user"
      ? el("div", { class: "msg-user" }, m.content, el("div", { class: "msg-time" }, fmtDateTime(m.created_at)))
      : el("div", {}, md(m.content), el("div", { class: "msg-time" }, fmtDateTime(m.created_at)))));
    $("viewer").replaceChildren(
      el("h2", {}, conv.title),
      el("div", { class: "viewer-meta" },
        t("chats.meta", { user: conv.username || "–", time: fmtDateTime(conv.created_at) }),
        conv.deleted_at ? badge(t("chats.deletedBy", { time: fmtDateTime(conv.deleted_at) }), "warn") : badge(t("chats.active"), "ok")),
      conv.messages.length ? thread : el("p", { class: "muted" }, t("chats.noMessages")));
    $("viewer").scrollTop = 0;
  }

  // ------------------------------------------------------------ users

  async function loadUsers() {
    await loadUserOptions();
    $("user-rows").replaceChildren(...state.users.map((u) => {
      const self = state.me && u.id === state.me.id;
      const actions = el("div", { class: "row-actions" },
        el("button", { class: "btn", type: "button", disabled: self, onclick: () => updateUser(u, { disabled: !u.disabled }) },
          t(u.disabled ? "users.enable" : "users.disable")),
        el("button", { class: "btn", type: "button", disabled: self, onclick: () => updateUser(u, { role: u.role === "admin" ? "user" : "admin" }) },
          t(u.role === "admin" ? "users.makeUser" : "users.makeAdmin")),
        el("button", { class: "btn", type: "button", onclick: () => resetPassword(u) }, t("users.resetPassword")),
        el("button", { class: "btn", type: "button", onclick: () => { $("act-user").value = u.id; showTab("activity"); loadActivity(); } }, t("users.activity")),
        el("button", { class: "btn", type: "button", onclick: () => { $("chat-user").value = u.id; showTab("chats"); } }, t("users.chats")));
      return el("tr", {},
        el("td", { class: "nowrap" }, u.username, self ? el("span", { class: "muted" }, t("users.you")) : null),
        el("td", {}, badge(roleName(u.role), u.role === "admin" ? "accent" : "")),
        el("td", {}, u.disabled ? badge(t("users.disabled"), "err") : badge(t("users.active"), "ok")),
        el("td", { class: "nowrap" }, fmtDateTime(u.created_at)),
        el("td", { class: "nowrap" }, fmtDateTime(u.last_login_at)),
        el("td", { class: "nowrap" }, fmtDateTime(u.last_activity)),
        el("td", { class: "num" }, u.deleted_chats ? t("users.chatsDeleted", { chats: u.chats, deleted: u.deleted_chats }) : u.chats),
        el("td", { class: "num" }, u.questions),
        el("td", {}, actions));
    }));
  }

  async function updateUser(user, change) {
    const key = "disabled" in change
      ? (change.disabled ? "users.confirmDisable" : "users.confirmEnable")
      : (change.role === "admin" ? "users.confirmMakeAdmin" : "users.confirmMakeUser");
    if (!confirm(t(key, { name: user.username }))) return;
    try {
      await api("PATCH", `/api/admin/users/${user.id}`, change);
    } catch (err) {
      alert(err.message);
    }
    loadUsers();
    loadStats();
  }

  async function resetPassword(user) {
    const password = prompt(t("users.newPassword", { name: user.username }));
    if (!password) return;
    try {
      await api("PATCH", `/api/admin/users/${user.id}`, { password });
      alert(t("users.passwordChanged", { name: user.username }));
    } catch (err) {
      alert(err.message);
    }
  }

  async function addUser(e) {
    e.preventDefault();
    try {
      await api("POST", "/api/admin/users", {
        username: $("new-username").value.trim(),
        password: $("new-password").value,
        role: $("new-role").value,
      });
    } catch (err) {
      alert(err.message);
      return;
    }
    $("add-user").reset();
    loadUsers();
    loadStats();
  }

  // ------------------------------------------------------------ index

  const fmtNum = (n) => (n == null ? "–" : Number(n).toLocaleString());

  function fmtBytes(n) {
    if (!n) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1);
    return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`;
  }

  function fmtDuration(sec) {
    sec = Math.max(0, Math.round(sec || 0));
    const m = Math.floor(sec / 60), s = sec % 60;
    return m ? t("dur.ms", { m, s }) : t("dur.s", { s });
  }

  async function loadIndex() {
    clearTimeout(state.timers.index);
    let data;
    try {
      data = await api("GET", "/api/admin/index");
    } catch (err) {
      $("index-line").textContent = err.message;
      return;
    }
    renderIndex(data);
    const running = data.import.state === "running";
    if (!$("tab-index").hidden) state.timers.index = setTimeout(loadIndex, running ? 2000 : 15000);
    if (state.importRunning && !running) loadStats();
    state.importRunning = running;
  }

  function renderIndex(data) {
    const idx = data.index;
    const ex = data.import;
    const running = ex.state === "running";
    $("index-stats").replaceChildren(...[
      [fmtNum(idx.papers), t("index.papers")],
      [fmtNum(idx.points), t("index.chunks")],
      [String((idx.sources || []).length), t("index.sourcesCount")],
      [idx.embed_model || "–", t("index.model")],
    ].map(([value, label]) => el("div", { class: "stat" }, el("strong", {}, value), el("span", {}, label))));

    const line = [];
    if (idx.error) line.push(idx.error);
    else if (!idx.collection) line.push(t("index.none"));
    else {
      if (idx.version) line.push(t("index.version", { v: String(idx.version) }));
      if (idx.built_at) line.push(t("index.built", { at: idx.built_at }));
      if (idx.imported_at) {
        line.push(idx.imported_by
          ? t("index.importedBy", { at: idx.imported_at, user: idx.imported_by })
          : t("index.imported", { at: idx.imported_at }));
      }
      const added = idx.added || [];
      if (added.length) line.push(t("index.added", { n: added.length, at: added[added.length - 1].added_at }));
      line.push(t("index.collection", { name: idx.collection, status: idx.status }));
      if (idx.format && idx.format !== data.index_format) line.push(t("index.format", { format: String(idx.format), app: String(data.index_format) }));
    }
    $("index-line").textContent = line.join(" · ");

    const q = idx.quality;
    $("index-quality").hidden = !q;
    if (q) {
      const low = Math.min(q.keyword_found, q.vector_found) < 0.8 * q.sample;
      $("index-quality").className = `index-quality${low ? " low" : ""}`;
      $("index-quality").textContent = t("index.quality", {
        mark: low ? "⚠️" : "✓", sample: q.sample, kw: q.keyword_found, vec: q.vector_found, top: q.top,
      });
    }
    const sources = idx.sources || [];
    $("index-sources").hidden = !sources.length;
    $("index-source-rows").replaceChildren(...sources.map((s) => el("tr", {},
      el("td", {}, s.name), el("td", { class: "num" }, t("index.sourcePapers", { n: fmtNum(s.papers) })), el("td", { class: "muted nowrap" }, s.added_at || ""))));

    state.bundlesDir = data.bundles_dir;
    $("bundles-dir").textContent = data.bundles_dir;
    const banner = $("import-banner");
    banner.hidden = !ex.state || ex.state === "idle";
    banner.className = `banner ${ex.state}`;
    const elapsed = ex.started_at ? fmtDuration((ex.finished_at || Date.now() / 1000) - ex.started_at) : "";
    let text = "";
    if (running) text = t(ex.add ? "import.runningAdd" : "import.running", { bundle: ex.bundle, step: ex.step, elapsed });
    else if (ex.state === "completed") {
      const r = ex.result || {};
      text = r.mode === "add"
        ? t("import.doneAdd", { bundle: r.bundle, elapsed, new: fmtNum(r.papers_new), replaced: fmtNum(r.papers_replaced), total: fmtNum(r.index_papers) })
        : t("import.done", { bundle: r.bundle, elapsed, papers: fmtNum(r.papers) });
    } else if (ex.state === "failed") text = t(ex.add ? "import.failedAdd" : "import.failed", { bundle: ex.bundle, error: ex.error });
    $("import-text").textContent = text;
    const withBar = running && ex.total > 1;
    $("import-bar-wrap").hidden = !withBar;
    if (withBar) $("import-bar").style.width = `${(100 * ex.done) / ex.total}%`;
    $("low-memory").disabled = running;

    const rows = data.bundles.map((b) => {
      const ok = !b.problems.length;
      // The problems are server messages and stay in English.
      const status = b.in_use ? badge(t("bundle.inUse"), "accent") : ok ? badge(t("bundle.ready"), "ok")
        : [badge(t("bundle.cannot"), "err"), el("ul", { class: "problems" }, b.problems.map((p) => el("li", {}, p)))];
      return el("tr", {},
        el("td", {}, b.name),
        el("td", { class: "num" }, fmtNum(b.papers)),
        el("td", { class: "num" }, fmtNum(b.chunks)),
        el("td", { class: "num nowrap" }, fmtBytes(b.size)),
        el("td", { class: "nowrap" }, b.created_at || ""),
        el("td", {}, status),
        el("td", {}, el("div", { class: "row-actions" },
          el("button", { class: "btn-primary", type: "button", title: t("bundle.replaceTitle"), disabled: running || !ok || b.in_use, onclick: () => startImport(b, idx, false) }, t("bundle.replace")),
          el("button", { class: "btn", type: "button", title: t("bundle.addTitle"), disabled: running || !ok || b.in_use, onclick: () => startImport(b, idx, true) }, t("bundle.add")),
          el("button", { class: "btn", type: "button", disabled: running && ex.bundle === b.name, onclick: () => deleteBundle(b) }, t("bundle.delete")))));
    });
    $("bundle-rows").replaceChildren(...(rows.length ? rows
      : [el("tr", {}, el("td", { colspan: "7", class: "muted" }, t("bundle.none")))]));
  }

  async function startImport(bundle, idx, add) {
    const lowMemory = !add && $("low-memory").checked;
    const sizeGb = bundle.size / 2 ** 30;
    let question;
    if (add && idx.collection) {
      // Loading the snapshot, then copying the smaller side (about 20 s per 1,000 chunks on a hard disk).
      const intoBundle = (bundle.chunks || 0) > (idx.points || 0);
      const copied = Math.min(bundle.chunks || 0, idx.points || 0);
      const minutes = Math.max(1, Math.round(sizeGb * 1.7 + copied / 3000));
      const how = intoBundle
        ? t("import.howIntoBundle", { chunks: fmtNum(idx.points) })
        : t("import.howIntoIndex", { chunks: fmtNum(bundle.chunks) });
      question = t("import.confirmAdd", { bundle: bundle.name, papers: fmtNum(bundle.papers), current: fmtNum(idx.papers), how, min: minutes });
    } else {
      const minutes = Math.max(1, Math.round(sizeGb * 1.7));
      const how = t(lowMemory ? "import.howLowMemory" : "import.howKeep");
      question = idx.collection
        ? t("import.confirmReplace", { bundle: bundle.name, papers: fmtNum(bundle.papers), current: fmtNum(idx.papers), how, min: minutes })
        : t("import.confirmNew", { bundle: bundle.name, papers: fmtNum(bundle.papers), how, min: minutes });
    }
    if (!confirm(question)) return;
    try {
      await api("POST", "/api/admin/index/import", { bundle: bundle.name, low_memory: lowMemory, add });
    } catch (err) {
      alert(t("import.cannotStart", { error: err.message }));
    }
    loadIndex();
  }

  async function deleteBundle(bundle) {
    const note = bundle.in_use ? t("bundle.deleteNote") : "";
    if (!confirm(t("bundle.confirmDelete", { bundle: bundle.name, size: fmtBytes(bundle.size), note }))) return;
    try {
      await api("DELETE", `/api/admin/index/bundles/${encodeURIComponent(bundle.name)}`);
    } catch (err) {
      alert(err.message);
    }
    loadIndex();
  }

  // ------------------------------------------------------------ start

  UI.setUnauthorizedHandler(() => { location.href = "/"; });

  document.querySelectorAll(".admin-tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  $("logout").addEventListener("click", logout);
  $("act-user").addEventListener("change", () => loadActivity());
  $("act-action").addEventListener("change", () => loadActivity());
  $("act-q").addEventListener("input", () => debounce("act", () => loadActivity()));
  $("act-more").addEventListener("click", () => loadActivity(true));
  $("chat-user").addEventListener("change", loadChats);
  $("chat-status").addEventListener("change", loadChats);
  $("chat-q").addEventListener("input", () => debounce("chat", loadChats));
  $("add-user").addEventListener("submit", addUser);

  // Language switched: rebuild what this script wrote (the HTML texts are done by i18n.js).
  I18N.onChange(() => {
    // The import hint was rebuilt from the text and lost the folder name.
    $("bundles-dir").textContent = state.bundlesDir;
    if (!state.me || $("admin-main").hidden) return;
    renderActionOptions();
    loadStats();
    loadUserOptions();
    loadActivity();
    const tab = currentTab();
    if (tab === "chats") {
      loadChats();
      if (state.chatId) openChat(state.chatId);
    }
    if (tab === "users") loadUsers();
    if (tab === "index") loadIndex();
  });

  (async () => {
    const authState = await api("GET", "/api/auth/state");
    if (!authState.user) { location.href = "/"; return; }
    state.me = authState.user;
    $("me").textContent = authState.user.username;
    if (authState.user.role !== "admin") {
      $("denied").hidden = false;
      return;
    }
    $("admin-main").hidden = false;
    await Promise.all([loadStats(), loadUserOptions()]);
    await loadActivity();
    setInterval(loadStats, 30000);
  })();
})();
