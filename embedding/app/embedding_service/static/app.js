"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const UPLOAD_EXT = [".frm", ".myd", ".myi", ".ris"];

  const state = {
    path: "",           // current folder, relative to the import root
    folder: null,       // last /api/folders response
    status: null,       // last /api/status response
    jobId: null,
    jobState: null,
    watchJob: null,     // job started from this page: alert even if it ends before a poll sees it running
    alerted: new Set(),
    logSeq: 0,
    uploading: false,
  };

  // ---------------------------------------------------------------- helpers

  const fmtNum = (n) => (n == null ? "–" : Number(n).toLocaleString());

  function fmtBytes(n) {
    if (!n) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1);
    return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`;
  }

  function fmtDuration(sec) {
    if (sec == null) return "";
    sec = Math.max(0, Math.round(sec));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    if (h) return `${h}h ${m}m`;
    if (m) return `${m}m ${s}s`;
    return `${s}s`;
  }

  const fmtClock = (t) => new Date(t * 1000).toLocaleString();

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const child of children.flat()) {
      if (child == null || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  async function api(method, url, body) {
    const res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) { /* not JSON */ }
      // Session expired or rights removed while the page was open.
      if (!url.startsWith("/api/auth/") && (res.status === 401 || res.status === 403)) boot();
      const err = new Error(detail);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  function storageGet(key) {
    try { return localStorage.getItem(key); } catch (_) { return null; }
  }
  function storageSet(key, value) {
    try { localStorage.setItem(key, value); } catch (_) { /* private mode */ }
  }

  const FOLDER_ICON = () => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
    p.setAttribute("d", "M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8l-2-2z");
    svg.append(p);
    return svg;
  };

  // ---------------------------------------------------------------- folders

  async function openFolder(path) {
    try {
      state.folder = await api("GET", `/api/folders?path=${encodeURIComponent(path)}`);
      state.path = state.folder.path;
      storageSet("embedding.path", state.path);
    } catch (err) {
      if (path) return openFolder("");  // remembered folder disappeared
      $("folders").replaceChildren(el("li", { class: "empty" }, `Cannot open folder: ${err.message}`));
      return;
    }
    renderFolder();
  }

  function renderFolder() {
    const f = state.folder;

    $("crumbs").replaceChildren(...f.breadcrumbs.flatMap((c, i) => {
      const last = i === f.breadcrumbs.length - 1;
      const btn = el("button", {
        type: "button",
        "aria-current": last ? "page" : null,
        title: c.name,
        onclick: last ? null : () => openFolder(c.path),
      }, c.name);
      return i ? [el("span", { class: "sep" }, "›"), btn] : [btn];
    }));

    $("up-btn").disabled = f.parent == null;
    $("upload-btn").disabled = !f.writable || state.uploading;
    $("upload-btn").title = f.writable
      ? "Copy a folder from this computer into the import folder"
      : "The import folder is mounted read-only";

    const items = f.folders.map((d) => {
      const parts = [];
      if (d.tables) parts.push(`${d.tables} table${d.tables > 1 ? "s" : ""}`);
      if (d.ris_files) parts.push(`${d.ris_files} RIS`);
      return el("li", {},
        el("button", { type: "button", class: parts.length ? "has-data" : null, onclick: () => openFolder(d.path) },
          FOLDER_ICON(),
          el("span", { class: "name" }, d.name),
          parts.length ? el("span", { class: "badge" }, parts.join(" · ")) : null));
    });
    $("folders").replaceChildren(...(items.length ? items
      : [el("li", { class: "empty" }, "No sub-folders.")]));

    $("selected-folder").textContent = f.display;
    renderImportables();
  }

  function renderImportables() {
    const scan = state.folder.scan;
    const box = $("importables");
    const children = [];

    if (!scan.importable) {
      children.push(el("div", { class: "empty-box" },
        "No importable files in this folder.", el("br"),
        "Open a folder that contains .frm / .MYD / .MYI or .ris files."));
    } else {
      const rows = [];
      for (const t of scan.tables) {
        const disabled = !t.complete;
        rows.push(el("label", { class: disabled ? "disabled" : null },
          el("input", { type: "checkbox", class: "table-check", value: t.name, checked: !disabled, disabled, onchange: updateIngestButton }),
          el("span", { class: "tname", title: t.name }, t.name),
          t.needs_index_rebuild ? el("span", { class: "note" }, "no .MYI – index rebuilt") : null,
          disabled ? el("span", { class: "note" }, `missing ${t.missing.join(", ")}`) : null,
          el("span", { class: "size" }, fmtBytes(t.size))));
      }
      if (scan.ris_files.length) {
        const size = scan.ris_files.reduce((a, r) => a + r.size, 0);
        rows.push(el("label", {},
          el("input", { type: "checkbox", id: "ris-check", checked: true, onchange: updateIngestButton }),
          el("span", { class: "tname" }, `RIS files (${scan.ris_files.length})`),
          el("span", { class: "size" }, fmtBytes(size))));
      }
      const complete = scan.tables.filter((t) => t.complete).length;
      children.push(el("div", { class: "importables" },
        el("div", { class: "head" },
          el("span", {}, [
            complete ? `${complete} MySQL table${complete > 1 ? "s" : ""}` : null,
            scan.ris_files.length ? `${scan.ris_files.length} RIS file${scan.ris_files.length > 1 ? "s" : ""}` : null,
          ].filter(Boolean).join(" · ")),
          el("span", { class: "muted" }, fmtBytes(scan.total_size))),
        rows));
    }

    if (scan.warnings.length) {
      children.push(el("ul", { class: "warnings" }, scan.warnings.map((w) => el("li", {}, w))));
    }
    box.replaceChildren(...children);
    updateIngestButton();
  }

  function selection() {
    const tables = [...document.querySelectorAll(".table-check:checked")].map((c) => c.value);
    const ris = $("ris-check");
    return { tables, includeRis: !!(ris && ris.checked) };
  }

  function updateIngestButton() {
    const btn = $("ingest-btn");
    const hint = $("ingest-hint");
    const running = state.jobState === "running";
    const services = (state.status && state.status.services) || {};
    const sel = state.folder ? selection() : { tables: [], includeRis: false };
    const nothing = !sel.tables.length && !sel.includeRis;

    let reason = "";
    if (running) reason = "An ingest job is running.";
    else if (exportRunning()) reason = "An export is running; start the ingest when it has finished.";
    else if (!state.folder || !state.folder.scan.importable) reason = "Select a folder with data first.";
    else if (nothing) reason = "Select at least one table or the RIS files.";
    else if (services.qdrant && !services.qdrant.ok) reason = "Qdrant is not reachable.";
    else if (sel.tables.length && services.mariadb && !services.mariadb.ok) reason = "MariaDB is not reachable (needed for MySQL tables).";

    btn.disabled = !!reason;
    btn.textContent = running ? "Ingest running…" : "Start ingest";
    hint.textContent = reason;
  }

  async function startIngest() {
    const sel = selection();
    const mode = document.querySelector('input[name="mode"]:checked').value;
    const idx = state.status && state.status.index;
    const hasIndex = idx && idx.collection;

    if (mode === "replace" && hasIndex) {
      const papers = idx.meta && idx.meta.papers ? `${fmtNum(idx.meta.papers)} papers` : `${fmtNum(idx.points)} vectors`;
      const ok = confirm(
        `Initialize the index?\n\nThe current index (${papers}) will be deleted and a new index is built ` +
        `from this folder only:\n${state.folder.display}\n\n` +
        "The current index is kept until the new one is complete. To keep the indexed papers, " +
        "cancel and use \"Add to index\".");
      if (!ok) return;
    }

    $("ingest-btn").disabled = true;
    try {
      const started = await api("POST", "/api/ingest", {
        path: state.path,
        tables: sel.tables,
        include_ris: sel.includeRis,
        mode,
      });
      state.watchJob = started.job_id;
      $("log-box").open = true;
      $("status-title").scrollIntoView({ behavior: "smooth", block: "start" });
      poll(true);
    } catch (err) {
      alert(`Could not start ingest:\n\n${err.message}`);
      updateIngestButton();
    }
  }

  // ---------------------------------------------------------------- upload

  async function uploadFolder(fileList) {
    const files = [...fileList].filter((f) => UPLOAD_EXT.some((e) => f.name.toLowerCase().endsWith(e)));
    if (!files.length) {
      alert("The selected folder contains no .frm, .MYD, .MYI or .ris files.");
      return;
    }
    const rootName = (files[0].webkitRelativePath || "folder").split("/")[0];
    // Files from different sub-folders would collide; keep the first of each name.
    const seen = new Set();
    const unique = files.filter((f) => !seen.has(f.name) && seen.add(f.name));
    const total = unique.reduce((a, f) => a + f.size, 0);

    state.uploading = true;
    $("upload-btn").disabled = true;
    $("upload-progress").hidden = false;
    let sent = 0;

    try {
      const upload = await api("POST", "/api/uploads", { name: rootName });
      for (const [i, file] of unique.entries()) {
        $("upload-label").textContent = `Uploading ${file.name} (${i + 1}/${unique.length})`;
        await putFile(`/api/uploads/${encodeURIComponent(upload.id)}/${encodeURIComponent(file.name)}`, file, (loaded) => {
          const pct = total ? ((sent + loaded) / total) * 100 : 100;
          $("upload-bar").style.width = `${pct}%`;
          $("upload-pct").textContent = `${pct.toFixed(0)}% · ${fmtBytes(sent + loaded)} / ${fmtBytes(total)}`;
        });
        sent += file.size;
      }
      $("upload-label").textContent = `Uploaded ${unique.length} file(s)`;
      await openFolder(upload.path);
    } catch (err) {
      alert(`Upload failed:\n\n${err.message}`);
    } finally {
      state.uploading = false;
      setTimeout(() => { $("upload-progress").hidden = true; $("upload-bar").style.width = "0"; }, 1500);
      if (state.folder) $("upload-btn").disabled = !state.folder.writable;
      $("upload-input").value = "";
    }
  }

  function putFile(url, file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", url);
      xhr.upload.onprogress = (e) => onProgress(e.loaded);
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) return resolve();
        let detail = xhr.statusText;
        try { detail = JSON.parse(xhr.responseText).detail || detail; } catch (_) { /* not JSON */ }
        reject(new Error(`${file.name}: ${detail}`));
      };
      xhr.onerror = () => reject(new Error(`${file.name}: network error`));
      xhr.send(file);
    });
  }

  // ---------------------------------------------------------------- status

  let pollTimer = null;

  async function poll() {
    clearTimeout(pollTimer);
    try {
      const status = await api("GET", `/api/status?log_since=${state.logSeq}`);
      const job = status.job;
      if (job && job.id !== state.jobId) {
        // New job: restart the log from its first line.
        const firstLoad = state.jobId === null;
        state.jobId = job.id;
        state.jobState = firstLoad ? job.state : null;
        state.logSeq = 0;
        $("log").replaceChildren();
        if (job.logs.length && job.logs[0].seq !== 1) {
          pollTimer = setTimeout(poll, 0);
          return;
        }
      }
      state.status = status;
      render(status);
    } catch (err) {
      if (err.status === 401 || err.status === 403) return;  // signed out: boot() takes over
      renderServices(null);
    }
    const running = state.jobState === "running" || exportRunning();
    pollTimer = setTimeout(poll, running ? 1000 : 4000);
  }

  function render(status) {
    renderServices(status.services);
    renderIndex(status.index);
    renderJob(status.job);
    renderExport(status);
    updateIngestButton();
  }

  function renderServices(services) {
    const list = $("services");
    if (!services) {
      list.replaceChildren(el("li", {}, el("span", { class: "dot bad" }), "Embedding service unreachable"));
      return;
    }
    const entries = [["qdrant", "Qdrant"], ["mariadb", "MariaDB"], ["device", "Device"]];
    list.replaceChildren(...entries.map(([key, name]) => {
      const s = services[key] || {};
      const cls = s.ok == null ? "" : s.ok ? "ok" : key === "device" ? "warn" : "bad";
      return el("li", { title: `${name}: ${s.detail || "unknown"}` },
        el("span", { class: `dot ${cls}` }),
        key === "device" ? s.detail || "Device" : name);
    }));
  }

  function renderIndex(idx) {
    const stats = $("index-stats");
    const meta = idx.meta;
    $("index-version").textContent = meta ? `version ${meta.version} · built ${meta.built_at}` : "";

    if (idx.error) {
      stats.replaceChildren(el("span", { class: "muted" }, idx.error));
    } else if (!idx.collection) {
      stats.replaceChildren(el("span", { class: "muted" }, "No index yet. Select a data folder below and start an ingest."));
    } else if (!meta) {
      stats.replaceChildren(
        stat(fmtNum(idx.points), "vectors"),
        el("span", { class: "muted" }, "Built by the previous ingest script (no details recorded)."));
    } else {
      stats.replaceChildren(
        stat(fmtNum(meta.papers), "papers"),
        stat(fmtNum(meta.chunks), "chunks"),
        stat(fmtNum(idx.points), "vectors in Qdrant"),
        stat(String((meta.sources || []).length), "sources"),
        stat(meta.embed_model || "–", "embedding model"));
    }

    const q = meta && meta.quality;
    const quality = $("index-quality");
    quality.hidden = !q;
    if (q) {
      const low = Math.min(q.keyword_found, q.vector_found) < 0.8 * q.sample;
      quality.className = `quality ${low ? "low" : "ok"}`;
      quality.textContent = `${low ? "⚠️" : "✓"} Quality check of the last ingest: of ${q.sample} new papers looked up by title, ` +
        `${q.keyword_found} were found by keyword search and ${q.vector_found} by vector search (top ${q.top}).`;
    }

    const sources = (meta && meta.sources) || [];
    $("index-sources").hidden = !sources.length;
    $("index-sources-body").replaceChildren(...sources.map((s) =>
      el("tr", {},
        el("td", {}, s.name),
        el("td", { class: "num" }, `${fmtNum(s.papers)} papers`),
        el("td", { class: "num muted" }, s.added_at || ""))));
  }

  function stat(value, label) {
    return el("div", { class: "stat" }, el("strong", {}, value), el("span", {}, label));
  }

  const COUNTERS = [
    ["tables", "MySQL tables"],
    ["ris_files", "RIS files"],
    ["rows", "Rows read"],
    ["papers", "Papers kept"],
    ["skipped_no_abstract", "No abstract"],
    ["skipped_duplicate", "Duplicates"],
    ["chunks", "Chunks"],
    ["embedded", "Vectors embedded"],
  ];

  function renderJob(job) {
    $("job-empty").hidden = !!job;
    $("job-view").hidden = !job;
    $("job-pill").hidden = !job;
    if (!job) { state.jobState = null; return; }

    const previous = state.jobState;
    state.jobState = job.state;

    const pill = $("job-pill");
    pill.className = `pill ${job.state}`;
    pill.textContent = job.state[0].toUpperCase() + job.state.slice(1);
    $("cancel-btn").hidden = job.state !== "running";
    $("cancel-btn").disabled = job.cancel_requested;

    const current = job.stages.find((s) => s.key === job.stage);
    let stageText;
    if (job.state === "running") {
      stageText = current ? current.label : "Starting…";
      if (current && current.total) stageText += ` — ${fmtNum(current.done)} / ${fmtNum(current.total)}`;
    } else {
      stageText = {
        completed: "Finished",
        failed: "Failed",
        cancelled: "Cancelled",
        interrupted: "Interrupted",
      }[job.state] || job.state;
    }
    $("job-stage").textContent = stageText;

    const times = [`elapsed ${fmtDuration(job.elapsed)}`];
    if (job.eta != null) times.push(`about ${fmtDuration(job.eta)} left`);
    if (job.state !== "running" && job.finished_at) times.push(`ended ${fmtClock(job.finished_at)}`);
    $("job-times").textContent = times.join(" · ");

    $("job-percent").textContent = `${job.percent.toFixed(job.percent < 100 ? 1 : 0)}%`;
    $("job-bar").style.width = `${job.percent}%`;
    $("job-bar").className = `bar-fill ${job.state}`;
    $("job-bar-wrap").setAttribute("aria-valuenow", String(Math.round(job.percent)));

    $("stages").replaceChildren(...job.stages.map((s) => {
      let detail = s.detail || "";
      if (s.state === "running" && s.total && !detail) detail = `${fmtNum(s.done)} / ${fmtNum(s.total)}`;
      if (s.state === "pending") detail = "waiting";
      return el("li", { class: s.state, title: detail },
        el("span", { class: "icon" }),
        el("div", { class: "body" },
          el("div", { class: "label" }, s.label),
          el("div", { class: "detail" }, detail)));
    }));

    $("counters").replaceChildren(...COUNTERS
      .filter(([key]) => job.counters[key] != null)
      .map(([key, label]) => el("div", {}, el("dt", {}, label), el("dd", {}, fmtNum(job.counters[key])))));

    appendLogs(job.logs, job.log_seq);
    renderBanner(job);

    document.title = job.state === "running"
      ? `${Math.floor(job.percent)}% · Embedding service`
      : "Embedding service · Scopus AI";

    if (job.state !== "running" && (previous === "running" || state.watchJob === job.id)) {
      state.watchJob = null;
      notifyFinished(job);
    }
  }

  function appendLogs(entries, lastSeq) {
    if (!entries.length) return;
    const pre = $("log");
    const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 30;
    for (const e of entries) {
      if (e.seq <= state.logSeq) continue;
      const time = new Date(e.t * 1000).toLocaleTimeString();
      pre.append(el("span", { class: e.level }, `${time}  ${e.msg}\n`));
    }
    while (pre.childNodes.length > 1500) pre.removeChild(pre.firstChild);
    state.logSeq = Math.max(state.logSeq, lastSeq);
    $("log-count").textContent = `(${pre.childNodes.length} lines)`;
    if (atBottom) pre.scrollTop = pre.scrollHeight;
  }

  function renderBanner(job) {
    const banner = $("banner");
    if (job.state === "running") { banner.hidden = true; return; }

    banner.className = `banner ${job.state}`;
    banner.replaceChildren();
    if (job.state === "completed" && job.summary) {
      const s = job.summary;
      banner.append(
        `✅ Embedding finished: ${fmtNum(s.papers_added)} papers added (${fmtNum(s.papers_total)} in the index, ` +
        `${fmtNum(s.chunks_total)} chunks) in ${fmtDuration(s.seconds)}. Export the index (step 4) to use it in the search app.`);
    } else if (job.state === "failed") {
      banner.append(`❌ Ingest failed: ${errorText(job)}. The index is unchanged.`);
    } else if (job.state === "cancelled") {
      banner.append("Ingest was cancelled. The index is unchanged.");
    } else if (job.state === "interrupted") {
      banner.append(`⚠️ ${errorText(job)}. The index is unchanged.`);
    }
    banner.hidden = false;
  }

  // Server messages end with a period; drop it so it can be followed by more text.
  const errorText = (job) => (job.error || "unknown error").replace(/\.+$/, "");

  function notifyFinished(job) {
    // Several open tabs, or a reload, must not alert twice for the same job.
    if (state.alerted.has(job.id) || storageGet(`embedding.alerted.${job.id}`)) return;
    state.alerted.add(job.id);
    storageSet(`embedding.alerted.${job.id}`, "1");

    // Let the final state paint before the blocking alert.
    setTimeout(() => {
      if (job.state === "completed" && job.summary) {
        const s = job.summary;
        alert(
          "✅ Embedding finished!\n\n" +
          `${fmtNum(s.papers_added)} papers added · ${fmtNum(s.papers_total)} papers / ${fmtNum(s.chunks_total)} chunks in the index\n` +
          `Time: ${fmtDuration(s.seconds)}\n\n` +
          "Export the index (step 4) to use it in the search app.");
      } else if (job.state === "failed") {
        alert(`❌ Ingest failed\n\n${errorText(job)}.\n\nThe index is unchanged.`);
      }
    }, 150);
  }

  // ---------------------------------------------------------------- export

  function exportRunning() {
    return !!(state.status && state.status.export && state.status.export.state === "running");
  }

  function renderExport(status) {
    const ex = status.export || {};
    const idx = status.index || {};
    const running = ex.state === "running";
    $("export-dir").textContent = status.config.export_dir;

    let reason = "";
    if (running) reason = "";
    else if (state.jobState === "running") reason = "Export is possible when the ingest has finished.";
    else if (!idx.exportable) reason = idx.collection ? "This index was built by an older version; initialize it again first."
      : "There is no index to export yet.";
    $("export-btn").disabled = running || !!reason;
    $("export-btn").textContent = running ? "Exporting…" : "Export current index";
    $("export-hint").textContent = reason;
    $("export-hint").hidden = !reason;

    const pill = $("export-pill");
    pill.hidden = !ex.state || ex.state === "idle";
    pill.className = `pill ${ex.state}`;
    pill.textContent = { running: "Running", completed: "Completed", failed: "Failed" }[ex.state] || "";

    const banner = $("export-banner");
    banner.hidden = !ex.state || ex.state === "idle";
    banner.className = `banner ${ex.state}`;
    const elapsed = ex.started_at ? fmtDuration((ex.finished_at || Date.now() / 1000) - ex.started_at) : "";
    if (running) banner.textContent = `${ex.step} (${elapsed})`;
    else if (ex.state === "completed") banner.textContent = `✅ Bundle ${ex.bundle} written in ${elapsed}. Copy it to the search PC.`;
    else if (ex.state === "failed") banner.textContent = `❌ Export failed: ${(ex.error || "unknown error").replace(/\.+$/, "")}.`;

    const bundles = ex.bundles || [];
    $("bundles").hidden = !bundles.length;
    $("bundles-empty").hidden = !!bundles.length;
    $("bundles-body").replaceChildren(...bundles.map((b) =>
      el("tr", { class: b.name === ex.bundle ? "new" : null },
        el("td", { class: "name" }, b.name),
        el("td", { class: "num" }, fmtNum(b.papers)),
        el("td", { class: "num" }, fmtNum(b.chunks)),
        el("td", { class: "num" }, fmtBytes(b.size)),
        el("td", { class: "muted" }, b.created_at || ""),
        el("td", { class: "action" },
          el("button", { type: "button", class: "btn btn-small btn-danger", onclick: () => deleteBundle(b.name) }, "Delete")))));
  }

  async function startExport() {
    const idx = state.status && state.status.index;
    const papers = idx && idx.meta ? fmtNum(idx.meta.papers) : "?";
    if (!confirm(`Export the current index (${papers} papers) as a bundle for the search app?\n\n` +
      "Ingests cannot start while the export runs.")) return;
    $("export-btn").disabled = true;
    try {
      await api("POST", "/api/exports");
    } catch (err) {
      alert(`Could not start the export:\n\n${err.message}`);
    }
    poll();
  }

  async function deleteBundle(name) {
    if (!confirm(`Delete the export bundle\n${name}?\n\nThe index itself is not affected.`)) return;
    try {
      await api("DELETE", `/api/exports/${encodeURIComponent(name)}`);
    } catch (err) {
      alert(`Could not delete the bundle:\n\n${err.message}`);
    }
    poll();
  }

  // ---------------------------------------------------------------- wiring

  $("up-btn").addEventListener("click", () => state.folder && state.folder.parent != null && openFolder(state.folder.parent));
  $("refresh-btn").addEventListener("click", () => openFolder(state.path));
  $("upload-btn").addEventListener("click", () => $("upload-input").click());
  $("upload-input").addEventListener("change", (e) => e.target.files.length && uploadFolder(e.target.files));
  $("ingest-btn").addEventListener("click", startIngest);
  $("export-btn").addEventListener("click", startExport);
  $("cancel-btn").addEventListener("click", async () => {
    if (!confirm("Cancel the running ingest? The current index stays unchanged.")) return;
    $("cancel-btn").disabled = true;
    try { await api("POST", "/api/cancel"); } catch (err) { alert(err.message); }
    poll(true);
  });

  // ---------------------------------------------------------------- sign in

  let authMode = "login";
  let authState = {};
  let started = false;

  function showScreen(name) {
    for (const id of ["auth", "denied", "app"]) $(id).hidden = id !== name;
  }

  function setAuthMode(mode) {
    authMode = mode;
    const signup = mode === "signup";
    $("tab-login").setAttribute("aria-selected", String(!signup));
    $("tab-signup").setAttribute("aria-selected", String(signup));
    $("auth-confirm-wrap").hidden = !signup;
    $("auth-confirm").required = signup;
    $("auth-password").autocomplete = signup ? "new-password" : "current-password";
    $("auth-submit").textContent = signup ? "Create account" : "Sign in";
    $("auth-hint").textContent = signup
      ? "Username: 3–32 letters, digits, dots, dashes or underscores. Password: at least 8 characters."
      : "Only administrator accounts can use the embedding service.";
    $("auth-error").hidden = true;
  }

  function showAuth(st) {
    authState = st || authState;
    clearTimeout(pollTimer);
    started = false;
    showScreen("auth");
    $("auth-first").hidden = !authState.first_user;
    $("tab-signup").hidden = !authState.allow_signup;
    setAuthMode(authState.first_user ? "signup" : "login");
    $("auth-username").focus();
  }

  async function submitAuth(e) {
    e.preventDefault();
    const signup = authMode === "signup";
    const showError = (text) => { $("auth-error").textContent = text; $("auth-error").hidden = false; };
    if (signup && $("auth-password").value !== $("auth-confirm").value) return showError("The passwords do not match.");
    $("auth-submit").disabled = true;
    try {
      await api("POST", signup ? "/api/auth/signup" : "/api/auth/login", {
        username: $("auth-username").value.trim(),
        password: $("auth-password").value,
      });
      $("auth-password").value = "";
      $("auth-confirm").value = "";
      await boot();
    } catch (err) {
      showError(err.message);
    } finally {
      $("auth-submit").disabled = false;
    }
  }

  async function logout() {
    try { await api("POST", "/api/auth/logout"); } catch (_) { /* already signed out */ }
    location.reload();
  }

  /** Decide which screen to show from the current session. */
  async function boot() {
    let st;
    try {
      st = await api("GET", "/api/auth/state");
    } catch (err) {
      document.body.textContent = `Embedding service unreachable: ${err.message}`;
      return;
    }
    if (!st.user) return showAuth(st);
    if (st.user.role !== "admin") {
      clearTimeout(pollTimer);
      started = false;
      $("denied-user").textContent = st.user.username;
      return showScreen("denied");
    }
    showScreen("app");
    $("me-name").textContent = st.user.username;
    $("me-avatar").textContent = st.user.username.slice(0, 1).toUpperCase();
    if (started) return;
    started = true;
    // ?path=mysql opens that folder directly (bookmarkable); otherwise the last one used.
    const linked = new URLSearchParams(location.search).get("path");
    openFolder(linked !== null ? linked : storageGet("embedding.path") || "");
    poll();
  }

  $("auth-form").addEventListener("submit", submitAuth);
  $("tab-login").addEventListener("click", () => setAuthMode("login"));
  $("tab-signup").addEventListener("click", () => setAuthMode("signup"));
  $("logout-btn").addEventListener("click", logout);
  $("denied-logout").addEventListener("click", logout);

  boot();
})();
