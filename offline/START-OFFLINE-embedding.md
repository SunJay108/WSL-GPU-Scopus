# Embedding PC – start offline

Package: `Scopus-Embedding-Ubuntu24.tar` (+ `.sha256`). Contains WSL Ubuntu 24.04 with Docker, the
NVIDIA Container Toolkit, the images `scopus-embedding:1.0`, `qdrant/qdrant:v1.12.6`, `mariadb:10.11.19`
and the embedding app in `/home/adonis/ScopusEmbedding`:

- the embedding model `multilingual-e5-small`;
- an **empty index** and an **empty data folder** (`~/ScopusEmbedding/data/import`);
- the administrator account(s) of the online embedding service (change the password, step 5).

## 1. Check the copy (PowerShell)

```powershell
Get-FileHash -Algorithm SHA256 E:\Scopus-Offline\embedding\Scopus-Embedding-Ubuntu24.tar
Get-Content E:\Scopus-Offline\embedding\Scopus-Embedding-Ubuntu24.tar.sha256
```

Both values must be the same.

## 2. Import

```powershell
mkdir D:\WSL\ScopusEmbedding
wsl --import ScopusEmbedding D:\WSL\ScopusEmbedding E:\Scopus-Offline\embedding\Scopus-Embedding-Ubuntu24.tar --version 2
wsl -l -v
```

The distribution starts as user `adonis` (set in `/etc/wsl.conf`).

## 3. Keep the PC and WSL running (once per PC)

An ingest of a million papers takes over an hour. Windows sleep pauses it, and WSL stops when no
WSL window is open:

- Settings › System › Power › Sleep: **Never** when plugged in (or, as administrator:
  `powercfg /change standby-timeout-ac 0`).
- Create `C:\Users\<you>\.wslconfig` (keep any lines already in it), then run `wsl --shutdown` once:

  ```ini
  [wsl2]
  vmIdleTimeout=-1

  [general]
  instanceIdleTimeout=-1
  ```

## 4. Start

```powershell
wsl -d ScopusEmbedding
```

```bash
cd ~/ScopusEmbedding
docker compose up -d
scripts/check_gpu.sh          # GPU visible, embedding speed (passages/s)
```

Open http://localhost:7861.

## 5. Sign in

Sign in with the administrator account of the online embedding service and change its password:

```bash
docker exec -it embed-service python users.py list
docker exec -it embed-service python users.py reset-password admin
```

## 6. Copy the data in

One folder per data set below `~/ScopusEmbedding/data/import`, for example from a USB drive:

```bash
mkdir -p ~/ScopusEmbedding/data/import/mysql_2026_10
cp /mnt/e/export/*.frm /mnt/e/export/*.MYD /mnt/e/export/*.MYI ~/ScopusEmbedding/data/import/mysql_2026_10/
mkdir -p ~/ScopusEmbedding/data/import/ris_2026_10
cp /mnt/e/export/*.ris ~/ScopusEmbedding/data/import/ris_2026_10/
```

A MySQL data folder is a database folder of the MySQL data directory (for XAMPP:
`xampp/mysql/data/<database>/`): `.frm`, `.MYD` and `.MYI` file per table. **Upload folder from this
PC…** in the web UI copies a Windows folder the same way.

## 7. Build the index and export it

1. **Select data folder** – the folders with data show a badge such as `3 tables` or `1 RIS`.
2. **Add to index** (default) for each folder; the first one builds the index. **Initialize index**
   starts over with only the selected folder.
3. After each ingest the index card shows the quality check (both numbers should be close to 100 of 100).
4. **Export for the search app** writes a bundle into `~/ScopusEmbedding/data/exports/`. Copy the whole
   bundle folder to a USB drive, e.g. `cp -r ~/ScopusEmbedding/data/exports/papers_… /mnt/e/`, and
   import it on the search PC (its `START-OFFLINE.md`, step 5). The import replaces the search PC's
   whole index, so always export the complete index.

Measured on an RTX 2060: about 250–320 chunks per second (about 1.1 chunks per paper); 128,000
papers take about 8 minutes, 950,000 papers about 80 minutes.

Details: `~/ScopusEmbedding/README.md` and `~/ScopusEmbedding/Deploy-GPU-Project-Offline.md`.

## Daily use

```bash
cd ~/ScopusEmbedding && docker compose up -d      # start
docker compose down                              # stop
```
