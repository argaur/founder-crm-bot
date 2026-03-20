"""
seed.py — Demo data for Founder CRM case study.

Usage:
  python seed.py --seed    Insert all demo data
  python seed.py --clear   Delete all demo data
"""

import argparse
from datetime import datetime, timezone, timedelta
from db import create_user, users_table, contacts_table, interactions_table

DEMO_USER_ID = "demo-gaurav-001"

DEMO_USER = {
    "user_id": DEMO_USER_ID,
    "first_name": "Gaurav",
    "email": "gaurav@rethink.systems",
    "company": "Rethink Systems",
}

def days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


CONTACTS = [
    {
        "name": "Arjun Mehta",
        "company": "TechCorp Solutions",
        "role": "CTO",
        "stage": "Negotiating",
        "source": "whatsapp_forward",
        "last_updated_days": 2,
        "interactions": [
            {
                "type": "whatsapp_forward",
                "raw_content": "Hey Gaurav, we've reviewed your proposal. The team loves it. Can we hop on a call tomorrow to finalize the pricing? We're looking at a 12-month contract.",
                "ai_summary": "Arjun is ready to finalize. Team has approved the proposal. Wants to discuss pricing for a 12-month contract. High buying intent.",
            },
            {
                "type": "voice_note",
                "raw_content": "Just spoke with Arjun. He's concerned about the onboarding timeline. Wants it done in 2 weeks. Also asked about API access. I said we can do it.",
                "ai_summary": "Concern raised about onboarding timeline — Arjun wants 2-week completion. API access confirmed. Gaurav committed to timeline.",
            },
            {
                "type": "whatsapp_forward",
                "raw_content": "Gaurav bhai, one more thing — our legal team needs a DPA before we sign. Can you send that over? Everything else looks good.",
                "ai_summary": "Legal requires a Data Processing Agreement before contract sign-off. Blocker to closing — DPA needs to be sent urgently.",
            },
        ],
    },
    {
        "name": "Priya Sharma",
        "company": "Razorpay",
        "role": "VP Partnerships",
        "stage": "Proposal Sent",
        "source": "whatsapp_forward",
        "last_updated_days": 4,
        "interactions": [
            {
                "type": "whatsapp_forward",
                "raw_content": "Hi Gaurav, thanks for the detailed deck. I've shared it with our partnerships team internally. We should have feedback by end of week.",
                "ai_summary": "Proposal shared internally at Razorpay. Priya is the internal champion. Expecting feedback by end of week.",
            },
            {
                "type": "voice_note",
                "raw_content": "Priya called. She said the budget is tight this quarter but next quarter looks good. She's personally excited. Told her we can do a pilot.",
                "ai_summary": "Budget constrained this quarter. Priya keen on Q2 start. Pilot option discussed as entry point. Keep warm.",
            },
        ],
    },
    {
        "name": "Rahul Gupta",
        "company": "Swiggy",
        "role": "Product Head",
        "stage": "Evaluating",
        "source": "whatsapp_forward",
        "last_updated_days": 6,
        "interactions": [
            {
                "type": "whatsapp_forward",
                "raw_content": "Gaurav, we're evaluating 3 vendors right now including you. Can you send a comparison doc and also references from similar-scale companies?",
                "ai_summary": "Swiggy in competitive evaluation with 3 vendors. Needs comparison doc and client references. Critical to respond quickly.",
            },
            {
                "type": "whatsapp_forward",
                "raw_content": "Also, does your platform handle 10L+ transactions per day? That's our current volume and we can't compromise on that.",
                "ai_summary": "Scale requirement: 10L+ transactions/day. Hard requirement. Confirm platform capacity before next call.",
            },
            {
                "type": "voice_note",
                "raw_content": "Had a product walkthrough with Rahul's team. They were impressed with the dashboard. Main concern is integration with their internal tools.",
                "ai_summary": "Product demo went well. Dashboard well-received. Integration with internal tooling is the key technical concern.",
            },
        ],
    },
    {
        "name": "Sneha Patel",
        "company": "Zepto",
        "role": "Founder",
        "stage": "Lead",
        "source": "manual",
        "last_updated_days": 8,
        "interactions": [
            {
                "type": "manual_note",
                "raw_content": "Met Sneha at SaaSBoomi Delhi. She's scaling ops rapidly and mentioned they need better B2B tooling. Said to follow up in 2 weeks.",
                "ai_summary": "Warm intro at SaaSBoomi. Zepto in rapid ops scaling phase. Sneha open to follow-up in 2 weeks.",
            },
        ],
    },
    {
        "name": "Vikram Singh",
        "company": "Meesho",
        "role": "CXO",
        "stage": "Won",
        "source": "whatsapp_forward",
        "last_updated_days": 7,
        "interactions": [
            {
                "type": "whatsapp_forward",
                "raw_content": "Gaurav, contract signed and payment initiated. Looking forward to working with you. Onboarding call Monday?",
                "ai_summary": "Deal closed. Contract signed, payment initiated. Onboarding call scheduled for Monday.",
            },
            {
                "type": "voice_note",
                "raw_content": "Vikram's team had initial concerns about data security. I sent them our SOC2 report. They were satisfied immediately.",
                "ai_summary": "Data security concern resolved by sharing SOC2 report. Removed the final blocker to closing.",
            },
            {
                "type": "whatsapp_forward",
                "raw_content": "Hey, just wanted to say the pilot results were fantastic. 40% reduction in ops overhead. Board loved it. Let's go full rollout.",
                "ai_summary": "Pilot delivered 40% ops overhead reduction. Board approved full rollout.",
            },
            {
                "type": "whatsapp_forward",
                "raw_content": "Can you also rope in 2 more seats for my ops leads? I want them trained before we go live next month.",
                "ai_summary": "Upsell: 2 additional seats for ops leads. Training required before go-live next month.",
            },
            {
                "type": "voice_note",
                "raw_content": "Final negotiation call done. They asked for a 10% discount on annual plan. I agreed. Closed at ₹18L ARR.",
                "ai_summary": "Closed at ₹18L ARR after 10% discount on annual plan.",
            },
        ],
    },
    {
        "name": "Ananya Kumar",
        "company": "Groww",
        "role": "Head of Operations",
        "stage": "Evaluating",
        "source": "whatsapp_forward",
        "last_updated_days": 3,
        "interactions": [
            {
                "type": "whatsapp_forward",
                "raw_content": "Hi Gaurav, your intro email was well-timed. We're actually revisiting our ops stack this month. Can we do a 30-min call this week?",
                "ai_summary": "Strong timing — Groww actively revisiting ops stack. Ananya initiated call request. High-intent inbound.",
            },
            {
                "type": "voice_note",
                "raw_content": "Discovery call done. Ananya's team of 12 is manually tracking ops in sheets. Clear pain point. She asked for a custom demo focused on reporting.",
                "ai_summary": "12-person ops team on spreadsheets. Pain validated. Custom reporting demo requested as next step.",
            },
        ],
    },
    {
        "name": "Raj Kapoor",
        "company": "CRED",
        "role": "Business Dev",
        "stage": "Lost",
        "source": "whatsapp_forward",
        "last_updated_days": 14,
        "interactions": [
            {
                "type": "whatsapp_forward",
                "raw_content": "Gaurav, after careful consideration we've decided to go with an in-house solution. The team felt it aligned better with our existing infrastructure. Thanks for your time.",
                "ai_summary": "Deal lost to in-house build. Not a competitor loss. CRED felt internal build fit their infra better.",
            },
            {
                "type": "voice_note",
                "raw_content": "Spoke with Raj post-loss. He said the decision was top-down from their CTO. Not about product quality. Suggested reconnecting in 6 months.",
                "ai_summary": "Loss was CTO-driven, not product-driven. Door open for 6-month re-engagement.",
            },
        ],
    },
    {
        "name": "Nisha Joshi",
        "company": "Dukaan",
        "role": "Co-founder",
        "stage": "Proposal Sent",
        "source": "voice_note",
        "last_updated_days": 5,
        "interactions": [
            {
                "type": "voice_note",
                "raw_content": "Nisha called to say they loved the demo. She wants to move fast. Asked if we can customize the onboarding for vernacular language support.",
                "ai_summary": "Strong post-demo enthusiasm. Customization ask: vernacular language support for onboarding.",
            },
            {
                "type": "whatsapp_forward",
                "raw_content": "Gaurav, sharing the proposal with our board this Friday. Can you send a one-pager version too? Something crisp for non-technical people.",
                "ai_summary": "Board review Friday. One-pager needed for non-technical board members. Send before Thursday EOD.",
            },
            {
                "type": "whatsapp_forward",
                "raw_content": "Also, Dukaan has around 50k merchants. Any volume pricing available? Want to include that in our board presentation.",
                "ai_summary": "Volume pricing query for 50k merchant base. Needs to be in board presentation. Prepare volume pricing tier.",
            },
        ],
    },
]


