import os
import re
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from telegram.helpers import escape_markdown
from dotenv import load_dotenv

import db
import ai

load_dotenv()
logger = logging.getLogger(__name__)

APP_BASE_URL = os.getenv("APP_BASE_URL", "")

# ─── ConversationHandler states for /addcontact ───────────────
ADD_NAME, ADD_COMPANY, ADD_STAGE, ADD_SOURCE = range(4)

ACTIVE_STAGES = ["Lead", "Evaluating", "Proposal Sent", "Negotiating"]

# Human-readable labels shown to user → Airtable field values stored in DB
SOURCE_LABELS = ["WhatsApp", "Phone / Meeting", "LinkedIn", "Referral", "Other"]
SOURCE_VALUES = ["whatsapp_forward", "manual", "manual", "manual", "other"]


# ─── Utilities ────────────────────────────────────────────────

def md(text) -> str:
    """Escape text for Telegram MarkdownV2."""
    return escape_markdown(str(text) if text is not None else "", version=2)


def _get_user(telegram_id: int):
    """
    Look up a registered user by Telegram ID.
    Returns the Airtable record dict or None.
    Note: db functions are synchronous — no await needed.
    """
    try:
        return db.get_user_by_telegram_id(telegram_id)
    except Exception as e:
        logger.error(f"Error fetching user {telegram_id}: {e}")
        return None


def _flatten_contact(rec: dict) -> dict:
    """
    Adapt an Airtable contact record for use with ai.py functions and display.

    db.py returns Airtable records in the form:
        rec["id"]                       → Airtable record ID (e.g. "recXXXXX")
        rec["fields"]["name"]           → contact name
        rec["heat_score"]               → {"score": int, "label": str}  (injected by get_all_contacts)

    ai.py expects a flat dict with keys like contact_name, company, heat_score, etc.
    This adapter bridges the two.
    """
    fields = rec.get("fields", {})
    heat = rec.get("heat_score", {})
    score = heat.get("score", 0) if isinstance(heat, dict) else 0
    label = heat.get("label", "Cold") if isinstance(heat, dict) else "Cold"
    return {
        "contact_name": fields.get("name", "Unknown"),
        "company": fields.get("company", ""),
        "role": fields.get("role", ""),
        "stage": fields.get("stage", "Lead"),
        "budget_signal": fields.get("budget_signal"),
        "next_action": fields.get("next_action"),
        "heat_score": score,
        "heat_label": label,
        "interaction_count": fields.get("interaction_count", 0),
        "_record_id": rec.get("id"),
    }


async def _not_registered(update: Update):
    base = md(APP_BASE_URL) if APP_BASE_URL else "the signup page"
    await update.message.reply_text(
        f"You're not registered yet\\. Visit {base} to create your account\\.",
        parse_mode="MarkdownV2",
    )


