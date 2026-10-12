# 7. Prepare and export Scopus Ubuntu on the online PC

The WSL export contains everything the offline PC needs: Ubuntu, Docker Engine,
NVIDIA Container Toolkit, the Docker images and `~/Scopus` (code, models, data, index).

## Offline PC requirements

Only WSL 2 and the NVIDIA Windows driver need to be installed, but they must match the image:

```text
GPU      NVIDIA RTX 20xx / GTX 16xx or newer (compute capability 7.5+)
Driver   NVIDIA Windows driver 580 or newer (the image's PyTorch uses CUDA 13.0)
CPU      x86-64 with AVX2 (Intel 4th gen / AMD Ryzen or newer)
Disk     about 60 GB for the imported distro, plus space for the TAR file
RAM      16 GB recommended
```

Check the driver on the offline PC in PowerShell with `nvidia-smi` (top right: `Driver Version`).

## Build the image (online, once)

Inside Ubuntu-24.04:

```bash
cd ~/Scopus
./scripts/upgrade_image.sh     # scopus-ai-gpu:1.0 -> 1.1 (+ MariaDB image)
docker compose up -d
```

Check http://localhost:7861 (embedding service) and http://localhost:7860 (search).
Optionally build the index now (embedding service → folder `mysql` → **Start ingest**) so the
offline PC starts with a ready index.

## Export

In Windows PowerShell:

```powershell
wsl --shutdown
mkdir D:\WSL-Backup
wsl --export Ubuntu-24.04 D:\WSL-Backup\Scopus-Ubuntu24.tar
```

The TAR is about 45–50 GB. Copy it to the offline PC (USB SSD etc.).
A plain `.tar` imports on every WSL version. If you compress it with 7-Zip for the transfer,
extract it back to `.tar` before importing.

Files outside the distro are not part of the export, e.g. `C:\Users\<you>\.wslconfig`
(optional WSL settings) – copy it separately if you use one.

---

# 8. Import Scopus Ubuntu on the offline PC

Create a WSL location:

```powershell
mkdir D:\WSL
mkdir D:\WSL\ScopusAI
```

Import:

```powershell
wsl --import ScopusAI `
D:\WSL\ScopusAI `
D:\WSL-Backup\Scopus-Ubuntu24.tar `
--version 2
```

Check:

```powershell
wsl -l -v
```

Expected:

```text
NAME        STATE       VERSION
ScopusAI    Stopped     2
```

Start it:

```powershell
wsl -d ScopusAI
```

---

# 9. Set your normal Linux user

Imported WSL distros may start as `root`.

Check:

```bash
whoami
```

If:

```text
root
```

check existing users:

```bash
ls /home
```

You should see:

```text
adonis
```

Switch:

```bash
su - adonis
```

Check:

```bash
whoami
```

Expected:

```text
adonis
```

Then:

```bash
cd ~/Scopus
```

---

# 10. Verify Docker after import

Run:

```bash
docker --version
```

If the Docker daemon is not running:

```bash
sudo systemctl start docker
```

Check:

```bash
docker ps
```

Check images:

```bash
docker images
```

Expected:

```text
scopus-ai-gpu      1.1
scopus-ai-gpu      1.0      (base of 1.1; shares its layers, takes no extra space)
qdrant/qdrant      v1.12.6
mariadb            10.11.19
```

If those images exist, no Internet is required.

---

# 11. Verify GPU on the offline PC

First test WSL GPU:

```bash
nvidia-smi
```

You should see your NVIDIA GPU.

Then start Scopus:

```bash
cd ~/Scopus

