# Scopus AI — GPU-ready for Ubuntu 24.04 WSL2

Offline Scopus AI / RAG system: hybrid retrieval (BM25 + Qdrant vectors + RRF fusion),
BGE reranking and Qwen GGUF answer generation on an NVIDIA GPU.

It runs as two web services that share one search index:

| Service | URL | Purpose |
|---|---|---|
| **Embedding service** (`scopus-embedding`) | http://localhost:7861 | Select the folder with the MySQL data, press **Start ingest**, watch progress, get an alert when it is done |
| **Scopus search UI** (`scopus-ai`) | http://localhost:7860 | Chat with history: ask research questions and follow-ups; answers cite the indexed papers |

```text
 MySQL data folder (.frm/.MYD/.MYI)                                  Browser
            │                                                        │      │
            ▼                                                   :7861│      │:7860
 ┌────────────────────┐  stage + SQL  ┌─────────┐        ┌───────────▼──┐ ┌─▼──────────┐
 │ scopus-embedding   │──────────────▶│ mariadb │        │  embedding   │ │ scopus-ai  │
 │ (FastAPI + UI)     │◀──────────────│ (intern)│        │  UI          │ │ (chat UI)  │
 └─────────┬──────────┘     rows      └─────────┘        └──────────────┘ └─────┬──────┘
           │ vectors (new collection, then alias swap)                          │ search
           ▼                                                                    ▼
      ┌─────────┐   alias "papers"  ◀─────────────────────────────────────────────
      │ qdrant  │
      └─────────┘   app/data/storage/bm25.pkl + index_meta.json  (shared files)
```

- The build happens on an **online** PC; the result is copied to a **fully offline** PC.
- Docker Engine in WSL2 (Docker Desktop not required), NVIDIA Container Toolkit, NVIDIA Windows driver.
- Images: `scopus-ai-gpu:1.1` (both app services), `qdrant/qdrant:v1.12.6`, `mariadb:10.11.19`.
- Models are mounted from `./models`, the index from `./app/data` and `./qdrant_data`.

## 1. Project layout