# ─── /start ───────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Three cases:
    1. /start <user_id>  — deep link from landing page signup
    2. /start            — returning registered user
    3. /start            — unregistered user
    """
    telegram_id = update.effective_user.id

    # Case 1: deep link
    if context.args:
        user_id = context.args[0]
        try:
            result = db.link_telegram_to_user(user_id, telegram_id)
            if result:
                await update.message.reply_text(
                    "You're set up\\! Forward any WhatsApp chat or send a voice note to get started\\.\n\n"
                    "Use /help to see all commands\\.",
                    parse_mode="MarkdownV2",
                )
            else:
                await update.message.reply_text(
                    f"Couldn't find an account for that link\\. "
                    f"Visit {md(APP_BASE_URL)} to register\\.",
                    parse_mode="MarkdownV2",
                )
        except Exception as e:
            logger.error(f"Error linking user {user_id}: {e}")
            await update.message.reply_text(
                "Something went wrong during setup\\. Please try the link again\\.",
                parse_mode="MarkdownV2",
            )
        return

    # Cases 2 & 3
    user = _get_user(telegram_id)
    if user:
        first_name = user["fields"].get("first_name", "there")
        try:
            pipeline = db.get_all_contacts(user["fields"]["user_id"])
            # get_all_contacts returns a dict keyed by stage; Won/Lost are closed stages
            active_count = sum(
                len(v) for k, v in pipeline.items() if k not in ["Won", "Lost"]
            )
        except Exception:
            active_count = 0

        await update.message.reply_text(
            f"Welcome back, *{md(first_name)}*\\! "
            f"You have *{active_count}* active deal\\(s\\)\\.\n\n"
            "Use /pipeline to see your full view\\.",
            parse_mode="MarkdownV2",
        )
    else:
        base = md(APP_BASE_URL) if APP_BASE_URL else "the signup page"
        await update.message.reply_text(
            f"Welcome to *Founder CRM*\\!\n\n"
            f"Visit {base} to create your account and get your personal bot link\\.",
            parse_mode="MarkdownV2",
        )


# ─── /help ────────────────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "*Founder CRM — Commands*\n\n"
        "/pipeline — View full pipeline grouped by stage\n"
        "/deals — Same as /pipeline\n"
        "/context \\[name\\] — Pre\\-call brief for a contact\n"
        "/ask \\[question\\] — Natural language pipeline query\n"
        "/addnote — Add a note to a deal \\(guided\\)\n"
        "/note \\[text\\] — Quick note to your most recent deal\n"
        "/won \\[name\\] — Mark a deal as Won\n"
        "/lost \\[name\\] — Mark a deal as Lost\n"
        "/addcontact — Add a contact manually \\(guided\\)\n"
        "/cancel — Exit any active flow\n\n"
        "Or just *forward a WhatsApp chat* or send a *voice note* — AI captures the deal automatically\\."
    )
    await update.message.reply_text(text, parse_mode="MarkdownV2")


# ─── /pipeline (and /deals) ───────────────────────────────────

async def pipeline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Text Kanban. db.get_all_contacts() returns a dict keyed by stage name,
    with each value being a list of Airtable records. heat_score is injected
    by get_all_contacts as rec["heat_score"] = {"score": int, "label": str}.
    Shows top 3 per stage sorted by heat score.
    """
    telegram_id = update.effective_user.id
    user = _get_user(telegram_id)
    if not user:
        await _not_registered(update)
        return

    user_id = user["fields"]["user_id"]
    try:
        pipeline_data = db.get_all_contacts(user_id)
    except Exception as e:
        logger.error(f"Pipeline fetch error: {e}")
        await update.message.reply_text(
            "Couldn't fetch pipeline\\. Try again\\.", parse_mode="MarkdownV2"
        )
        return

    active_stages = ["Lead", "Evaluating", "Proposal Sent", "Negotiating"]
    total_active = sum(len(pipeline_data.get(s, [])) for s in active_stages)
    won_count = len(pipeline_data.get("Won", []))
    lost_count = len(pipeline_data.get("Lost", []))

    if total_active == 0 and won_count == 0 and lost_count == 0:
        await update.message.reply_text(
            "No deals yet\\. Forward a WhatsApp conversation to get started\\.",
            parse_mode="MarkdownV2",
        )
        return

    lines = ["*YOUR PIPELINE*", "─────────────────────"]

    for stage in active_stages:
        contacts = pipeline_data.get(stage, [])
        # Sort by heat score descending, show top 3
        contacts_sorted = sorted(
            contacts,
            key=lambda r: (
                r["heat_score"]["score"]
                if isinstance(r.get("heat_score"), dict)
                else 0
            ),
            reverse=True,
        )
        top3 = contacts_sorted[:3]
        count = len(contacts)

        lines.append(f"*{md(stage.upper())} \\({count}\\)*")
        if not top3:
            lines.append("  _none_")
        else:
            for rec in top3:
                flat = _flatten_contact(rec)
                lines.append(
                    f"  • {md(flat['contact_name'])} @ {md(flat['company'])} "
                    f"\\[{md(flat['heat_label'])} {flat['heat_score']}\\]"
                )

    lines.append("─────────────────────")
    lines.append(f"WON: {won_count} \\| LOST: {lost_count}")

    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


# ─── /context [name] ──────────────────────────────────────────