docker compose up -d
```

Check:

```bash
docker ps
```

Expected:

```text
scopus-ai-gpu       Scopus search UI
scopus-llm          language model server (llama.cpp), used by the search UI
scopus-embedding    embedding service
scopus-qdrant       vector database
scopus-mariadb      reads MySQL table files during an ingest
```

`docker ps` shows `scopus-llm` as `(healthy)` once the model is loaded (up to a minute after start).

Now test the GPU inside Scopus:

```bash
docker exec -it scopus-ai-gpu nvidia-smi
```

Then test PyTorch:

```bash
docker exec -it scopus-ai-gpu python -c \
"import torch; print('CUDA:', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE')"
```

Expected:

```text
CUDA: True
NVIDIA ...
```

Test the language model server after the first question (speed of the last requests):

```bash
docker logs scopus-llm 2>&1 | grep "prompt eval time" | tail -3
```

Expected: several hundred tokens per second or more (on the GPU), not a few dozen (CPU):

```text
prompt eval time =  605.71 ms /  560 tokens ( 1.08 ms per token,  924.53 tokens per second)
```

Or run all GPU checks at once:

```bash
cd ~/Scopus
./scripts/verify_gpu.sh
```

---

# 12. Start Scopus AI normally

Every time you want to use it:

```bash
wsl -d ScopusAI
```

Then:

```bash
cd ~/Scopus
docker compose up -d
```

Check:

```bash
docker ps
```

Then on Windows open:

```text
http://localhost:7860     Scopus search
http://localhost:7861     Embedding service (build / update the index)
```

No Internet connection is required.

The search page asks you to sign in. On the first start, choose **Create account**: the
first account becomes the **administrator**. Other people then create their own accounts
(or the administrator adds them in the admin panel when `ALLOW_SIGNUP=false` in `.env`).

The search page works like a chat: earlier chats are listed on the left (search, rename,
delete, export as Markdown) and you can ask follow-up questions in the same chat. Each user
sees only their own chats.

Administrators also see **Admin panel** in the sidebar (http://localhost:7860/admin): all
user activity (logins, questions, renamed and deleted chats, …), every chat including those
users deleted, and user management (add, disable, reset password, change role).

Accounts, chats and the activity log are stored in
`~/Scopus/app/data/history/chat_history.sqlite3`, so they are included in WSL exports and
backups (section 26).

Keep the Ubuntu window open while you use Scopus: WSL stops the distro (and with it
all containers) shortly after the last Ubuntu window is closed. The containers start
again automatically the next time you open `wsl -d ScopusAI`.

---

# 13. Stop Scopus AI

When finished:

```bash
cd ~/Scopus
docker compose down
```

This stops all four containers.

Your data remains preserved.

You can also stop only the AI application:

```bash
docker stop scopus-ai-gpu
```

and later:

```bash
docker start scopus-ai-gpu
```

Restart only the application:

```bash
docker restart scopus-ai-gpu
```

Qdrant remains running.

---

# 14. Check application logs

If the UI does not open:

```bash
docker logs scopus-ai-gpu
```

Live logs:

```bash
docker logs -f scopus-ai-gpu
```

Exit log viewing with:

```text
Ctrl+C
```

Qdrant logs:

```bash
docker logs scopus-qdrant
```

Embedding service (ingest progress is printed here too):

```bash
docker logs -f scopus-embedding
```

Language model server (model loading, GPU offload, requests):

```bash
docker logs scopus-llm
```

Readiness of the search service (models, language model server, index) without signing in:

```bash
curl -s http://localhost:7860/api/health
```

---

# 15. Check service ports

Scopus UI:

```text
7860
```

Embedding service (reachable from this PC only by default):

```text
7861
```

Qdrant:

```text
6333
```

MariaDB has no published port (internal to Docker).

Check:

```bash
curl http://localhost:6333/collections
```

Check UI from WSL:

```bash
curl http://localhost:7860
```

---

# 16. Updating Python code offline

Your v1.2 Compose configuration uses:

```yaml
volumes:
  - ./app:/app/app
  - ./models:/app/models:ro
```

Therefore you can edit Python code without rebuilding Docker.

For example:

```bash
nano ~/Scopus/app/search_service/query.py
```

Then:

```bash
docker restart scopus-ai-gpu
```

No rebuild. The code is organised as:

```text
app/core/                shared by both services (settings, accounts, activity log)
app/search_service/      search page: query.py, guard.py (what is answered),
                         filters.py (keyword lists), followup.py, history.py, server.py
