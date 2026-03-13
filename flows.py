import os
import logging
import tempfile

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from telegram.helpers import escape_markdown

import db
import ai

logger = logging.getLogger(__name__)

# ConversationHandler states for /addnote
SELECTING_CONTACT, ADDING_NOTE, AWAITING_FOLLOWUP = range(3)


# ─── Helpers ──────────────────────────────────────────────────

def md(text) -> str:
    return escape_markdown(str(text) if text is not None else "", version=2)


def _get_user(telegram_id: int):
    try:
        return db.get_user_by_telegram_id(telegram_id)
    except Exception as e:
        logger.error(f"Error fetching user {telegram_id}: {e}")
        return None


def _flatten_contact(rec: dict) -> dict:
    """
    Adapter: Airtable record → flat dict expected by ai.py functions.
    Duplicated from commands.py to avoid circular imports.
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
    base = md(os.getenv("APP_BASE_URL", ""))
    await update.message.reply_text(
        f"You're not registered yet\\. Visit {base} to create your account\\.",
        parse_mode="MarkdownV2",
    )


# ─── Core save logic (shared by all capture flows) ───────────

async def _save_capture(
    update: Update, user_id: str, extracted: dict, raw_text: str, source: str = "whatsapp_forward"
):
    """
    Finds or creates the contact, logs the interaction, and sends
    the confirmation card with Looks good / Edit stage buttons.
    Called after quality check passes.
    """
    contact_name = extracted.get("contact_name", "Unknown")
    company = extracted.get("company", "Unknown")
    stage = extracted.get("stage", "Lead")
    summary = extracted.get("summary", "")
    next_action = extracted.get("next_action", "Follow up soon.")
    role = extracted.get("role", "")

    valid_stages = ["Lead", "Evaluating", "Proposal Sent", "Negotiating", "Won", "Lost"]
    if stage not in valid_stages:
        stage = "Lead"

    # Find or create contact
    existing = db.find_contact(contact_name, user_id)

    if not existing:
        # create_contact returns the Airtable record directly
        new_rec = db.create_contact(
            name=contact_name,
            company=company,
            role=role,
            source=source,
            user_id=user_id,
        )
        record_id = new_rec["id"]
        if stage != "Lead":
            db.update_contact_stage(record_id, stage)
    else:
        record_id = existing[0]["id"]
        db.update_contact_stage(record_id, stage)

    # Log the interaction (also increments interaction_count via db.log_interaction)
    db.log_interaction(
        contact_id=record_id,
        type=source,
        raw_content=raw_text[:5000],
        ai_summary=summary,
        telegram_message_id=update.message.message_id,
    )

    # Re-fetch to get updated interaction_count for heat score calculation
    updated_rec = db.get_contact_by_id(record_id)
    heat = db.calculate_heat_score(updated_rec)
    score = heat["score"]
    label = heat["label"]

    card = (
        f"*Got it\\.*\n\n"
        f"*{md(contact_name)}* @ {md(company)}\n"
        f"Stage: {md(stage)} \\| Heat: {score} \\({md(label)}\\)\n\n"
        f"*Summary:* {md(summary)}\n"
        f"*Next:* {md(next_action)}"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Looks good ✓", callback_data="capture_ok"),
        InlineKeyboardButton("Edit stage", callback_data=f"edit_stage:{record_id}"),
    ]])

    await update.message.reply_text(card, reply_markup=keyboard, parse_mode="MarkdownV2")


# ─── Forwarded text / plain text capture ─────────────────────

async def forward_or_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles two cases:
    1. Normal: forwarded message or text > 50 chars → extract → quality check → save
    2. Follow-up: if context.user_data has a pending_capture (incomplete note from
       a previous message), this message is the follow-up answer → concatenate → save

    Note: pending_capture state is stored in user_data instead of a ConversationHandler
    because entry points for ConversationHandlers can't overlap with plain MessageHandlers.
    """
    try:
        telegram_id = update.effective_user.id
        logger.info(f"[capture] text received from {telegram_id}, len={len(update.message.text or '')}")

        user = _get_user(telegram_id)
        if not user:
            await _not_registered(update)
            return

        user_id = user["fields"]["user_id"]
        logger.info(f"[capture] user_id={user_id}")

        # Case 2: pending follow-up from a previous incomplete capture
        if context.user_data.get("pending_capture"):
            pending = context.user_data.pop("pending_capture")
            combined = pending["raw_text"] + "\n" + update.message.text
            await _save_capture(update, user_id, pending["extracted"], combined)
            return

        # Case 1: new capture
        content = update.message.text or ""

        if len(content) <= 50:
            logger.info(f"[capture] ignored — too short ({len(content)} chars)")
            return

        logger.info("[capture] calling ai.extract_from_text...")
        extracted = ai.extract_from_text(content)
        logger.info(f"[capture] extracted={extracted}")

        if not extracted.get("contact_name"):
            await update.message.reply_text(
                "I couldn't identify a contact\\. Try /addcontact to add manually\\.",
                parse_mode="MarkdownV2",
            )
            return

        quality = ai.evaluate_note_quality(content)
        logger.info(f"[capture] quality={quality}")

        if not quality.get("is_complete"):
            context.user_data["pending_capture"] = {
                "raw_text": content,
                "extracted": extracted,
            }
            followup_q = quality.get("follow_up_question") or "Could you share more details about this interaction?"
            await update.message.reply_text(md(followup_q), parse_mode="MarkdownV2")
            return

        await _save_capture(update, user_id, extracted, content)

    except Exception as e:
        logger.exception(f"[capture] unhandled error: {e}")
        await update.message.reply_text(f"Error: {e}", parse_mode=None)