async def context_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Pre-call brief. If multiple contacts match, shows an inline keyboard for disambiguation.
    Callback data format: "ctx:{airtable_record_id}"
    """
    telegram_id = update.effective_user.id
    user = _get_user(telegram_id)
    if not user:
        await _not_registered(update)
        return

    user_id = user["fields"]["user_id"]
    name_query = " ".join(context.args).strip() if context.args else ""

    if not name_query:
        await update.message.reply_text(
            "Which contact? Usage: /context \\[name\\]", parse_mode="MarkdownV2"
        )
        return

    try:
        matches = db.find_contact(name_query, user_id)
    except Exception as e:
        logger.error(f"Contact search error: {e}")
        await update.message.reply_text("Search failed\\. Try again\\.", parse_mode="MarkdownV2")
        return

    if not matches:
        await update.message.reply_text(
            f"No contact found matching _{md(name_query)}_\\.", parse_mode="MarkdownV2"
        )
        return

    if len(matches) > 1:
        # Inline keyboard for disambiguation — store record ID in callback_data
        buttons = [
            [InlineKeyboardButton(
                f"{rec['fields'].get('name', '?')} @ {rec['fields'].get('company', '?')}",
                callback_data=f"ctx:{rec['id']}",
            )]
            for rec in matches[:5]
        ]
        await update.message.reply_text(
            "Multiple matches found\\. Which one?",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="MarkdownV2",
        )
        return

    await _send_context_brief(update, matches[0], via_callback=False)


async def context_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles disambiguation button press for /context."""
    query = update.callback_query
    await query.answer()
    record_id = query.data.split(":", 1)[1]

    try:
        rec = db.get_contact_by_id(record_id)
    except Exception as e:
        logger.error(f"Contact fetch error: {e}")
        await query.edit_message_text("Couldn't load contact\\.", parse_mode="MarkdownV2")
        return

    await _send_context_brief(update, rec, via_callback=True)


async def _send_context_brief(update: Update, rec: dict, via_callback: bool):
    """Fetches recent interactions and generates an AI pre-call brief."""
    record_id = rec["id"]
    flat = _flatten_contact(rec)

    try:
        interactions_raw = db.get_interactions(record_id)
        summaries = [
            r["fields"].get("ai_summary", "")
            for r in interactions_raw
            if r["fields"].get("ai_summary")
        ]
        brief = ai.generate_context_brief(flat, summaries)
    except Exception as e:
        logger.error(f"Brief generation error: {e}", exc_info=True)
        brief = f"Error: {e}"

    # AI output is escaped to prevent MarkdownV2 parse errors from unpredictable content
    msg = f"*Pre\\-call Brief: {md(flat['contact_name'])}*\n\n{md(brief)}"

    if via_callback:
        await update.callback_query.edit_message_text(msg, parse_mode="MarkdownV2")
    else:
        await update.message.reply_text(msg, parse_mode="MarkdownV2")


# ─── /won and /lost ───────────────────────────────────────────

async def won_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _mark_handler(update, context, action="won")


async def lost_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _mark_handler(update, context, action="lost")


async def _mark_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str):
    """
    Shared logic for /won and /lost.
    Uses inline Yes/Cancel keyboard. Callback data uses ":" separator to safely
    handle Airtable record IDs (which can contain underscores).
    Format: "mark_won:{record_id}" or "mark_cancel:{record_id}"
    """
    telegram_id = update.effective_user.id
    user = _get_user(telegram_id)
    if not user:
        await _not_registered(update)
        return

    user_id = user["fields"]["user_id"]
    name_query = " ".join(context.args).strip() if context.args else ""

    if not name_query:
        await update.message.reply_text(
            f"Usage: /{action} \\[name\\]", parse_mode="MarkdownV2"
        )
        return

    try:
        matches = db.find_contact(name_query, user_id)
    except Exception as e:
        logger.error(f"Contact search error: {e}")
        await update.message.reply_text("Search failed\\. Try again\\.", parse_mode="MarkdownV2")
        return

    if not matches:
        await update.message.reply_text(
            f"No contact found matching _{md(name_query)}_\\.", parse_mode="MarkdownV2"
        )
        return

    rec = matches[0]
    flat = _flatten_contact(rec)
    record_id = flat["_record_id"]
    label = "WON" if action == "won" else "LOST"

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"Yes, mark as {label}", callback_data=f"mark_{action}:{record_id}"
        ),
        InlineKeyboardButton("Cancel", callback_data=f"mark_cancel:{record_id}"),
    ]])

    await update.message.reply_text(
        f"Mark *{md(flat['contact_name'])}* from *{md(flat['company'])}* as *{label}*?",
        reply_markup=keyboard,
        parse_mode="MarkdownV2",
    )


