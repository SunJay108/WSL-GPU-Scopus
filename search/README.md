# Scopus AI search app — GPU-ready for Ubuntu 24.04 WSL2

Offline Scopus AI / RAG system: hybrid retrieval in Qdrant (vector + BM25 keyword search, RRF fusion),
BGE reranking and Qwen GGUF answer generation on an NVIDIA GPU.

This folder is the **search app**. The index it searches is built by the **embedding app**
([`../embedding`](../embedding/README.md)) on its own PC and copied here as a **bundle** (Qdrant
snapshot + checksums), which an administrator imports in the admin panel.

| Service | URL | Purpose |
|---|---|---|
| **Scopus search UI** (`scopus-ai`) | http://localhost:7860 | Chat with history: ask research questions and follow-ups; answers cite the indexed papers. Admin panel: users, activity, chats, **index import** |
| **Language model server** (`llm`) | internal | llama.cpp server: one copy of the model, `LLM_PARALLEL` answers at the same time |
| **Qdrant** (`qdrant`) | port 6333 | the imported index: vectors, keyword index, paper texts |

```text
 Embedding PC (../embedding)                     Search PC (this folder)
 ┌───────────────────────────────┐              ┌──────────────────────────────────────────────────┐
 │ MySQL / RIS ─► ingest ─► export├── bundle ──►│ bundles/ ─► import ─► qdrant (alias "papers")    │
 └───────────────────────────────┘  USB / LAN   │                          ▲ search                │
                                                │ Browser :7860 ─► scopus-ai ─► llm (answers)      │
                                                └──────────────────────────────────────────────────┘
```

- The images are built on an **online** PC; the result is copied to a **fully offline** PC.
- Docker Engine in WSL2 (Docker Desktop not required), NVIDIA Container Toolkit, NVIDIA Windows driver.
- Images: `scopus-ai-gpu:1.1` (search UI), `scopus-llm:b10991` (llama.cpp server, built from
  `Dockerfile.llm` on top of the app image), `qdrant/qdrant:v1.12.6`.
- Models are mounted from `./models`, the index lives in `./qdrant_data` (+ `app/data/storage/index_meta.json`).

## 1. Project layout

```text
.
├── app/
│   ├── core/
│   │   ├── config.py           settings (environment variables from .env)
│   │   ├── db.py               SQLite file in app/data/history (accounts, chats, activity log)
│   │   ├── auth.py             accounts, password hashes, login sessions
│   │   ├── web_auth.py         sign-up / login endpoints
│   │   ├── audit.py            activity log shown in the admin panel
│   │   ├── index_meta.py       description of the live index (written by an import)
│   │   ├── paper_index.py      Qdrant layout: dense + BM25 keyword vectors, payload  (same file in ../embedding)
│   │   └── embedding.py        embedding model (multilingual-e5-small) for questions   (same file in ../embedding)
│   ├── search_service/         Scopus search UI (port 7860)
│   │   ├── server.py           REST API: chat, history, admin panel
│   │   ├── query.py            hybrid retrieval + grounded answer; picks up an imported index
│   │   ├── index_import.py     checks and imports index bundles
│   │   ├── llm.py              client of the language model server (service llm)
│   │   ├── grounding.py        checks every answer sentence against the source papers
│   │   ├── followup.py         turns follow-up questions into standalone search questions
│   │   ├── guard.py            decides which questions are answered (Scopus-AI-style)
│   │   ├── safety.py           Qwen3Guard safety models: question check, answer check while streaming
│   │   ├── filters.py          keyword lists used by the guard
│   │   ├── history.py          chat history (own chats; deleted chats kept for admins)
│   │   └── static/             chat UI and admin panel (no external resources; works offline)
│   ├── import_index.py         command line: import an index bundle
│   ├── users.py                command line: accounts (password reset)
│   └── data/
│       ├── storage/            index_meta.json (written by an import)
│       └── history/            chat_history.sqlite3
├── bundles/                    index bundles copied from the embedding PC (BUNDLES_DIR)
├── models/
├── qdrant_data/
├── scripts/                    build, upgrade, export/import offline bundle, GPU check, load test,
│                               safety model download
├── .env / .env.example
├── Dockerfile                  full image build
├── Dockerfile.upgrade          quick 1.0 → 1.1 image upgrade
├── Dockerfile.llm              language model server (llama.cpp llama-server) on the app image
├── third_party/                llama.cpp source archive for Dockerfile.llm (downloaded, not in git)
├── docker-compose.yml
└── requirements.txt
```

