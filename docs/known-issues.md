# Known issues

Defects and limitations that are understood but not yet fixed. Each one that can
be expressed as a test is recorded as a `strict=True` `xfail`, so it fails loudly
the moment the behaviour changes — the test suite is the source of truth, and this
document explains the reasoning.

## Open

### Shelf-life heuristic matches substrings (offline path only)

**Where:** `backend/app/services/shelf_life.py`, `_heuristic_fallback()`
**Test:** `tests/test_shelf_life.py::TestTier4Heuristic::test_substring_matching_misclassifies_shelf_stable_items`

The heuristic tier tests membership with `token in name`, so any name *containing*
a keyword inherits that keyword's shelf life. `"milk chocolate"` is assigned 5
days because it contains `"milk"`, when it actually keeps for months. Same class
of error for `"coconut milk"` and `"beef jerky"`.

**Largely mitigated.** The model tier now answers before both the token match and
the heuristic, so with `OPENAI_API_KEY` configured `"milk chocolate"` resolves to
365 days. Verified on a running server. The defect only surfaces on the offline
path, when no model is available — which is why the test remains a strict xfail
rather than being deleted.

**Fix direction:** match on word boundaries rather than substrings, and treat the
keyword families as tokens rather than fragments.

### Curated and estimated shelf lives can disagree

**Where:** `backend/app/services/shelf_life.py`

Tier ordering means an exact curated entry wins, but a *token* match does not.
So `"spinach"` resolves to 4 days from the curated table, while `"fresh spinach"`
resolves to whatever the model says — 7 days when measured. Two spellings of the
same item can therefore get different answers.

This is a deliberate trade: the model is more accurate than a token match, but a
deliberately curated exact value should still win. The inconsistency is the price.

**Fix direction:** either expand the curated table so more names match exactly, or
let the model see the curated value as a prior and reconcile the two.

### No schema migrations

**Where:** `backend/app/main.py`

Schema is created by `Base.metadata.create_all()` at import time. That handles a
fresh database but cannot evolve one, so any model change requires deleting
`data/shelfit.db`. Blocks any deployment that must preserve data.

**Fix direction:** Alembic, introduced alongside the move to Postgres.

### Single-tenant by construction

There is no user model, so every request reads and writes one global inventory.
Acceptable for a local single-user MVP, but it is an architectural assumption
baked into every query rather than a feature flag.

### Permissive CORS

`allow_origins=["*"]` together with `allow_credentials=True` in
`backend/app/main.py`. Fine for local development, not shippable.

### Frontend has one shared status slot

`App.jsx` funnels every message — errors, progress, success — through a single
`status` string, so concurrent operations overwrite each other's feedback and
nothing can be dismissed independently.

## Fixed

### "Upcoming Expirations" had no lower bound

**Fixed in:** the urgency commit
**Test:** `tests/test_reminders.py::TestAlreadyExpired::test_expired_items_are_distinguishable_from_upcoming`

The query filtered `expiration_date <= cutoff` with no floor and returned nothing
to tell the results apart, so an item that expired six months ago was presented
identically to one expiring tomorrow.

Every entry now carries an `urgency` bucket (`expired`, `today`, `soon`,
`this_week`, `later`, `unknown`) and a signed `days_remaining`, the list is sorted
most urgent first, and the response includes per-bucket counts plus an
`action_required` total. Expired items can also be excluded outright with
`include_expired=false`. Items with a null expiry are now excluded from the query
explicitly rather than relying on SQL's NULL comparison semantics.

The classification is a pure function in `app/services/urgency.py`, computed on
the server so every client agrees and the rule can be tested without a browser.
The same fields are exposed on inventory items themselves as Pydantic computed
fields.

### Shelf-life provenance overstated an external API

**Fixed in:** the provenance commit
**Test:** `tests/test_shelf_life.py::TestProvenance::test_no_answer_claims_an_external_data_provider`

Spoonacular does not publish shelf-life data. The call only confirmed that a
string was a recognisable food, after which the code returned a hardcoded 5 days
and labelled the result `source="api"` — which reads as "a data provider told us
five days" but meant "something confirmed this is food, so we guessed." It is why
sugar and cooking oil were both assigned five days.

The tier was removed rather than relabelled, and replaced with a model that can
actually answer the question, reported as `source="llm"`. Measured against a
running server: ketchup went from 5 days to 365, olive oil from 5 to 365, saffron
from no answer to 365, and sugar now correctly returns no date at all rather than
a fabricated five days.

A regression test asserts that no tier may report `source="api"` again.

### Uploaded filenames were not sanitised

**Fixed in:** the caching commit

`scan_item()` and `upload_image()` interpolated the client-supplied
`file.filename` into a path without reducing it to a basename, so a name
containing `../` could write outside the uploads directory. Both paths now share
a single `_persist_upload()` helper that applies `Path(...).name`, and a test
uploads `../../../../tmp/escaped.png` to prove the file lands inside the uploads
directory.

### Deprecated UTC handling

**Fixed in:** the caching commit

`datetime.utcnow()` is deprecated from Python 3.12. Replaced by
`app/core/clock.py`, which also fixes a latent bug: the previous
`datetime.utcnow().timestamp()` spelling used for upload filenames interpreted a
naive UTC value as local time, so it was wrong by the local UTC offset.
`epoch_seconds()` returns a true POSIX timestamp.

### Expiration write succeeded but returned 500

**Fixed in:** `1c13614`

`POST /api/inventory/{id}/expiration` committed the change and *then* raised.
`db.merge()` returns a new session-managed instance, but the code called
`db.refresh()` on the original transient object, which the session did not own.
Callers saw a failure for a mutation that had already been applied — so the UI
reported an error while the data had in fact changed.

Found by the test suite, then reproduced against a live server before fixing.

### Invalid API key surfaced as an unhandled 500

**Fixed in:** `1c13614`

The chat service guarded a *missing* key but not an *invalid* one, so an expired
key raised `openai.AuthenticationError` and produced a bare 500 with no usable
message. Provider errors are now translated to `ChatUnavailableError` and
returned as 503.

### Configuration depended on the working directory

**Fixed in:** `80fec90`

`env_file` and `database_url` were relative paths, so the effective configuration
depended on where the server was launched from. Following the README and starting
from the repo root silently loaded no `.env` and pointed at an empty database,
while starting from `backend/` picked up the real ones. This is how two divergent
`shelfit.db` files came to exist.