def seed():
    print("Seeding demo data for Founder CRM...\n")

    # Create demo user if not exists
    existing = users_table.first(formula=f"{{user_id}}='{DEMO_USER_ID}'")
    if existing:
        print("Demo user already exists — skipping user creation.")
    else:
        create_user(
            user_id=DEMO_USER["user_id"],
            first_name=DEMO_USER["first_name"],
            email=DEMO_USER["email"],
            company=DEMO_USER["company"],
        )
        print(f"Created demo user: {DEMO_USER['first_name']} ({DEMO_USER_ID})\n")

    for contact_data in CONTACTS:
        # Create contact with correct stage and past timestamp directly
        rec = contacts_table.create({
            "name": contact_data["name"],
            "company": contact_data["company"],
            "role": contact_data["role"],
            "source": contact_data["source"],
            "user_id": DEMO_USER_ID,
            "stage": contact_data["stage"],
            "interaction_count": len(contact_data["interactions"]),
            "last_updated": days_ago(contact_data["last_updated_days"]),
            "added_on": days_ago(contact_data["last_updated_days"] + 7),
        })
        rec_id = rec["id"]

        # Log interactions directly (bypassing log_interaction to preserve past dates)
        for interaction in contact_data["interactions"]:
            interactions_table.create({
                "contact_id": rec_id,
                "type": interaction["type"],
                "raw_content": interaction["raw_content"],
                "ai_summary": interaction["ai_summary"],
                "telegram_message_id": 0,
                "logged_on": days_ago(contact_data["last_updated_days"]),
            })

        print(f"Created {contact_data['name']} @ {contact_data['company']} [{contact_data['stage']}] ✓")

    print("\nSeed complete! Open the dashboard to see all 8 deals.")


def clear():
    print("Clearing all demo data...\n")

    contacts = contacts_table.all(formula=f"{{user_id}}='{DEMO_USER_ID}'")
    for rec in contacts:
        interactions = interactions_table.all(formula=f"{{contact_id}}='{rec['id']}'")
        for interaction in interactions:
            interactions_table.delete(interaction["id"])
        contacts_table.delete(rec["id"])
        print(f"Deleted {rec['fields'].get('name', 'unknown')} + interactions ✓")

    user_rec = users_table.first(formula=f"{{user_id}}='{DEMO_USER_ID}'")
    if user_rec:
        users_table.delete(user_rec["id"])
        print("Deleted demo user ✓")

    print("\nClear complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Founder CRM demo data seeder")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--seed", action="store_true", help="Insert demo data")
    group.add_argument("--clear", action="store_true", help="Delete all demo data")
    args = parser.parse_args()

    if args.seed:
        seed()
    elif args.clear:
        clear()
