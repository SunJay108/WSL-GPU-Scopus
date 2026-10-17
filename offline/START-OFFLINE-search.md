# Search PC – start offline

Package: `Scopus-Search-Ubuntu24.tar` (+ `.sha256`). Contains WSL Ubuntu 24.04 with Docker, the
NVIDIA Container Toolkit, the images `scopus-ai-gpu:1.1`, `scopus-llm:b10991`, `qdrant/qdrant:v1.12.6`
and the search app in `/home/adonis/Scopus`: models, the current index
and the accounts and chats of the online PC (index version: admin panel › Index).

## 1. Check the copy (PowerShell)

```powershell
Get-FileHash -Algorithm SHA256 E:\Scopus-Offline\search\Scopus-Search-Ubuntu24.tar
Get-Content E:\Scopus-Offline\search\Scopus-Search-Ubuntu24.tar.sha256
```

Both values must be the same.

## 2. Import

```powershell
mkdir D:\WSL\ScopusSearch
wsl --import ScopusSearch D:\WSL\ScopusSearch E:\Scopus-Offline\search\Scopus-Search-Ubuntu24.tar --version 2
wsl -l -v
```

The distribution starts as user `adonis` (set in `/etc/wsl.conf`).

## 3. Keep WSL running (once per PC)

Create `C:\Users\<you>\.wslconfig` (keep any lines already in it), then run `wsl --shutdown` once:

```ini
[wsl2]
vmIdleTimeout=-1

[general]
instanceIdleTimeout=-1
```

Otherwise WSL stops Docker and the search page 15 seconds after the last WSL window closes.

## 4. Start

```powershell
wsl -d ScopusSearch
```

```bash
cd ~/Scopus
docker compose up -d
docker ps                      # scopus-ai-gpu, scopus-llm (healthy after ~1 min), scopus-qdrant
./scripts/verify_gpu.sh        # GPU visible in the containers
```

Open http://localhost:7860 and sign in with your account (accounts from the online PC are included).
The admin panel is http://localhost:7860/admin.

## 5. A new index from the embedding PC

1. On the embedding PC: *Export for the search app*; copy the bundle folder (e.g.
   `papers_20261020_101500_20261020_110000`) to a USB drive.
2. Copy it into the search app:

   ```bash
   cp -r /mnt/e/papers_20261020_101500_20261020_110000 ~/Scopus/bundles/
   ```

3. http://localhost:7860/admin › **Index** › **Import** (or
   `docker exec -it scopus-ai-gpu python import_index.py <bundle>`). Searches keep using the current
   index until the new one is loaded and checked; then the bundle folder can be deleted there.

Details: `~/Scopus/README.md` (section 6) and `~/Scopus/Deploy-GPU-Project-Offline.md`.

## Daily use

```bash
cd ~/Scopus && docker compose up -d      # start
docker compose down                      # stop
```