The search UI runs as a Python package from `app/` (`python -m search_service`); `docker-compose.yml`
starts it with Qdrant and the language model server.

The git repository contains code and configuration only. Models, bundles, `qdrant_data/` and
`app/data/` are not committed (size and licensing): copy the models into `models/` and
`cp .env.example .env`.

## 2. Required local models

```text
models/
├── multilingual-e5-small/
├── bge-reranker-base/
├── qwen2.5-3b-instruct-q4_k_m.gguf
├── Qwen3Guard-Gen-0.6B/        question safety check
└── Qwen3Guard-Stream-0.6B/     answer safety check
```

Download the two Qwen3Guard models (Apache-2.0, about 1.5 GB each) on the online PC with
`./scripts/download_safety_models.sh`; without them the safety checks are skipped.
Nothing is downloaded at runtime (Hugging Face offline mode is enforced in `docker-compose.yml`).

The embedding app uses the same `multilingual-e5-small`; questions and papers must be embedded by the
same model, which the import checks.

## 3. Index bundles

The embedding app (*Export for the search app*) writes one folder per export:

```text
papers_<index version>_<export time>/
    papers.snapshot    Qdrant snapshot: vectors, keyword index, texts, metadata
    index_meta.json    papers, chunks, sources, model, index format, quality check
    manifest.json      bundle format, sizes and SHA-256 checksums
    SHA256SUMS         the same checksums, for  sha256sum -c SHA256SUMS
    README.txt
```

Copy the **whole folder** into `bundles/` (`BUNDLES_DIR` in `.env`) on the search PC, e.g. from a USB
drive: `cp -r /mnt/e/papers_20261016_081114_20261016_081707 bundles/`. Keep `BUNDLES_DIR` on the Linux
disk of WSL (not `/mnt/c/...`): Qdrant reads the snapshot from there.

A bundle can be imported when
- its index format equals this app's `INDEX_FORMAT` (`app/core/paper_index.py`, currently
  `scopus-papers-v2`) – both apps have the same `paper_index.py` and `embedding.py`;
- it was built with this app's embedding model (`multilingual-e5-small`, 384 dimensions);
- it was made with the same or an older Qdrant version;
- all files are complete and their SHA-256 checksums match `manifest.json`.

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

This builds `scopus-ai-gpu:1.1`, downloads the llama.cpp source and builds the language model server
`scopus-llm` on top of it (about 30 minutes; CUDA kernels for RTX 20xx/30xx/40xx), pulls Qdrant, and
starts the three containers. The embedding app's image is made from `scopus-ai-gpu:1.1` as well
(`../embedding/Dockerfile`), so build this one first.

If `scopus-ai-gpu:1.0` already exists, `./scripts/upgrade_image.sh` is much faster: it builds 1.1 on
top of 1.0 (`Dockerfile.upgrade`: adds PyMySQL and recompiles llama.cpp for RTX 20xx–40xx GPUs),
reusing all other layers.

For a fully offline PC, export the whole WSL distro – see `Deploy-GPU-Project-Offline.md`.

## 6. Import the index

1. Copy the bundle folder into `bundles/` (section 3).
2. Open **http://localhost:7860/admin** as an administrator › **Index**. The tab shows the current
   index (papers, version, when it was built and imported, sources, the quality check of the embedding
   app) and every bundle in `bundles/` with its status: *ready*, *in use*, or *cannot be imported* with
   the reason (wrong format or model, missing or incomplete files).
3. Press **Import**. Progress is shown while it runs:
   checking the files (SHA-256) → Qdrant loads the snapshot → checking the loaded index (number of
   chunks, vector layout, a vector and a keyword search) → switching to the new index → deleting the
   previous index.
4. Searches use the new index right away (no restart); it is warmed up in the background.
5. **Delete** removes a bundle folder that is no longer needed (the index stays in use).

Command line (same steps; the running search UI notices the new index within 15 seconds):

```bash
docker exec -it scopus-ai-gpu python import_index.py               # list bundles and their status
docker exec -it scopus-ai-gpu python import_index.py <bundle>      # import
docker exec -it scopus-ai-gpu python import_index.py <bundle> --verify   # only check the files
```

Safety: the bundle is loaded into a new Qdrant collection `papers_<index version>` and checked before the
alias `papers` is switched to it in one step, so searches keep working on the current index during the
whole import, and a damaged bundle or a failed import changes nothing. Only one import runs at a time.

