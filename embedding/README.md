# Scopus AI – Embedding service

Builds the search index of Scopus AI from paper metadata (MySQL MyISAM tables,
RIS exports) and exports it as a **bundle** for the search app.

It is a separate application with its own Docker images, Qdrant database,
accounts and data folders. The search app never connects to it: the only
thing that moves between the two is the bundle folder.

```
 Embedding PC (this project)                         Search PC (search app)
 ┌──────────────────────────────────────┐            ┌──────────────────────┐
 │ data folder ─► ingest ─► Qdrant index │  bundle   │ import ─► Qdrant ─►   │
 │  (MySQL/RIS)   (GPU)      ─► export ──┼──────────►│ search UI + LLM      │
 └──────────────────────────────────────┘  USB/LAN  └──────────────────────┘
```

## Contents

| Path | |
|---|---|
| `docker-compose.yml` | services `qdrant`, `mariadb`, `embedding` (containers `embed-*`) |
| `Dockerfile` | image `scopus-embedding:1.0`, built from the search app's image |
| `app/embedding_service/` | web UI, ingest pipeline (`pipeline.py`), export (`exporter.py`) |
| `app/core/` | settings, accounts, index layout (`paper_index.py`), embedding model |
| `app/ingest.py`, `app/export.py`, `app/users.py` | command-line tools |
| `models/` | the embedding model `multilingual-e5-small` (not in git) |
| `data/import/` | default folder the UI can browse (`IMPORT_DIR`) |
| `data/exports/` | export bundles (`EXPORT_DIR`) |
| `data/state/` | accounts, activity log, job state, index description |
| `data/qdrant/` | the index (created on the first start) |
| `scripts/` | save/load images for the offline PC, GPU check |

## Requirements

- Linux or Windows with WSL2, Docker with Compose, and the NVIDIA Container Toolkit.
- NVIDIA driver with CUDA 13.0 support (driver 580 or newer); any GPU with 2 GB
  free memory works, a larger GPU is faster. Without a GPU it embeds on the CPU (slow).
- RAM: about 3 GB plus 2.5 GB per million chunks for Qdrant.
- Disk: about 5 GB per million papers for the index, the same again for each export bundle.

## Set-up (PC with internet access)

The image reuses the Python packages of the search app's image
(`scopus-ai-gpu:1.1`, built from the repository root), so both compute the same
vectors and no download is needed:

```bash
cd embedding
cp .env.example .env                                     # adjust IMPORT_DIR etc.
cp -r ../models/multilingual-e5-small models/            # see models/PUT_MODELS_HERE.txt
docker compose build                                     # scopus-embedding:1.0
docker compose up -d
scripts/check_gpu.sh                                     # GPU visible, embedding speed
```

Open http://localhost:7861. The first account you create is the administrator;
further sign-ups are off (`ALLOW_SIGNUP=false`). More administrators:
`docker exec -it embed-service python users.py create-admin NAME`.

## Moving it to the offline embedding PC

```bash
scripts/save_images.sh        # on the online PC: images/scopus-embedding-images.tar
```

Copy the whole `embedding` folder (including `models/` and `images/`) to the
offline PC, then:

```bash
scripts/load_images.sh        # checks the file, loads the images, checks the GPU
cp .env.example .env          # if not copied; set IMPORT_DIR to the data folder
docker compose up -d
```

## Using it

1. **Select data folder** – browse `IMPORT_DIR`, or upload a folder from the browser.
   A folder can hold MySQL MyISAM tables (`.frm` + `.MYD` + `.MYI`) and `.ris` files.
2. **Ingest** – choose the mode:

   | Mode | |
   |---|---|
   | **Add to index** (default) | adds the papers that are not indexed yet (same DOI, or same title without DOI, is skipped). Without an index it builds a new one. If the run fails or is cancelled, the added papers are removed again. |
   | **Initialize index** | deletes the current index and builds a new one from this folder only (asks for confirmation). The current index is kept until the new one is complete. |

3. **Status** – progress, speed, log. After the papers are uploaded, a
   **quality check** looks up a sample of the new papers by title with keyword
   and vector search; both should find almost all of them (a warning is shown
   below 80 %). The result is shown in the index card.
4. **Export for the search app** – writes a bundle into `EXPORT_DIR`. Copy the
   whole bundle folder to the search PC and import it there.

An ingest and an export never run at the same time.

## Export bundle

```
data/exports/papers_<index version>_<export time>/
    papers.snapshot    Qdrant snapshot: vectors, keyword index, texts, metadata
    index_meta.json    papers, chunks, sources, model, index format, quality check
    manifest.json      bundle format, sizes and SHA-256 checksums
    SHA256SUMS         the same checksums, for  sha256sum -c SHA256SUMS
    README.txt
```

