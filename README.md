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
python -m scripts.migrate                # create or update the schema
uvicorn app.main:app --reload
```

All configured paths are absolute and derived from the repo layout, so the server
behaves identically regardless of which directory you launch it from.

The app no longer creates tables on startup. A missing or outdated schema is
logged, not repaired: silently mutating a database on boot is how adding a column
used to look like success and then fail on every query. `python -m scripts.migrate`
is the one command that creates a fresh database, applies outstanding revisions,
or *stamps* a pre-migration file whose tables already match the models. A mismatch
is refused rather than papered over. New model changes need
`alembic revision --autogenerate -m "..."` and a review of the generated file;
`tests/test_migrations.py` fails if a model change ships without one.

After pulling, run `python -m scripts.migrate` again before starting the server.
The auth revision assigns existing inventory to the demo user; it does not wipe
the fridge.

CORS is an explicit localhost list (`http://localhost:5173` and
`http://127.0.0.1:5173`). Cookies cannot be sent to `*`, so the previous wildcard
would have silently dropped the session.

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

Sign in as `juhi` or `juhi@local` / `shelfit`, or use **Open the demo kitchen**.
That password is 7 characters on purpose; the 8-character rule applies to new
accounts only. Migrating a pre-auth database keeps existing rows rather than
wiping them. A new account starts with an empty kitchen. Sessions are an
httpOnly cookie, not a token the JavaScript can read.

Google sign-in is optional. Create an OAuth 2.0 Client ID (Web application) in
Google Cloud, add `http://localhost:8000/api/auth/google/callback` as an
authorized redirect, and set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
Until both are set the Gmail button is hidden, so a half-configured flow cannot
be started.

Hosted Postgres (Neon/Supabase) is supported via `DATABASE_URL` — see
[`docs/postgres.md`](docs/postgres.md). Copy `backend/.env.example` to
`backend/.env` for the full list of keys (including optional `EXA_API_KEY`).

Interview docs live under [`docs/`](docs/): [ADRs](docs/adrs.md),
[demo script](docs/demo-script.md), [Q&A](docs/interview-qa.md),
[file walkthrough](docs/walkthrough.md), [known issues](docs/known-issues.md).

## Still to do

Product slices for the interview are in: auth (incl. Google), diet (extras,
heavy recipes, macros/charts), scan nutrition (OFF + Exa), Postgres URL path,
and PWA (install / offline / local reminders).

Ongoing polish (not blockers for the demo):

1. Items in [`docs/known-issues.md`](docs/known-issues.md) — non-food axis, learned-category review tooling, chat same-name disambiguation.
2. Practice the [demo script](docs/demo-script.md) once on Neon and once on SQLite fallback.

## Tests

```bash
cd backend
pytest                                       # full suite
pytest --cov=app --cov=scripts               # with coverage
```

Tests run against an isolated database with outbound HTTP blocked, so a run
cannot touch your real database, uploads, or API quota. The suite also runs
without the ML stack installed, which doubles as a regression test for the
classifier's graceful degradation.

Per-test setup still uses `create_all` for speed. That is only safe because
`test_migrations.py` asserts the migrations produce the same schema; a model
change without a migration fails there rather than the fixture quietly lying.

Known limitations that can be expressed as a test are recorded as strict `xfail`
rather than prose. See [`docs/known-issues.md`](docs/known-issues.md).

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
| `CHAT_HISTORY_MESSAGES` | `20` | Turns replayed per request; older ones are dropped |
| `CLASSIFIER_BACKEND` | `vision_llm` | `vision_llm`, `yolo`, or `null` |
| `VISION_MODEL` | `gpt-4o-mini` | Model used for image recognition |
| `MAX_DETECTIONS_PER_IMAGE` | `10` | Cap on items recognised in one photo |
| `MODEL_CONFIDENCE_THRESHOLD` | `0.7` | Below this, a scan asks the user to label |
| `CACHE_BACKEND` | `sql` | `sql`, `memory`, or `none` |
| `CACHE_TTL_DAYS` | `30` | Lifetime of cached lookups |
| `MODEL_PATH` | `<repo>/data/model.pt` | Local YOLO weights, for the `yolo` backend |
| `SHELF_LIFE_PATH` | `<repo>/data/shelf_life.json` | Curated shelf lives, read-only at runtime |
| `CATEGORIES_PATH` | `<repo>/data/categories.json` | Curated categories, read-only at runtime |
| `RECIPES_PATH` | `<repo>/data/recipes.json` | Curated recipes for diet plans, read-only at runtime |
| `UPLOAD_DIR` | `<repo>/data/uploads` | Scanned images |
| `GOOGLE_CLIENT_ID` | — | Enables “Continue with Google” |
| `GOOGLE_CLIENT_SECRET` | — | Server-side exchange; never sent to the browser |
| `GOOGLE_REDIRECT_URI` | `http://localhost:8000/api/auth/google/callback` | Must match the Cloud Console redirect |
| `FRONTEND_URL` | `http://localhost:5173` | Where Google sends the browser after sign-in |
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

