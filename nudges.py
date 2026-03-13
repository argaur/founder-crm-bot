"""
nudges.py — Scheduled background jobs for Founder CRM.

Jobs:
  1. daily_digest()    — 8:00 AM IST every day
  2. inactivity_nudge() — 12:00 PM IST every day

Uses APScheduler AsyncIOScheduler so jobs run on the same event loop as the bot.
Both jobs catch all exceptions silently — a scheduling error must never crash the bot.
"""

import logging
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Bot

import db

logger = logging.getLogger(__name__)

# IST = UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))

# Inactivity thresholds per stage (days without update before nudging)
STAGE_THRESHOLDS = {
    "Lead": 7,
    "Evaluating": 5,
    "Proposal Sent": 3,
    "Negotiating": 2,
}

# In-memory dedup: set of (user_id, contact_record_id) nudged today.
# Reset daily at midnight via the scheduler.
_nudged_today: set = set()


# ─── Helpers ──────────────────────────────────────────────────

def _days_since(date_str: str) -> int:
    """Returns days since an ISO date string. Returns 0 on parse error."""
    if not date_str:
        return 0
    try:
        # Airtable returns ISO 8601; strip timezone for naive comparison
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        now = datetime.now(tz=timezone.utc)
        return max(0, (now - dt).days)
    except (ValueError, TypeError):
        return 0


def _get_stale_contacts(pipeline: dict) -> list:
    """
    Returns a flat list of (contact_record, days_stale) tuples for contacts
    that exceed their stage's inactivity threshold.
    Only includes active stages (Lead → Negotiating).
    """
    stale = []
    for stage, contacts in pipeline.items():
        threshold = STAGE_THRESHOLDS.get(stage)
        if threshold is None:
            continue  # Skip Won/Lost
        for rec in contacts:
            last_updated = rec["fields"].get("last_updated", "")
            days = _days_since(last_updated)
            if days >= threshold:
                stale.append((rec, days))
    return stale


# ─── Job 1: Daily digest (8 AM IST) ───────────────────────────

async def daily_digest(bot: Bot):
    """
    Sends a morning digest to every registered user with:
    - Contacts needing follow-up today (past inactivity threshold)
    - Active deal counts per stage
    - Hottest deal by heat score
    """
    logger.info("[nudge] Running daily_digest")
    try:
        users = db.get_all_users()
    except Exception as e:
        logger.error(f"[nudge] daily_digest: failed to fetch users: {e}")
        return

    for user_rec in users:
        fields = user_rec.get("fields", {})
        telegram_id = fields.get("telegram_id")
        user_id = fields.get("user_id")
        first_name = fields.get("first_name", "there")

        if not telegram_id or not user_id:
            continue

        try:
            pipeline = db.get_all_contacts(user_id)
        except Exception as e:
            logger.error(f"[nudge] daily_digest: pipeline fetch failed for {user_id}: {e}")
            continue

        active_stages = ["Lead", "Evaluating", "Proposal Sent", "Negotiating"]

        # Count active deals
        counts = {s: len(pipeline.get(s, [])) for s in active_stages}
        total_active = sum(counts.values())

        # Find stale contacts
        stale = _get_stale_contacts(pipeline)
        if stale:
            follow_up_lines = [
                f"• {rec['fields'].get('name', '?')} @ {rec['fields'].get('company', '?')} "
                f"({stage} — {days}d ago)"
                for rec, days in stale[:5]
                for stage in [rec["fields"].get("stage", "?")]
            ]
            follow_up_text = "\n".join(follow_up_lines)
        else:
            follow_up_text = "None — you're on top of everything!"

        # Hottest deal
        all_contacts = [rec for contacts in pipeline.values() for rec in contacts]
        if all_contacts:
            hottest = max(
                all_contacts,
                key=lambda r: (
                    r["heat_score"]["score"] if isinstance(r.get("heat_score"), dict) else 0
                ),
            )
            h_name = hottest["fields"].get("name", "?")
            h_company = hottest["fields"].get("company", "?")
            h_score = (
                hottest["heat_score"]["score"]
                if isinstance(hottest.get("heat_score"), dict)
                else 0
            )
            hottest_text = f"{h_name} @ {h_company} ({h_score}/100)"
        else:
            hottest_text = "No active deals"

        stage_summary = " | ".join(f"{s[:4]}({counts[s]})" for s in active_stages)

        message = (
            f"Good morning, {first_name}! Here's your pipeline:\n\n"
            f"📋 Active deals: {total_active} — {stage_summary}\n\n"
            f"🔔 Follow up today:\n{follow_up_text}\n\n"
            f"🔥 Hottest deal: {hottest_text}"
        )

        try:
            await bot.send_message(chat_id=int(telegram_id), text=message)
            logger.info(f"[nudge] Digest sent to {telegram_id}")
        except Exception as e:
            logger.error(f"[nudge] Failed to send digest to {telegram_id}: {e}")


