# Scopus AI – offline deployment (embedding PC and search PC)

Scopus AI runs offline on two PCs, each with its own package:

| Package | PC | Application | URL |
|---|---|---|---|
| `Scopus-Embedding-Ubuntu24.tar` | embedding PC | `~/ScopusEmbedding` – ingests MySQL/RIS data on the GPU, exports index bundles | http://localhost:7861 |
| `Scopus-Search-Ubuntu24.tar` | search PC | `~/Scopus` – chat UI, admin panel, language model, imports index bundles | http://localhost:7860 |

Each package is a complete WSL Ubuntu 24.04 with Docker Engine, the NVIDIA Container Toolkit, the
Docker images, the models and one app. The offline PCs need only WSL 2 and the NVIDIA Windows driver;
nothing is downloaded or installed there. Only the index bundle moves between the PCs (USB drive):

```text
 embedding PC: data ─► ingest ─► Export for the search app ─► bundle folder ── USB ──┐
 search PC:    ~/Scopus/bundles ─► admin panel › Index › Import ─► questions & answers ◄┘
```

Contents:

1. Requirements of the offline PCs
2. Online PC: build and test
3. Online PC: make the two packages
4. Search PC
5. Embedding PC
6. Updates, backups and daily use

---

# 1. Requirements of the offline PCs

```text
Windows   10 22H2 / 11 with WSL 2 installed (wsl --version)
GPU       NVIDIA RTX 20xx / GTX 16xx or newer (compute capability 7.5+)
Driver    NVIDIA Windows driver 580 or newer (PyTorch in the images uses CUDA 13.0)
CPU       x86-64 with AVX2 (Intel 4th gen / AMD Ryzen or newer)
RAM       16 GB or more; search PC: about 3.5 GB extra per million chunks of index
          (twice that while a new index is imported)
Disk      search PC about 60 GB, embedding PC about 30 GB, plus room for the TAR file
```

Check the driver in PowerShell with `nvidia-smi` (top right: `Driver Version`). Do not install a Linux
NVIDIA driver inside WSL; the GPU comes from the Windows driver.

---

# 2. Online PC: build and test

The online PC (with Internet) has one WSL distribution (`Ubuntu-24.04`) with both apps:
`~/Scopus` (copy of `search/` of the repository) and `~/ScopusEmbedding` (copy of `embedding/`).

## 2.1 Images

All images can be built from zero (public base images only):

```bash
cd ~/Scopus
./scripts/build_online.sh          # llama.cpp source, then docker compose build: scopus-llm:b10991 + scopus-ai-gpu:1.1
cd ~/ScopusEmbedding
docker compose build               # scopus-embedding:1.0, made from scopus-ai-gpu:1.1 (same Python packages)
docker compose pull qdrant mariadb # qdrant/qdrant:v1.12.6, mariadb:10.11.19
```

`search/Dockerfile` has two targets: `llm` (llama.cpp `llama-server`, compiled in the CUDA devel image,
run on the CUDA runtime image) and `app` (Ubuntu 24.04 + Python venv with the pinned packages of
`requirements.txt`; PyTorch brings its own CUDA libraries). A rebuild is needed only when the
Dockerfile, `requirements.txt` or the llama.cpp version change; code changes under `app/` take effect
after restarting the container (the folder is bind-mounted).

## 2.2 Models

```text
~/Scopus/models/                       ~/ScopusEmbedding/models/
├── multilingual-e5-small/             └── multilingual-e5-small/   (the same model)
├── bge-reranker-base/
├── qwen2.5-3b-instruct-q4_k_m.gguf
├── Qwen3Guard-Gen-0.6B/
└── Qwen3Guard-Stream-0.6B/
```

See `search/README.md` (section 2) and `embedding/models/PUT_MODELS_HERE.txt`.

## 2.3 Test

```bash
cd ~/ScopusEmbedding && docker compose up -d && scripts/check_gpu.sh     # http://localhost:7861
cd ~/Scopus && docker compose up -d && ./scripts/verify_gpu.sh           # http://localhost:7860
```

