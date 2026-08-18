# Hosting on Render (friends kitchen)

One public URL serves the React app and `/api`. Login stays an httpOnly
`SameSite=Lax` cookie. The hosted database is the **friends** Supabase
project, not the interview one.

Demo kitchen is off (`ENABLE_DEMO_LOGIN=false`). Friends register or use
Google. Each account is its own fridge.

## 1. Push this branch to GitHub

Render builds from `https://github.com/Anant2709/ShelfIt`. The deploy files
(`Dockerfile`, `render.yaml`) must be on `main`.

## 2. Create the Render web service

1. Sign in at [render.com](https://render.com) with GitHub.
2. **New → Blueprint** and pick `Anant2709/ShelfIt`, or **New → Web Service**
   and choose the repo. Docker runtime. Root directory empty. Health check
   `/health`.
3. The first deploy will fail until secrets are set. That is expected.

## 3. Environment variables

In the service **Environment** tab, set:

| Key | Value |
|---|---|
| `DATABASE_URL` | The **friends** URL from `backend/.env.friends` (psycopg form, `%40` if the password has `@`, `?sslmode=require`) |
| `OPENAI_API_KEY` | Same key as local chat/scan |
| `GOOGLE_CLIENT_ID` | Same as local `.env` |
| `GOOGLE_CLIENT_SECRET` | Same as local `.env` |
| `ENABLE_DEMO_LOGIN` | `false` |
| `COOKIE_SECURE` | `true` |
| `EXA_API_KEY` | Optional |

Do not paste the interview Supabase URL. Render sets `RENDER_EXTERNAL_URL`
itself. The app uses that for `FRONTEND_URL`, CORS, and
`GOOGLE_REDIRECT_URI` so you do not have to type the onrender hostname.

Manual deploy after saving env vars.

## 4. Google after the first successful boot

Copy the public URL (example `https://shelfit.onrender.com`).

In [Google Cloud Console](https://console.cloud.google.com/) → **APIs &
Services → Credentials →** your existing OAuth 2.0 Client:

- **Authorized JavaScript origins:** add `https://YOUR-SERVICE.onrender.com`
- **Authorized redirect URIs:** add
  `https://YOUR-SERVICE.onrender.com/api/auth/google/callback`
- Keep the localhost entries so the laptop interview still works.

**OAuth consent screen:**

- **Testing** (default for a new Cloud project): only emails you list under
  **Test users** can click Continue with Google. Cap is 100. Fine for a
  handful of friends this week.
- **In production:** any Google account can sign in. You do **not** add
  every human. Publish the consent screen (same page, **Publish app**).
  This app only asks for `openid email profile`, which are basic scopes,
  so you usually do not need Google’s full brand verification. People may
  still see “Google hasn’t verified this app” and have to click **Advanced
  → Go to Shelf It**. That is normal for a student/demo project.

Keep localhost redirect URIs so the laptop interview still works.

## 5. Smoke test

1. Open the Render URL. You should see Sign in, Register, and Continue with
   Google. You should **not** see Open the demo kitchen.
2. Register a throwaway account or use Google. The fridge must be empty.
3. Ask a friend to do the same with **their** Google account. They must not
   see your items.

Free Render services sleep after idle time. The first request after a nap
can take a minute.

## Local interview is unchanged

Laptop `.env` still points at the interview database. Vite on :5173 still
shows the demo kitchen. Do not copy `ENABLE_DEMO_LOGIN=false` into the
laptop `.env` unless you want that button gone locally too.