# ─── Voice capture ────────────────────────────────────────────

async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Downloads voice file → transcribes with Whisper → classifies intent.
    - "recall": user wants a pre-call brief (e.g. "prep me for call with Arjun")
    - "capture": user is logging an interaction → same flow as text capture
    Temp file is always deleted via try/finally.
    """
    telegram_id = update.effective_user.id
    user = _get_user(telegram_id)
    if not user:
        await _not_registered(update)
        return

    user_id = user["fields"]["user_id"]
    status_msg = await update.message.reply_text("Transcribing\\.\\.\\.", parse_mode="MarkdownV2")

    # Use tempfile so this works on both Windows and Linux
    tmp_path = os.path.join(
        tempfile.gettempdir(), f"voice_{update.message.message_id}.ogg"
    )

    try:
        voice_file = await update.message.voice.get_file()
        await voice_file.download_to_drive(tmp_path)

        transcript = ai.transcribe_voice(tmp_path)
        logger.info(f"[voice] transcript={transcript[:120]}")

        # classify_intent returns the string "capture" or "recall" — not a dict
        intent = ai.classify_intent(transcript)
        logger.info(f"[voice] intent={intent}")

        if intent == "recall":
            # Extract contact name from transcript to know who they're asking about
            extracted = ai.extract_from_voice(transcript)
            contact_name = extracted.get("contact_name")

            if contact_name:
                matches = db.find_contact(contact_name, user_id)
                if matches:
                    rec = matches[0]
                    rec["heat_score"] = db.calculate_heat_score(rec)
                    flat = _flatten_contact(rec)
                    interactions_raw = db.get_interactions(rec["id"])
                    summaries = [
                        r["fields"].get("ai_summary", "")
                        for r in interactions_raw
                        if r["fields"].get("ai_summary")
                    ]
                    brief = ai.generate_context_brief(flat, summaries)
                    await status_msg.edit_text(md(brief), parse_mode="MarkdownV2")
                    return

            await status_msg.edit_text(
                "Couldn't identify the contact\\. Try /context \\[name\\]\\.",
                parse_mode="MarkdownV2",
            )

        else:
            # Capture intent — extract and save immediately, no quality gate for voice
            extracted = ai.extract_from_voice(transcript)
            logger.info(f"[voice] extracted={extracted}")
            await status_msg.delete()

            if not extracted.get("contact_name"):
                await update.message.reply_text(
                    "I couldn't identify a contact\\. Try /addcontact\\.",
                    parse_mode="MarkdownV2",
                )
                return

            await _save_capture(update, user_id, extracted, transcript, source="voice_note")

    except Exception as e:
        logger.exception(f"[voice] unhandled error: {e}")
        try:
            await status_msg.edit_text(f"Error processing voice note: {e}", parse_mode=None)
        except Exception:
            await update.message.reply_text(f"Error processing voice note: {e}", parse_mode=None)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# ─── Image / screenshot capture ───────────────────────────────

async def image_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Downloads the highest-resolution photo and extracts contact info via Claude Vision."""
    telegram_id = update.effective_user.id
    user = _get_user(telegram_id)
    if not user:
        await _not_registered(update)
        return

    user_id = user["fields"]["user_id"]
    status_msg = await update.message.reply_text("Reading image\\.\\.\\.", parse_mode="MarkdownV2")

    try:
        # photo[-1] is always the highest resolution version
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        logger.info(f"[image] downloaded {len(image_bytes)} bytes")

        extracted = ai.extract_from_image(bytes(image_bytes), "image/jpeg")
        logger.info(f"[image] extracted={extracted}")
        await status_msg.delete()

        if not extracted.get("contact_name"):
            if extracted.get("company"):
                extracted["contact_name"] = extracted["company"]
            else:
                await update.message.reply_text(
                    "I couldn't extract contact info from this image\\. Try /addcontact to add manually\\.",
                    parse_mode="MarkdownV2",
                )
                return

        await _save_capture(update, user_id, extracted, "Contact info from screenshot", source="screenshot")

    except Exception as e:
        logger.exception(f"[image] unhandled error: {e}")
        try:
            await status_msg.edit_text(f"Error processing image: {e}", parse_mode=None)
        except Exception:
            await update.message.reply_text(f"Error processing image: {e}", parse_mode=None)