Ingest a folder in the embedding app, export it, copy the bundle into `~/Scopus/bundles` and import it
in the search app (admin panel › Index); ask a question.

---

# 3. Online PC: make the two packages

1. Decide what goes into the packages:
   - **embedding**: the package always gets an empty index and an empty data folder (the data is
     ingested on the embedding PC); accounts of the embedding app are kept;
   - **search**: the package gets the search app as it is – its index, accounts and chats.
2. Stop both apps:

   ```bash
   cd ~/Scopus && docker compose down
   cd ~/ScopusEmbedding && docker compose down
   ```

3. In Windows PowerShell, from the repository folder:

   ```powershell
   powershell -ExecutionPolicy Bypass -File offline\make_packages.ps1 -OutDir E:\Scopus-Offline
   ```

   Parameters: `-Distro` (default `Ubuntu-24.04`), `-OutDir`, `-TempDir` (default
   `D:\WSL\_scopus_tmp`), `-User` (default `adonis`). It needs free space for about two exports on
   the drive of `-OutDir` and one on the drive of `-TempDir`, and takes 1–2 hours. The PC does not go to
   sleep while it runs; the log is `<OutDir>\make_packages.log`.

What the script does (the distribution itself is not changed):

1. `wsl --shutdown`, then `wsl --export Ubuntu-24.04` to a temporary file;
2. imports it as a temporary distribution, removes the search app (`~/Scopus`, with its accounts and
   index) and its images, empties the embedding app's data folder, index and bundles
   (`offline/strip_to_embedding.sh`), starts the embedding app and checks it (web UI, GPU, empty index),
   and exports it as the embedding package;
3. does the same the other way round for the search package (`offline/strip_to_search.sh`: removes
   `~/ScopusEmbedding`, its images and volume; starts the search app and checks health, index and GPU);
4. deletes the temporary distributions and file, writes `.sha256` files and copies the guides
   (`offline/PACKAGES.md`, `offline/START-OFFLINE-*.md`).

Result:

```text
E:\Scopus-Offline\
├── README.md
├── embedding\
│   ├── Scopus-Embedding-Ubuntu24.tar (+ .sha256)
│   └── START-OFFLINE.md
└── search\
    ├── Scopus-Search-Ubuntu24.tar (+ .sha256)
    └── START-OFFLINE.md
```

Copy each folder to its PC (USB SSD). A plain `.tar` imports on every WSL version; if you compress it
for the transfer, extract it back to `.tar` before importing. `C:\Users\<you>\.wslconfig` is not part
of a package – create it on each offline PC (sections 4.3 and 5.3).

---

# 4. Search PC

## 4.1 Check the copy and import

```powershell
Get-FileHash -Algorithm SHA256 E:\Scopus-Offline\search\Scopus-Search-Ubuntu24.tar
Get-Content E:\Scopus-Offline\search\Scopus-Search-Ubuntu24.tar.sha256       # same value?

mkdir D:\WSL\ScopusSearch
wsl --import ScopusSearch D:\WSL\ScopusSearch E:\Scopus-Offline\search\Scopus-Search-Ubuntu24.tar --version 2
wsl -l -v
```

## 4.2 Start

```powershell
wsl -d ScopusSearch
```

The distribution starts as `adonis` with systemd, so Docker starts by itself (`whoami`, `docker ps`;
if Docker is not running: `sudo systemctl start docker`).

```bash
cd ~/Scopus
docker compose up -d
docker ps                 # scopus-ai-gpu, scopus-llm (healthy after about a minute), scopus-qdrant
./scripts/verify_gpu.sh   # GPU in the containers, language model on the GPU
```

Open http://localhost:7860 and sign in (the accounts of the online PC are included; on a fresh
install the first account created becomes the administrator). The admin panel is
http://localhost:7860/admin: users, activity, all chats, **Index**.

## 4.3 Keep WSL running (once per PC)

