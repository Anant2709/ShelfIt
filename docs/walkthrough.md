# File-by-file walkthrough

A cold-open map of the repo for interview navigation. Prefer this over scrolling
the tree live.

## Top level

| Path | Role |
|---|---|
| `README.md` | Quick start, config table, still-to-do |
| `docker-compose.yml` | API + web; optional `postgres` profile |
| `data/` | SQLite DB (local), uploads, curated JSON |
| `docs/` | ADRs, demo script, Q&A, Postgres, known issues, chat flow |
| `backend/` | FastAPI app |
| `frontend/` | Vite + React PWA |

## Backend entry

| Path | Role |
|---|---|
| `backend/app/main.py` | FastAPI app, CORS, routers, schema check on boot |
| `backend/app/core/config.py` | Settings from env / `.env` |
| `backend/app/core/clock.py` | Timezone-aware "today" for expiry and diet |
| `backend/app/db/session.py` | Engine; SQLite `check_same_thread` only |
| `backend/app/db/schema.py` | Alembic helpers used by `scripts.migrate` |
| `backend/scripts/migrate.py` | The only supported schema apply path |
| `backend/alembic/versions/` | Revision chain (head evolves with features) |

## Auth

| Path | Role |
|---|---|
| `backend/app/api/endpoints/auth.py` | Login, register, demo, Google OAuth |
| `backend/app/services/auth.py` | Password hashing, sessions, demo user |
| `backend/app/api/deps.py` | `get_current_user` from cookie |

## Inventory & scan

| Path | Role |
|---|---|
| `backend/app/api/endpoints/inventory.py` | CRUD, scan, dispositions, reminders |
| `backend/app/models/inventory.py` | Item, expiration, disposition + nutrition cols |
| `backend/app/services/inventory.py` | Create, expiry attach, category |
| `backend/app/services/classifier/` | Vision LLM / YOLO / null backends |
| `backend/app/services/packaged_label.py` | Brand + product gate |
| `backend/app/services/nutrition.py` | OFF then Exa |
| `backend/app/services/shelf_life.py` | Curated → learned → LLM tiers |
| `backend/app/services/category.py` | Closed category set |

## Chat

| Path | Role |
|---|---|
| `backend/app/api/endpoints/chat.py` | Streaming conversations |
| `backend/app/services/chatbot.py` | Tool loop |
| `backend/app/services/chat_tools.py` | Inventory-grounded tools |
| `docs/chatbot-backend-flow.md` | Deeper narrative |

## Diet

| Path | Role |
|---|---|
| `backend/app/api/endpoints/diet.py` | Profile, plan, log, extras, progress |
| `backend/app/models/diet.py` | Profile, plan, meals, logs, weigh-ins, extras |
| `backend/app/services/diet.py` | TDEE, plans, matching, progress |
| `backend/app/services/llm_recipes.py` | Week proposals + nutrition estimates |
| `backend/app/services/recipes.py` | Closed sets + curated fallback loader |
| `data/recipes.json` | No-key fallback recipes (heavy cards) |

## Frontend

| Path | Role |
|---|---|
| `frontend/src/main.jsx` | Router + PWA service worker register |
| `frontend/src/App.jsx` | Auth gate, offline banner, local reminders |
| `frontend/src/api.js` | Cookie `credentials: 'include'` fetch helpers |
| `frontend/src/components/AppShell.jsx` | Rail / bottom nav |
| `frontend/src/pages/*` | Login, Shelf, Scan, Diet, Chat, Account |
| `frontend/src/diet/*` | Wizard, dashboard, meal row, charts, gauge |
| `frontend/vite.config.js` | `vite-plugin-pwa` |

## Tests (how to speak about them)

| Path | Role |
|---|---|
| `backend/tests/conftest.py` | Temp SQLite, auth clients |
| `backend/tests/test_diet*.py` | Diet + extras + progress |
| `backend/tests/test_nutrition.py` | Label gate + OFF/Exa wiring |
| `backend/tests/test_migrations.py` | Model ↔ Alembic drift guard |
| `backend/tests/test_auth.py` | 404 isolation patterns |

Run: `cd backend && PYTHONPATH=. pytest` (venv active).
