"""
main.py — Entry point for Founder CRM.

Runs two services in the same asyncio event loop:
  1. Telegram bot (python-telegram-bot v21, polling mode)
  2. FastAPI web server (uvicorn)

Usage:
  python main.py          # local development
  uvicorn main:app ...    # Railway uses the FastAPI app object directly

Railway Procfile:
  web: uvicorn main:app --host 0.0.0.0 --port $PORT
  (Note: when Railway runs uvicorn main:app, the lifespan handler starts the bot.)
"""

import asyncio
import logging
import os
import uuid

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager
from telegram.ext import ApplicationBuilder

import db
import commands
import flows

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
APP_BASE_URL = os.getenv("APP_BASE_URL", "")
BOT_NAME = os.getenv("BOT_NAME", "")  # e.g. "foundercrm_bot" (without @)

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in environment.")


# ─── Build the Telegram application ───────────────────────────

def _build_application():
    """Creates the PTB Application and registers all handlers."""
    telegram_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Register command + callback handlers from commands.py first
    for handler in commands.get_handlers():
        telegram_app.add_handler(handler)

    # Register flow handlers (addnote ConversationHandler before generic text handler)
    for handler in flows.get_handlers():
        telegram_app.add_handler(handler)

    logger.info("All handlers registered.")
    return telegram_app


# ─── FastAPI lifespan ─────────────────────────────────────────

telegram_app = _build_application()


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    """
    FastAPI lifespan context manager.
    On startup: initialise bot, start polling.
    On shutdown: stop bot.

    This runs inside the same asyncio event loop as uvicorn, so the bot's
    async handlers all work natively.
    """
    logger.info("Starting Founder CRM bot...")

    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot is polling.")

    yield  # FastAPI serves requests here

    logger.info("Shutting down...")
    await telegram_app.updater.stop()
    await telegram_app.stop()
    await telegram_app.shutdown()
    logger.info("Bot stopped.")


# ─── FastAPI app ───────────────────────────────────────────────

app = FastAPI(title="Founder CRM API", lifespan=lifespan)

# Allow cross-origin requests from GitHub Pages (dashboard + landing page)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://argaur.github.io", "http://localhost", "http://127.0.0.1"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ─── Request/Response schemas ──────────────────────────────────

class RegisterRequest(BaseModel):
    first_name: str
    email: str
    company: str


class RegisterResponse(BaseModel):
    user_id: str
    deep_link: str


# ─── Endpoints ────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check — Railway pings this to confirm the service is alive."""
    return {"status": "ok"}


@app.post("/register", response_model=RegisterResponse)
async def register(body: RegisterRequest):
    """
    Called by the landing page signup form.
    Creates a user in Airtable and returns a Telegram deep link.
    The user clicks the link → bot receives /start <user_id> → links their Telegram account.
    """
    user_id = str(uuid.uuid4())

    db.create_user(
        user_id=user_id,
        first_name=body.first_name,
        email=body.email,
        company=body.company,
    )
    logger.info(f"Registered new user: {user_id} ({body.email})")

    deep_link = f"https://t.me/{BOT_NAME}?start={user_id}"

    return RegisterResponse(user_id=user_id, deep_link=deep_link)


# ─── Local dev entry point ─────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