WSL stops a distribution 15 seconds after its last window closes, and with it Docker and the search
page. Create `C:\Users\<you>\.wslconfig` (keep lines already in it), then run `wsl --shutdown` once:

```ini
[wsl2]
vmIdleTimeout=-1

[general]
instanceIdleTimeout=-1
```

The services then keep running without a WSL window, until `wsl --shutdown` or a Windows restart
(`docker compose up -d` starts them again).

## 4.4 Import a new index

1. Copy the bundle folder from the embedding PC into `~/Scopus/bundles/`:

   ```bash
   cp -r /mnt/e/papers_20261020_101500_20261020_110000 ~/Scopus/bundles/
   ```

2. Admin panel › **Index**: the bundle is listed as *ready* (or *cannot be imported*, with the reason).
3. **Import**. Progress: file check (SHA-256) → Qdrant loads the snapshot → checks → switch. Searches use
   the current index until the switch; a damaged bundle or a failed import changes nothing. With
   *Remove the current index first* it needs half the memory, but search is off during the import.
4. Afterwards **Delete** the bundle folder.

Command line: `docker exec -it scopus-ai-gpu python import_index.py [<bundle>] [--verify] [--low-memory]`.
Measured: 5.5 GB bundle (1,082,282 papers) in 7.8 minutes on a hard disk.

## 4.5 Settings

Runtime settings are in `~/Scopus/.env` (defaults in `app/core/config.py`); after a change run
`docker compose up -d` (a restart does not re-read `.env`):

```text
LLM_MODEL=qwen2.5-3b-instruct-q4_k_m.gguf   # model file in ./models
LLM_PARALLEL=1           # answers written at the same time (raise on a larger GPU)
LLM_CTX=4096             # context per answer
LLM_GPU_LAYERS=all       # all model layers on the GPU
BUNDLES_DIR=./bundles    # index bundles from the embedding PC
ALLOW_SIGNUP=true        # false: only administrators add users
```

On a larger GPU raise `LLM_PARALLEL` and measure with `./scripts/load_test.sh -e LLM_PARALLEL=4`
(about 20 minutes; real accounts and chats are not touched; reports in `loadtest_results/`).

## 4.6 Logs and troubleshooting

```bash
docker logs -f scopus-ai-gpu      # search UI, questions, guard decisions, imports
docker logs scopus-llm            # language model server
docker logs scopus-qdrant
curl -s http://localhost:7860/api/health
docker exec -it scopus-ai-gpu python users.py reset-password <name>   # forgotten password
```

| Symptom | Fix |
|---|---|
| Page does not open | `docker ps`; `docker logs --tail 100 scopus-ai-gpu`; `docker compose up -d` |
| "The language model is not available" | `scopus-llm` must be `(healthy)`; `docker logs scopus-llm` (GPU memory: lower `LLM_PARALLEL` / `LLM_CTX`) |
| "No search index is available yet" | import a bundle (4.4) |
| A bundle "cannot be imported" | copy it again (incomplete/damaged), or update the older app (index format/model) |
| Docker not running | `sudo systemctl restart docker` |

Ports: 7860 (search UI), 6333 (Qdrant).

---

# 5. Embedding PC

## 5.1 Check the copy and import

```powershell
Get-FileHash -Algorithm SHA256 E:\Scopus-Offline\embedding\Scopus-Embedding-Ubuntu24.tar
Get-Content E:\Scopus-Offline\embedding\Scopus-Embedding-Ubuntu24.tar.sha256    # same value?

mkdir D:\WSL\ScopusEmbedding
wsl --import ScopusEmbedding D:\WSL\ScopusEmbedding E:\Scopus-Offline\embedding\Scopus-Embedding-Ubuntu24.tar --version 2
```

## 5.2 Start

```powershell
wsl -d ScopusEmbedding
```

```bash
cd ~/ScopusEmbedding
docker compose up -d
scripts/check_gpu.sh      # GPU visible and embedding speed (about 450 passages/s on an RTX 2060)
```

