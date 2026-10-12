"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const { el, api, md, fmtDateTime, logout } = window.UI;

  const state = { me: null, users: [], actions: {}, lastId: null, chatId: null, timers: {} };

  const STATUS_BADGE = {
    answered: ["Answered", "ok"],
    no_results: ["No relevant papers", "warn"],
    no_index: ["No index", "warn"],
    blocked: ["Blocked", "err"],
    unavailable: ["Model unavailable", "err"],
    error: ["Error", "err"],
  };

  function badge(text, cls = "") { return el("span", { class: `badge ${cls}` }, text); }

  const debounce = (key, fn) => {
    clearTimeout(state.timers[key]);
    state.timers[key] = setTimeout(fn, 300);
  };

  // ------------------------------------------------------------ tabs

  function showTab(name) {
    document.querySelectorAll(".admin-tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    for (const tab of ["activity", "chats", "users"]) $(`tab-${tab}`).hidden = tab !== name;
    if (name === "chats") loadChats();
    if (name === "users") loadUsers();
  }

  // ------------------------------------------------------------ overview

  async function loadStats() {
    const s = await api("GET", "/api/admin/stats");
    const cards = [
      [s.users, "users"],
      [s.active_today, "active today"],
      [`${s.chats}`, `chats (${s.deleted_chats} deleted)`],
      [s.questions, `questions (${s.questions_today} today)`],
      [s.blocked, "blocked questions"],
      [s.failed_logins_today, "failed logins today"],
    ];
    $("stats").replaceChildren(...cards.map(([value, label]) =>
      el("div", { class: "stat" }, el("strong", {}, Number(value).toLocaleString()), el("span", {}, label))));
  }

  async function loadUserOptions() {
    state.users = await api("GET", "/api/admin/users");
    for (const id of ["act-user", "chat-user"]) {
      const select = $(id);
      const current = select.value;
      select.replaceChildren(el("option", { value: "" }, "All users"),
        ...state.users.map((u) => el("option", { value: u.id }, u.username)));
      select.value = current;
    }
  }

  // ------------------------------------------------------------ activity

  function describe(item) {
    const d = item.detail || {};
    switch (item.action) {
      case "ask": {
        const [label, cls] = STATUS_BADGE[d.status] || [d.status || "?", ""];
        const note = d.status === "blocked" && d.category ? `${label}: ${d.category.toLowerCase().replace("_", "-")}` : label;
        return [
          el("div", { class: "detail-main" }, d.question || ""),
          el("div", { class: "detail-sub" }, badge(note, cls),
            d.searched_as ? ` searched as “${d.searched_as}”` : "",
            d.reason && d.status === "blocked" ? ` · ${d.reason}` : "",
            d.removed_sentences ? ` · ${d.removed_sentences} unsupported sentence(s) removed` : "",
            d.seconds ? ` · ${d.seconds} s` : ""),
        ];
      }
      case "rename_chat":
        return [el("div", { class: "detail-main" }, `“${d.old}” → “${d.new}”`)];
      case "delete_chat":
        return [el("div", { class: "detail-main" }, `“${d.title}”`), el("div", { class: "detail-sub" }, `${d.questions || 0} question(s)`)];
      case "clear_history":
        return [el("div", { class: "detail-main" }, `${d.chats} chat(s) deleted`)];
      case "export_chat":
        return [el("div", { class: "detail-main" }, `“${d.title}”`)];
      case "login_failed":
        return [el("div", { class: "detail-sub" }, [serviceName(d), d.reason].filter(Boolean).join(" · "))];
      case "signup":
        return [el("div", { class: "detail-sub" }, `${serviceName(d)} · role: ${d.role}`)];
      case "login":
      case "logout":
      case "password_change":
        return [el("div", { class: "detail-sub" }, serviceName(d))];
      case "upload_folder":
        return [el("div", { class: "detail-main" }, d.folder || "")];
      case "ingest_start": {
        const what = d.tables && d.tables.length ? `${d.tables.length} table(s)` : "all tables";
        return [el("div", { class: "detail-main" }, d.folder || ""),
          el("div", { class: "detail-sub" }, `${d.mode === "append" ? "Add to index" : "Replace index"} · ${what}${d.include_ris ? " + RIS" : ""}`)];
      }
      case "ingest_completed": {
        const s = d.summary || {};
        return [el("div", { class: "detail-main" }, d.folder || ""),
          el("div", { class: "detail-sub" },
            `${Number(s.papers_added || 0).toLocaleString()} papers added · ${Number(s.papers_total || 0).toLocaleString()} in index · ${Math.round(s.seconds || 0)} s`)];
      }
      case "ingest_failed":
        return [el("div", { class: "detail-main" }, d.folder || ""), el("div", { class: "detail-sub" }, d.error || "")];
      case "ingest_cancel":
      case "ingest_cancelled":
        return [el("div", { class: "detail-main" }, d.folder || (d.job ? `job ${d.job}` : ""))];
      default:
        if (d.target) {
          const extra = d.role ? ` → ${d.role}${d.previous ? ` (was ${d.previous})` : ""}` : "";
          return [el("div", { class: "detail-main" }, `${d.target}${extra}`)];
        }
        return [];
    }
  }

  const serviceName = (d) => (d.service === "embedding" ? "embedding service" : d.service === "search" ? "search UI" : "");

  function actionBadge(item) {
    const a = item.action;
    const cls = a === "login_failed" || a === "ingest_failed" ? "err"
      : a === "ingest_completed" ? "ok"
        : a.startsWith("admin_") || a.startsWith("ingest_") || a === "upload_folder" ? "accent"
          : a.startsWith("delete") || a === "clear_history" ? "warn" : "";
    return badge(item.action_label, cls);
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
      $("act-action").append(...Object.entries(data.actions).map(([k, label]) => el("option", { value: k }, label)));
    }

    const rows = data.items.map((item) => el("tr", {},
      el("td", { class: "nowrap" }, fmtDateTime(item.created_at)),
      el("td", { class: "nowrap" }, item.username || "–"),
      el("td", {}, actionBadge(item)),
      el("td", {}, describe(item)),
      el("td", { class: "nowrap" }, item.conversation_id
        ? [el("button", { class: "link-btn", type: "button", onclick: () => openChat(item.conversation_id, true) }, "Open"),
          item.conversation_deleted_at ? [" ", badge("deleted", "warn")] : null]
        : "")));

    if (more) $("act-rows").append(...rows); else $("act-rows").replaceChildren(...rows);
    if (!more && !rows.length) $("act-rows").append(el("tr", {}, el("td", { colspan: "5", class: "muted" }, "No activity found.")));
    if (data.items.length) state.lastId = data.items[data.items.length - 1].id;
    $("act-more").hidden = data.items.length < 100;
    $("act-count").textContent = `${$("act-rows").querySelectorAll("tr").length} entries shown`;
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
      el("td", {}, c.title, c.deleted_at ? [" ", badge("deleted", "warn")] : null),
      el("td", { class: "nowrap" }, c.username || "–"),
      el("td", { class: "num" }, c.questions),
      el("td", { class: "nowrap" }, fmtDateTime(c.updated_at)))) : [el("tr", {}, el("td", { colspan: "4", class: "muted" }, "No chats found."))]));
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
    const thread = el("div", { class: "thread" }, conv.messages.map((m) => m.role === "user"
      ? el("div", { class: "msg-user" }, m.content, el("div", { class: "msg-time" }, fmtDateTime(m.created_at)))
      : el("div", {}, md(m.content), el("div", { class: "msg-time" }, fmtDateTime(m.created_at)))));
    $("viewer").replaceChildren(
      el("h2", {}, conv.title),
      el("div", { class: "viewer-meta" },
        `by ${conv.username || "–"} · started ${fmtDateTime(conv.created_at)}`,
        conv.deleted_at ? badge(`deleted by user ${fmtDateTime(conv.deleted_at)}`, "warn") : badge("active", "ok")),
      conv.messages.length ? thread : el("p", { class: "muted" }, "No messages."));
    $("viewer").scrollTop = 0;
  }

  // ------------------------------------------------------------ users

  async function loadUsers() {
    await loadUserOptions();
    $("user-rows").replaceChildren(...state.users.map((u) => {
      const self = state.me && u.id === state.me.id;
      const actions = el("div", { class: "row-actions" },
        el("button", { class: "btn", type: "button", disabled: self, onclick: () => updateUser(u, { disabled: !u.disabled }) },
          u.disabled ? "Enable" : "Disable"),
        el("button", { class: "btn", type: "button", disabled: self, onclick: () => updateUser(u, { role: u.role === "admin" ? "user" : "admin" }) },
          u.role === "admin" ? "Make user" : "Make admin"),
        el("button", { class: "btn", type: "button", onclick: () => resetPassword(u) }, "Reset password"),
        el("button", { class: "btn", type: "button", onclick: () => { $("act-user").value = u.id; showTab("activity"); loadActivity(); } }, "Activity"),
        el("button", { class: "btn", type: "button", onclick: () => { $("chat-user").value = u.id; showTab("chats"); } }, "Chats"));
      return el("tr", {},
        el("td", { class: "nowrap" }, u.username, self ? el("span", { class: "muted" }, " (you)") : null),
        el("td", {}, badge(u.role, u.role === "admin" ? "accent" : "")),
        el("td", {}, u.disabled ? badge("disabled", "err") : badge("active", "ok")),
        el("td", { class: "nowrap" }, fmtDateTime(u.created_at)),
        el("td", { class: "nowrap" }, fmtDateTime(u.last_login_at)),
        el("td", { class: "nowrap" }, fmtDateTime(u.last_activity)),
        el("td", { class: "num" }, u.deleted_chats ? `${u.chats} (${u.deleted_chats} deleted)` : u.chats),
        el("td", { class: "num" }, u.questions),
        el("td", {}, actions));
    }));
  }

  async function updateUser(user, change) {
    const what = "disabled" in change ? (change.disabled ? "disable" : "enable") : `make ${change.role}`;
    if (!confirm(`${what[0].toUpperCase()}${what.slice(1)} “${user.username}”?`)) return;
    try {
      await api("PATCH", `/api/admin/users/${user.id}`, change);
    } catch (err) {
      alert(err.message);
    }
    loadUsers();
    loadStats();
  }

  async function resetPassword(user) {
    const password = prompt(`New password for “${user.username}” (at least 8 characters):`);
    if (!password) return;
    try {
      await api("PATCH", `/api/admin/users/${user.id}`, { password });
      alert(`Password of “${user.username}” changed. Their open sessions were signed out.`);
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