async def mark_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles Yes/Cancel callbacks for /won and /lost.
    Splits on first ":" only so Airtable record IDs are preserved intact.
    """
    query = update.callback_query
    await query.answer()

    action_key, record_id = query.data.split(":", 1)

    if action_key == "mark_cancel":
        await query.edit_message_text("Cancelled\\.", parse_mode="MarkdownV2")
        return

    try:
        if action_key == "mark_won":
            db.mark_won(record_id)
        elif action_key == "mark_lost":
            db.mark_lost(record_id)

        rec = db.get_contact_by_id(record_id)
        name = md(rec["fields"].get("name", "Contact"))
        label = "WON" if action_key == "mark_won" else "LOST"
        note_tip = "Add a win note: /addnote" if label == "WON" else "Log what happened: /addnote"

        await query.edit_message_text(
            f"*{name}* marked as *{label}*\\.\n\n{md(note_tip)}",
            parse_mode="MarkdownV2",
        )
    except Exception as e:
        logger.error(f"Mark callback error ({action_key}): {e}")
        await query.edit_message_text("Update failed\\. Try again\\.", parse_mode="MarkdownV2")


# ─── /ask [question] ──────────────────────────────────────────

async def ask_pipeline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Natural language Q&A over the pipeline.
    Serializes all contacts as plain text, passes to ai.answer_pipeline_query().
    """
    telegram_id = update.effective_user.id
    user = _get_user(telegram_id)
    if not user:
        await _not_registered(update)
        return

    user_id = user["fields"]["user_id"]
    question = " ".join(context.args).strip() if context.args else ""

    if not question:
        await update.message.reply_text(
            "Ask me anything about your pipeline\\.\n"
            "E\\.g\\. /ask who should I follow up today?",
            parse_mode="MarkdownV2",
        )
        return

    try:
        pipeline_data = db.get_all_contacts(user_id)
    except Exception as e:
        logger.error(f"Pipeline fetch error: {e}")
        await update.message.reply_text("Couldn't fetch pipeline data\\.", parse_mode="MarkdownV2")
        return

    # Serialize all contacts as plain text for the AI context window
    lines = []
    for stage, contacts in pipeline_data.items():
        for rec in contacts:
            flat = _flatten_contact(rec)
            lines.append(
                f"- {flat['contact_name']} @ {flat['company']} | "
                f"Stage: {flat['stage']} | "
                f"Heat: {flat['heat_score']} ({flat['heat_label']}) | "
                f"Next: {flat['next_action'] or 'not set'}"
            )

    pipeline_context = "\n".join(lines) if lines else "No contacts in pipeline."

    try:
        answer = ai.answer_pipeline_query(question, pipeline_context)
    except Exception as e:
        logger.error(f"AI query error: {e}")
        await update.message.reply_text("AI query failed\\. Try again\\.", parse_mode="MarkdownV2")
        return

    await update.message.reply_text(md(answer), parse_mode="MarkdownV2")


# ─── /addcontact — ConversationHandler ───────────────────────

async def addcontact_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update.effective_user.id)
    if not user:
        await _not_registered(update)
        return ConversationHandler.END

    context.user_data["addcontact"] = {}
    await update.message.reply_text(
        "Let's add a new contact\\. What's their *name*?", parse_mode="MarkdownV2"
    )
    return ADD_NAME


async def addcontact_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Please enter a valid name\\.", parse_mode="MarkdownV2")
        return ADD_NAME

    context.user_data["addcontact"]["name"] = name
    await update.message.reply_text(
        f"*{md(name)}* — got it\\. What *company* are they from?", parse_mode="MarkdownV2"
    )
    return ADD_COMPANY


async def addcontact_company(update: Update, context: ContextTypes.DEFAULT_TYPE):
    company = update.message.text.strip()
    context.user_data["addcontact"]["company"] = company

    stage_list = "\n".join(f"{i + 1}\\. {md(s)}" for i, s in enumerate(ACTIVE_STAGES))
    await update.message.reply_text(
        f"*Company:* {md(company)}\n\nWhat *pipeline stage* are they at? "
        f"Reply with a number:\n{stage_list}",
        parse_mode="MarkdownV2",
    )
    return ADD_STAGE