Recording an outcome is reversible. `DELETE /api/inventory/{id}/dispositions/{event_id}`
puts the quantity back and removes the event rather than writing an opposite one, since
a correction is not an outcome and a matched pair would inflate both counts with things
that never happened. This exists mainly because the assistant can record outcomes
itself — see below.

## The assistant

The chat assistant is grounded in the inventory and can change it. Three decisions
shape how it works.

**Expiry is pre-computed, not handed over as dates.** A model does not reliably know
today's date and cannot be trusted to subtract one date from another, so the prompt
says `2 days left` and `EXPIRED 6 days ago` rather than `2026-08-17`. The wording comes
from the same thresholds as the badges, so the assistant and the interface cannot
disagree about what is urgent. Items are listed most urgent first.

**The inventory is rebuilt every turn and never stored in the transcript.** Only what
the person said and what the assistant replied is durable. A fridge captured in the
history would mean turn four reasons about turn one's shelf and confidently suggests
recipes using milk that has since been drunk.

**Streaming is the only implementation.** `POST /api/chat/` drains the same generator
`POST /api/chat/stream` serves, so the buffered and streamed paths cannot drift into
answering differently — which they would, since the tool loop is the complicated part
and nobody would remember to fix it twice.

### What it is allowed to do

Letting a model write to a database on its reading of a sentence is the riskiest thing
here, so the boundaries are narrow:

| It can | It cannot |
|---|---|
| Record an item as used or binned | Delete anything |
| Add an item the user says they bought | Rename, re-quantify, or recategorise |

**It cannot delete.** Delete erases an item and its whole outcome history; a
disposition appends an event a person can inspect. The model gets the append-only
operations and never the destructive one, so a misread sentence costs a wrong log entry
rather than lost records. The same distinction that makes delete a *correction* and
disposition an *outcome* doubles here as a privilege boundary.

**It cannot name an item, only choose one.** Tools take an `item_id`, and the id must be
one shown to the model on that turn. Matching on a name would let "mark the yogurt as
finished" resolve to an item the model never saw. This is the closed-set principle from
categories applied again: select from what you were given, so a hallucination fails
loudly instead of hitting a real row.

**It can only act on an item the user named.** Two guards are enforced in code rather
than asked for in the prompt, because measurement showed the prompt was not enough. If the
message literally contains an item's name, no other item can be recorded — asked about
paneer, the model was otherwise willing to record the bread, being the most urgent thing
on the shelf. And two items sharing a name cannot both be recorded in one turn, since that
is never a correct reading of one sentence. Neither guard fires when the user names
nothing recognisable ("I finished it"), which keeps ordinary follow-up turns working.

Which of two same-named items was meant is still not guaranteed — see
[known issues](docs/known-issues.md), with the measured numbers.

**Every write it makes is reversible.** Each action is returned to the client with an
undo handle, so a change is visible immediately rather than discovered later, and can be
put back. Events record `source` as `user` or `assistant`, because an action a model
took on its own reading of a sentence should not be indistinguishable from one a person
performed. The undo handle is deliberately withheld from the model: it has no business
reversing its own work.

Failed and refused calls are returned to the model rather than raised, so it can explain
the problem in its reply instead of the request collapsing. The tool loop is bounded,
and the final attempt withholds the tools entirely to force a prose answer.

