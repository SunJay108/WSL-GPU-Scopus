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
scopus-ai-gpu      1.0
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
scopus-ai-gpu
scopus-qdrant
```

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

Test llama.cpp GPU support:

```bash
docker exec -it scopus-ai-gpu python -c \
"from llama_cpp import llama_cpp; print('GPU offload:', llama_cpp.llama_supports_gpu_offload())"
```

Expected:

```text
GPU offload: True
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
http://localhost:7860
```

No Internet connection is required.

---

# 13. Stop Scopus AI

When finished:

```bash
cd ~/Scopus
docker compose down
```

This stops both containers.

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
nano ~/Scopus/app/query.py
```

Then:

```bash
docker restart scopus-ai-gpu
```

Same for:

```text
config.py
query.py
filters.py
main.py
```

Workflow:

```bash
nano app/query.py

docker restart scopus-ai-gpu
```

No rebuild.

---

# 17. Updating `ingest.py`

For `ingest.py`:

```bash
nano app/ingest.py
```

You usually do not need to restart because:

```bash
docker exec -it scopus-ai-gpu python ingest.py
```

starts a new Python process.

---

# 18. When you need to rebuild the paper index

You normally do **not** rebuild the database when changing:

```text
query.py
config.py
main.py
filters.py
UI design
generation settings
summary settings
continuation settings
```

You should rebuild when changing:

```text
RIS papers
embedding model
embedding dimension
chunk size
chunk overlap
stored metadata
index design
```

To rebuild:

```bash
docker exec -it scopus-ai-gpu python ingest.py
```

Your updated ingestion uses a longer Qdrant timeout and CUDA embeddings.

---

# 19. Adding new RIS papers offline

Copy your RIS file to:

```text
~/Scopus/app/data/ris_index/
```

For example:

```bash
cp new_papers.ris ~/Scopus/app/data/ris_index/
```

Check:

```bash
ls -lh ~/Scopus/app/data/ris_index
```

Then rebuild:

```bash
docker exec -it scopus-ai-gpu python ingest.py
```

Important: your current ingestion pipeline recreates the `papers` collection, so this is a **full rebuild**, not an incremental update.

---

# 20. GPU embedding configuration

Your v1.2 system can use:

```text
multilingual-e5-small → CUDA GPU
Qwen2.5 GGUF          → CUDA GPU
BGE reranker          → GPU where supported
Qdrant                → CPU
BM25                   → CPU
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

# 21. Output truncation protection in v1.2

Your v1.2 system includes:

```text
bounded configuration
+
automatic continuation
+
explicit <END_OF_ANSWER>
+
complete-sentence cleanup
```

So:

```text
generation chunk
      ↓
not finished
      ↓
automatic continuation
      ↓
not finished
      ↓
automatic continuation
      ↓
<END_OF_ANSWER>
      ↓
combine
      ↓
remove END marker
      ↓
display complete answer
```

Therefore `LLM_MAX_NEW_TOKENS` is no longer necessarily the maximum final answer length.

It is primarily the size of one generation segment.

---

# 22. Configuration file

Main configuration:

```bash
nano ~/Scopus/app/config.py
```

Typical runtime values:

```python
LLM_CTX = 4096
LLM_BATCH = 256
LLM_THREADS = 4
LLM_GPU_LAYERS = -1
```

Full GPU layer offload:

```python
LLM_GPU_LAYERS = -1
```

After changing `config.py`:

```bash
docker restart scopus-ai-gpu
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
llama-cpp-python build
system Linux packages
```

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

If it works, your system is truly offline.

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

If both need restarting:

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
http://localhost:7860
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
http://localhost:7860

# GPU status
nvidia-smi

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

# Rebuild research index
docker exec -it scopus-ai-gpu python ingest.py

# Stop
docker compose down
```

For your current setup, the **best offline deployment method is to transfer the cleaned WSL TAR**, because it preserves the working Docker/CUDA/Python environment you already spent time building and avoids needing `apt`, `pip`, Docker Hub, or Hugging Face on the offline machine.