app/embedding_service/   embedding service
```

Files in `app/search_service/static/` (the chat page and admin panel) need no restart:
reload the browser page.

After changing `app/core/` restart both containers. For the embedding service
(`app/embedding_service/`, `app/ingest.py`) restart that container:

```bash
docker restart scopus-embedding
```

---

# 17. Build the paper index (embedding service)

Open:

```text
http://localhost:7861
```

Sign in with an **administrator** account (same accounts as the search page; it has its own
login, so sign in here even if you are already signed in to the search page). Normal users
cannot use the embedding service.

1. **Select data folder** – click the folder that contains the MySQL table files
   (`.frm`, `.MYD`, `.MYI`), e.g. `mysql`. Folders with data show a badge such as `3 tables`.
2. Untick tables you do not want.
3. Mode: **Replace index** (fresh index) or **Add to index** (only new DOIs are added).
4. Press **Start ingest** and follow the progress. An alert appears when it is finished.

The search page switches to the new index automatically; no restart is needed.
Searches keep using the old index while the ingest runs, and a failed or cancelled ingest
changes nothing.

The folders shown in the embedding service are those under `~/Scopus/app/data`
(`IMPORT_DIR` in `~/Scopus/.env`).

Command-line alternative:

```bash
cd ~/Scopus
./scripts/ingest.sh mysql                  # replace
./scripts/ingest.sh mysql --mode append    # add
```

---

# 18. When you need to rebuild the paper index

You normally do **not** rebuild the database when changing:

```text
search code (app/search_service/)
settings (.env, app/core/config.py)
guard keyword lists (filters.py)
UI design
generation settings
summary settings
continuation settings
```

You should rebuild (embedding service, **Replace index**) when changing:

```text
the MySQL / RIS source data (removing papers)
embedding model
embedding dimension
chunk size
chunk overlap
stored metadata
index design
```

New papers alone do not need a rebuild: use **Add to index** (section 19).

---

# 19. Adding new papers offline

Copy the new data into its own folder under `~/Scopus/app/data/`, for example a new MySQL export:

```bash
mkdir -p ~/Scopus/app/data/mysql_2026_06
cp /mnt/e/export/*.frm /mnt/e/export/*.MYD /mnt/e/export/*.MYI ~/Scopus/app/data/mysql_2026_06/
```

or RIS files:

```bash
mkdir -p ~/Scopus/app/data/ris_new
cp new_papers.ris ~/Scopus/app/data/ris_new/
```

You can also use **Upload folder from this PC…** in the embedding service, which copies a
Windows folder for you.

Then in http://localhost:7861 open that folder, choose **Add to index** and press **Start ingest**.
Papers whose DOI is already indexed are skipped.

---

# 20. GPU embedding configuration

Your v1.2 system can use:

```text
multilingual-e5-small → CUDA GPU (search + embedding service, fp16)
Qwen2.5 GGUF          → CUDA GPU
BGE reranker          → CUDA GPU (fp16)
Qwen3Guard-Stream     → GPU if it fits (answer safety check), else CPU
Qwen3Guard-Gen        → GPU if it fits (question safety check), else CPU
Qdrant                → CPU
BM25                  → CPU
MariaDB               → CPU (only during an ingest)
```

On a 6 GB GPU the search UI uses about 4.5 GB (LLM + embedding + reranker + answer safety
model; the question safety model runs on the CPU). `docker logs scopus-ai-gpu | grep -i safety`
shows where each safety model was loaded. The search UI and an ingest share the GPU memory.
Both work, but an ingest is about 1.5x faster with the search UI stopped:

```bash
docker stop scopus-ai-gpu       # before a large ingest
docker start scopus-ai-gpu      # afterwards
```

Check GPU utilization while asking a question:

```bash
watch -n 1 nvidia-smi
```

Then submit a query in:

```text
http://localhost:7860
```

GPU memory and utilization should increase.

---

# 21. Answers only from the indexed papers

Each answer is written in one call to the language model server from the numbered source papers, and
every sentence is checked against those papers before it is shown (see "Answers only from the indexed
papers" in `README.md`):

```text
question → guard → search → numbered sources → model writes with citations
        → each finished sentence: supported by a source? numbers and names in the sources?
        → only supported sentences are shown → summary (checked the same way)
```

`LLM_ANSWER_TOKENS` (default 900, about 3,500 characters) is the longest answer; an answer cut off at the
limit loses only its unfinished last sentence. Removed sentences are logged:

```bash
docker logs scopus-ai-gpu | grep Grounding
```

---

# 22. Configuration file

Runtime settings live in `.env` (they override the defaults in `app/core/config.py`):

```bash
nano ~/Scopus/.env
```

Typical runtime values:

```text
LLM_MODEL=qwen2.5-3b-instruct-q4_k_m.gguf   # model file in ./models
LLM_PARALLEL=1           # answers written at the same time (raise on a larger GPU)
LLM_CTX=4096             # context per answer
LLM_GPU_LAYERS=all       # all model layers on the GPU
EMBED_BATCH_SIZE=128
EMBED_FP16=true
IMPORT_DIR=./app/data    # folders shown in the embedding service
```

On a larger GPU on the offline PC, raise `LLM_PARALLEL` (and optionally use a larger model) and measure
with `./scripts/load_test.sh -e LLM_PARALLEL=4` (see section 24).

After changing `.env`, recreate the containers (`docker restart` does not re-read `.env`):

```bash
cd ~/Scopus
docker compose up -d
```

---

# 23. Do not rebuild Docker for normal application changes

Do not run:

```bash
docker compose build
```

for ordinary Python modifications.

Use:

```bash
docker restart scopus-ai-gpu
```

A Docker rebuild is needed mainly when changing:

```text
Dockerfile
requirements.txt
Python libraries/packages
CUDA build configuration
language model server (Dockerfile.llm, llama.cpp version)
system Linux packages
```

Such rebuilds need Internet, so do them on the online PC (`./scripts/upgrade_image.sh` for the
quick 1.0 → 1.1 path, `./scripts/build_online.sh` for a full rebuild) and export the distro again.

---

# 24. Full offline operation test

A useful final test is to disconnect:

```text
Wi-Fi
Ethernet
VPN
```

Then:

```bash
wsl -d ScopusAI
```

and:

```bash
cd ~/Scopus
docker compose up -d
```

Check:

```bash
docker ps
```

Then open:

```text
http://localhost:7860
```

Ask a research question.

Then open http://localhost:7861, select a small folder (or untick all but one table) and
run an ingest with **Add to index**.

If both work, your system is truly offline.

To find out how many users this PC's GPU can serve, run the load test (about 20 minutes; the search
page is offline meanwhile, real accounts and chats are not touched):

```bash
cd ~/Scopus
./scripts/load_test.sh
cat loadtest_results/*/report.md
```

See "How many users? (load test)" in `README.md` for the reference results of an RTX 2060.

---

# 25. Offline troubleshooting

If Docker is unavailable:

```bash
sudo systemctl restart docker
```

Check:

```bash
docker info
```

If GPU is unavailable:

```bash
nvidia-smi
```

then:

```bash
docker exec scopus-ai-gpu nvidia-smi
```

If Qdrant is unavailable:

```bash
docker restart scopus-qdrant
```

Then:

```bash
curl http://localhost:6333/collections
```

If Scopus AI is unavailable:

```bash
docker restart scopus-ai-gpu
```

Then:

```bash
docker logs --tail 100 scopus-ai-gpu
```

If the administrator password is forgotten:

```bash
cd ~/Scopus
docker exec -it scopus-ai-gpu python users.py list
docker exec -it scopus-ai-gpu python users.py reset-password <name>
```

If the embedding service is unavailable or an ingest fails:

```bash
docker logs --tail 100 scopus-embedding
docker restart scopus-embedding
```

The embedding page also shows a status chip for Qdrant, MariaDB and the GPU.
"MariaDB unreachable" right after the first start is normal for ~20 seconds.

If everything needs restarting:

```bash
cd ~/Scopus