```text
.
├── app/
│   ├── main.py                 Scopus search UI: chat API + web UI (port 7860)
│   ├── web/                    chat UI (no external resources; works offline)
│   ├── query.py                hybrid retrieval + generation; reloads the index after an ingest
│   ├── guard.py                decides which questions are answered (Scopus-AI-style)
│   ├── filters.py              keyword lists used by the guard
│   ├── history.py              chat history (SQLite in app/data/history)
│   ├── config.py               settings (env variables)
│   ├── index_meta.py           shared description of the live index
│   ├── ingest.py               command-line ingest (same pipeline as the UI)
│   ├── embedding_service/      embedding service (port 7861)
│   │   ├── server.py           REST API
│   │   ├── jobs.py             background job + progress (one worker process per ingest)
│   │   ├── pipeline.py         read → chunk → BM25 → embed → Qdrant → publish
│   │   ├── sources.py          MySQL (MyISAM via MariaDB) and RIS readers
│   │   └── static/             web UI (no external resources; works offline)
│   └── data/
│       ├── mysql/              example MySQL data (3 tables)
│       ├── storage/            bm25.pkl, index_meta.json (written by ingest)
│       └── history/            chat_history.sqlite3 (search UI history)
├── models/
├── qdrant_data/
├── scripts/
├── .env / .env.example
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

The git repository contains code and configuration only. Models, MySQL/RIS data, `qdrant_data/`
and `app/data/storage/` are not committed (size and licensing): copy the models into `models/`,
your data folders below `IMPORT_DIR`, and `cp .env.example .env`.

## 2. Required local models

```text
models/
├── multilingual-e5-small/
├── bge-reranker-base/
└── qwen2.5-3b-instruct-q4_k_m.gguf
```

Nothing is downloaded at runtime (Hugging Face offline mode is enforced in `docker-compose.yml`).

## 3. Input data

The embedding service reads **MySQL / MariaDB MyISAM table files** – the files you find in a
database folder of the MySQL data directory (for XAMPP: `xampp/mysql/data/<database>/`):

```text
nature_2026_02_12.frm   table definition
nature_2026_02_12.MYD   rows
nature_2026_02_12.MYI   indexes
```

Copy each data folder below the **import folder** (`IMPORT_DIR` in `.env`, default `./app/data`),
e.g. `app/data/mysql/`. Alternatively use **Upload folder from this PC…** in the embedding UI.
`.ris` files in the selected folder are imported as well.

Used columns (matched by name, case-insensitive): `title`, `abstract`, `author` (`;`-separated),
`keywords` (`;`-separated), `year`, `journal`, `doi`. Rows without an abstract of at least
50 characters are skipped (`MIN_ABSTRACT_CHARS`), and duplicate DOIs are indexed once.

How it works: the files are copied into the internal `mariadb` container (MariaDB 10.11 reads the
MariaDB 10.4 / XAMPP format), read with SQL and then removed again. Your original files are never
opened by MariaDB or modified. InnoDB tables (`.ibd`) cannot be imported from files.

## 4. Verify WSL GPU first

Inside Ubuntu 24.04 WSL:

```bash
nvidia-smi                       # or /usr/lib/wsl/lib/nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu24.04 nvidia-smi
```

Do not install a Linux NVIDIA display driver inside WSL; WSL GPU support comes from the Windows driver.

## 5. Online build

```bash
cp .env.example .env            # first time only
chmod +x scripts/*.sh
./scripts/build_online.sh
```

This builds `scopus-ai-gpu:1.1` (llama-cpp-python compiled with CUDA), pulls Qdrant and MariaDB,
and starts all four containers.

If `scopus-ai-gpu:1.0` already exists, `./scripts/upgrade_image.sh` is much faster: it builds 1.1 on
top of 1.0 (`Dockerfile.upgrade`: adds PyMySQL and recompiles llama.cpp for RTX 20xx–40xx GPUs),
reusing all other layers.

For a fully offline PC, export the whole WSL distro – see `Deploy-GPU-Project-Offline.md`.

## 6. Build the index (embedding service)

1. Open **http://localhost:7861**.
2. **Select data folder** – click through the folders (e.g. `mysql`). Folders containing data show a
   badge such as `3 tables`. The right panel lists the tables found, with sizes; untick tables you
   do not want.
3. Choose the mode:
   - **Replace index** – builds a fresh index from this folder.
   - **Add to index** – adds papers whose DOI is not indexed yet (keeps the existing index).
4. Press **Start ingest**. The status panel shows each stage
   (scan → load MySQL tables → read → chunk → BM25 → embed → optimize → publish),
   a progress bar with ETA, counters and a log. The browser tab title shows the percentage.
5. When it finishes, a browser **alert** reports the result. The Scopus search UI switches to the
   new index automatically (no restart).

The job runs on the server: you can close the browser and come back later; the last result is
remembered across restarts. **Cancel** stops the job and leaves the current index untouched.

Safety: a replace builds a new Qdrant collection `papers_<timestamp>` and only then moves the alias
`papers` to it, so searches keep working on the old index during the whole ingest and a failed or
cancelled run changes nothing.

For the example data (160,139 rows → 127,883 papers → 148,040 chunks) the ingest took 16 minutes
on a laptop RTX 2060 with the GPU to itself, and about 25 minutes while the search UI also holds
its models on that 6 GB GPU. Almost all of the time is the embedding stage.

Tip: `http://localhost:7861/?path=mysql` opens a folder directly (bookmarkable).

Command-line alternative (same pipeline):

```bash
./scripts/ingest.sh                    # IMPORT_DIR/mysql, replace mode
./scripts/ingest.sh mysql --mode append
```

## 7. Ask questions (Scopus search UI)

Open **http://localhost:7860** – a chat interface similar to ChatGPT:

- **History sidebar**: every chat is saved. *New chat*, search, rename (✎), delete (🗑),
  *Clear all history*. History is stored on the server in `app/data/history/chat_history.sqlite3`,
  so it survives browser resets and is part of backups / the WSL export.
- **Follow-up questions** in the same chat ("What barriers did they report?") are rewritten into a
  standalone search question using the conversation; the UI shows what was searched.
- **Answers** stream in while they are written: summary, detailed answer with clickable citations
  `[n]`, reference cards (journal, year, DOI link, relevance) and three suggested follow-up questions.
- **Export** a chat as Markdown (⤓ in the top bar); **copy** a single answer.

### Which questions are answered

Like Scopus AI, the guard (`app/guard.py`) judges what a question is about instead of only matching
words:

| Step | What happens |
|---|---|
| 1. Hard keywords | Names and phrases in `HARD_BLOCK_KEYWORDS` (`app/filters.py`), e.g. political leaders, extremist groups, conspiracy slogans, are always refused. Spelling variants are caught (`xijinping`, `Xi-Jinping`, full-width letters). |
| 2. Language model | The local model classifies the question: research → answered; restricted topic (politics, war/military, religion, ideology, conspiracy), personal information about a person, harmful requests, off-topic chat → a short explanation instead. |
| 3. Topic check | Words in `TOPIC_KEYWORDS` that also have research meanings ("support vector machine", "heart attack", "nuclear magnetic resonance", "air pollution in China") only block if the model confirms the question is really about that topic. |
| 4. Relevance | If the best paper scores below `RELEVANCE_MIN_SCORE` the answer is "no relevant papers" instead of an answer from unrelated sources; below `RELEVANCE_WARN_SCORE` the answer carries a "loosely related papers" notice. |

`docker logs scopus-ai-gpu` shows each decision, e.g. `Guard: RESTRICTED (keyword 'xi jinping')`.
Edit the lists in `app/filters.py` and restart the container to change the policy; `GUARD_MODE=keywords`
in `.env` switches back to plain keyword blocking.

## 8. GPU settings

`.env`:

```text
LLM_CTX=4096
LLM_BATCH=256
LLM_THREADS=4
LLM_GPU_LAYERS=-1        # -1 = all layers on the GPU; lower it (20, 15, …) if VRAM is short
EMBED_BATCH_SIZE=128     # embedding batch; halved automatically on CUDA out-of-memory
EMBED_FP16=true          # half-precision ingest embeddings (~4x faster, same retrieval results)
```

On small GPUs (6 GB) the search UI (LLM + embedding + reranker ≈ 5 GB) and an ingest share VRAM.
Both keep working, but for the fastest ingest stop the search UI while it runs:

```bash
docker compose stop scopus-ai
# … ingest in the embedding UI …
docker compose start scopus-ai
```

Each ingest runs in its own worker process, so all GPU memory used for embedding is released when
the job ends.

## 9. Verify GPU acceleration

```bash
./scripts/verify_gpu.sh
```

Expected: `llama_supports_gpu_offload = True` and `torch.cuda.is_available = True` for both services.

## 10. Logs and troubleshooting

```bash
docker compose ps
docker logs -f scopus-embedding     # ingest progress is printed here too
docker logs -f scopus-ai-gpu
docker logs -f scopus-qdrant
docker logs -f scopus-mariadb
```

| Symptom | Fix |
|---|---|
| UI chip "MariaDB unreachable" right after start | MariaDB initialises its data directory on first start; wait ~20 s |
| "No search index is available yet" in the search UI | Run an ingest in the embedding UI |
| A research question is refused | `docker logs scopus-ai-gpu \| grep Guard:` shows why; adjust `app/filters.py`, or lower `RELEVANCE_MIN_SCORE` for "no relevant papers" |
| Embedding shows "on cpu" | GPU not visible in the container (`./scripts/verify_gpu.sh`) or `EMBED_DEVICE=cpu` |
| Embedding UI not reachable from another PC | Set `EMBED_SERVICE_BIND=0.0.0.0` in `.env`, then `docker compose up -d` |
| Data folder on a Windows drive | Set `IMPORT_DIR=/mnt/d/your/folder` in `.env`, then `docker compose up -d` |

## 11. Create the fully-offline bundle

After the online build has been tested:

```bash
./scripts/export_offline.sh
```

```text
offline_bundle/
├── images/
│   ├── scopus-ai-gpu-1.1.tar
│   ├── qdrant-1.12.6.tar
│   └── mariadb-10.11.19.tar
├── app/                 application code + data (MySQL example, index files)
├── models/
├── qdrant_data/
├── docker-compose.yml
├── .env, .env.example
├── import_offline.sh
├── verify_gpu.sh
└── START_OFFLINE.txt
```

Copy `offline_bundle` to the offline PC (USB SSD etc.).

## 12. Run on the fully-offline PC

Prerequisites: WSL2/Ubuntu 24.04, Docker Engine, NVIDIA Windows driver, NVIDIA Container Toolkit.

```bash
chmod +x import_offline.sh verify_gpu.sh
./import_offline.sh            # docker load + docker compose up -d --no-build --pull never
./verify_gpu.sh
```

Then open http://localhost:7861 to ingest data and http://localhost:7860 to search.
Everything needed at runtime (models, tokenizer data, UI assets, fonts) is local.

## 13. Notes

1. Build and validate the CUDA image online first; do not compile `llama-cpp-python` on the offline PC.
2. Keep `qdrant_data` and `app/data/storage` together when transferring a prebuilt index.
3. An index built by the old `ingest.py` keeps working; the first **Replace** ingest converts it to the
   alias layout (the old `papers` collection is replaced).
4. The ingest uses the same chunking and text layout as before (title and keywords weighted, 512-token
   chunks), so retrieval quality is unchanged.
5. The embedding UI has no login. It binds to `127.0.0.1` by default; only open it to the network
   (`EMBED_SERVICE_BIND=0.0.0.0`) on a trusted network.

## 14. Typical commands

```bash
docker compose up -d                       # start
docker compose down                        # stop
docker compose restart scopus-ai           # restart the search UI
docker compose restart scopus-embedding    # restart the embedding service
docker compose build --no-cache scopus-ai  # rebuild the image after dependency changes
```

Python and UI changes under `app/` take effect after restarting the container (the folder is
bind-mounted); no image rebuild is needed.
