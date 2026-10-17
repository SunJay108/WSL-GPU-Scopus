# Scopus AI – offline packages

Two separate packages, one per PC. Each is a complete WSL Ubuntu 24.04 with Docker, the
NVIDIA Container Toolkit, the Docker images and one application – nothing has to be
downloaded or installed on the offline PCs except WSL 2 and the NVIDIA Windows driver.

| Folder | For | Contains |
|---|---|---|
| `embedding\` | the **embedding PC** | embedding app (`~/ScopusEmbedding`, http://localhost:7861): ingest MySQL/RIS data on the GPU, export index bundles. Empty index and empty data folder: copy your data in (`START-OFFLINE.md`, step 6). |
| `search\` | the **search PC** | search app (`~/Scopus`, http://localhost:7860): chat UI, admin panel, language model, safety models, the current index and the accounts and chats of the online PC. |

Workflow offline:

```text
 embedding PC: copy data in ─► ingest ─► Export for the search app ─► bundle folder
                                                                        │  USB drive
 search PC:    copy into ~/Scopus/bundles ─► admin panel › Index › Import
```

Start with `START-OFFLINE.md` in each folder. Check every copied `.tar` against its `.sha256`
before importing it.

Offline PC requirements (both):

```text
Windows   10 22H2 / 11 with WSL 2 (wsl --version)
GPU       NVIDIA RTX 20xx / GTX 16xx or newer (compute capability 7.5+)
Driver    NVIDIA Windows driver 580 or newer (PyTorch in the images uses CUDA 13.0)
CPU       x86-64 with AVX2
RAM       16 GB or more (search PC: about 3.5 GB extra per million chunks of index)
```

How the packages are made: `Deploy-GPU-Project-Offline.md` in the repository.