Memory and disk: during the import Qdrant holds both indexes, i.e. twice the memory and disk of one
index (about 3.5 GB of RAM and 6 GB of disk per million chunks each). If that is too much, tick
**Remove the current index first** (`--low-memory`): it deletes the current index before loading the new
one – half the memory, but no search during the import, and no index if the import fails.

Measured on the development PC (RTX 2060, hard disk, WSL2 with 16 GB): a 5.5 GB bundle with
1,082,282 papers (1,381,112 chunks) was imported in 7.8 minutes – file check 70 s, loading 5.7 min,
checks 25 s, switch 1 s – while the search kept answering from the previous index; the lowest free
memory meanwhile was 6.2 GB. The service noticed the new index within 15 s and warmed it up in 27 s.
A 0.66 GB bundle (136,671 papers) takes about 1 minute.

## 7. Ask questions (Scopus search UI)

Open **http://localhost:7860** – a chat interface similar to ChatGPT:

- **History sidebar**: every chat is saved. *New chat*, search, rename (✎), delete (🗑),
  *Delete all my chats*. History is stored on the server in `app/data/history/chat_history.sqlite3`,
  so it survives browser resets and is part of backups / the WSL export.
- **Follow-up questions** in the same chat ("What barriers did they report?") are rewritten into a
  standalone search question using the conversation; the UI shows what was searched. Only real
  follow-ups are rewritten (`app/search_service/followup.py`): a message that names its own subject
  ("what is graphene", "tell me about Tokyo") is searched and checked exactly as in a new chat, so an
  earlier topic never takes over a new question.
- **Answers** stream in while they are written: summary, detailed answer with clickable citations
  `[n]`, reference cards (journal, year, DOI link, relevance) and three suggested follow-up questions.
  The same question gives the same papers: candidates with tied search scores are ordered by id.
- **Export** a chat as Markdown (⤓ in the top bar); **copy** a single answer.

### Accounts and admin panel

- **Sign in / create account** on http://localhost:7860. The **first account created becomes the
  administrator**; later accounts are normal users. With `ALLOW_SIGNUP=false` in `.env` only
  administrators can add users. Passwords are stored as salted scrypt hashes; a login lasts
  `SESSION_DAYS` (default 7).
- Users see and manage **only their own chats** (rename, delete, delete all). Deleting hides a chat
  from its owner; it is kept for administrators.
- **Admin panel** – http://localhost:7860/admin (link in the sidebar for administrators):
  - *Activity*: every action – sign-ups, logins and failed logins, questions (with the guard decision
    and answer status), renamed / deleted / exported chats, password changes, index imports, admin
    actions – filterable by user, action and text.
  - *Chats*: all chats of all users, including deleted ones, readable in full.
  - *Users*: add users, disable / enable accounts (signs them out), make admin / user, reset passwords.
  - *Index*: the current index and the bundles to import (section 6).
- The activity log cannot be edited or deleted from the UI.

Forgotten administrator password (no Internet needed):

```bash
docker exec -it scopus-ai-gpu python users.py list
docker exec -it scopus-ai-gpu python users.py reset-password <name>
docker exec -it scopus-ai-gpu python users.py create-admin <name>
```

Accounts, chats and the activity log are stored in `app/data/history/chat_history.sqlite3`.

### Which questions are answered

Like Scopus AI, the guard (`app/search_service/guard.py`) judges what a question is about instead of only matching
words:

| Step | What happens |
|---|---|
| 1. Hard keywords | Names and phrases in `HARD_BLOCK_KEYWORDS` (`app/search_service/filters.py`), e.g. political leaders, extremist groups, conspiracy slogans, are always refused. Spelling variants are caught (`xijinping`, `Xi-Jinping`, full-width letters). Asking what a religious group thinks ("what do Muslims think about it?") is refused too. |
| 2. Safety model (question) | **Qwen3Guard-Gen-0.6B** (`app/search_service/safety.py`) rates the question Safe / Controversial / Unsafe: violence, illegal acts, sexual content, self-harm, personal data, unethical acts, copyright, jailbreak attempts and politics. Unsafe is refused; Controversial only for Political and Jailbreak (`SAFETY_INPUT_CONTROVERSIAL`). Research questions on sensitive subjects (suicide prevention, opioid epidemiology, explosive detection) pass. |
| 3. Language model | The local model classifies the question: research → answered; restricted topic (politics, war/military, religion, ideology, conspiracy), personal information about a person, harmful requests, off-topic chat → a short explanation instead. |
| 4. Topic check | Words in `TOPIC_KEYWORDS` that also have research meanings ("support vector machine", "heart attack", "nuclear magnetic resonance", "air pollution in China") only block if the model confirms the question is really about that topic. For a follow-up, both the message as typed and the rewritten question are checked. |
| 5. Relevance | If the best paper scores below `RELEVANCE_MIN_SCORE` the answer is "no relevant papers" instead of an answer from unrelated sources; below `RELEVANCE_WARN_SCORE` the answer carries a "loosely related papers" notice. |
| 6. Safety model (answer) | **Qwen3Guard-Stream-0.6B** checks the answer token by token while it is written. The page shows only text that has been checked (about one sentence behind), and an answer judged Unsafe is stopped and replaced by a notice. The summary and suggested questions are checked too. |

