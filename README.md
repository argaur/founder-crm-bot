# Founder CRM — Bot

A Telegram-based sales pipeline tool for Indian B2B founders. Forward WhatsApp conversations → AI extracts deal data → stored in Airtable. No manual entry.

---

## Deployment

### Step 1 — Push code to GitHub

```bash
git init
git add .
git commit -m "initial commit"
gh repo create founder-crm-bot --private --source=. --push
```

### Step 2 — Deploy on Railway

1. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
2. Select `founder-crm-bot`
3. Railway detects `Procfile` automatically — no extra config needed

### Step 3 — Add environment variables

In Railway dashboard → your service → **Variables**, add every key from `.env.example`:

| Variable | Where to get it |
|---|---|
| `TELEGRAM_BOT_TOKEN` | @BotFather on Telegram |
| `BOT_NAME` | The username you gave @BotFather (without @) |
| `AIRTABLE_PAT` | airtable.com/account → Developer Hub → PATs |
| `AIRTABLE_BASE_ID` | Your base URL: `airtable.com/{BASE_ID}/...` |
| `ANTHROPIC_API_KEY` | console.anthropic.com |
| `OPENAI_API_KEY` | platform.openai.com |
| `APP_BASE_URL` | Set AFTER deploy — copy the Railway-provided URL |

### Step 4 — Deploy and verify

1. Railway auto-deploys on every push. Click **Deploy** if needed.
2. Watch the build logs — look for:
   ```
   All handlers registered.
   Telegram bot is polling.
   Nudge scheduler started.
   Application startup complete.
   ```
3. Hit the health check endpoint to confirm the API is live:
   ```bash
   curl https://your-service.up.railway.app/health
   # → {"status": "ok"}
   ```

### Step 5 — Test the full flow

```bash
# Register a user
curl -X POST https://your-service.up.railway.app/register \
  -H "Content-Type: application/json" \
  -d '{"first_name": "Gaurav", "email": "you@example.com", "company": "Rethink Systems"}'

# Returns:
# {"user_id": "...", "deep_link": "https://t.me/YourBot?start=..."}
```

Click the deep link → bot sends `/start` confirmation → you're registered.

---

## Dashboard

The Kanban dashboard is a standalone HTML file (`dashboard/index.html`) that reads from Airtable directly.

**Deploy to GitHub Pages:**

1. Copy `dashboard/index.html` into the `argaur/founder-crm-landing` repo:
   ```bash
   mkdir -p ../founder-crm-landing/dashboard
   cp dashboard/index.html ../founder-crm-landing/dashboard/index.html
   ```
2. In `dashboard/index.html`, fill in your Airtable credentials at the top of the script:
   ```js
   const AIRTABLE_PAT     = "your-read-only-pat";
   const AIRTABLE_BASE_ID = "your-base-id";
   const USER_ID          = "your-user-id";
   ```
   > Use a **separate read-only PAT** for the dashboard (scoped to `data.records:read` only).
   > The PAT will be visible in page source to anyone with the URL.

3. Push and the dashboard will be live at:
   `https://argaur.github.io/founder-crm-landing/dashboard/`

---

## Local development

```bash
# Install dependencies
python -m venv venv
source venv/Scripts/activate   # Windows
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Fill in your values in .env

# Run locally
python main.py
# Bot starts polling + API available at http://localhost:8000
```

> **One bot instance at a time.** If Railway is running the bot, pause it in the Railway dashboard before running locally — otherwise messages split randomly between the two instances.

---

## File structure

| File | Purpose |
|---|---|
| `main.py` | Entry point — FastAPI app + bot polling + scheduler |
| `db.py` | All Airtable read/write functions |
| `ai.py` | Claude (text/voice/image extraction) + Whisper transcription |
| `commands.py` | Slash command handlers |
| `flows.py` | Capture flows (forward, voice, image, /addnote, /note) |
| `nudges.py` | APScheduler jobs (daily digest, inactivity nudges) |
| `dashboard/index.html` | Live Kanban dashboard (GitHub Pages) |
| `Procfile` | Railway start command |
| `railway.json` | Railway deploy config + health check |
| `.env.example` | Environment variable template |
