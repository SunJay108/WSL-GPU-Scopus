# 7. Prepare and export Scopus Ubuntu on the online PC

The WSL export contains everything the offline search PC needs: Ubuntu, Docker Engine,
NVIDIA Container Toolkit, the Docker images and `~/Scopus` (the search app: code, models,
accounts, index).

The index is built by the separate embedding app (`embedding/` in the repository) on its own
PC and brought to the search PC as a bundle (section 17); setting up the embedding PC is
described in `embedding/README.md`.

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
./scripts/upgrade_image.sh     # scopus-ai-gpu:1.0 -> 1.1
docker compose up -d
```

Check http://localhost:7860 (search). Optionally import an index bundle now (section 17) so
the offline PC starts with a ready index.

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
(WSL settings) – create it on the offline PC as shown in section 12.

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
scopus-qdrant       vector database (the imported index)
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

**Once per PC: keep WSL running.** By default WSL shuts a distribution down 15 seconds after
the last WSL window closes, which also stops Docker, the search page and a running import.
Create or edit `C:\Users\<you>\.wslconfig` on Windows (keep any lines already in it):

```ini
[wsl2]
vmIdleTimeout=-1

[general]
instanceIdleTimeout=-1
```

Then run `wsl --shutdown` in PowerShell once and start WSL again. From now on the services
keep running after you close the WSL window (they stop only on `wsl --shutdown` or a Windows
restart; `docker compose up -d` starts them again).

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
http://localhost:7860         Scopus search
http://localhost:7860/admin   Admin panel (users, activity, index import)
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

Qdrant:

```text
6333
```

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
app/core/                settings, accounts, activity log, index layout
app/search_service/      search page: query.py, guard.py (what is answered),
                         filters.py (keyword lists), followup.py, history.py, server.py,
                         index_import.py (bundle import)
```

Files in `app/search_service/static/` (the chat page and admin panel) need no restart:
reload the browser page.

---

# 17. Get the paper index (import a bundle)

The index is built by the embedding app on the embedding PC (`embedding/README.md`):
*Ingest*, then *Export for the search app*. The export is a folder such as
`papers_20261016_081114_20261016_081707` with the Qdrant snapshot and its checksums.

1. Copy the whole folder to the search PC into `~/Scopus/bundles/`, e.g. from a USB drive:

   ```bash
   cp -r /mnt/e/papers_20261016_081114_20261016_081707 ~/Scopus/bundles/
   ```

2. Open http://localhost:7860/admin as an administrator › **Index**. The bundle is listed as
   *ready* (or *cannot be imported*, with the reason).
3. Press **Import** and follow the progress: file check (SHA-256), loading the snapshot,
   checking the loaded index, switching to it.

The search page uses the new index right away; no restart is needed. Searches keep using the
current index during the import, and a damaged bundle or a failed import changes nothing.
Qdrant holds both indexes during the import (about 3.5 GB of RAM per million chunks each);
tick **Remove the current index first** if the PC does not have the memory for both.

Command-line alternative:

```bash
docker exec -it scopus-ai-gpu python import_index.py            # list bundles
docker exec -it scopus-ai-gpu python import_index.py <bundle>   # import
```

After the import the bundle folder can be deleted (**Delete** in the Index tab).

---

# 18. When you need a new index

You normally do **not** need a new index when changing:

```text
search code (app/search_service/)
settings (.env, app/core/config.py)
guard keyword lists (filters.py)
UI design
generation settings
summary settings
continuation settings
```

A new index (built on the embedding PC, then imported here) is needed when changing:

```text
the MySQL / RIS source data (new or removed papers)
embedding model
embedding dimension
chunk size
chunk overlap
stored metadata
index design (INDEX_FORMAT in app/core/paper_index.py, in both apps)
```

---

# 19. Adding new papers

New papers are added on the embedding PC: copy the new MySQL tables or RIS files into a
folder there, choose **Add to index** (papers already indexed are skipped) and export again.
Then import the new bundle here (section 17). The import replaces the whole index, so the new
bundle contains the earlier papers too.

An index built by a version before October 2026 (with `app/data/storage/bm25.pkl`) still answers
questions, with vector search only, until the first import replaces it.

---

# 20. GPU configuration

Your v1.2 system can use:

```text
multilingual-e5-small → CUDA GPU (questions, fp16)
Qwen2.5 GGUF          → CUDA GPU
BGE reranker          → CUDA GPU (fp16)
Qwen3Guard-Stream     → GPU if it fits (answer safety check), else CPU
Qwen3Guard-Gen        → GPU if it fits (question safety check), else CPU
Qdrant                → CPU (vectors + BM25 keyword index in RAM; texts on disk)
```

On a 6 GB GPU the search UI uses about 4.5 GB (LLM + embedding + reranker + answer safety
model; the question safety model runs on the CPU). `docker logs scopus-ai-gpu | grep -i safety`
shows where each safety model was loaded. Papers are embedded on the embedding PC, so this
GPU only serves questions.

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
BUNDLES_DIR=./bundles    # index bundles from the embedding PC
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

Then open http://localhost:7860/admin › **Index** and check that the current index is listed
(and, if you have one, import a bundle from the embedding PC).

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

If an index import fails, the Index tab shows the reason (damaged or incomplete copy, other
index format or model, Qdrant error); the current index is unchanged. More detail:

```bash
docker logs --tail 100 scopus-ai-gpu
docker logs --tail 100 scopus-qdrant
```

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
http://localhost:7860         search
http://localhost:7860/admin   admin panel (administrators; Index tab to import a new index)
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
http://localhost:7860/admin    # admin panel

# GPU status
nvidia-smi
./scripts/verify_gpu.sh

# GPU inside container
docker exec scopus-ai-gpu nvidia-smi

# Qdrant status
curl http://localhost:6333/collections

# App logs
docker logs -f scopus-ai-gpu

# Restart app only
docker restart scopus-ai-gpu

# Restart Qdrant only
docker restart scopus-qdrant

# New research index (bundle from the embedding PC copied into ~/Scopus/bundles)
docker exec -it scopus-ai-gpu python import_index.py <bundle>   (or admin panel › Index)

# Stop
docker compose down
```

For your current setup, the **best offline deployment method is to transfer the cleaned WSL TAR**, because it preserves the working Docker/CUDA/Python environment you already spent time building and avoids needing `apt`, `pip`, Docker Hub, or Hugging Face on the offline machine.