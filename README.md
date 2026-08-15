# Shelf It

Grocery tracking with image-based item recognition, inferred expiry dates, and an
inventory-grounded assistant. The goal is to reduce food waste by making logging
groceries nearly effortless and by knowing when things are about to go bad.

Full documentation lives in [`docs/`](docs/).

## Quick start

### With Docker

```bash
export OPENAI_API_KEY=sk-...        # optional; chat is disabled without it
docker compose up --build
```

Frontend on http://localhost:5173, API on http://localhost:8000, interactive API
docs on http://localhost:8000/docs.

### Locally

Backend:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # core API
pip install -r requirements-dev.txt      # adds test tooling
pip install -r requirements-ml.txt       # optional: local YOLO inference
uvicorn app.main:app --reload
```

All configured paths are absolute and derived from the repo layout, so the server
behaves identically regardless of which directory you launch it from.

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Demo data:

```bash
cd backend
python -m scripts.seed --reset
```

The seed set deliberately spans every expiry horizon — already expired, due today,
due this week, long-dated, and one item whose shelf life could not be resolved.

## Tests

```bash
cd backend
pytest                                       # 158 tests
pytest --cov=app --cov=scripts               # with coverage
```

Tests run against an in-memory database with outbound HTTP blocked, so a run
cannot touch your real database, uploads, or API quota. The suite also runs
without the ML stack installed, which doubles as a regression test for the
classifier's graceful degradation.

Two known defects are recorded as strict `xfail` tests rather than prose, so they
fail loudly the moment the behaviour changes. See
[`docs/known-issues.md`](docs/known-issues.md).

## Dependency layout

| File | Contents |
|---|---|
| `requirements.txt` | Core API runtime. No ML dependencies. |
| `requirements-ml.txt` | `torch`, `ultralytics`, `roboflow` for local inference and training. |
| `requirements-dev.txt` | Test tooling. |

The classifier imports `ultralytics` lazily and returns `("unknown", 0.0)` when it
is unavailable, so the API boots and serves every endpoint without a ~2GB
dependency tree. CI relies on this.

## Configuration

Set in `backend/.env` or the environment.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///<repo>/data/shelfit.db` | Storage |
| `OPENAI_API_KEY` | — | Enables the assistant, image recognition, and shelf-life estimation |
| `OPENAI_MODEL` | `gpt-4o-mini` | Assistant and shelf-life estimation model |
| `CLASSIFIER_BACKEND` | `vision_llm` | `vision_llm`, `yolo`, or `null` |
| `VISION_MODEL` | `gpt-4o-mini` | Model used for image recognition |
| `MAX_DETECTIONS_PER_IMAGE` | `10` | Cap on items recognised in one photo |
| `MODEL_CONFIDENCE_THRESHOLD` | `0.7` | Below this, a scan asks the user to label |
| `CACHE_BACKEND` | `sql` | `sql`, `memory`, or `none` |
| `CACHE_TTL_DAYS` | `30` | Lifetime of cached lookups |
| `MODEL_PATH` | `<repo>/data/model.pt` | Local YOLO weights, for the `yolo` backend |
| `UPLOAD_DIR` | `<repo>/data/uploads` | Scanned images |
| `ROBOFLOW_API_KEY` / `ROBOFLOW_WORKSPACE` / `ROBOFLOW_PROJECT` / `ROBOFLOW_VERSION` | — | Training data source |

### How an expiry date is chosen

With no user-supplied date, the shelf life is resolved from two sources of truth
and one resolver. Every answer records where it came from, so a guess is never
mistaken for a fact:

| Order | Source | `source` | Cost |
|---|---|---|---|
| 1 | Exact match in `data/shelf_life.json` — human-authored, read-only at runtime | `dataset` | free |
| 2 | Exact match in the learned table — previously resolved | `learned` | free |
| 3 | The model, shown the closest known items, anchoring where it can | `llm` | one call per new name |
| — | Nothing resolved; no date is invented and the user is asked | `unknown` | free |

A date the user typed is always recorded as `user` and never overwritten.

**Anchoring.** When the model resolves a name, it reports which known item it
reasoned from. `baby spinach` anchors to the curated `spinach` and inherits its 4
days, so variants of one item cannot disagree. The anchor is stored, which turns a
bare number into a checkable claim — `coconut milk: 5, derived from milk` is
visibly wrong on inspection, whereas `coconut milk: 5` is not. The anchor's value
at derivation time is stored too, so editing a curated value flags every entry
derived from it as stale instead of letting them drift silently.

**The app never writes to `data/shelf_life.json`.** That file is human-authored and
version-controlled; mixing machine output into it would make learned values
indistinguishable from curated ones and grant them authority they have not earned.
Learned values live in the database and are promoted only by a person:

```bash
cd backend
python -m scripts.review_shelf_life                     # what has been learned, and from what
python -m scripts.review_shelf_life --approve-anchored  # bulk-approve entries with a known basis
python -m scripts.review_shelf_life --correct "coconut milk" 730
python -m scripts.review_shelf_life --stale             # anchors whose curated value changed
```

Promoting an entry writes it into the curated file, so it resolves for free
thereafter and ships with the repo — a fresh install inherits prior curation. This
is the same human-in-the-loop pattern as image labelling: the system does the work,
flags what it is unsure of, and a person's decision becomes ground truth.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/inventory/scan` | Upload an image; classify and add, or ask for a label |
| `POST` | `/api/inventory/label` | Supply a label after low confidence |
| `POST` | `/api/inventory/` | Add an item manually |
| `GET` | `/api/inventory/` | List inventory |
| `GET` | `/api/inventory/{id}` | Fetch one item |
| `PATCH` | `/api/inventory/{id}` | Update name, quantity, unit, category |
| `DELETE` | `/api/inventory/{id}` | Remove an item |
| `POST` | `/api/inventory/{id}/image` | Attach an image to an existing item |
| `POST` | `/api/inventory/{id}/expiration` | Set an expiry date explicitly |
| `GET` | `/api/inventory/reminders?days=7` | Items expiring within the window |
| `POST` | `/api/chat` | Ask the inventory-grounded assistant |
| `GET` | `/health` | Liveness |

## Training the local model

```bash
cd backend
pip install -r requirements-ml.txt
python -m scripts.train_yolo      # downloads from Roboflow, trains, writes data/model.pt
```

Datasets are not committed. `scripts/train_yolo.py` re-downloads from Roboflow
using the configured credentials.
