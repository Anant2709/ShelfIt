# Known issues

Defects and limitations that are understood but not yet fixed. Each one that can
be expressed as a test is recorded as a `strict=True` `xfail`, so it fails loudly
the moment the behaviour changes — the test suite is the source of truth, and this
document explains the reasoning.

## Open

### Shelf-life heuristic matches substrings

**Where:** `backend/app/services/shelf_life.py`, `_heuristic_fallback()`
**Test:** `tests/test_shelf_life.py::TestTier4Heuristic::test_substring_matching_misclassifies_shelf_stable_items`

The heuristic tier tests membership with `token in name`, so any name *containing*
a keyword inherits that keyword's shelf life. `"milk chocolate"` is assigned 5
days because it contains `"milk"`, when it actually keeps for months. Same class
of error for `"coconut milk"` and `"beef jerky"`.

Note that tier 2 (the curated dataset) does *not* have this problem — it
tokenizes and matches whole words, which is why `"milkshake"` correctly fails to
match the `"milk"` key there. Only the heuristic is affected.

**Fix direction:** match on word boundaries rather than substrings, and treat the
keyword families as tokens rather than fragments.

### "Upcoming Expirations" has no lower bound

**Where:** `backend/app/api/endpoints/inventory.py`, `reminders()`
**Test:** `tests/test_reminders.py::TestAlreadyExpired::test_expired_items_should_be_distinguishable_from_upcoming`

The query filters `expiration_date <= cutoff` with no floor, so an item that
expired six months ago is returned alongside one expiring tomorrow, with nothing
in the payload to tell them apart. The UI presents both under the same heading.

**Fix direction:** urgency bucketing — expired, due today, due within three days,
due this week — surfaced as an explicit field rather than inferred client-side.

### Shelf-life provenance overstates the external API

**Where:** `backend/app/services/shelf_life.py`, `_fetch_from_web()`

Spoonacular does not return shelf-life data. The call only confirms that a string
is a recognisable food, after which the code returns a hardcoded 5 days but labels
the result `source="api"`. That reads as "a data provider told us five days" when
it means "something we asked confirmed this is food, so we guessed." It is why
sugar and cooking oil are both assigned five days.

Now that external resolutions are cached, this misleading label is also
*persisted* for 30 days, which raises the stakes slightly: a wrong value used to
be recomputed, and now it sticks. The cache namespace is versioned
(`shelf_life_external_v1`) precisely so fixing the logic can invalidate every
previously stored answer.

**Fix direction:** either drop the tier, or relabel it honestly and let the
assistant estimate a shelf life for unresolved items with its own provenance
value.

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
