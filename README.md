# Rethink CRM — Conversation-First Sales Tool

> A Telegram bot that turns WhatsApp conversation context into structured CRM data — zero new app, zero context-switching.

**Stage:** PRD · Prototype &nbsp;|&nbsp; **Stack:** Python · Telegram Bot API · Claude AI · Supabase

---

## The Problem

60–70% of founders abandon their CRM within 4 weeks. Not because they don't care about relationships — but because CRMs demand structured input at exactly the wrong moment. Founders already manage relationships inside WhatsApp. The tool had to meet them there.

## What I Built

A Telegram bot that acts as the CRM interface. Founders forward conversation snippets, voice notes, or quick updates directly to the bot. Claude parses intent, extracts deal context (contact, stage, next action, sentiment), and writes structured records to a Supabase backend — without the founder ever opening a dashboard.

Key design decisions:
- **No new UI to learn** — the entire interface is a Telegram conversation
- **AI does the structuring** — founders capture in natural language; Claude normalises it
- **Progressive disclosure** — bot only asks clarifying questions when confidence is low
- **Built on a 39-page PRD** informed by 10 founder interviews and analysis of 16 competing tools

## Research

| Method | Output |
|--------|--------|
| 10 founder interviews | Pain point mapping + abandonment triggers |
| 16 CRM tools analysed | Feature gap matrix + positioning whitespace |
| 39-page PRD | Full problem definition, user stories, system design |

## Tech Stack

| Layer | Choice |
|-------|--------|
| Bot interface | Telegram Bot API (python-telegram-bot) |
| AI / NLP | Claude API (Anthropic) |
| Database | Supabase (PostgreSQL) |
| Language | Python |
| Hosting | Railway |

## Links

- **Landing page:** https://argaur.github.io/founder-crm-landing/
- **Case study:** https://gauravg-portfolio.vercel.app/case-study-founder-crm.html
- **Portfolio:** https://gauravg-portfolio.vercel.app

---

> Built by [Gaurav Gupta](https://linkedin.com/in/ar-gaurav) — Senior PM & AI Strategist