Open http://localhost:7861 and sign in with the administrator account; change its password:
`docker exec -it embed-service python users.py reset-password admin`.

## 5.3 Keep the PC and WSL running (once per PC)

An ingest of a million papers takes over an hour. Windows sleep pauses it (WSL and the GPU stop with
it), and WSL stops when no WSL window is open:

- Settings › System › Power › Sleep: **Never** when plugged in (or, as administrator,
  `powercfg /change standby-timeout-ac 0`);
- `C:\Users\<you>\.wslconfig` as in section 4.3, then `wsl --shutdown` once.

## 5.4 Data, ingest, export

1. Copy each data set into its own folder below `~/ScopusEmbedding/data/import`:

   ```bash
   mkdir -p ~/ScopusEmbedding/data/import/mysql_2026_10
   cp /mnt/e/export/*.frm /mnt/e/export/*.MYD /mnt/e/export/*.MYI ~/ScopusEmbedding/data/import/mysql_2026_10/
   ```

   MySQL data = a database folder of the MySQL data directory (for XAMPP `xampp/mysql/data/<database>/`),
   `.frm` + `.MYD` + `.MYI` per table; `.ris` files work too. **Upload folder from this PC…** in the web UI
   copies a Windows folder.
2. **Select data folder**, keep **Add to index** (default; the first ingest builds the index) and
   **Start ingest**. **Initialize index** starts over with only the selected folder.
3. Check the quality line of the index card (both numbers close to 100 of 100).
4. **Export for the search app** → `~/ScopusEmbedding/data/exports/<bundle>`; copy the whole folder to
   the USB drive (`cp -r ~/ScopusEmbedding/data/exports/papers_… /mnt/e/`). The search PC's import replaces
   its whole index, so export the complete index each time.

Speed on an RTX 2060: 250–320 chunks/s (about 1.1 chunks per paper) – 128,000 papers in 8 minutes,
950,000 papers in 80 minutes; a faster GPU is faster in proportion.

## 5.5 Logs and troubleshooting

```bash
docker logs -f embed-service      # ingest progress, export
docker exec -it embed-service python users.py list
```

| Symptom | Fix |
|---|---|
| Device chip says "CPU" | `scripts/check_gpu.sh`; restart Docker (`sudo systemctl restart docker`) |
| An ingest stopped for a while | Windows went to sleep (5.3) |
| "All papers in this folder are already in the index" | nothing new in that folder |
| Table "crashed" / cannot be read | the copy of the `.MYD`/`.MYI` files is incomplete; copy the folder again |

Ports: 7861 (embedding UI); Qdrant and MariaDB are internal.

---

# 6. Updates, backups and daily use

**Code updates offline**: copy changed files into `~/Scopus/app` or `~/ScopusEmbedding/app` and restart
the container (`docker restart scopus-ai-gpu` / `docker restart embed-service`). Changes of the
Dockerfile, `requirements.txt` or images need Internet: rebuild on the online PC (2.1) and make new
packages (3).

**Index layout**: `app/core/paper_index.py` and `app/core/embedding.py` are the same files in both apps;
when they change (`INDEX_FORMAT`), update both PCs and rebuild the index.

**Backups** (PowerShell; Docker data, models, index and accounts included):

```powershell
wsl --shutdown
wsl --export ScopusSearch D:\Backup\ScopusSearch-backup.tar
wsl --export ScopusEmbedding D:\Backup\ScopusEmbedding-backup.tar
```

Keep a current and a previous backup. Files under `/mnt/c`, `/mnt/d` … are Windows folders and are
**not** part of an export – keep everything the apps need below `/home/adonis`.

**Daily use**:

```text
Search PC     wsl -d ScopusSearch      cd ~/Scopus && docker compose up -d           http://localhost:7860
Embedding PC  wsl -d ScopusEmbedding   cd ~/ScopusEmbedding && docker compose up -d  http://localhost:7861
Stop          docker compose down (in the same folder)
```
