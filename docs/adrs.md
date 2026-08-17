# Architecture Decision Records

Short records of choices that interviewers often probe. Each one names the
trade-off, not just the outcome.

## ADR-001 · Cookie sessions, not bearer tokens in localStorage

**Decision:** Auth is an httpOnly session cookie (`SameSite=Lax`).

**Why:** JavaScript cannot read the cookie, which removes the usual XSS token
theft path. CORS is an explicit localhost list because cookies cannot be sent
to `*`.

**Trade-off:** Cross-site SPA hosting needs careful CORS + cookie flags
(`cookie_secure` for HTTPS). Acceptable for the demo and for a same-site
deploy.

## ADR-002 · Other users' resources return 404, not 403

**Decision:** Cross-user access looks like "not found".

**Why:** A 403 confirms that the id exists. Enumeration is a real risk on
inventory and conversation ids. Diet extras, plans, and inventory all follow
the same rule.

## ADR-003 · The LLM is not trusted for arithmetic or shelf identity

**Decision:** Models propose titles, ingredient *names*, calorie *estimates*,
and packaged brand/product text. Matching against inventory, summing progress,
Mifflin-St Jeor targets, and expiry math stay on the server.

**Why:** Measured failure: the chat tool once recorded bread when asked about
paneer because bread was expiring sooner. Urgency is not identity. Estimates
always carry a `*_source` label (`planned`, `user`, `llm`, `open_food_facts`,
`exa`, `none`).

## ADR-004 · Closed sets over free-text taxonomy

**Decision:** Categories, diet goals, patterns, allergens, slots, and
preferences are fixed vocabularies.

**Why:** Free text fragments every later grouping ("dairy products" vs
"Dairy"). Invalid curated-file entries are dropped rather than trusted.

## ADR-005 · Alembic is the only schema writer

**Decision:** App startup never calls `create_all`. Migrations are applied with
`python -m scripts.migrate`.

**Why:** Silently creating tables on boot hid missing columns until the first
query. Drift is refused; stamps only happen when tables already match.

## ADR-006 · Extra intake is not a plan-slot log

**Decision:** `DietExtraIntake` is a separate table. `DietLog` stays unique on
`(user_id, logged_date, slot)`.

**Why:** Snacks and restaurant food must not steal breakfast/lunch/dinner.
Progress intake = plan logs + extras for the day.

## ADR-007 · Packaged nutrition: readable label → OFF → Exa

**Decision:** Vision extracts brand + product name only when confident. If not
readable, skip all lookups. Otherwise Open Food Facts first; Exa only on miss.

**Why:** Never invent shelf identity from a calorie API. Source is always
stored. Multi-item shelf photos skip nutrition (only single confident
detection + readable label attaches macros).

## ADR-008 · Hosted Postgres primary, SQLite fallback

**Decision:** Interview demo prefers Neon/Supabase via `DATABASE_URL`. Tests
and day-to-day stay on SQLite. Types are portable (`String`/`Text`, no native
enums/JSON).

**Why:** Production-shaped story without forcing Docker live. Wi‑Fi failure
falls back to the local file URL — see `docs/postgres.md`.
