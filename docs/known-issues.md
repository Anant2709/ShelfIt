# Known issues

Defects and limitations that are understood but not yet fixed. Each one that can
be expressed as a test is recorded as a `strict=True` `xfail`, so it fails loudly
the moment the behaviour changes — the test suite is the source of truth, and this
document explains the reasoning.

## Open

### The two resolvers disagree about what counts as a grocery

**Where:** `backend/app/services/llm_estimator.py` and
`backend/app/services/llm_categorizer.py`

Observed live: adding "Dish Soap" produced no category — correctly refused, it is not
food — while the shelf-life resolver gave it 365 days without hesitation. Both
answers are individually defensible, since soap does have a shelf life, but the pair
is incoherent: one resolver decided the item was out of domain and the other did not.

The categoriser refuses more readily because it picks from a closed set and anything
off it is discarded, whereas shelf life accepts any integer in a wide range. So the
constraint that makes categories trustworthy is also what makes the two disagree.

Nothing is corrupted by this today — an uncategorised item is still listed, filtered,
and reported. It matters because "is this even a grocery" is currently answered twice,
independently, by two prompts that cannot see each other.

**Fix direction:** decide domain membership once, before either resolver runs, and
have both honour that answer.

### Learned categories cannot be reviewed or promoted

**Where:** `backend/app/services/category_store.py`

Learned shelf lives have `scripts/review_shelf_life.py`: list what was learned, correct
it, promote it into the curated file so it ships with the repo. Learned categories have
the storage and the provenance but no equivalent tooling, so a wrong category can only
be fixed per-item through the API, and a correct one never graduates to curated data
that a fresh install would inherit.

The store already exposes what a review script needs, and `confirmed_at` exists and is
never set by anything.

**Fix direction:** the same script shape as `review_shelf_life`, minus the staleness
check, which has no meaning without anchors.

### No offline shelf-life inference

**Where:** `backend/app/services/shelf_life.py`

With the pattern-matching tiers deleted, an item that is in neither the curated
file nor the learned table cannot be resolved without the model. So with no
`OPENAI_API_KEY`, or during an outage, a new item gets no date and the user is
asked for one.

This is deliberate rather than a regression: the deleted tiers produced wrong
answers confidently, and asking is better than guessing. It also self-corrects over
time, because every resolved item is stored and promoted entries ship with the
repo, so offline coverage grows with use.

Worth knowing because it is a visible behaviour difference if the key is missing.

### No schema migrations

**Where:** `backend/app/main.py`

Schema is created by `Base.metadata.create_all()` at import time. That handles a
fresh database but cannot evolve one, so any model change requires deleting
`data/shelfit.db`. Blocks any deployment that must preserve data.

This has now bitten twice in development. Adding `category_source` to
`inventory_items` made every query fail with `no such column`, and the only recovery
was moving the database aside and reseeding — losing whatever was in it. On a real
deployment that would have been data loss rather than an inconvenience, which is the
reason this is listed as blocking rather than untidy.

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

### Two spellings of one item could disagree

**Fixed in:** the anchoring commit
**Test:** `tests/test_shelf_life.py::TestConsistency::test_variants_of_one_item_agree`

The curated file was consulted in two places at two different priorities: an exact
match outranked the model, but a whole-word match lost to it. So the file's
authority depended on string formatting, and `"spinach"` resolved to 4 days from
curation while `"fresh spinach"`, `"baby spinach"`, and `"spinach leaves"` each got
independent estimates — measured at 7, 7, and 5 days. Three different answers for
one vegetable.

The two pattern-matching tiers were deleted rather than reordered. Token similarity
now only *retrieves* which known items to show the model, which then anchors the
name to one of them. All four spellings resolve to 4 days on a running server.

The distinction between retrieval and judgment is the point: choosing what to put
in front of a resolver is not the same act as making the decision. A poor retrieval
means the model reasons without a useful reference; a poor match previously became
the answer outright.

### Shelf-life heuristic matched substrings

**Fixed in:** the anchoring commit

`_heuristic_fallback()` tested membership with `token in name`, so any name
containing a keyword inherited that keyword's shelf life — `"milk chocolate"` was
assigned 5 days because it contains `"milk"`. The function was deleted along with
the token-matching tier. `"milk chocolate"` now resolves to 365 days, and the model
declines to anchor it to `"milk"`, which was an explicit counter-example in the
prompt.

This was the last of the two defects originally recorded as strict `xfail` tests.
Both are now gone rather than suppressed, so the suite has no expected failures.

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