# ─── /addnote ConversationHandler ────────────────────────────

async def addnote_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_user(update.effective_user.id)
    if not user:
        await _not_registered(update)
        return ConversationHandler.END

    context.user_data["addnote"] = {}
    await update.message.reply_text("Which contact is this note for?")
    return SELECTING_CONTACT


async def addnote_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    SELECTING_CONTACT state. Searches for the contact.
    - 1 result: stores contact, moves to ADDING_NOTE
    - 0 results: prompts again (stays in SELECTING_CONTACT)
    - Multiple: shows ReplyKeyboard with names; next message (exact name from keyboard)
      comes back to this same handler and matches as 1 result
    """
    user = _get_user(update.effective_user.id)
    user_id = user["fields"]["user_id"]
    query = update.message.text.strip()

    matches = db.find_contact(query, user_id)

    if not matches:
        await update.message.reply_text(
            "No contact found\\. Try a different name or /cancel\\.",
            parse_mode="MarkdownV2",
        )
        return SELECTING_CONTACT

    if len(matches) == 1:
        context.user_data["addnote"]["contact"] = matches[0]
        name = matches[0]["fields"].get("name", "?")
        await update.message.reply_text(
            f"*{md(name)}* — what happened?",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="MarkdownV2",
        )
        return ADDING_NOTE

    # Multiple matches — show keyboard so user picks exact name
    buttons = [[m["fields"].get("name", "?")] for m in matches[:5]]
    await update.message.reply_text(
        "Multiple matches — pick one:",
        reply_markup=ReplyKeyboardMarkup(buttons, one_time_keyboard=True, resize_keyboard=True),
    )
    return SELECTING_CONTACT  # Keyboard selection comes back here as exact name


async def addnote_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ADDING_NOTE state. Saves the note directly — no quality check for manual notes.
    Quality check is only meaningful for auto-captured text where context may be missing."""
    note_text = update.message.text.strip()
    await _save_addnote(update, context, note_text)
    return ConversationHandler.END


