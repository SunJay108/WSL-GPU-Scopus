"use strict";

/* Helpers shared by the chat page (app.js) and the admin panel (admin.js). */
window.UI = (() => {
  const ICONS = {
    search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/></svg>',
    logo: '<svg viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/></svg>',
    edit: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="m13.5 6.5 4 4"/></svg>',
    trash: '<svg viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
    copy: '<svg viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="m5 12 5 5L20 7"/></svg>',
  };

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const child of children.flat(Infinity)) {
      if (child == null || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  const icon = (name) => el("span", { html: ICONS[name], style: "display:contents" });

  /** Called with no arguments when the server answers 401 (not signed in). */
  let onUnauthorized = () => {};

  async function errorText(res) {
    try { return (await res.json()).detail || res.statusText; } catch (_) { return res.statusText; }
  }

  async function request(method, url, body) {
    const res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401 && !url.startsWith("/api/auth/")) onUnauthorized();
    if (!res.ok) {
      const err = new Error(await errorText(res));
      err.status = res.status;
      throw err;
    }
    return res;
  }

  const api = async (method, url, body) => (await request(method, url, body)).json();

  const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) =>
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
      if (/^&gt;\s?/.test(line)) { flushPara(); flushList(); html += `<blockquote>${inline(line.replace(/^&gt;\s?/, ""), refPrefix)}</blockquote>`; continue; }
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

  const fmtDateTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : "–");

  async function logout() {
    try { await request("POST", "/api/auth/logout"); } catch (_) { /* already signed out */ }
    location.href = "/";
  }

  return {
    ICONS, el, icon, api, request, errorText, escapeHtml, renderMarkdown, md, copyText, fmtDateTime, logout,
    setUnauthorizedHandler: (fn) => { onUnauthorized = fn; },
  };
})();