Steps 2 and 3 run at the same time. `docker logs scopus-ai-gpu` shows each decision, e.g.
`Guard: RESTRICTED (keyword 'xi jinping')`, `Guard: HARMFUL (safety model: Unsafe (Violent))` or
`Answer stopped by the safety check: Unsafe (Violent)`; the admin panel shows the same reason.
Edit the lists in `app/search_service/filters.py` and restart the container to change the policy; `GUARD_MODE=keywords`
in `.env` switches back to plain keyword blocking, `SAFETY_INPUT=false` / `SAFETY_OUTPUT=false` turn the safety
models off.

The safety models use the GPU when they fit next to the other models (`SAFETY_*_DEVICE=auto`), otherwise
the CPU. On a 6 GB GPU the answer check runs on the GPU and the question check on the CPU; together they
add about 0.8 s per answer (10.3 s instead of 9.5 s) and 0.5 s per refused question.

### Answers only from the indexed papers

The language model may only restate what the retrieved papers say (`app/search_service/grounding.py`):

1. **Sources in, citations out.** The model receives the top `ANSWER_SOURCES` papers (title, year, journal,
   abstract) as numbered sources and is told to use nothing else, to cite a source after every sentence and
   to reply `NO_ANSWER` when the sources do not answer the question. Decoding is greedy
   (`LLM_TEMPERATURE=0`). The references under the answer are exactly these papers.
2. **Every sentence is checked before it is shown**, while the answer streams:
   - the cross-encoder of the search scores the sentence against each source; the best score must reach
     `GROUNDING_MIN_SCORE`;
   - every number (results, years, sample sizes) must occur in the sources;
   - every name, acronym or capitalised term ("ResNet", "LSTM", "Smith et al.") must occur in the sources.

   Sentences that fail are not shown (they are logged: `docker logs scopus-ai-gpu | grep Grounding`, and
   counted in the admin panel). Sentences that only say what the sources do not cover are kept. The
   citation `[n]` of every kept sentence points to the source that supports it best (a model that
   writes "(n)" at the end of a sentence or list item is understood the same way).
3. **The summary is checked the same way**; it is written from the checked answer only.
4. **No supported sentence → no answer.** If nothing survives the check, or the model replies `NO_ANSWER`,
   the user gets "The papers found in the database do not answer this question" instead of an answer.