# ─── Job 2: Inactivity nudge (12 PM IST) ─────────────────────

async def inactivity_nudge(bot: Bot):
    """
    Sends one nudge per stale contact per user.
    Skips contacts already nudged today (in-memory dedup via _nudged_today).
    """
    logger.info("[nudge] Running inactivity_nudge")
    try:
        users = db.get_all_users()
    except Exception as e:
        logger.error(f"[nudge] inactivity_nudge: failed to fetch users: {e}")
        return

    for user_rec in users:
        fields = user_rec.get("fields", {})
        telegram_id = fields.get("telegram_id")
        user_id = fields.get("user_id")

        if not telegram_id or not user_id:
            continue

        try:
            pipeline = db.get_all_contacts(user_id)
        except Exception as e:
            logger.error(f"[nudge] inactivity_nudge: pipeline fetch failed for {user_id}: {e}")
            continue

        stale = _get_stale_contacts(pipeline)

        for rec, days in stale:
            record_id = rec["id"]
            dedup_key = (user_id, record_id)
            if dedup_key in _nudged_today:
                continue

            name = rec["fields"].get("name", "?")
            company = rec["fields"].get("company", "?")
            stage = rec["fields"].get("stage", "?")

            message = (
                f"You haven't updated {name} @ {company} in {days} day(s).\n"
                f"Last stage: {stage}. Still active?\n\n"
                f"Use /context {name} for a pre-call brief, or /lost {name} to close it out."
            )

            try:
                await bot.send_message(chat_id=int(telegram_id), text=message)
                _nudged_today.add(dedup_key)
                logger.info(f"[nudge] Sent inactivity nudge for {name} to {telegram_id}")
            except Exception as e:
                logger.error(f"[nudge] Failed to send nudge for {name} to {telegram_id}: {e}")


# ─── Midnight reset ────────────────────────────────────────────

async def _reset_nudge_dedup():
    """Clears the in-memory dedup set at midnight so each day starts fresh."""
    logger.info("[nudge] Resetting daily nudge dedup set")
    _nudged_today.clear()


# ─── Scheduler factory ────────────────────────────────────────

def create_scheduler(bot: Bot) -> AsyncIOScheduler:
    """
    Creates and returns a configured AsyncIOScheduler.
    Call scheduler.start() in main.py after the event loop is running.
    """
    scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")

    scheduler.add_job(
        daily_digest,
        CronTrigger(hour=8, minute=0, timezone="Asia/Kolkata"),
        args=[bot],
        id="daily_digest",
        replace_existing=True,
        misfire_grace_time=3600,  # Allow up to 1h late start on Railway cold boot
    )

    scheduler.add_job(
        inactivity_nudge,
        CronTrigger(hour=12, minute=0, timezone="Asia/Kolkata"),
        args=[bot],
        id="inactivity_nudge",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        _reset_nudge_dedup,
        CronTrigger(hour=0, minute=0, timezone="Asia/Kolkata"),
        id="reset_nudge_dedup",
        replace_existing=True,
    )

    return scheduler
