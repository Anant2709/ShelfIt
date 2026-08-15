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
| `SHELF_LIFE_PATH` | `<repo>/data/shelf_life.json` | Curated shelf lives, read-only at runtime |
| `CATEGORIES_PATH` | `<repo>/data/categories.json` | Curated categories, read-only at runtime |
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

### How a category is chosen

The same three-part shape as shelf life, applied to a second kind of uncertainty:
`data/categories.json`, then the learned table, then the model, then `unknown`. A
category the user states is recorded as `user` and inference never overwrites it —
including an explicit "unknown", so a deliberate "I don't know" is distinguishable
from a failed lookup.

The categories are a **closed set**: `produce`, `dairy`, `meat_seafood`, `bakery`,
`grains_pulses`, `spices_condiments`, `snacks_sweets`, `beverages`, `pantry`. The
model picks from that list rather than naming a category, and a reply outside it is
discarded instead of being coerced to the nearest match. Free text would return
"Dairy", "dairy products", and "milk & dairy" for one shelf, and the waste report
that groups by category would fragment while every individual answer still looked
reasonable. This is also why these entries need no anchor: the anchor exists in
shelf life to stop unbounded numbers from disagreeing, and a closed set has nothing
for it to constrain.

The set is one axis on purpose — what the food *is*. `frozen` is deliberately
absent despite being an obvious shelf label, because frozen chicken is both frozen
and meat, and a field mixing food type with storage state cannot be grouped by
either.

## Using and wasting

Taking something off the shelf is an outcome, not a deletion. `DELETE` remains the
correction path — "I added this by mistake" — and still erases the row and its
history. Using or binning an item writes a **disposition** event instead:

- Item `quantity` is *what is on the shelf now*; dispositions are *what happened to
  it*. Partial amounts are supported, because using half the milk is not finishing it.
- When nothing remains the item is **resolved**: it leaves the list, reminders, and
  the assistant's context, but stays fetchable with its history intact.
- Analytics read the event log, never the live fridge.

Each event snapshots the item's name, category, and days remaining at the time, so
renaming or recategorising an item later cannot rewrite last month's report.

**What the report does not claim.** Waste rate is wasted *events* over wasted plus
consumed events. Quantities are never summed across units — a litre of milk and 200g
of paneer are not a number — and there is no money total, because there are no
prices in the data. Inventing a rupee figure would be the same category of error as
the `source="api"` shelf-life tier that was removed.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/inventory/scan` | Upload an image; classify and add, or ask for a label |
| `POST` | `/api/inventory/label` | Supply a label after low confidence |
| `POST` | `/api/inventory/` | Add an item manually |
| `GET` | `/api/inventory/` | List inventory — see query parameters below |
| `GET` | `/api/inventory/{id}` | Fetch one item |
| `PATCH` | `/api/inventory/{id}` | Update name, quantity, unit, category |
| `DELETE` | `/api/inventory/{id}` | Remove an item added by mistake, with its history |
| `POST` | `/api/inventory/{id}/image` | Attach an image to an existing item |
| `POST` | `/api/inventory/{id}/expiration` | Set an expiry date explicitly |
| `POST` | `/api/inventory/{id}/dispositions` | Record using or binning some or all of an item |
| `GET` | `/api/inventory/{id}/dispositions` | That item's outcome history |
| `GET` | `/api/inventory/reminders?days=7` | Items expiring within the window |
| `GET` | `/api/analytics/waste?days=30` | Waste versus use over a trailing window |
| `POST` | `/api/chat` | Ask the inventory-grounded assistant |
| `GET` | `/health` | Liveness |

Filtering and sorting happen in the database, so every client agrees and the rules
are testable without a browser.

| Parameter | Values | Notes |
|---|---|---|
| `search` | any text | Case-insensitive substring on name; `%` and `_` match literally |
| `category` | any category, or `unknown` | Repeatable; `unknown` selects uncategorised items |
| `urgency` | `expired`, `today`, `soon`, `this_week`, `later`, `unknown` | Repeatable |
| `sort` | `urgency` (default), `name`, `category`, `created`, `quantity` | |
| `direction` | `asc` (default), `desc` | |
| `include_resolved` | `false` (default), `true` | Include items already used or binned |

Undated and uncategorised items sort last in **both** directions. An item with no
expiry date is not the most urgent or the least urgent, it is unknown, and sorting a
gap as though it were a value is how "unknown" ends up presented as "fine".

The urgency filter's date ranges are derived from the same thresholds as the badge
each item is displayed with, rather than written out a second time in SQL, and a test
checks every offset across a two-month span to keep the two from drifting apart.

## Training the local model

```bash
cd backend
pip install -r requirements-ml.txt
python -m scripts.train_yolo      # downloads from Roboflow, trains, writes data/model.pt
```

Datasets are not committed. `scripts/train_yolo.py` re-downloads from Roboflow
using the configured credentials.
