# Demo script (~8–10 minutes)

Practice once against Neon if using hosted Postgres. Have SQLite fallback ready.

## 0. Setup (before they join)

- Backend: `uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`
- Frontend: `npm run dev` (or production build + preview for PWA install)
- Demo login: `juhi` / `shelfit`
- Optional: Google OAuth button visible only if keys are set

## 1. Auth & kitchen isolation (1 min)

- Open demo kitchen or sign in.
- Mention httpOnly cookie session; Google is a *separate* kitchen when used.
- One line: other users' ids are **404 not 403**.

## 2. Shelf & scan (2 min)

- Show inventory grouped by category.
- Scan a packaged item (or walk through): vision names the grocery; if the
  label is readable → Open Food Facts, else Exa, else `nutrition_source=none`.
- Point at kcal + source on the shelf row.
- Note: produce / unreadable labels skip lookups on purpose.

## 3. Chat grounded in the fridge (1–2 min)

- Ask what's expiring / what to cook.
- Mention the measured failure (urgency ≠ identity) and that tools write
  through the same inventory services a human uses — with undo.

## 4. Diet (3–4 min)

- Profile: Mifflin-St Jeor estimate; typed calorie target wins.
- Generate pantry week vs ideal week + shopping list.
- Expand a recipe card (servings, times, steps, macros).
- Log eaten / skipped with substitute.
- **Log something else** (extra intake) — does not steal a meal slot; gauge moves.
- Charts: kcal vs target, macro split, weight toward goal.

## 5. PWA / offline (30s)

- Account → local reminders (no push server).
- Optional: Install app; toggle offline to show the banner + cached shell.

## 6. Close (30s)

- Alembic-only schema; Postgres is a URL (`docs/postgres.md`).
- 100% backend coverage bar; known issues documented honestly.
- Invite questions.
