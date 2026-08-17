# Known issues

Defects and limitations that are understood but not yet fixed. Each one that can
be expressed as a test is recorded as a `strict=True` `xfail`, so it fails loudly
the moment the behaviour changes — the test suite is the source of truth, and this
document explains the reasoning.

## Open

### Non-food items cannot be represented

**Where:** `backend/app/services/category.py`

Observed live: adding "Dish Soap" produced no category, while the shelf-life resolver
gave it 365 days.

It is tempting to read that as the two resolvers disagreeing about what a grocery is.
They do not. The categoriser holds nine options, all of them food types, and none
applies to soap, so it returned nothing — that is the absence of an applicable
answer, not a judgment about domain membership. And 365 days is correct; unopened
soap does keep about a year. Neither resolver is wrong.

The real gap is that the model has no way to say "a real item on the shelf that is
not food", so such an item comes out looking half-resolved.

Nothing is corrupted by this. The item lists, matches the `unknown` category filter,
and appears in the waste report as an uncategorised row.

**Why it is still open.** Every fix considered so far is worse than the gap:

- A domain gate before both resolvers costs a third call per item and would reject
  legitimate entries. Tracking soap, foil, or bin bags in a kitchen app is a real use,
  and its expiry is useful information.
- A `household` or `non_food` member widens the closed set but makes it answer two
  questions instead of one — the same mixing of axes that `frozen` was rejected for.
- One prompt answering both category and shelf life removes the incoherence but
  couples two things with different lifecycles: shelf life is promoted and corrected
  by a review script and is skipped entirely when the user supplies a date, and a
  single malformed reply would then spoil both answers rather than one.

**Fix direction:** a second axis for what an item *is for* (food or household), kept
separate from the food-type category rather than merged into it.

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

### The assistant picks between same-named items instead of asking

**Where:** `backend/app/services/chatbot.py`, `backend/app/services/chat_tools.py`

Found by measurement, not by reading the code. The probe puts two items called "Paneer"
on the shelf — 200 g expiring tomorrow, 500 g expiring in five days — asks "I used up the
paneer" repeatedly, and undoes every action between trials so each run starts identically.

The first version of the prompt opened with a blanket *"prefer items that are expiring
soonest"*, meant as advice for recommendations. Measured, the model applied it to tool
targets as well:

| Behaviour | Prompt as written | Urgency advice scoped | Plus deterministic guards |
|---|---|---|---|
| Recorded an unrelated item (bread) | 2/5 | still occurred | **0/12** |
| Recorded *both* Paneers | — | 3/8 | **0/12** |
| Recorded exactly one Paneer | 3/5 | 5/8 | 10/12 |
| Asked which was meant | 0/5 | 5/8 | 2/12 |

The worst row is the first. Asked about paneer, it recorded **Whole Wheat Bread** — the
most urgent item in the fridge and the first one listed. Not a near miss; an unrelated
item, because urgency was bleeding from "what should I suggest" into "what should I act
on". Splitting the prompt into a suggesting section and a recording section, with the
exclusion stated outright, made that rarer. It did not remove it, which is the general
lesson: **an instruction in a prompt is a request, not a constraint.**

**What is enforced deterministically now.** Two guards, both in `chat_tools.py`, both
narrow enough to have no plausible false positive:

- *Only an item the user named.* If the message literally contains an item's name, the
  tool refuses any other item. Matching is on the whole name, so "the whole packet" does
  not name "Whole Wheat Bread". If the message names nothing recognisable — "I finished
  it", after a previous turn — no constraint applies, because refusing pronouns would
  break ordinary use.
- *Not two items of the same name in one turn.* Recording both is never a correct reading
  of one sentence. Two items with *different* names is fine ("I used the milk and the
  bread"), and the same item twice is fine ("used half, binned the rest").

**What is still not guaranteed.** Which of two same-named items was meant. That is
genuinely unknowable at the tool layer: the tool receives an `item_id`, and by the time an
id exists the ambiguity is already resolved — the id *is* the disambiguation. The guards
bound the damage to one item that the user did name, and 2/12 of the time it asks
properly, but it usually just picks.

**Why that is tolerable for now.** Every action is shown to the user the moment it
happens rather than discovered later, records `source="assistant"`, and carries an undo
handle. The model cannot delete, so nothing is destroyed. `DELETE
/api/inventory/{id}/dispositions/{event_id}` was built *because* of this finding:
ambiguity could not be prevented, so it was made cheap to correct.

**Fix direction:** two-phase tool calls — the model proposes, the user confirms, the write
happens on confirmation. That is a change to the streaming protocol and the interface, so
it waits for the redesign.

### CORS is localhost-only

Origins are an explicit list (`http://localhost:5173`, `http://127.0.0.1:5173`)
because cookies cannot be sent to `*`. That is enough for local development.
Shipping would also need `cookie_secure=True` behind HTTPS and the real frontend
origin on the list. Google sign-in is implemented but off until
`GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are set. There is still no
password reset or email verification.

### Frontend has one shared status slot

`App.jsx` funnels every message — errors, progress, success — through a single
`status` string, so concurrent operations overwrite each other's feedback and
nothing can be dismissed independently.

## Fixed

### Single-tenant by construction

**Fixed in:** the auth commit
**Test:** `tests/test_auth.py::TestIsolation`

Every request used to read one global fridge. Inventory, conversations, and the
waste report are now scoped to the signed-in user. Another user's id is 404, not
403, so guessing cannot map someone else's kitchen. Existing rows were assigned
to the demo account (`juhi@local`) rather than deleted.

What is still out of scope: password reset, email verification, OAuth, and roles.

### No per-user timezone

**Fixed in:** the auth commit
**Test:** `tests/test_clock.py` (named zone) and
`tests/test_auth.py::TestTimezoneOnRequests`

`clock.today(tz_name)` uses the IANA zone on the user record. Reminders, list
urgency, and chat all pass `user.timezone`. UTC remains the fallback when no
user is in scope, so stored timestamps stay internally consistent.

### Adding a column broke the existing database

**Fixed in:** the Alembic commit
**Test:** `tests/test_migrations.py::TestModelsAndMigrationsAgree::test_models_and_migrations_agree`

Schema used to be created by `Base.metadata.create_all()` at import time. That
creates missing *tables* and never looks inside one it decides already exists. Adding
`category_source` to `inventory_items` made every query fail with `no such column`,
and the only recovery was moving the database aside and reseeding. Adding the chat
feature later created two new tables correctly and silently skipped
`dispositions.source` — a partial failure that looked like success.

The test suite could not catch this. Tests built their schema from the models on
every run, so they only ever exercised the one case `create_all` handles. A test
that proves a schema change *applies* has to start from the old schema, and nothing
recorded what the old schema was.

Migrations replace it. The baseline revision describes the schema as it stood when
`create_all` was removed. A pre-migration database is stamped at that revision
rather than upgraded, and only after the live tables are checked against the
models; a mismatch is refused. `test_models_and_migrations_agree` builds a database
from the migrations alone and diffs it against the models, so a model change
without a migration fails in CI. Startup logs whether the database is current and
does not repair it.

SQLite cannot `ALTER TABLE` in most of the ways a later change needs, so batch mode
is on: those operations become create-copy-swap. That is what makes the auth
migration — a `user_id` on every existing table — possible without deleting the
database.

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