async def addnote_followup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """AWAITING_FOLLOWUP state. Concatenates follow-up with original note and saves."""
    followup = update.message.text.strip()
    original = context.user_data.get("addnote", {}).get("note", "")
    combined = original + " " + followup
    await _save_addnote(update, context, combined)
    return ConversationHandler.END


async def _save_addnote(update: Update, context: ContextTypes.DEFAULT_TYPE, note_text: str):
    """Writes the note to Airtable and sends confirmation."""
    contact = context.user_data.get("addnote", {}).get("contact")
    if not contact:
        await update.message.reply_text(
            "Something went wrong\\. Try /addnote again\\.", parse_mode="MarkdownV2"
        )
        context.user_data.pop("addnote", None)
        return

    try:
        db.log_interaction(
            contact_id=contact["id"],
            type="addnote_command",
            raw_content=note_text,
            ai_summary=note_text,  # Manual notes are self-descriptive
            telegram_message_id=update.message.message_id,
        )
        name = contact["fields"].get("name", "Contact")
        await update.message.reply_text(
            f"Note saved for *{md(name)}*\\.",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="MarkdownV2",
        )
    except Exception as e:
        logger.error(f"Save addnote error: {e}")
        await update.message.reply_text("Failed to save note\\. Try again\\.", parse_mode="MarkdownV2")
    finally:
        context.user_data.pop("addnote", None)


# ─── /note [text] ─────────────────────────────────────────────

async def quick_note_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Quick note to the most recently updated contact.
    If two contacts have the same last_updated timestamp, shows inline buttons to disambiguate.
    """
    telegram_id = update.effective_user.id
    user = _get_user(telegram_id)
    if not user:
        await _not_registered(update)
        return

    user_id = user["fields"]["user_id"]
    note_text = " ".join(context.args).strip() if context.args else ""

    if not note_text:
        await update.message.reply_text(
            "Usage: /note \\[your note text\\]", parse_mode="MarkdownV2"
        )
        return

    try:
        pipeline = db.get_all_contacts(user_id)
        # Flatten dict-of-lists to a single list, then sort by last_updated descending
        all_contacts = [rec for contacts in pipeline.values() for rec in contacts]
        all_contacts.sort(
            key=lambda r: r["fields"].get("last_updated", ""),
            reverse=True,
        )
        latest = all_contacts[:2]
    except Exception as e:
        logger.error(f"Quick note pipeline fetch error: {e}")
        await update.message.reply_text("Couldn't fetch contacts\\.", parse_mode="MarkdownV2")
        return

    if not latest:
        await update.message.reply_text(
            "No active deals found\\. Forward a message first\\.", parse_mode="MarkdownV2"
        )
        return

    # Unambiguous if only one contact, or top two have different timestamps
    if len(latest) == 1 or (
        latest[0]["fields"].get("last_updated") != latest[1]["fields"].get("last_updated")
    ):
        rec = latest[0]
        try:
            db.log_interaction(
                contact_id=rec["id"],
                type="addnote_command",
                raw_content=note_text,
                ai_summary=note_text,
                telegram_message_id=update.message.message_id,
            )
            name = rec["fields"].get("name", "Contact")
            await update.message.reply_text(
                f"Note added to *{md(name)}*\\.", parse_mode="MarkdownV2"
            )
        except Exception as e:
            logger.error(f"Quick note save error: {e}")
            await update.message.reply_text("Failed to save note\\.", parse_mode="MarkdownV2")
    else:
        # Same timestamp — ask which one
        context.user_data["quick_note_text"] = note_text
        buttons = [[
            InlineKeyboardButton(
                f"{c['fields'].get('name', '?')} @ {c['fields'].get('company', '?')}",
                callback_data=f"qnote:{c['id']}",
            )
        ] for c in latest]
        await update.message.reply_text(
            "Which contact is this note for?",
            reply_markup=InlineKeyboardMarkup(buttons),
        )


# ─── Callback handlers ────────────────────────────────────────

async def capture_ok_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User tapped 'Looks good ✓' — remove the inline keyboard."""
    query = update.callback_query
    await query.answer("Saved!")
    await query.edit_message_reply_markup(reply_markup=None)