docker compose down
docker compose up -d
```

---

# 26. Backup the offline machine

After successfully deploying the offline PC, periodically export it again.

From Windows PowerShell:

```powershell
wsl --shutdown
```

Then:

```powershell
wsl --export ScopusAI D:\Backup\ScopusAI-backup.tar
```

I recommend keeping at least:

```text
ScopusAI-backup-current.tar
ScopusAI-backup-previous.tar
```

---

# 27. Very important: files under `/mnt/c`, `/mnt/d`, etc.

WSL export contains the Linux filesystem:

```text
/home/adonis
/etc
/opt
/usr
/var
```

But Windows-mounted files such as:

```text
/mnt/c/...
/mnt/d/...
```

are **not included** in the WSL TAR.

Therefore keep critical Scopus files under:

```text
/home/adonis/Scopus
```

not:

```text
/mnt/c/Scopus
```

if you expect the WSL export to contain them.

---

# 28. Daily user workflow

Once installed, your normal daily usage is very simple.

Start:

```powershell
wsl -d ScopusAI
```

Inside Ubuntu:

```bash
cd ~/Scopus
docker compose up -d
```

Windows browser:

```text
http://localhost:7860     search
http://localhost:7861     embedding service (administrators, only when adding data)
```

When finished:

```bash
docker compose down
```

That's essentially all an ordinary user needs.

---

## Quick-reference card

```bash
# Start
cd ~/Scopus
docker compose up -d

# Status
docker ps

# Open browser
http://localhost:7860          # search
http://localhost:7861          # embedding service

# GPU status
nvidia-smi
./scripts/verify_gpu.sh

# GPU inside container
docker exec scopus-ai-gpu nvidia-smi

# Qdrant status
curl http://localhost:6333/collections

# App logs
docker logs -f scopus-ai-gpu
docker logs -f scopus-embedding

# Restart app only
docker restart scopus-ai-gpu

# Restart Qdrant only
docker restart scopus-qdrant

# Rebuild research index
http://localhost:7861  (or ./scripts/ingest.sh mysql)

# Stop
docker compose down
```

For your current setup, the **best offline deployment method is to transfer the cleaned WSL TAR**, because it preserves the working Docker/CUDA/Python environment you already spent time building and avoids needing `apt`, `pip`, Docker Hub, or Hugging Face on the offline machine.