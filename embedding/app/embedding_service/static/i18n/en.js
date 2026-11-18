"use strict";

/*
 * English page texts of the embedding app (index.html), by key; zh.js has the
 * same keys in Chinese.  See i18n.js for how they are used.
 * {name} is filled in by the page; "key_one" is the text for exactly one.
 * The ingest stage labels (stage.*) come from the server in English
 * (pipeline.py STAGES), so only zh.js lists them.
 */
(window.I18N_TEXTS = window.I18N_TEXTS || {}).en = {
  // ---------------------------------------------------------- shared
  "app.title": "Embedding service · Scopus AI",
  "app.titleRunning": "{p}% · Embedding service",
  "app.name": "Embedding service",
  "app.subtitle": "Build the Scopus AI search index and export it for the search app",
  "app.unreachable": "Embedding service unreachable: {error}",
  "common.cancel": "Cancel",
  "common.delete": "Delete",
  "common.logout": "Log out",
  "common.unknownError": "unknown error",
  "dur.hm": "{h}h {m}m",
  "dur.ms": "{m}m {s}s",
  "dur.s": "{s}s",

  // ---------------------------------------------------------- sign in
  "auth.adminsOnly": "Administrators only",
  "auth.signIn": "Sign in",
  "auth.createAccount": "Create account",
  "auth.firstUser": "No accounts exist yet. The first account becomes the <strong>administrator</strong>.",
  "auth.username": "Username",
  "auth.password": "Password",
  "auth.repeatPassword": "Repeat password",
  "auth.signupHint": "Username: 3–32 letters, digits, dots, dashes or underscores. Password: at least 8 characters.",
  "auth.adminHint": "Only administrator accounts can use the embedding service.",
  "auth.passwordsDiffer": "The passwords do not match.",
  "auth.denied": "You are signed in as <strong id=\"denied-user\"></strong>, which is not an administrator account. "
    + "Ask an administrator to give your account the admin role.",
  "auth.otherAccount": "Sign in with another account",

  // ---------------------------------------------------------- top bar
  "svc.label": "Service status",
  "svc.unreachable": "Embedding service unreachable",
  "svc.unknown": "unknown",
  "svc.device": "Device",

  // ---------------------------------------------------------- current index
  "index.title": "Current index",
  "index.loading": "Loading…",
  "index.sources": "Sources",
  "index.version": "version {v} · built {at}",
  "index.none": "No index yet. Select a data folder below and start an ingest.",
  "index.legacy": "Built by the previous ingest script (no details recorded).",
  "index.vectors": "vectors",
  "index.papers": "papers",
  "index.chunks": "chunks",
  "index.points": "vectors in Qdrant",
  "index.sourcesCount": "sources",
  "index.model": "embedding model",
  "index.quality": "{mark} Quality check of the last ingest: of {sample} new papers looked up by title, "
    + "{kw} were found by keyword search and {vec} by vector search (top {top}).",
  "index.sourcePapers": "{n} papers",

  // ---------------------------------------------------------- 1 folder
  "folder.title": "Select data folder",
  "folder.hint": "Open the folder that contains the MySQL table files (<code>.frm</code>, <code>.MYD</code>, "
    + "<code>.MYI</code>) or <code>.ris</code> exports.",
  "folder.path": "Folder path",
  "folder.up": "↑ Up",
  "folder.refresh": "Refresh",
  "folder.upload": "Upload folder from this PC…",
  "folder.uploadTitle": "Copy a folder from this computer into the import folder",
  "folder.readOnly": "The import folder is mounted read-only",
  "folder.subfolders": "Sub-folders",
  "folder.cannotOpen": "Cannot open folder: {error}",
  "folder.none": "No sub-folders.",
  "folder.tables": "{n} tables",
  "folder.tables_one": "{n} table",
  "folder.ris": "{n} RIS",

  "upload.noFiles": "The selected folder contains no .frm, .MYD, .MYI or .ris files.",
  "upload.file": "Uploading {name} ({i}/{total})",
  "upload.done": "Uploaded {n} files",
  "upload.done_one": "Uploaded {n} file",
  "upload.failed": "Upload failed:\n\n{error}",
  "upload.network": "network error",

  // ---------------------------------------------------------- 2 ingest
  "ingest.title": "Ingest",
  "ingest.folder": "Folder",
  "ingest.mode": "Mode",
  "ingest.append": "Add to index",
  "ingest.appendHint": "Add papers that are not indexed yet (same DOI, or same title without DOI, is skipped). "
    + "Without an index, a new one is built.",
  "ingest.replace": "Initialize index",
  "ingest.replaceHint": "Delete the current index and build a new one from this folder only. The current index is "
    + "kept until the new one is complete.",
  "ingest.start": "Start ingest",
  "ingest.running": "Ingest running…",
  "ingest.noFiles": "No importable files in this folder.",
  "ingest.noFilesHint": "Open a folder that contains .frm / .MYD / .MYI or .ris files.",
  "ingest.noMyi": "no .MYI – index rebuilt",
  "ingest.missing": "missing {files}",
  "ingest.risFiles": "RIS files ({n})",
  "ingest.tables": "{n} MySQL tables",
  "ingest.tables_one": "{n} MySQL table",
  "ingest.risCount": "{n} RIS files",
  "ingest.risCount_one": "{n} RIS file",
  "ingest.whyRunning": "An ingest job is running.",
  "ingest.whyExport": "An export is running; start the ingest when it has finished.",
  "ingest.whyNoFolder": "Select a folder with data first.",
  "ingest.whyNothing": "Select at least one table or the RIS files.",
  "ingest.whyQdrant": "Qdrant is not reachable.",
  "ingest.whyMariadb": "MariaDB is not reachable (needed for MySQL tables).",
  "ingest.papers": "{n} papers",
  "ingest.vectors": "{n} vectors",
  "ingest.confirmReplace": "Initialize the index?\n\nThe current index ({what}) will be deleted and a new index is built "
    + "from this folder only:\n{folder}\n\nThe current index is kept until the new one is complete. To keep the "
    + "indexed papers, cancel and use \"Add to index\".",
  "ingest.cannotStart": "Could not start ingest:\n\n{error}",

  // ---------------------------------------------------------- 3 status
  "job.title": "Status",
  "job.empty": "No ingest has run yet. Select a folder and press <strong>Start ingest</strong>.",
  "job.log": "Log",
  "job.logLines": "({n} lines)",
  "job.logLines_one": "({n} line)",
  "job.starting": "Starting…",
  "job.waiting": "waiting",
  "job.elapsed": "elapsed {t}",
  "job.left": "about {t} left",
  "job.ended": "ended {t}",
  "state.running": "Running",
  "state.completed": "Completed",
  "state.failed": "Failed",
  "state.cancelled": "Cancelled",
  "state.interrupted": "Interrupted",
  "end.completed": "Finished",
  "end.failed": "Failed",
  "end.cancelled": "Cancelled",
  "end.interrupted": "Interrupted",
  "count.tables": "MySQL tables",
  "count.ris_files": "RIS files",
  "count.rows": "Rows read",
  "count.papers": "Papers kept",
  "count.skipped_no_abstract": "No abstract",
  "count.skipped_duplicate": "Duplicates",
  "count.chunks": "Chunks",
  "count.embedded": "Vectors embedded",
  "job.done": "✅ Embedding finished: {added} papers added ({total} in the index, {chunks} chunks) in {time}. "
    + "Export the index (step 4) to use it in the search app.",
  "job.failed": "❌ Ingest failed: {error}. The index is unchanged.",
  "job.cancelled": "Ingest was cancelled. The index is unchanged.",
  "job.interrupted": "⚠️ {error}. The index is unchanged.",
  "job.alertDone": "✅ Embedding finished!\n\n{added} papers added · {total} papers / {chunks} chunks in the index\n"
    + "Time: {time}\n\nExport the index (step 4) to use it in the search app.",
  "job.alertFailed": "❌ Ingest failed\n\n{error}.\n\nThe index is unchanged.",
  "job.confirmCancel": "Cancel the running ingest? The current index stays unchanged.",

  // ---------------------------------------------------------- 4 export
  "export.title": "Export for the search app",
  "export.button": "Export current index",
  "export.running": "Exporting…",
  "export.hint": "An export bundle is a folder with a Qdrant snapshot of the index and its checksums. "
    + "Copy the whole folder from <code id=\"export-dir\"></code> to the search PC and import it there.",
  "export.whyIngest": "Export is possible when the ingest has finished.",
  "export.whyOld": "This index was built by an older version; initialize it again first.",
  "export.whyNone": "There is no index to export yet.",
  "export.done": "✅ Bundle {bundle} written in {elapsed}. Copy it to the search PC.",
  "export.failed": "❌ Export failed: {error}.",
  "export.none": "No export bundles yet.",
  "export.confirm": "Export the current index ({papers} papers) as a bundle for the search app?\n\n"
    + "Ingests cannot start while the export runs.",
  "export.cannotStart": "Could not start the export:\n\n{error}",
  "export.confirmDelete": "Delete the export bundle\n{name}?\n\nThe index itself is not affected.",
  "export.cannotDelete": "Could not delete the bundle:\n\n{error}",
  "col.bundle": "Bundle",
  "col.papers": "Papers",
  "col.chunks": "Chunks",
  "col.size": "Size",
  "col.created": "Created",
};