async def edit_stage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User tapped 'Edit stage' — replace buttons with a stage picker."""
    query = update.callback_query
    await query.answer()
    record_id = query.data.split(":", 1)[1]

    stages = ["Lead", "Evaluating", "Proposal Sent", "Negotiating", "Won", "Lost"]
    buttons = [
        [InlineKeyboardButton(s, callback_data=f"set_stage:{record_id}:{s}")]
        for s in stages
    ]
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))


async def set_stage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Applies the chosen stage.
    Callback format: "set_stage:{record_id}:{stage_name}"
    Uses split(":", 2) so "Proposal Sent" (with a space, not colon) is preserved intact.
    """
    query = update.callback_query
    _, record_id, new_stage = query.data.split(":", 2)

    try:
        db.update_contact_stage(record_id, new_stage)
        rec = db.get_contact_by_id(record_id)
        name = rec["fields"].get("name", "Contact")
        await query.answer("Stage updated!")
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            f"Stage updated: *{md(name)}* → *{md(new_stage)}*\\.",
            parse_mode="MarkdownV2",
        )
    except Exception as e:
        logger.error(f"Set stage callback error: {e}")
        await query.answer("Update failed. Try again.")


async def quick_note_contact_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles contact selection when /note was ambiguous."""
    query = update.callback_query
    await query.answer()
    record_id = query.data.split(":", 1)[1]
    note_text = context.user_data.pop("quick_note_text", "")

    if not note_text:
        await query.edit_message_text("Note text was lost\\. Try /note again\\.", parse_mode="MarkdownV2")
        return

    try:
        db.log_interaction(
            contact_id=record_id,
            type="addnote_command",
            raw_content=note_text,
            ai_summary=note_text,
            telegram_message_id=query.message.message_id,
        )
        rec = db.get_contact_by_id(record_id)
        name = rec["fields"].get("name", "Contact")
        await query.edit_message_text(
            f"Note added to *{md(name)}*\\.", parse_mode="MarkdownV2"
        )
    except Exception as e:
        logger.error(f"Quick note callback error: {e}")
        await query.edit_message_text("Failed to save note\\.", parse_mode="MarkdownV2")


# ─── Cancel (fallback for addnote ConversationHandler) ────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled\\.", parse_mode="MarkdownV2")
    return ConversationHandler.END


# ─── Handler registration ─────────────────────────────────────

def get_addnote_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("addnote", addnote_start)],
        states={
            SELECTING_CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, addnote_search)],
            ADDING_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addnote_note)],
            AWAITING_FOLLOWUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, addnote_followup)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="addnote",
        persistent=False,
    )


def get_handlers() -> list:
    """
    Returns all flow handlers. Register these in main.py / run_bot.py AFTER
    commands.py handlers. Handler order matters:
    - addnote ConversationHandler must come before the generic text MessageHandler
    - Voice and photo handlers are filtered by message type so order doesn't matter
    - forward_or_text_handler (generic TEXT) must be last — it's the catch-all
    """
    return [
        CommandHandler("note", quick_note_handler),
        CallbackQueryHandler(capture_ok_callback, pattern="^capture_ok$"),
        CallbackQueryHandler(edit_stage_callback, pattern="^edit_stage:"),
        CallbackQueryHandler(set_stage_callback, pattern="^set_stage:"),
        CallbackQueryHandler(quick_note_contact_callback, pattern="^qnote:"),
        get_addnote_conversation(),
        MessageHandler(filters.VOICE, voice_handler),
        MessageHandler(filters.PHOTO, image_handler),
        MessageHandler(filters.TEXT & ~filters.COMMAND, forward_or_text_handler),
    ]