If the provider fails *after* a tool has already run, the tool summaries become the
reply instead of a 503. The write happened; a bare error would leave the inventory
altered with nothing explaining why.

## Diet

Per-person, which is one of the reasons accounts exist. The intake answers are a
closed set — goal, eating pattern, allergens, meals per day, sex, activity,
cooking time, and preferences — so a typed-in "high protein vegetarian" cannot
fragment later grouping the way free-text categories would have. Weight, height,
age, and a target weight are required. Later weigh-ins are optional history.

After those answers, there are two plan modes:

- **Pantry** — the model writes a week that uses what is on the shelf now
  (expired items are omitted; urgency is written in words so it is not asked to
  subtract dates). Copy item names as given; invented extras show up as missing.
- **Ideal** — the model ignores the fridge and recommends the week the profile
  describes. The server then diffs those grocery names against the shelf.
  Anything not on hand becomes a **shopping list**.

The UI shows all seven days. The model invents titles, ingredient *names*, and
an estimated kcal per meal. It does not get to say what is already in the
fridge: matching is done afterwards, the same way chat tools cannot pick an item
by urgency. `data/recipes.json` is only the fallback when there is no API key —
the same role as the curated shelf-life file, not the menu you choose from.

Calorie targets are Mifflin-St Jeor from the body stats, then a modest deficit
or surplus for the goal, labeled as an estimate, not medical advice. A typed
calorie target still wins. Eating a planned meal logs that meal's kcal.
Skipping with what you ate instead logs the calories you typed, or a model
estimate if you only typed the food and a key is configured; otherwise you are
asked to enter the number. Progress is daily intake versus the target, plus
weight toward the target from the weigh-in history.

## API

Inventory, chat, analytics, and diet require a session cookie. `/health` and the
OpenAPI schema do not.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/auth/register` | Create an account (username, email, password) |
| `POST` | `/api/auth/login` | Sign in with username or email |
| `GET` | `/api/auth/providers` | Whether Google is configured |
| `GET` | `/api/auth/google` | Start Google sign-in |
| `GET` | `/api/auth/google/callback` | Google returns here; sets the session cookie |
| `POST` | `/api/auth/logout` | Revoke the session |
| `GET` | `/api/auth/me` | The signed-in user |
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
| `DELETE` | `/api/inventory/{id}/dispositions/{event_id}` | Undo a recorded outcome |
| `GET` | `/api/inventory/reminders?days=7` | Items expiring within the window |
| `GET` | `/api/analytics/waste?days=30` | Waste versus use over a trailing window |
| `POST` | `/api/chat/` | Ask the assistant; returns the reply and any actions taken |
| `POST` | `/api/chat/stream` | The same turn as server-sent events |
| `GET` | `/api/chat/conversations` | This user's past threads |
| `GET` | `/api/chat/conversations/{id}` | Read a transcript back |
| `DELETE` | `/api/chat/conversations/{id}` | Delete a conversation and its messages |
| `GET` | `/api/diet/questionnaire` | Closed options for the intake form |
| `GET` | `/api/diet/profile` | This user's intake answers |
| `PUT` | `/api/diet/profile` | Save or replace the intake answers |
| `POST` | `/api/diet/plan?mode=pantry` | Generate a week: `pantry` or `ideal` |
| `GET` | `/api/diet/plan` | The latest plan, with uses/missing and a shopping list |
| `GET` | `/api/diet/today` | Today's meals from that plan |
| `POST` | `/api/diet/log` | Mark a slot eaten or skipped; skipped meals can record a substitute |
| `GET` | `/api/diet/adherence?days=7` | Eaten versus skipped over a trailing window |
| `POST` | `/api/diet/weigh-ins` | Record today's (or a past) weight |
| `GET` | `/api/diet/progress?days=7` | Calorie intake, replacements, and weight toward the target |
| `GET` | `/health` | Liveness |

`/api/chat/stream` emits `token`, `action`, `done`, and `error` events. A missing API key
is a 503 before the body starts; a failure once streaming has begun arrives as an
`error` event, because the status code is already sent. A turn that fails is not written
to the transcript, so no question is left sitting there with no answer beneath it.

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
