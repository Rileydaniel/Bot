"""Editable NexVisions support answers for the public Support Hub chat."""

import re

SUPPORT_EMAIL = "nextechdevassist@gmail.com"
DISCORD_INVITE = "https://discord.gg/hWVJ5jgAwh"
STAFF_ROLE = "Verified Hybrid & Virtual Operations Staff"
SUPPORT_HOURS = (
    "Monday-Friday: 7:30 AM-6:30 PM Pacific Time (PT); "
    "6:30 PM-7:30 PM Australian Eastern Time (AET)."
)

# Add or edit keyword groups and replies here. Keep answers suitable for all visitors.
SUPPORT_ANSWERS = (
    {
        "keywords": ("roblox login", "sign in", "sign-in", "login", "log in", "dashboard"),
        "answer": "To access member tools, choose Continue with Roblox from the Dashboard and use the Roblox account linked to NexTech Hub. The dashboard is at /dashboard.",
    },
    {
        "keywords": ("discord", "join server", "community invite"),
        "answer": f"Join the NexTech Discord community here: {DISCORD_INVITE}",
    },
    {
        "keywords": ("report user", "report someone", "report a user", "report member", "player report"),
        "answer": f"To report a user, contact us in Discord and ask for the {STAFF_ROLE} role, or email {SUPPORT_EMAIL}. Include the username and a clear description of your concern. Please do not share passwords or private account details.",
    },
    {
        "keywords": ("ticket", "support request", "contact support", "help with account"),
        "answer": f"Visit the Dashboard at /dashboard and sign in with your linked Roblox account for member support tools. You can also email {SUPPORT_EMAIL}.",
    },
    {
        "keywords": ("ban appeal", "appeal", "banned", "warning", "moderation"),
        "answer": f"For a moderation question or appeal, contact the {STAFF_ROLE} team in our Discord or email {SUPPORT_EMAIL}. Include the account name and relevant context.",
    },
    {
        "keywords": ("hours", "support hours", "when are you open", "time zone", "timezone"),
        "answer": f"Our team is available {SUPPORT_HOURS}",
    },
    {
        "keywords": ("wiki", "commands", "slash command", "help article"),
        "answer": "Browse help articles and bot command references in the NexTech Wiki at /wiki.",
    },
    {
        "keywords": ("status", "bot offline", "uptime", "service status"),
        "answer": "Check current bot availability, uptime, and command status on the System Status page at /status.",
    },
)

SUGGESTED_QUESTIONS = (
    "How do I sign in?",
    "How do I report a user?",
    "What are your support hours?",
    "How do I join Discord?",
)

FALLBACK_ANSWER = (
    f"I don't have an answer for that yet. Please contact our team in Discord "
    f"and ask for the {STAFF_ROLE} role, or email {SUPPORT_EMAIL}."
)


def answer_support_message(message: str) -> str:
    """Return the best configured answer for a visitor's message."""
    normalized = " ".join(re.findall(r"[a-z0-9]+", message.casefold()))
    if not normalized:
        return FALLBACK_ANSWER

    padded_message = f" {normalized} "
    for item in SUPPORT_ANSWERS:
        for keyword in item["keywords"]:
            normalized_keyword = " ".join(re.findall(r"[a-z0-9]+", keyword.casefold()))
            if normalized_keyword and f" {normalized_keyword} " in padded_message:
                return item["answer"]
    return FALLBACK_ANSWER
