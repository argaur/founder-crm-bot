import os
import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from dotenv import load_dotenv
from pyairtable import Api
from pyairtable.formulas import match

# Load environment variables
load_dotenv()

# Configuration
AIRTABLE_PAT = os.getenv("AIRTABLE_PAT")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")

if not AIRTABLE_PAT or not AIRTABLE_BASE_ID:
    raise ValueError("Missing Airtable credentials in environment variables.")

# Initialize Airtable API
api = Api(AIRTABLE_PAT)
base = api.base(AIRTABLE_BASE_ID)

# Table References
users_table = base.table("users")
contacts_table = base.table("contacts")
interactions_table = base.table("interactions")

# --- UTILS ---

def calculate_heat_score(contact_record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculates dynamic heat score:
    score = 100 - (days_since_last_update * 5) + (interaction_count * 3)
    """
    fields = contact_record.get("fields", {})
    last_updated_str = fields.get("last_updated")
    interaction_count = fields.get("interaction_count", 0)

    if last_updated_str:
        last_updated = datetime.fromisoformat(last_updated_str.replace("Z", "+00:00"))
        days_since = (datetime.now(timezone.utc) - last_updated).days
    else:
        days_since = 0

    score = 100 - (days_since * 5) + (interaction_count * 3)
    score = max(0, min(100, score))

    if score >= 70:
        label = "Hot"
    elif score >= 40:
        label = "Warm"
    else:
        label = "Cold"

    return {"score": score, "label": label}

# --- USER FUNCTIONS ---

def create_user(user_id: str, first_name: str, email: str, company: str):
    """Creates a new user record from the landing page/signup flow."""
    return users_table.create({
        "user_id": user_id,
        "first_name": first_name,
        "email": email,
        "company": company,
        "joined_at": datetime.now(timezone.utc).isoformat()
    })

def get_user_by_telegram_id(telegram_id: int):
    """Retrieves a user based on their unique Telegram ID."""
    formula = match({"telegram_id": int(telegram_id)})
    record = users_table.first(formula=formula)
    return record if record else None

def link_telegram_to_user(user_id: str, telegram_id: int):
    """Links a specific user_id (from signup) to a Telegram ID."""
    record = users_table.first(formula=match({"user_id": user_id}))
    if record:
        return users_table.update(record["id"], {"telegram_id": int(telegram_id)})
    return None

def get_all_users() -> List[Dict]:
    """Returns all registered users who have linked their Telegram account."""
    return users_table.all(formula="NOT({telegram_id}='')")

# --- CONTACT FUNCTIONS ---

def create_contact(name: str, company: str, role: str, source: str, user_id: str):
    """Initializes a new lead in the pipeline."""
    return contacts_table.create({
        "name": name,
        "company": company,
        "role": role,
        "source": source,
        "user_id": user_id,
        "stage": "Lead",
        "interaction_count": 0,
        "last_updated": datetime.now(timezone.utc).isoformat()
    })

def find_contact(partial_name: str, user_id: str) -> List[Dict]:
    """Case-insensitive search for contacts belonging to a specific user."""
    formula = f"AND({{user_id}}='{user_id}', FIND(LOWER('{partial_name}'), LOWER({{name}})))"
    return contacts_table.all(formula=formula)

def get_contact_by_id(contact_id: str):
    """Fetch a single contact by Airtable Record ID."""
    return contacts_table.get(contact_id)

def update_contact_stage(contact_id: str, stage: str):
    """Updates the pipeline stage and refreshes the last_updated timestamp."""
    valid_stages = ["Lead", "Evaluating", "Proposal Sent", "Negotiating", "Won", "Lost"]
    if stage not in valid_stages:
        raise ValueError(f"Invalid stage: {stage}")
    
    return contacts_table.update(contact_id, {
        "stage": stage,
        "last_updated": datetime.now(timezone.utc).isoformat()
    })

def update_contact_next_action(contact_id: str, next_action: str):
    """Updates the 'Next Action' field for a contact."""
    return contacts_table.update(contact_id, {"next_action": next_action})

def get_all_contacts(user_id: str) -> Dict[str, List[Dict]]:
    """Returns all contacts for a user, grouped by their pipeline stage."""
    formula = match({"user_id": user_id})
    records = contacts_table.all(formula=formula)
    
    pipeline = {
        "Lead": [], "Evaluating": [], "Proposal Sent": [], 
        "Negotiating": [], "Won": [], "Lost": []
    }
    
    for rec in records:
        stage = rec["fields"].get("stage", "Lead")
        # Attach dynamic heat score before returning
        rec["heat_score"] = calculate_heat_score(rec)
        if stage in pipeline:
            pipeline[stage].append(rec)
            
    return pipeline

def mark_won(contact_id: str):
    return update_contact_stage(contact_id, "Won")

def mark_lost(contact_id: str):
    return update_contact_stage(contact_id, "Lost")

def increment_interaction_count(contact_id: str):
    """Increments the interaction counter for heat score calculation."""
    record = contacts_table.get(contact_id)
    current_count = record["fields"].get("interaction_count", 0)
    return contacts_table.update(contact_id, {
        "interaction_count": current_count + 1,
        "last_updated": datetime.now(timezone.utc).isoformat()
    })

def get_stale_contacts(user_id: str, days: int = 3) -> List[Dict]:
    """Returns contacts that haven't been updated in N days."""
    formula = match({"user_id": user_id})
    records = contacts_table.all(formula=formula)
    stale = []
    
    for rec in records:
        last_updated_str = rec["fields"].get("last_updated")
        if last_updated_str:
            last_updated = datetime.fromisoformat(last_updated_str.replace("Z", "+00:00"))
            delta = (datetime.now(timezone.utc) - last_updated).days
            if delta >= days and rec["fields"].get("stage") not in ["Won", "Lost"]:
                rec["heat_score"] = calculate_heat_score(rec)
                stale.append(rec)
    return stale

# --- INTERACTION FUNCTIONS ---

def log_interaction(contact_id: str, type: str, raw_content: str, ai_summary: str, telegram_message_id: str = None):
    """Logs a new interaction (forwarded text, voice, etc.) linked to a contact."""
    # We also increment the count and update last_updated on the contact
    increment_interaction_count(contact_id)
    
    return interactions_table.create({
        "contact_id": contact_id,
        "type": type,
        "raw_content": raw_content,
        "ai_summary": ai_summary,
        "telegram_message_id": telegram_message_id if telegram_message_id else 0,
        "logged_on": datetime.now(timezone.utc).isoformat()
    })

def get_interactions(contact_id: str, limit: int = 5):
    """Retrieves the most recent interactions for a contact."""
    formula = f"{{contact_id}}='{contact_id}'"
    records = interactions_table.all(formula=formula, sort=["-logged_on"])
    return records[:limit]