Measured on 12 research questions (index of 1,082,282 papers): 75 of 76 sentences of real answers passed
the check, and 0 of 96 fabricated sentences passed — invented numbers and names,
off-topic facts, and plausible general knowledge that the papers do not state ("deep learning needs large
labelled datasets", "further research is needed"). In the end-to-end test, hallucinated sentences injected
into the model's reply never reached the screen, and a reply with nothing supported became "the papers do
not answer this question".

Limit: the support score measures whether a passage backs a sentence, not strict logical entailment, so a
sentence that rewords a paper's finding inaccurately but stays on its subject can still pass. Raise
`GROUNDING_MIN_SCORE` for stricter answers (more sentences are dropped) or lower it for fuller answers.

## 8. GPU settings

`.env`:

```text
LLM_MODEL=qwen2.5-3b-instruct-q4_k_m.gguf   # model file in ./models (any GGUF instruct model)
LLM_PARALLEL=1           # answers written at the same time (server slots)
LLM_CTX=4096             # context per answer: sources + question + answer
LLM_GPU_LAYERS=all       # all model layers on the GPU; a number (20, 15, …) if VRAM is short
```

GPU memory on a 6 GB card: Windows ≈ 0.6 GB, language model server ≈ 2.2 GB (3B model, one slot),
search UI ≈ 2 GB (embedding model, reranker, answer safety model; the question safety model runs on the
CPU). The index is built on the embedding PC, so the search PC's GPU serves questions only.

### Scaling to a larger GPU

The parts that limit the number of users are separated so each can grow with the GPU:

| Part | How it scales |
|---|---|
| Language model (`llm`) | llama.cpp server: one copy of the weights, `LLM_PARALLEL` slots decoded together (continuous batching). More GPU memory → more slots → more answers at the same time. A faster GPU writes each answer faster. |
| Model size | `LLM_MODEL` can point to a larger GGUF model (e.g. a 7B or 14B instruct model) for better answers; the grounding check still keeps only supported sentences. |
| Search UI | Answer streams are asynchronous (no thread per waiting user); up to `LLM_PARALLEL` questions are processed at once, the rest wait in arrival order. |
| Safety models | Put on the GPU automatically when they fit (`SAFETY_*_DEVICE=auto`), on the CPU otherwise. |
| Retrieval | Vector + BM25 search in Qdrant (vectors and keyword index in RAM, texts on disk) + reranker take well under 1 s per question. |
| Index size | Built and exported on the embedding PC; the search PC only needs RAM for the index (about 3.5 GB per million chunks). |

To use a larger GPU: set `LLM_PARALLEL` (memory per slot: about 150 MB for this 3B model at
`LLM_CTX=4096`, more for larger models), run `docker compose up -d`, then measure with
`./scripts/load_test.sh -e LLM_PARALLEL=4` and compare runs with `--compare`.

### How many users? (load test)

```bash
./scripts/load_test.sh                                         # full test, about 20 minutes
./scripts/load_test.sh -e LLM_PARALLEL=4 -- --scenarios baseline,burst   # other settings, a few minutes
python3 scripts/load_test.py --compare loadtest_results/*      # table of all runs
```

The test starts a throwaway copy of the search service with its own empty database (real accounts and
chats are not touched). The search page is offline while it runs (GPU memory). `LLM_*` settings restart
the language model server with them for the test. Reports are written to `loadtest_results/<run>/report.md`.

The language model server writes `LLM_PARALLEL` answers at the same time; further questions wait in
arrival order. Reference results (RTX 2060 6 GB, 127,883 papers, one slot, safety models and sentence
check on):

| | Result |
|---|---|
| One research question | 8.0 s (first text after 2.9 s); refused question 1.1 s |
| Time per stage | check 0.8 s, search 1.0 s, answer 2.7 s, summary 1.1 s, follow-up suggestions 1.3 s |
| Throughput | 490–585 answers per hour |
| 16 users asking at the same moment | all answered after 115 s (median 57 s); no errors |
| 10 users asking every ~60 s for 6 min | mean response 13.5 s, p95 25.6 s |
| 48 users waiting at once | the rest of the site stayed responsive (chat list max 1.3 s) |
| GPU | 5.1 of 6.1 GB, 42–55 % busy, max 71 °C |

Estimated number of users who keep asking (think time = time spent reading between questions):

| Think time | Mean response ≤ 30 s | Mean response ≤ 60 s |
|---|---|---|
| 60 s | 11–13 users | 16–19 users |
| 120 s | 17–22 users | 24–28 users |
| 300 s | 36–45 users | 46–56 users |

Logged-in users who are idle or reading old chats cost nothing; only questions use the GPU.

Parallel slots on the same RTX 2060 (users = think time 120 s, mean response ≤ 60 s):

| `LLM_PARALLEL` | 8 users at once: answers/min | Wait of the 2nd of 2 users | Users |
|---|---|---|---|
| 1 | 10.1 | 6.0 s | 24–28 |
| 2 | 11.6 | 0.4 s | 37 |
| 4 | 12.8 | 0.4 s | 41 |

On this GPU the gain from more slots is small: most GPU time goes to reading the prompts (the sources,
about 1,500 tokens per answer, at about 900 tokens/s), which is compute-bound. A faster GPU reads and
writes several times faster and runs more slots, so capacity grows with both; measure it there with
`./scripts/load_test.sh -e LLM_PARALLEL=N`.

Keep everything on the GPU whenever it fits (an earlier test with the language model on the CPU took
72 s per answer instead of 9 s). `SUGGEST_FOLLOWUPS=false` saves the follow-up stage (about 15 % more
capacity). `LLM_ANSWER_TOKENS` caps the longest answer (default 900 tokens).

## 9. Verify GPU acceleration

```bash
./scripts/verify_gpu.sh
```

Expected: `llama_supports_gpu_offload = True` and `torch.cuda.is_available = True`.

## 10. Logs and troubleshooting

```bash
docker compose ps
docker logs -f scopus-ai-gpu        # search UI; import progress is printed here too
docker logs -f scopus-llm           # language model server
docker logs -f scopus-qdrant
curl -s http://localhost:7860/api/health   # search UI ready? (models, language model server, index)
```

| Symptom | Fix |
|---|---|
| "No search index is available yet" in the search UI | Import a bundle (section 6) |
| A bundle shows "cannot be imported" | The reason is listed: copy the bundle again if a file is missing, incomplete or damaged; update the older app if the index format differs |
| A bundle does not appear in the Index tab | The folder must be directly below `bundles/` and contain `manifest.json` |
| Import fails while Qdrant loads the snapshot | `docker logs scopus-qdrant`; usually not enough disk or memory for two indexes – free space, or use *Remove the current index first* |
| "The language model is not available right now" | `docker ps` should show `scopus-llm (healthy)`; `docker logs scopus-llm` shows model loading or GPU memory errors (lower `LLM_PARALLEL` or `LLM_CTX`) |
| A research question is refused | `docker logs scopus-ai-gpu \| grep Guard:` shows why; adjust `app/search_service/filters.py`, or lower `RELEVANCE_MIN_SCORE` for "no relevant papers" |
| "The papers found in the database do not answer this question" | The model found nothing in the sources, or no sentence passed the check; `docker logs scopus-ai-gpu \| grep Grounding` lists dropped sentences |

## 11. Create the fully-offline bundle

After the online build has been tested:

```bash
./scripts/export_offline.sh
```

```text
offline_bundle/
├── images/
│   ├── scopus-ai-gpu-1.1.tar    (search UI and language model server)
│   └── qdrant-1.12.6.tar
├── app/                 application code + data (accounts, index description)
├── bundles/             empty: index bundles from the embedding PC go here
├── models/
├── qdrant_data/
├── docker-compose.yml
├── .env, .env.example
├── import_offline.sh
├── verify_gpu.sh
└── START_OFFLINE.txt
```

Copy `offline_bundle` to the offline PC (USB SSD etc.). The embedding app has its own offline
images (`../embedding/scripts/save_images.sh`).

## 12. Run on the fully-offline PC

Prerequisites: WSL2/Ubuntu 24.04, Docker Engine, NVIDIA Windows driver, NVIDIA Container Toolkit.

```bash
chmod +x import_offline.sh verify_gpu.sh
./import_offline.sh            # docker load + docker compose up -d --no-build --pull never
./verify_gpu.sh
```

Then copy an index bundle from the embedding PC into `bundles/`, import it (section 6) and search on
http://localhost:7860. Everything needed at runtime (models, tokenizer data, UI assets, fonts) is local.

Keep WSL running when no WSL window is open (otherwise WSL stops Docker and the search page 15 seconds
after the last window closes): add to `C:\Users\<you>\.wslconfig`

```ini
[wsl2]
vmIdleTimeout=-1

[general]
instanceIdleTimeout=-1
```

and run `wsl --shutdown` once (see section 12 of `Deploy-GPU-Project-Offline.md`).

## 13. Notes

1. Build and validate the CUDA images online first; do not build `scopus-llm` on the offline PC.
2. `app/core/paper_index.py` and `app/core/embedding.py` are the same files as in `../embedding/app/core/`.
   Change them in both apps together and raise `INDEX_FORMAT` when the index layout changes; the import
   then refuses bundles of the other version instead of searching them wrongly.
3. An index from before the bundle import (built by the earlier built-in embedding service) keeps working
   until the first import replaces it.
4. The search UI requires a login. Qdrant's port 6333 is published for tools and checks; close it in
   `docker-compose.yml` on an untrusted network.

## 14. Typical commands

```bash
docker compose up -d                       # start
docker compose down                        # stop
docker compose restart scopus-ai           # restart the search UI
docker compose build --no-cache scopus-ai  # rebuild the image after dependency changes
docker exec -it scopus-ai-gpu python import_index.py   # list index bundles
```

Python and UI changes under `app/` take effect after restarting the container (the folder is
bind-mounted); no image rebuild is needed.
