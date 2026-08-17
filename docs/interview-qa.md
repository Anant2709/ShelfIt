# Interview Q&A

## Why not let the model update inventory directly from chat?

Because it already failed in measurement: it preferred an expiring same-named
item over asking. Tools call the same services as the UI; dispositions are
undoable. The model proposes; the server owns truth.

## Why 404 instead of 403 for another user's item?

403 confirms existence. 404 does not leak whether the id is real.

## How do calorie targets work?

Mifflin-St Jeor from sex, age, height, weight, activity, then a modest
surplus/deficit for the goal. A typed `calorie_target` overrides. Always labeled
as an estimate, not medical advice.

## Why a separate extras table?

Plan logs are unique per `(user, date, slot)` so a second lunch is a
*correction*. Snacks would collide. Extras are many-per-day and still feed
progress intake and macros.

## How do you know an LLM kcal is not a lab fact?

Every path stores `calories_source` / `macros_source` / `nutrition_source`.
UI shows the source next to the number.

## Scan nutrition — agentic story?

1. Detect groceries (vision or YOLO).
2. Separately: read brand + product **only if confident**.
3. Unreadable → skip network.
4. Readable → Open Food Facts → Exa fallback.
5. Persist with source.

## Why Text JSON columns for allergens / recipe cards?

SQLite ↔ Postgres portability without dialect-specific JSON types. Parse in
Python; closed sets validated before write.

## How do migrations work?

`python -m scripts.migrate` applies Alembic head. Startup never mutates schema.
`tests/test_migrations.py` fails if models drift without a revision.

## Postgres for the interview?

Set `DATABASE_URL` to Neon/Supabase (`docs/postgres.md`). Same code path as
SQLite. If Wi‑Fi dies, unset the URL and use the local file.

## PWA notifications without a push server?

Account toggles + Notification API while the app is open. Service worker caches
the shell; no subscription backend to operate mid-demo.

## What is still honestly unfinished?

See `docs/known-issues.md` — non-food axis, learned-category review tooling,
chat same-name disambiguation. Documented with xfails where possible.