The snapshot contains the finished vector and keyword indexes, so the search
PC does not rebuild anything. `manifest.json` names the index format
(`scopus-papers-v2`, defined in `app/core/paper_index.py`); the search app
must use the same format and embedding model.

Check a copied bundle: `sha256sum -c SHA256SUMS` in the bundle folder, or
`docker exec embed-service python export.py --verify /exports/<bundle>`.

## Command line

```bash
docker exec -it embed-service python ingest.py /import/mysql                  # Add to index
docker exec -it embed-service python ingest.py /import/mysql --mode replace   # Initialize index
docker exec -it embed-service python export.py                                # export bundle
docker exec -it embed-service python export.py --list
docker exec -it embed-service python users.py list
```

## Speed and scale

Papers are read, embedded and uploaded in batches of `INGEST_BATCH` papers, so
memory use does not depend on the size of a table; the keyword (BM25) index is
part of the Qdrant collection. The GPU embeds while the previous batch is
uploaded.

Measured on the development PC (RTX 2060 6 GB, hard disk, WSL2 with 16 GB, 16 CPU threads):

| Step | Data | Time |
|---|---|---|
| GPU embedding alone (`scripts/check_gpu.sh`) | 1,024 passages | 452 passages/s |
| Initialize index from 3 MySQL tables | 160,139 rows → 127,883 papers, 149,181 chunks | 7.8 min |
| Add a 2.8 GB MySQL table | 1,322,638 rows → 945,611 papers, 1,219,887 chunks | 81 min |
| Add a RIS file to the 1.07 M-paper index | 10,377 records → 8,788 papers | 5.7 min (mostly reading the existing paper keys) |
| Export the full index | 1,082,282 papers, 1,381,112 chunks → 5.9 GB bundle | 6.3 min |
| Restore that bundle into another Qdrant (validation) | | 8.7 min, identical search results |
| Cancel a running ingest | | stops within 10 s; the added papers are removed |

- Ingest speed: about 320 chunks/s while the index is small, about 250 chunks/s
  on average up to 1.4 M chunks (Qdrant indexes the growing collection at the
  same time).
- Memory: the ingest process stays at about 2 GB whatever the table size;
  Qdrant needs about 3–4 GB for 1.4 M chunks.
- Quality check: 99–100 of 100 sampled papers were found by keyword search and
  by vector search after every run.

Embedding takes most of the time, so a faster GPU speeds up an ingest almost
proportionally; raise `EMBED_BATCH_SIZE` (256–512) on a GPU with more memory.
An SSD speeds up staging the MySQL tables, Qdrant and the export.

## Settings (.env)

| Setting | Default | |
|---|---|---|
| `IMPORT_DIR` | `./data/import` | folder tree the UI can browse |
| `EXPORT_DIR` | `./data/exports` | export bundles; keep it on a Linux disk |
| `EMBED_SERVICE_BIND` / `EMBED_SERVICE_PORT` | `127.0.0.1` / `7861` | `0.0.0.0` = reachable from the network |
| `EMBED_DEVICE` | `cuda` | `cpu` to embed without a GPU |
| `EMBED_BATCH_SIZE` | `128` | chunks per GPU batch (halved on out-of-memory) |
| `EMBED_FP16` | `true` | half precision on the GPU |
| `INGEST_BATCH` | `1000` | papers per batch |
| `QUALITY_SAMPLE` | `100` | papers looked up by the quality check (0 = off) |
| `ALLOW_SIGNUP` | `false` | allow sign-up after the first account |

## Troubleshooting

- **Port 7861 in use** – the old embedding service of the search app may still
  run: `docker stop scopus-embedding`, or set `EMBED_SERVICE_PORT`.
- **"CPU (no GPU visible)"** – check `scripts/check_gpu.sh` and the NVIDIA Container Toolkit.
- **WSL2 stops when no terminal is open** – set `vmIdleTimeout=-1` (under
  `[wsl2]`) and `instanceIdleTimeout=-1` (under `[general]`) in
  `%UserProfile%\.wslconfig`, then `wsl --shutdown`; otherwise a long ingest is
  killed when the last terminal closes.
- **An ingest pauses for a while** – Windows went to sleep (WSL and the GPU stop
  with it; the job continues after waking). On the embedding PC set
  *Settings › System › Power › Sleep* to *Never* when plugged in, or run
  `powercfg /change standby-timeout-ac 0` (as administrator).
- Logs: `docker logs -f embed-service`.
