import os
import json
import base64
import time
from typing import Dict, List, Optional, Any, Union
from dotenv import load_dotenv
from anthropic import Anthropic
from openai import OpenAI

load_dotenv()

# Client Initialization
anthropic_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Constants
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
WHISPER_MODEL = "whisper-1"

def retry_api_call(func):
    """Decorator for 2 retries with 1s delay."""
    def wrapper(*args, **kwargs):
        last_exception = None
        for attempt in range(3):  # Initial try + 2 retries
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                if attempt < 2:
                    time.sleep(1)
        raise last_exception
    return wrapper

@retry_api_call
def transcribe_voice(file_path: str) -> str:
    """Uses OpenAI Whisper to transcribe audio files."""
    with open(file_path, "rb") as audio_file:
        transcript = openai_client.audio.transcriptions.create(
            model=WHISPER_MODEL, 
            file=audio_file
        )
    return transcript.text

def _claude_json_extract(prompt: str, system_prompt: str, image_data: Optional[Dict] = None) -> Dict:
    """Internal helper to ensure Claude returns valid JSON."""
    messages = []
    
    content = [{"type": "text", "text": prompt}]
    if image_data:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": image_data["mime_type"],
                "data": image_data["base64"],
            },
        })
        
    messages.append({"role": "user", "content": content})

    response = anthropic_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1000,
        system=system_prompt + "\nOutput ONLY valid JSON.",
        messages=messages
    )
    
    try:
        text = response.content[0].text.strip()
        # Strip markdown code blocks if Claude wraps response in ```json ... ```
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except (json.JSONDecodeError, IndexError):
        return {"contact_name": None, "error": "Failed to parse AI response"}

@retry_api_call
def extract_from_text(text: str) -> Dict:
    """Extracts deal context from forwarded WhatsApp text."""
    system_prompt = """You are a sales operations assistant for Indian B2B founders. 
    Extract deal info from WhatsApp chats. If confidence is low on contact_name, set it to null.
    Stages: Lead, Evaluating, Proposal Sent, Negotiating, Won, Lost, unknown."""
    
    prompt = f"Extract structured data from this chat log:\n\n{text}"
    return _claude_json_extract(prompt, system_prompt)

@retry_api_call
def extract_from_voice(transcript: str) -> Dict:
    """Extracts deal context from voice note transcripts (handles Hinglish/informal)."""
    system_prompt = """You are a sales assistant for Indian B2B founders. The input is a voice note transcript (may include Hinglish, filler words, or informal speech).
Extract the deal context and return JSON with EXACTLY these fields:
{
  "contact_name": "full name of the person being discussed (string or null)",
  "company": "company or organisation name (string or null)",
  "role": "their job title or role (string or null)",
  "stage": "one of: Lead, Evaluating, Proposal Sent, Negotiating, Won, Lost, unknown",
  "summary": "one sentence summary of what happened in this interaction",
  "next_action": "suggested next step (string or null)",
  "budget_signal": "any pricing or budget info mentioned (string or null)"
}"""

    prompt = f"Extract deal data from this voice note transcript:\n\n{transcript}"
    return _claude_json_extract(prompt, system_prompt)

@retry_api_call
def extract_from_image(image_bytes: bytes, mime_type: str) -> Dict:
    """Uses Claude Vision to extract deal info from screenshots/business cards."""
    base64_image = base64.b64encode(image_bytes).decode("utf-8")
    
    system_prompt = """You are a sales CRM assistant for Indian B2B founders.
Extract contact and deal info from this image (LinkedIn profile, WhatsApp contact, business card, etc).
Return JSON with EXACTLY these fields:
{
  "contact_name": "full name of the individual person (string or null)",
  "company": "company or organisation name (string or null)",
  "role": "job title or designation (string or null)",
  "stage": "one of: Lead, Evaluating, Proposal Sent, Negotiating, Won, Lost, unknown",
  "summary": "one sentence describing what you see in the image",
  "next_action": "suggested next step based on context (string or null)",
  "budget_signal": "any pricing or budget info visible (string or null)"
}"""
    prompt = "Extract the contact and deal information from this image and return the JSON."
    
    return _claude_json_extract(
        prompt, 
        system_prompt, 
        image_data={"base64": base64_image, "mime_type": mime_type}
    )

@retry_api_call
def classify_intent(text: str) -> str:
    """Detects if a founder wants to 'capture' info or 'recall' for a meeting."""
    prompt = f"Classify this founder's intent as either 'capture' (adding info/deal) or 'recall' (asking for summary/prep). Text: {text}"
    
    response = anthropic_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=50,
        messages=[{"role": "user", "content": prompt}]
    )
    res_text = response.content[0].text.lower()
    return "recall" if "recall" in res_text else "capture"

@retry_api_call
def evaluate_note_quality(note_text: str) -> Dict:
    """Checks if a note is useful or needs a follow-up question."""
    system_prompt = "Check if this sales note is complete (has topic, outcome, or next step). If not, provide ONE specific follow-up question."
    prompt = f"Evaluate this note: {note_text}"
    
    # Using helper to get JSON: {"is_complete": bool, "follow_up_question": str|null}
    return _claude_json_extract(prompt, system_prompt)

@retry_api_call
def answer_pipeline_query(question: str, pipeline_context: str) -> str:
    """Natural language Q&A over the current pipeline status."""
    prompt = f"""
    Context (Current Pipeline):
    {pipeline_context}

    Question: {question}

    Answer concisely in plain text. If the question is too vague, return exactly ONE clarifying question.
    """
    
    response = anthropic_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

@retry_api_call
def generate_context_brief(contact_record: Dict, interactions: List[str]) -> str:
    """Generates a pre-call briefing string."""
    history = "\n".join(interactions[-5:]) # Last 5 interactions
    prompt = f"""
    Contact: {contact_record.get('contact_name')}
    Company: {contact_record.get('company')}
    Stage: {contact_record.get('stage')}
    Heat Score: {contact_record.get('heat_score')}
    Budget: {contact_record.get('budget_signal')}
    
    Recent History:
    {history}

    Generate a concise pre-call brief for the founder. 
    Include: Current status, last touchpoint summary, and 2 suggested talking points.
    """
    
    response = anthropic_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text