# Scopus AI (offline)

Offline literature search in the style of Scopus AI: research questions are answered only from
the indexed papers, with citations. It consists of two separate applications, each with its own
Docker images, data and accounts, meant for two PCs:

| Folder | Application | Runs on |
|---|---|---|
| [`embedding/`](embedding/README.md) | **Embedding app**: reads paper metadata (MySQL MyISAM tables, RIS files), embeds it on the GPU, builds the search index (vectors + BM25 keyword index in Qdrant) and exports it as a **bundle** | the embedding PC (http://localhost:7861) |
| [`search/`](search/README.md) | **Search app**: imports a bundle and answers questions – hybrid retrieval, reranking, a local language model, safety checks and a sentence-by-sentence check against the papers | the search PC (http://localhost:7860) |

```text
 Embedding PC                                        Search PC
 MySQL / RIS ─► ingest ─► index ─► export ── bundle ──► import ─► index ─► questions & answers
                (GPU)                        (USB)                          (GPU: LLM, reranker)
```

1. On the embedding PC: ingest the data (*Add to index* for each new folder), then *Export for the
   search app*. The bundle is one folder: a Qdrant snapshot, the index description, a manifest and
   SHA-256 checksums.
2. Copy the bundle folder to the search PC into `search/bundles/`.
3. In the search app: admin panel › **Index** › **Import**. The bundle is checked (format, embedding
   model, checksums), loaded and checked again before searches switch to it; until then the current
   index stays in use.

The two apps share no database or network connection. They must agree on the index layout and the
embedding model: `app/core/paper_index.py` and `app/core/embedding.py` are the same files in both apps,
and `INDEX_FORMAT` in `paper_index.py` names the layout. The search app refuses bundles of another
format or model.

Both apps are built and tested online, then moved to the offline PCs as two WSL packages (Ubuntu with
Docker, the images and one app each): [`Deploy-GPU-Project-Offline.md`](Deploy-GPU-Project-Offline.md)
explains how to make them (`offline/make_packages.ps1`) and how to install them.

All images can be built from zero: `search/Dockerfile` (targets `llm` and `app`), then
`embedding/Dockerfile`, which is made from the search app's image (same Python packages, so both
compute identical vectors).

| Path | |
|---|---|
| `embedding/` | embedding app ([README](embedding/README.md)) |
| `search/` | search app ([README](search/README.md)) |
| `offline/` | scripts and guides for the two offline packages |
| `Deploy-GPU-Project-Offline.md` | offline deployment of both PCs |
