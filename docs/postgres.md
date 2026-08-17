# Postgres (Neon / Supabase primary)

Shelf It is database-URL driven. Alembic migrations and SQLAlchemy models use
portable types (`String`, `Text`, `Float`, `Integer`, `Date`, `DateTime`) so the
same revision chain runs on SQLite and Postgres. JSON-looking fields are stored
as `Text` on purpose so SQLite and Postgres stay interchangeable without a
native JSON type.

## Interview primary: hosted Neon or Supabase

1. Create a free Postgres project (Neon or Supabase).
2. Copy the connection string. Prefer the **psycopg** (v3) form:

   ```text
   postgresql+psycopg://USER:PASSWORD@HOST/DB?sslmode=require
   ```

   A plain `postgresql://...` URL also works; the driver in
   `requirements.txt` is `psycopg[binary]`.
3. Put it in `backend/.env` (never commit this file):

   ```bash
   DATABASE_URL=postgresql+psycopg://...
   ```

4. From `backend/` with the venv active:

   ```bash
   python -m scripts.migrate
   uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```

5. Sign in with the demo kitchen or register a new account. Neon starts empty;
   run `python -m scripts.seed` if you want sample fridge rows.

## Wi‑Fi / demo fallback

If the hosted DB is unreachable during the interview, comment out `DATABASE_URL`
(or point it back at the local file) and restart:

```bash
# default when unset:
# sqlite:////absolute/path/to/ShelfIt/data/shelfit.db
```

Then `python -m scripts.migrate` against SQLite again. Say out loud: same app,
same Alembic head, different URL.

## Local Docker Postgres (optional)

`docker-compose.yml` includes an optional `postgres` service. Example:

```bash
docker compose up postgres -d
# DATABASE_URL=postgresql+psycopg://shelfit:shelfit@localhost:5432/shelfit
python -m scripts.migrate
```

Tests and CI stay on SQLite. Production-shaped demos use Neon.

## Type / driver notes reviewed

| Concern | Choice |
|---|---|
| Cross-thread SQLite | `check_same_thread=False` only when the URL starts with `sqlite` |
| ALTER TABLE | Alembic `render_as_batch=True` — required for SQLite, a no-op style on Postgres |
| Enums | Closed sets enforced in Python, stored as `String` |
| JSON lists | `Text` columns (`allergens`, `recipe_json`, …) |
| Driver | `psycopg` 3 already in `requirements.txt` |