async def addcontact_stage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    stage = None
    try:
        idx = int(text) - 1
        if 0 <= idx < len(ACTIVE_STAGES):
            stage = ACTIVE_STAGES[idx]
    except ValueError:
        stage = next((s for s in ACTIVE_STAGES if s.lower() == text.lower()), None)

    if not stage:
        stage_list = "\n".join(f"{i + 1}\\. {md(s)}" for i, s in enumerate(ACTIVE_STAGES))
        await update.message.reply_text(
            f"Please pick a number 1\\-{len(ACTIVE_STAGES)}:\n{stage_list}",
            parse_mode="MarkdownV2",
        )
        return ADD_STAGE

    context.user_data["addcontact"]["stage"] = stage

    source_list = "\n".join(f"{i + 1}\\. {md(s)}" for i, s in enumerate(SOURCE_LABELS))
    await update.message.reply_text(
        f"*Stage:* {md(stage)}\n\nWhere did you first connect with them? Reply with a number:\n{source_list}",
        parse_mode="MarkdownV2",
    )
    return ADD_SOURCE


async def addcontact_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    source = None
    try:
        idx = int(text) - 1
        if 0 <= idx < len(SOURCE_LABELS):
            source = SOURCE_VALUES[idx]  # store the Airtable value, not the display label
    except ValueError:
        # Allow typing the label directly
        match_idx = next(
            (i for i, s in enumerate(SOURCE_LABELS) if s.lower() == text.lower()), None
        )
        if match_idx is not None:
            source = SOURCE_VALUES[match_idx]

    if not source:
        source_list = "\n".join(f"{i + 1}\\. {md(s)}" for i, s in enumerate(SOURCE_LABELS))
        await update.message.reply_text(
            f"Please pick 1\\-{len(SOURCE_LABELS)}:\n{source_list}", parse_mode="MarkdownV2"
        )
        return ADD_SOURCE

    data = context.user_data.get("addcontact", {})
    data["source"] = source

    user = _get_user(update.effective_user.id)
    user_id = user["fields"]["user_id"]

    try:
        # db.create_contact always initialises stage as "Lead"
        db.create_contact(
            name=data["name"],
            company=data["company"],
            role="",
            source=source,
            user_id=user_id,
        )
        # If user picked a stage other than Lead, find the new record and update it
        if data["stage"] != "Lead":
            matches = db.find_contact(data["name"], user_id)
            if matches:
                db.update_contact_stage(matches[0]["id"], data["stage"])
    except Exception as e:
        logger.error(f"Create contact error: {e}")
        await update.message.reply_text(
            "Failed to save contact\\. Try again\\.", parse_mode="MarkdownV2"
        )
        context.user_data.pop("addcontact", None)
        return ConversationHandler.END

    await update.message.reply_text(
        f"*{md(data['name'])}* from *{md(data['company'])}* added\\!\n"
        f"Stage: {md(data['stage'])} \\| Source: {md(source)}\n\n"
        "Forward a WhatsApp chat to log their first interaction\\.",
        parse_mode="MarkdownV2",
    )
    context.user_data.pop("addcontact", None)
    return ConversationHandler.END


# ─── /cancel ─────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled\\.", parse_mode="MarkdownV2")
    return ConversationHandler.END


# ─── Handler registration ─────────────────────────────────────

def get_handlers() -> list:
    """
    Returns all handlers to register in main.py.

    Callback query handlers are split by pattern to avoid routing collisions:
      - "^ctx:"      → context disambiguation
      - "^mark_"     → won/lost confirmation

    The ConversationHandler for /addcontact must be registered before the
    generic MessageHandler in flows.py, otherwise text replies during the
    conversation would be intercepted by the forward/text capture flow.
    """
    addcontact_conv = ConversationHandler(
        entry_points=[CommandHandler("addcontact", addcontact_start)],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, addcontact_name)],
            ADD_COMPANY: [MessageHandler(filters.TEXT & ~filters.COMMAND, addcontact_company)],
            ADD_STAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addcontact_stage)],
            ADD_SOURCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addcontact_source)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="addcontact",
        persistent=False,
    )

    return [
        CommandHandler("start", start),
        CommandHandler("help", help_command),
        CommandHandler("pipeline", pipeline),
        CommandHandler("deals", pipeline),
        CommandHandler("context", context_cmd),
        CommandHandler("won", won_handler),
        CommandHandler("lost", lost_handler),
        CommandHandler("ask", ask_pipeline),
        CommandHandler("cancel", cancel),
        CallbackQueryHandler(context_callback, pattern="^ctx:"),
        CallbackQueryHandler(mark_callback, pattern="^mark_"),
        addcontact_conv,
    ]
