import discord
from discord import app_commands, Member, Activity, File, Embed, Color
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput, Select
import os
import secrets
import string
import asyncio
import random
import re
from datetime import timedelta, datetime, timezone
from collections import defaultdict, deque
import io
import aiohttp
import tempfile
import json
import time
import math
import uuid
import hashlib
import base64
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests as http_requests
import edge_tts
from flask import (
    Flask,
    Response,
    make_response,
    render_template_string,
    request,
    jsonify,
    redirect,
    url_for,
    session as flask_session,
)
from flask_socketio import SocketIO, disconnect, emit, join_room
from threading import Lock, Thread
from discord import ui, TextStyle
from typing import Optional
import httpx
from werkzeug.utils import secure_filename

FEATURE_REQUEST_CHANNEL_ID = 1258549985079853132  # Replace with the actual channel ID
FEATURE_REQUEST_THREAD_NAME = "Feature Requests"
REPORT_PORTAL_CHANNEL_ID = int(
    os.getenv("REPORT_PORTAL_CHANNEL_ID", str(FEATURE_REQUEST_CHANNEL_ID)) or 0
)
REPORT_PORTAL_ROLE_ID = int(
    os.getenv("REPORT_PORTAL_ROLE_ID", "1258549984387665943") or 0
)
# ============================================================
# AUTO-JOIN VOICE CHANNEL CONFIG
# ============================================================
AUTO_JOIN_VC_ID = 1543693046758117489  # Main VC the bot returns to
# Guild IDs with a temporary /vc-join channel override. In-memory by design.
_TEMP_VC_OVERRIDE_GUILDS = set()


# ============================================================
# WEB DASHBOARD CONFIG
# ============================================================
NEXTECH_GUILD_ID = "1258549984325009468"
NEXTECH_STAFF_ROLE_ID = "1258549984387665943"
NEXTECH_DISCORD_INVITE = "https://discord.gg/hWVJ5jgAwh"
MEMBER_NOTIFICATION_CHANNEL_ID = int(
    os.getenv("MEMBER_NOTIFICATION_CHANNEL_ID", "1258549985738363007") or 0
)

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
# Normalize common secret-formatting mistakes (quotes or a copied "Bot " prefix).
# Discord.py expects the raw bot token, not "Bot <token>".
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip().strip("\'\"")
if DISCORD_BOT_TOKEN[:4].casefold() == "bot ":
    DISCORD_BOT_TOKEN = DISCORD_BOT_TOKEN[4:].strip()
ROBLOX_CLIENT_ID = os.getenv("ROBLOX_CLIENT_ID", "").strip()
ROBLOX_CLIENT_SECRET = os.getenv("ROBLOX_CLIENT_SECRET", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

PRODUCTS_FILE = "products.json"
FAQ_FILE = "faq.json"
PORTAL_ACCOUNTS_FILE = "portal_accounts.json"
TICKETS_FILE = "case_tickets.json"
MOD_LOGS_FILE = "mod_logs.json"
WARNINGS_FILE = "warnings.json"
BOT_ACTIONS_FILE = "bot_actions.json"
COMMUNITY_NOTIFICATIONS_FILE = "community_notifications.json"
TEAM_PROFILES_FILE = "team_profiles.json"
TEAM_VERIFICATION_MARKS = {
    "formal_staff": ("Formal NexTech Staff", "✔"),
    "green": ("Whitelisted Group Member", "●"),
    "blue": ("Dedicated Member Supporter", "●"),
    "yellow": ("Staff Volunteer", "●"),
    "orange": ("Community Moderation Staff", "✔"),
    "silver": ("Verified Hybrid & Virtual Operations Staff", "✔"),
    "purple": ("Director or Chief-Level Staff", "✔"),
    "deep_blue": ("Owner of NexTech Interactive Development LLC", "✔"),
    "black": ("Suspended, Terminated, or Blacklisted Account", "●"),
}
COMMUNITY_NOTIFICATION_LOCK = Lock()
COMMUNITY_LIVE_MESSAGES = deque(maxlen=100)
COMMUNITY_LIVE_LOCK = Lock()
TEAM_PROFILES_LOCK = Lock()


def _load_community_notifications():
    try:
        with open(COMMUNITY_NOTIFICATIONS_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError):
        import logging
        logging.getLogger(__name__).exception("Could not load community notifications")
        raise


def add_community_notification(
    discord_id,
    notification_type,
    title,
    message,
    source_id,
    href="/dashboard",
):
    add_community_notifications([{
        "discord_id": discord_id,
        "type": notification_type,
        "title": title,
        "message": message,
        "source_id": source_id,
        "href": href,
    }])


def add_community_notifications(entries):
    with COMMUNITY_NOTIFICATION_LOCK:
        data = _load_community_notifications()
        changed = False
        for entry in entries:
            discord_id = str(entry.get("discord_id") or "")
            if not discord_id.isdecimal():
                continue
            notification_type = str(entry.get("type") or "mention")[:40]
            source_id = str(entry.get("source_id") or "")
            identity = hashlib.sha256(
                f"{discord_id}:{notification_type}:{source_id}".encode("utf-8")
            ).hexdigest()[:24]
            member_items = data.get(discord_id, [])
            if not isinstance(member_items, list):
                member_items = []
            if any(
                isinstance(item, dict) and item.get("id") == identity
                for item in member_items
            ):
                data[discord_id] = member_items
                continue
            member_items.append({
                "id": identity,
                "type": notification_type,
                "title": str(entry.get("title") or "NexTech notification")[:120],
                "message": str(entry.get("message") or "")[:300],
                "href": entry.get("href") if entry.get("href") in {"/dashboard", "/community", "/messages"} else "/dashboard",
                "created_at": time.time(),
                "read": False,
            })
            data[discord_id] = member_items[-200:]
            changed = True
        if not changed:
            return
        temporary_path = COMMUNITY_NOTIFICATIONS_FILE + ".tmp"
        try:
            with open(temporary_path, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
            os.replace(temporary_path, COMMUNITY_NOTIFICATIONS_FILE)
        finally:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)


def load_community_notifications(discord_id):
    discord_id = str(discord_id or "")
    with COMMUNITY_NOTIFICATION_LOCK:
        data = _load_community_notifications()
        items = data.get(discord_id, [])
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def mark_community_notifications_read(discord_id, notification_ids=None):
    discord_id = str(discord_id or "")
    if not discord_id.isdecimal():
        return 0
    selected_ids = set(notification_ids or [])
    with COMMUNITY_NOTIFICATION_LOCK:
        data = _load_community_notifications()
        items = data.get(discord_id, [])
        if not isinstance(items, list):
            return 0
        marked = 0
        for item in items:
            if (
                isinstance(item, dict)
                and not item.get("read")
                and (not selected_ids or item.get("id") in selected_ids)
            ):
                item["read"] = True
                marked += 1
        temporary_path = COMMUNITY_NOTIFICATIONS_FILE + ".tmp"
        try:
            with open(temporary_path, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
            os.replace(temporary_path, COMMUNITY_NOTIFICATIONS_FILE)
        finally:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
    return marked


def load_bot_actions():
    try:
        with open(BOT_ACTIONS_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def record_bot_action(action_type, details=None, **extra):
    """Persist a normalized audit record for any important bot action."""
    actions = load_bot_actions()
    entry = {
        "timestamp": time.time(),
        "timestamp_str": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "action_type": action_type,
        "details": details,
    }
    entry.update({key: value for key, value in extra.items() if value is not None})
    actions.append(entry)
    with open(BOT_ACTIONS_FILE, "w") as f:
        json.dump(actions[-5000:], f, indent=2)
    normalized_type = str(action_type).casefold()
    target_id = extra.get("target_discord_id") or extra.get("target_user_id")
    if normalized_type == "warning_issued":
        target_id = target_id or extra.get("user_id")
    member_action = any(
        keyword in normalized_type
        for keyword in ("warning", "warn", "ban", "kick", "timeout", "moderation")
    )
    guild_id = extra.get("guild_id")
    if (
        target_id
        and member_action
        and (guild_id is None or str(guild_id) == NEXTECH_GUILD_ID)
    ):
        try:
            add_community_notification(
                target_id,
                "staff_action",
                "A staff action was recorded",
                "A NexTech staff action was recorded for your account. Sign in to review your account notices.",
                f"{action_type}:{entry['timestamp']}",
            )
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "Could not create member notification for a staff action"
            )


def load_mod_logs():
    try:
        with open(MOD_LOGS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def save_mod_log(entry: dict):
    logs = load_mod_logs()
    logs.append(entry)
    with open(MOD_LOGS_FILE, "w") as f:
        json.dump(logs, f, indent=2)


def load_warnings():
    try:
        with open(WARNINGS_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_warnings(data):
    with open(WARNINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_portal_accounts():
    try:
        with open(PORTAL_ACCOUNTS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_portal_accounts(accts):
    with open(PORTAL_ACCOUNTS_FILE, "w") as f:
        json.dump(accts, f, indent=2)


def load_tickets():
    try:
        with open(TICKETS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_tickets(data: dict):
    with open(TICKETS_FILE, "w") as f:
        json.dump(data, f, indent=2)

def save_ticket_record(case_id: str, record: dict):
    data = load_tickets()
    data[case_id] = record
    save_tickets(data)

def close_ticket_record(case_id: str, messages: list):
    data = load_tickets()
    if case_id in data:
        data[case_id]["status"] = "closed"
        data[case_id]["closed_at"] = time.time()
        data[case_id]["messages"] = messages
        save_tickets(data)


def _record_portal_login(discord_id: str, username: str):
    accts = load_portal_accounts()
    now = time.time()
    if discord_id not in accts:
        accts[discord_id] = {"username": username, "roblox_username": "",
                             "purchases": [], "first_login": now, "last_login": now}
    else:
        accts[discord_id]["last_login"] = now
        accts[discord_id]["username"] = username
    save_portal_accounts(accts)


def _record_portal_purchase(discord_id: str, product: dict):
    accts = load_portal_accounts()
    if discord_id not in accts:
        accts[discord_id] = {"username": "", "roblox_username": "",
                             "purchases": [], "first_login": time.time(), "last_login": time.time()}
    existing_ids = {p.get("product_id") for p in accts[discord_id]["purchases"]}
    if product.get("id") not in existing_ids:
        accts[discord_id]["purchases"].append({
            "product_id": product.get("id", ""),
            "product_name": product.get("name", ""),
            "ts": time.time(),
        })
        save_portal_accounts(accts)


def _record_roblox_username(discord_id: str, roblox_username: str):
    accts = load_portal_accounts()
    if discord_id not in accts:
        accts[discord_id] = {"username": "", "roblox_username": roblox_username,
                             "purchases": [], "first_login": time.time(), "last_login": time.time()}
    else:
        accts[discord_id]["roblox_username"] = roblox_username
    save_portal_accounts(accts)


def load_faq():
    try:
        with open(FAQ_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_faq(faq):
    with open(FAQ_FILE, "w") as f:
        json.dump(faq, f, indent=2)


def load_products():
    try:
        with open(PRODUCTS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_products(products):
    with open(PRODUCTS_FILE, "w") as f:
        json.dump(products, f, indent=2)


start_time = time.time()
app = Flask(__name__)
app.config["SECRET_KEY"] = secrets.token_hex(16)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ============================================================
# EMAIL CONFIG
# ============================================================
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "NexTech Interactive Development LLC")
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "")

NEXTECH_AI_AUTOREPLY = (
    "Hello I am NexTech Core AI! Join our Discord server for faster support, "
    "or please wait a while - a human agent from our team will reply to you. "
    "- NexTech Interactive Development LLC | "
    "\"We are a Team, we Bring the Future for a Better Tomorrow.\" "
    "If you are new, we suggest you open a ticket under Service Consultation."
)
NEXTECH_DISCORD_INVITE_LINK = "https://discord.gg/hWVJ5jgAwh"

# Track message IDs we've already auto-replied to (IMAP)
_imap_replied = set()
_email_ticket_counter = 0


def _get_base_url():
    if SITE_BASE_URL:
        return SITE_BASE_URL.rstrip("/")
    domain = os.getenv("REPLIT_DEV_DOMAIN", "")
    if domain:
        return f"https://{domain}"
    return ""


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _roblox_pkce_pair():
    verifier = _base64url(secrets.token_bytes(32))
    challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _roblox_oauth_url(state: str, challenge: str, nonce: str) -> str:
    import urllib.parse as _up

    base = _get_base_url()
    if not base:
        raise RuntimeError(
            "SITE_BASE_URL or REPLIT_DEV_DOMAIN must be configured for Roblox OAuth."
        )
    params = {
        "client_id": ROBLOX_CLIENT_ID,
        "redirect_uri": f"{base}/auth/roblox/callback",
        "response_type": "code",
        "scope": "openid profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return "https://apis.roblox.com/oauth/v1/authorize?" + _up.urlencode(params)


def _discord_oauth_url(redirect_path: str, state: str) -> str:
    """Build a Discord OAuth URL using the exact callback registered in Discord."""
    import urllib.parse as _up

    base = _get_base_url()
    if not base:
        raise RuntimeError(
            "SITE_BASE_URL or REPLIT_DEV_DOMAIN must be configured for Discord OAuth."
        )
    redirect_uri = f"{base}{redirect_path}"
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "identify",
        "state": state,
    }
    return "https://discord.com/api/oauth2/authorize?" + _up.urlencode(params)


def send_email(to_addr: str, subject: str, html_body: str, text_body: str = ""):
    """Send an email via SMTP. Returns (ok: bool, error: str)."""
    if not SMTP_USER or not SMTP_PASS:
        return False, "SMTP credentials not configured."
    try:
        from email.utils import formataddr
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = formataddr((SMTP_FROM_NAME, SMTP_FROM or SMTP_USER))
        msg["To"] = to_addr
        if text_body:
            msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
            s.ehlo()
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, to_addr, msg.as_string())
        return True, ""
    except Exception as e:
        return False, str(e)


def _imap_autoreply_loop():
    """Background thread: polls Gmail inbox every 90 seconds, opens a staff ticket, and auto-replies."""
    import imaplib
    import email as _email
    from email.header import decode_header as _decode_header
    from email.utils import parseaddr

    global _email_ticket_counter

    def _decode(val):
        if not val:
            return ""
        parts = _decode_header(val)
        result = []
        for b, enc in parts:
            if isinstance(b, bytes):
                result.append(b.decode(enc or "utf-8", errors="replace"))
            else:
                result.append(b)
        return " ".join(result)

    def _extract_body(msg):
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                disp = str(part.get("Content-Disposition", ""))
                if ct == "text/plain" and "attachment" not in disp:
                    try:
                        body = part.get_payload(decode=True).decode(
                            part.get_content_charset() or "utf-8", errors="replace")
                        break
                    except Exception:
                        pass
        else:
            try:
                body = msg.get_payload(decode=True).decode(
                    msg.get_content_charset() or "utf-8", errors="replace")
            except Exception:
                body = ""
        return body.strip()[:2000]

    while True:
        try:
            if not SMTP_USER or not SMTP_PASS:
                time.sleep(90); continue
            imap = imaplib.IMAP4_SSL("imap.gmail.com", 993)
            imap.login(SMTP_USER, SMTP_PASS)
            imap.select("INBOX")
            _, data = imap.search(None, "UNSEEN")
            ids = data[0].split() if data[0] else []
            for num in ids:
                _, msg_data = imap.fetch(num, "(RFC822)")
                raw = msg_data[0][1]
                msg = _email.message_from_bytes(raw)
                msg_id = msg.get("Message-ID", "")
                if not msg_id or msg_id in _imap_replied:
                    continue
                from_header = msg.get("From", "")
                from_name, from_addr = parseaddr(from_header)
                if not from_addr or from_addr.lower() == SMTP_USER.lower():
                    continue

                subject_raw = msg.get("Subject", "")
                orig_subject = _decode(subject_raw)
                email_body = _extract_body(msg)

                # Generate ticket number and create staff dashboard session
                _email_ticket_counter += 1
                ticket_label = f"NTC-{_email_ticket_counter:04d}"
                session_id = str(uuid.uuid4())
                display_name = from_name.strip() if from_name.strip() else from_addr

                sys_text = (
                    f"Email ticket opened by {display_name} <{from_addr}>\n"
                    f"Subject: {orig_subject or '(no subject)'}\n"
                    f"Ticket: {ticket_label}"
                )
                msgs = [{"id": str(uuid.uuid4()), "from": "system",
                         "text": sys_text, "ts": time.time() * 1000}]
                if email_body:
                    msgs.append({"id": str(uuid.uuid4()), "from": "customer",
                                 "text": email_body, "ts": time.time() * 1000})

                chat_sessions[session_id] = {
                    "id": session_id,
                    "source": "email",
                    "cuid": None,
                    "customer_sid": None,
                    "name": display_name,
                    "emailFrom": from_addr,
                    "emailSubject": orig_subject,
                    "ticketNumber": ticket_label,
                    "roblox": None, "robloxUsername": None,
                    "robloxDisplay": None, "robloxAvatar": None,
                    "staff_sid": None, "staffName": None, "staffAvatar": None,
                    "messages": msgs,
                    "created": time.time(),
                    "status": "waiting",
                }
                _broadcast_sessions()

                # Auto-reply with ticket number
                reply_subject = (
                    f"[NexTech Support] Ticket {ticket_label} Opened"
                    + (f" – {orig_subject}" if orig_subject else "")
                )
                base_url = _get_base_url() or "our website"
                html_body = f"""
<div style="font-family:Verdana,Geneva,sans-serif;max-width:600px;margin:0 auto;background:#0b0d14;color:#e7ecf6;border-radius:14px;overflow:hidden;border:1px solid #222633">
  <div style="background:linear-gradient(90deg,#1a4fa0,#3b82f6);padding:24px 28px">
    <div style="font-size:20px;font-weight:900;color:#fff;font-family:Verdana,Geneva,sans-serif">NexTech Interactive Development LLC</div>
    <div style="font-size:12px;color:#dbeafe;margin-top:4px;font-family:Verdana,Geneva,sans-serif">NexTech Core — Automated Support Response</div>
  </div>
  <div style="padding:28px;font-family:Verdana,Geneva,sans-serif">
    <div style="background:#f4a830;color:#000;font-weight:900;font-size:15px;padding:12px 18px;border-radius:10px;margin-bottom:20px;font-family:Verdana,Geneva,sans-serif">Your ticket has been opened: {ticket_label}</div>
    <p style="margin:0 0 16px;font-size:14px;color:#e7ecf6">{NEXTECH_AI_AUTOREPLY}</p>
    <a href="{NEXTECH_DISCORD_INVITE_LINK}" style="display:inline-block;background:#5865F2;color:#fff;font-weight:800;padding:12px 22px;border-radius:10px;text-decoration:none;font-size:13px;font-family:Verdana,Geneva,sans-serif">Join Discord &rarr;</a>
    <p style="margin:22px 0 0;font-size:10px;color:#5b6373;font-family:Verdana,Geneva,sans-serif">Reference ticket number <b style="color:#f4a830">{ticket_label}</b> in all future emails. For direct support visit: {base_url}</p>
  </div>
</div>"""
                text_body = (
                    f"Your ticket has been opened: {ticket_label}\n\n"
                    f"{NEXTECH_AI_AUTOREPLY}\n\n"
                    f"Join our Discord: {NEXTECH_DISCORD_INVITE_LINK}\n\n"
                    f"Reference ticket number {ticket_label} in all future emails."
                )
                _imap_replied.add(msg_id)
                send_email(from_addr, reply_subject, html_body, text_body)
            imap.logout()
        except Exception:
            pass
        time.sleep(90)


Thread(target=_imap_autoreply_loop, daemon=True).start()

# In-memory live-chat state
chat_sessions = {}      # session_id -> dict
staff_online = {}       # sid -> {"name": str, "discord_id": str, "avatar": str}
staff_tokens = {}       # token -> {"name": str, "discord_id": str, "avatar": str}

# email reply tokens: token -> {session_id, to_email, ts}
email_reply_tokens = {}
_EMAIL_TOKEN_TTL = 86400 * 7  # 7 days

# Discord OAuth: state -> ts (CSRF), pending -> discord info awaiting step-2
_discord_oauth_states = {}
_discord_pending_auths = {}
_DISCORD_PENDING_TTL = 300  # 5 minutes

BUYER_ROLE_ID = 1311257043348492298
PRODUCT_FILES_DIR = os.path.join("static", "product_files")
os.makedirs(PRODUCT_FILES_DIR, exist_ok=True)
_portal_oauth_states = {}
portal_sessions = {}
_community_oauth_states = {}
community_sessions = {}
download_tokens = {}  # token -> {discord_id, product_id, product_name, file_url, expires_at}
disabled_commands: set = set()  # command names disabled by the developer via dev panel

# /case-support: user IDs currently going through the flow (prevent double-tickets)
_case_support_active = set()

# Customer account system
customer_tokens = {}  # token -> {provider, username, login_ts, ...}
_roblox_oauth_states = {}  # state -> {verifier, nonce, ts}
_ROBLOX_OAUTH_TTL = 300
_CUSTOMER_TOKEN_TTL = 86400 * 30
_DASHBOARD_EMAIL_LOGIN_TTL = 900
_dashboard_email_login_requests = {}
_DASHBOARD_PROFILE_CODE_TTL = 300
_DASHBOARD_PROFILE_CODE_ISSUE_COOLDOWN = 60
_DASHBOARD_PROFILE_CODE_ATTEMPT_WINDOW = 600
_DASHBOARD_PROFILE_CODE_MAX_ATTEMPTS = 5
_dashboard_profile_codes = {}
_dashboard_profile_code_issuance = {}
_dashboard_profile_login_attempts = {}
_dashboard_profile_code_lock = Lock()
_PROTECTED_ROLE_CACHE_TTL = 30
_protected_roblox_role_cache = {}

# Developer / staff account management
DEV_KEY = os.environ.get("DEV_KEY", "")
dev_tokens = {}          # token -> {ts}
staff_registrations = [] # [{id, discord_username, discord_id, roblox_url, group_url, submitted_at}]
staff_accounts = {}      # roblox_url_lower -> {discord_username, roblox_url, group_url, personal_key, created_at, suspended}

def _load_customers():
    try:
        with open("customers.json") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_customers(data):
    with open("customers.json", "w") as f:
        json.dump(data, f, indent=2)

def _hash_password(pw, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200000).hex()
    return f"{salt}${key}"

def _verify_password(pw, stored):
    try:
        salt, _ = stored.split("$", 1)
        return _hash_password(pw, salt) == stored
    except Exception:
        return False


_CUSTOMER_PASSWORD_RESET_TTL = 3600


def _set_customer_password_reset_token(account):
    token = secrets.token_urlsafe(32)
    account["password_reset_token_hash"] = hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()
    account["password_reset_expires_at"] = time.time() + _CUSTOMER_PASSWORD_RESET_TTL
    return token


def _send_customer_password_reset_email(email, username, token, account_setup=False):
    import html
    from urllib.parse import urlencode

    base_url = _get_base_url()
    if not base_url:
        return False, "SITE_BASE_URL is not configured."
    reset_url = f"{base_url}/password/reset?{urlencode({'email': email, 'token': token})}"
    safe_username = html.escape(username)
    safe_url = html.escape(reset_url, quote=True)
    if account_setup:
        subject = "Set up your NexTech account"
        action = "Set your password to activate your NexTech account."
        button = "Set my password"
    else:
        subject = "Reset your NexTech password"
        action = "Use this link to choose a new NexTech account password."
        button = "Reset my password"
    html_body = (
        f"<p>Hello {safe_username},</p><p>{action}</p>"
        f'<p><a href="{safe_url}">{button}</a></p>'
        "<p>This single-use link expires in one hour. If you did not request this, "
        "you can ignore this email.</p>"
    )
    text_body = (
        f"Hello {username},\n\n{action}\n\n{reset_url}\n\n"
        "This single-use link expires in one hour. If you did not request this, "
        "you can ignore this email."
    )
    return send_email(email, subject, html_body, text_body)

# Cache: roblox user id -> {"username", "displayName", "avatar", "url", "ts"}
_roblox_cache = {}
_ROBLOX_CACHE_TTL = 3600


def _extract_roblox_id(url: str):
    if not url:
        return None
    m = re.match(r"^https?://(?:www\.)?roblox\.com/users/(\d+)", url.strip(), re.I)
    return m.group(1) if m else None


def fetch_roblox_profile(url: str):
    """Resolve a Roblox profile URL to {username, displayName, avatar, url, userId}.
    Returns None on failure."""
    rid = _extract_roblox_id(url)
    if not rid:
        return None
    cached = _roblox_cache.get(rid)
    if cached and (time.time() - cached["ts"] < _ROBLOX_CACHE_TTL):
        return cached
    try:
        info = http_requests.get(
            f"https://users.roblox.com/v1/users/{rid}", timeout=5
        ).json()
        username = info.get("name") or f"User{rid}"
        display = info.get("displayName") or username
    except Exception:
        username = f"User{rid}"
        display = username
    avatar = None
    try:
        thumb = http_requests.get(
            "https://thumbnails.roblox.com/v1/users/avatar-headshot",
            params={"userIds": rid, "size": "150x150",
                    "format": "Png", "isCircular": "true"},
            timeout=5,
        ).json()
        data = (thumb.get("data") or [])
        if data:
            avatar = data[0].get("imageUrl")
    except Exception:
        pass
    profile = {
        "userId": rid, "username": username, "displayName": display,
        "avatar": avatar, "url": f"https://www.roblox.com/users/{rid}/profile",
        "ts": time.time(),
    }
    _roblox_cache[rid] = profile
    return profile


def _staff_account_is_authorized(info):
    roblox_id = _extract_roblox_id(str(info.get("roblox") or ""))
    discord_id = str(info.get("discord_id") or "")
    if not roblox_id or not discord_id.isascii() or not discord_id.isdecimal():
        return False
    if not _is_protected_roblox_staff_sync(roblox_id):
        return False
    try:
        guild = client.get_guild(int(NEXTECH_GUILD_ID))
        member = guild.get_member(int(discord_id)) if guild else None
    except (TypeError, ValueError):
        member = None
    return bool(member and not member.bot and _dashboard_role_access(member)[0] >= 2)


def _get_authorized_staff_session(token):
    info = staff_tokens.get(token)
    if not info:
        return None
    if not _staff_account_is_authorized(info):
        staff_tokens.pop(token, None)
        return None
    return info


def _get_authorized_staff_socket(sid):
    info = staff_online.get(sid)
    if not info:
        return None
    if not _staff_account_is_authorized(info):
        staff_online.pop(sid, None)
        disconnect()
        _broadcast_presence()
        _broadcast_sessions()
        return None
    return info


def _public_session(s):
    last = s["messages"][-1] if s["messages"] else None
    return {
        "id": s["id"], "name": s["name"], "roblox": s.get("roblox"),
        "robloxUsername": s.get("robloxUsername"),
        "robloxDisplay": s.get("robloxDisplay"),
        "robloxAvatar": s.get("robloxAvatar"),
        "status": s["status"], "staffName": s.get("staffName"),
        "staffAvatar": s.get("staffAvatar"),
        "created": s["created"], "lastMessage": last,
        "messageCount": len(s["messages"]),
        "source": s.get("source", "chat"),
        "emailFrom": s.get("emailFrom"),
        "emailSubject": s.get("emailSubject"),
        "ticketNumber": s.get("ticketNumber"),
    }


def _broadcast_sessions():
    arr = sorted(
        [_public_session(s) for s in chat_sessions.values() if s["status"] != "closed"],
        key=lambda x: x["created"], reverse=True,
    )
    socketio.emit("sessions:list", arr, room="staff")


def _broadcast_presence():
    socketio.emit("presence", {
        "online": len(staff_online),
        "names": [s["name"] for s in staff_online.values()],
    })


@app.route('/favicon.ico')
def favicon():
    return '', 204


@app.route('/health')
def health():
    """Health check endpoint for monitoring"""
    return jsonify({'status': 'ok', 'timestamp': time.time()}), 200

@app.route('/uptime.json')
def get_uptime_json():
    uptime_seconds = int(time.time() - start_time)
    hours = uptime_seconds // 3600
    minutes = (uptime_seconds % 3600) // 60
    seconds = uptime_seconds % 60
    return jsonify({'uptime': f"{hours}h {minutes}m {seconds}s",
                    'seconds': uptime_seconds})


@app.route('/api/commands')
def api_commands():
    cmds = sorted(
        [{"name": c.name, "description": c.description or "No description."}
         for c in tree.get_commands()],
        key=lambda x: x["name"]
    )
    return jsonify(cmds)


@app.route('/api/staff/login', methods=['POST'])
def api_staff_login():
    return jsonify({
        "error": "Use the secure staff sign-in page."
    }), 410


@app.route('/staff/login')
def staff_login_page():
    return redirect("/auth/roblox?next=/dashboard")


@app.route('/staff')
def staff_dashboard_page():
    return redirect("/dashboard")


# ============================================================
# DEVELOPER API
# ============================================================

async def _dm_product_delivery(discord_id: str, product: dict):
    try:
        user = await client.fetch_user(int(discord_id))
        embed = discord.Embed(
            title=f"📦 Your NexTech Purchase: {product.get('name', 'Product')}",
            description=(
                f"Thank you for your purchase from NexTech Interactive Development LLC!\n\n"
                f"**Product ID:** `{product.get('id', '—')}`\n"
                f"**Category:** {product.get('category', '—')}\n"
                + (f"**Price:** R$ {product['robux']:,}" if product.get('robux') else "")
            ),
            color=discord.Color.gold(),
        )
        embed.set_footer(text="NexTech Interactive Development LLC · \"We Bring the Future for a Better Tomorrow.\"")
        file_path = product.get("file_path", "")
        if file_path and os.path.exists(file_path):
            await user.send(embed=embed, file=discord.File(file_path))
        else:
            fallback = product.get("file_url") or product.get("image") or ""
            if fallback:
                embed.add_field(name="Your Files", value=fallback, inline=False)
            else:
                embed.add_field(name="Next Steps", value="A staff member will send your files shortly. Join our Discord for assistance.", inline=False)
            await user.send(embed=embed)
        _record_portal_purchase(str(discord_id), product)
    except Exception:
        pass


async def _dm_download_link(discord_id: str, product_name: str, dl_url: str, expires_ts: float):
    try:
        user = await client.fetch_user(int(discord_id))
        import datetime as _dt
        exp_str = _dt.datetime.utcfromtimestamp(expires_ts).strftime("%B %d, %Y at %H:%M UTC")
        embed = discord.Embed(
            title="📦 Your Download Link is Ready",
            description=(
                f"Your secure download link for **{product_name}** has been generated.\n\n"
                f"**[→ Click here to access your download]({dl_url})**\n\n"
                f"Open the link, copy the URL shown, and paste it into your browser to download your product."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(name="⏰ Link Expires", value=exp_str, inline=False)
        embed.add_field(name="🔒 Security Note", value="This link is personal — do not share it. After expiry, log into the portal to generate a new one.", inline=False)
        embed.set_footer(text="NexTech Interactive Development LLC · \"We Bring the Future for a Better Tomorrow.\"")
        await user.send(embed=embed)
    except Exception:
        pass


async def _dm_new_staff(discord_id: str, discord_username: str, personal_key: str, roblox_url: str):
    """Send a DM to the newly approved staff member with their login credentials."""
    try:
        user = await client.fetch_user(int(discord_id))
        embed = discord.Embed(
            title="✅ Your NexTech Staff Account Has Been Created",
            description=(
                "The developer has approved your registration. "
                "You can now log into the staff dashboard."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(name="Staff Dashboard", value="https://" + os.getenv("REPLIT_DEV_DOMAIN", "nextech.repl.co") + "/staff", inline=False)
        embed.add_field(name="Your Roblox URL", value=roblox_url, inline=False)
        embed.add_field(name="Your Personal Login Key", value=f"`{personal_key}`", inline=False)
        embed.set_footer(text="NexTech Interactive Development LLC · Keep your key private.")
        await user.send(embed=embed)
    except Exception:
        pass


def _require_dev_token(req):
    tok = req.headers.get("X-Dev-Token") or req.args.get("dev_token")
    if not tok or tok not in dev_tokens:
        return None
    if time.time() - dev_tokens[tok]["ts"] > 3600 * 8:
        dev_tokens.pop(tok, None)
        return None
    return tok


@app.route('/api/dev/verify', methods=['POST'])
def api_dev_verify():
    if not DEV_KEY:
        return jsonify({"error": "Developer-key sign-in is disabled. Use staff role-based sign-in."}), 503
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip()
    if key != DEV_KEY:
        return jsonify({"error": "Invalid developer key."}), 401
    tok = secrets.token_hex(24)
    dev_tokens[tok] = {"ts": time.time()}
    return jsonify({"token": tok})


@app.route('/api/dev/registrations')
def api_dev_registrations():
    if not _require_dev_token(request):
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"registrations": staff_registrations})


@app.route('/api/dev/create-staff', methods=['POST'])
def api_dev_create_staff():
    if not _require_dev_token(request):
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    reg_id = body.get("registration_id")
    reg = next((r for r in staff_registrations if r["id"] == reg_id), None)
    if not reg:
        return jsonify({"error": "Registration not found."}), 404
    roblox_key = reg["roblox_url"].lower().rstrip("/")
    if roblox_key in staff_accounts:
        return jsonify({"error": "A staff account already exists for this Roblox URL."}), 409
    personal_key = secrets.token_hex(8)
    staff_accounts[roblox_key] = {
        "discord_username": reg["discord_username"],
        "roblox_url": reg["roblox_url"],
        "group_url": reg.get("group_url", ""),
        "personal_key": personal_key,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "suspended": False,
        "registration_id": reg_id,
    }
    staff_registrations[:] = [r for r in staff_registrations if r["id"] != reg_id]
    discord_id = reg.get("discord_id")
    if discord_id and client and client.loop and not client.loop.is_closed():
        asyncio.run_coroutine_threadsafe(
            _dm_new_staff(discord_id, reg["discord_username"], personal_key, reg["roblox_url"]),
            client.loop,
        )
    return jsonify({"ok": True, "personal_key": personal_key, "discord_username": reg["discord_username"]})


@app.route('/api/dev/suspend-staff', methods=['POST'])
def api_dev_suspend_staff():
    if not _require_dev_token(request):
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    roblox_url = (body.get("roblox_url") or "").strip()
    suspended = bool(body.get("suspended", True))
    key = roblox_url.lower().rstrip("/")
    acct = staff_accounts.get(key)
    if not acct:
        return jsonify({"error": "Staff account not found."}), 404
    acct["suspended"] = suspended
    if suspended:
        for tok, info in list(staff_tokens.items()):
            if info.get("roblox", "").lower().rstrip("/") == key:
                staff_tokens.pop(tok, None)
    return jsonify({"ok": True, "suspended": suspended})


@app.route('/api/dev/staff-list')
def api_dev_staff_list():
    if not _require_dev_token(request):
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"accounts": list(staff_accounts.values())})


@app.route('/api/dev/portal-accounts')
def api_dev_portal_accounts():
    if not _require_dev_token(request):
        return jsonify({"error": "unauthorized"}), 401
    accts = load_portal_accounts()
    result = []
    for discord_id, data in accts.items():
        result.append({
            "discord_id": discord_id,
            "username": data.get("username", "Unknown"),
            "roblox_username": data.get("roblox_username", ""),
            "credits": data.get("credits", 0),
            "purchases": data.get("purchases", []),
            "first_login": data.get("first_login"),
            "last_login": data.get("last_login"),
            "locked": data.get("locked", False),
        })
    result.sort(key=lambda x: x.get("last_login") or 0, reverse=True)
    return jsonify({"accounts": result})


@app.route('/api/dev/lock-account', methods=['POST'])
def api_dev_lock_account():
    if not _require_dev_token(request):
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    discord_id = str(body.get("discord_id", "")).strip()
    locked = bool(body.get("locked", True))
    if not discord_id:
        return jsonify({"error": "discord_id required"}), 400
    accts = load_portal_accounts()
    if discord_id not in accts:
        accts[discord_id] = {}
    accts[discord_id]["locked"] = locked
    save_portal_accounts(accts)
    if locked:
        for tok, info in list(portal_sessions.items()):
            if str(info.get("discord_id", "")) == discord_id:
                portal_sessions.pop(tok, None)
    return jsonify({"ok": True, "locked": locked})


@app.route('/api/dev/commands')
def api_dev_commands():
    if not _require_dev_token(request):
        return jsonify({"error": "unauthorized"}), 401
    cmds = []
    for cmd in tree.get_commands():
        cmds.append({
            "name": cmd.name,
            "description": cmd.description or "",
            "disabled": cmd.name in disabled_commands,
        })
    cmds.sort(key=lambda x: x["name"])
    return jsonify({"commands": cmds})


@app.route('/api/dev/products', methods=['GET', 'POST', 'DELETE'])
def api_dev_products():
    if not _require_dev_token(request):
        return jsonify({"error": "unauthorized"}), 401
    products = load_products()
    if request.method == "GET":
        return jsonify({"products": products})
    if request.method == "DELETE":
        product_id = (request.args.get("product_id") or "").strip().lower()
        updated = [p for p in products if str(p.get("id", "")).lower() != product_id]
        if len(updated) == len(products):
            return jsonify({"error": "Product not found."}), 404
        save_products(updated)
        return jsonify({"ok": True})

    form = request.form
    product_id = (form.get("product_id") or "").strip()
    name = (form.get("name") or "").strip()
    category = (form.get("category") or "").strip()
    description = (form.get("description") or "").strip()
    if not product_id or not name or not category or not description:
        return jsonify({"error": "Product ID, name, category, and description are required."}), 400
    if any(str(p.get("id", "")).lower() == product_id.lower() for p in products):
        return jsonify({"error": "A product with that ID already exists."}), 409

    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", product_id)[:80] or "product"
    prod_dir = os.path.join(PRODUCT_FILES_DIR, safe_id)
    os.makedirs(prod_dir, exist_ok=True)
    image = request.files.get("image")
    product_file = request.files.get("product_file")
    image_url = (form.get("image_url") or "").strip()
    file_url = (form.get("file_url") or "").strip()
    image_name = ""
    file_name = ""
    if image and image.filename:
        image_name = os.path.basename(image.filename)
        image.save(os.path.join(prod_dir, image_name))
        image_url = f"/static/product_files/{safe_id}/{image_name}"
    if product_file and product_file.filename:
        file_name = os.path.basename(product_file.filename)
        product_file.save(os.path.join(prod_dir, file_name))
        file_url = f"/static/product_files/{safe_id}/{file_name}"

    try:
        robux_price = int(form.get("robux_price") or 0)
    except (TypeError, ValueError):
        robux_price = 0
    try:
        price_usd = float(form.get("price_usd") or 0)
    except (TypeError, ValueError):
        price_usd = 0.0
    product = {
        "id": product_id,
        "name": name,
        "category": category,
        "description": description,
        "image": image_url,
        "robux": robux_price,
        "created": time.time(),
        "added_by": "Website developer panel",
        "file_path": os.path.join(prod_dir, file_name) if file_name else "",
        "file_url": file_url,
        "gamepass_id": (form.get("gamepass_id") or "").strip(),
        "price_usd": price_usd,
    }
    products.append(product)
    save_products(products)
    return jsonify({"ok": True, "product": product}), 201


@app.route('/api/dev/faq', methods=['GET', 'POST', 'DELETE'])
def api_dev_faq():
    if not _require_dev_token(request):
        return jsonify({"error": "unauthorized"}), 401
    faq = load_faq()
    if request.method == "GET":
        return jsonify({"faq": faq})
    if request.method == "DELETE":
        faq_id = (request.args.get("faq_id") or "").strip()
        updated = [item for item in faq if item.get("id") != faq_id]
        if len(updated) == len(faq):
            return jsonify({"error": "FAQ entry not found."}), 404
        save_faq(updated)
        return jsonify({"ok": True})
    body = request.get_json(silent=True) or {}
    category = (body.get("category") or "").strip()
    question = (body.get("question") or "").strip()
    answer = (body.get("answer") or "").strip()
    if not category or not question or not answer:
        return jsonify({"error": "Category, question, and answer are required."}), 400
    entry = {
        "id": str(uuid.uuid4())[:8],
        "category": category,
        "question": question,
        "answer": answer,
        "created": time.time(),
        "added_by": "Website developer panel",
    }
    faq.append(entry)
    save_faq(faq)
    return jsonify({"ok": True, "faq": entry}), 201


@app.route('/api/dev/warnings')
def api_dev_warnings():
    if not _require_dev_token(request):
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"warnings": load_warnings()[-500:][::-1]})


@app.route('/api/dev/toggle-command', methods=['POST'])
def api_dev_toggle_command():
    if not _require_dev_token(request):
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    enabled = bool(body.get("enabled", True))
    if not name:
        return jsonify({"error": "name required"}), 400
    if enabled:
        disabled_commands.discard(name)
    else:
        disabled_commands.add(name)
    return jsonify({"ok": True, "name": name, "enabled": enabled})


@app.route('/api/dev/backfill-tickets', methods=['POST'])
def api_dev_backfill_tickets():
    if not _require_dev_token(request):
        return jsonify({"error": "unauthorized"}), 401
    if not client or not client.loop or client.loop.is_closed():
        return jsonify({"error": "Bot not ready"}), 503

    async def do_backfill():
        guild = client.get_guild(int(NEXTECH_GUILD_ID))
        if not guild:
            return {"error": "Guild not found", "added": 0, "skipped": 0}
        category = guild.get_channel(SUPPORT_CATEGORY_ID)
        if not category or not isinstance(category, discord.CategoryChannel):
            return {"error": "Support category not found", "added": 0, "skipped": 0}
        existing = load_tickets()
        added = 0
        skipped = 0
        for ch in category.text_channels:
            if not ch.name.startswith("ticket-"):
                skipped += 1
                continue
            topic = ch.topic or ""
            if "Case ID:" not in topic:
                skipped += 1
                continue
            try:
                parts = {p.split(":")[0].strip(): ":".join(p.split(":")[1:]).strip() for p in topic.split("|")}
                case_id = parts.get("Case ID", "").strip()
                opened_date = parts.get("Opened", "").strip()
                raw_case_type = topic.split(" Case |")[0].strip()
                user_mention_raw = parts.get("User", "").strip()
                discord_id = user_mention_raw.replace("<@", "").replace(">", "").replace("!", "").strip()
            except Exception:
                skipped += 1
                continue
            if not case_id:
                skipped += 1
                continue
            if case_id in existing:
                skipped += 1
                continue
            roblox_username = ""
            reason = ""
            confirmed_type = ""
            discord_username = ""
            try:
                async for msg in ch.history(limit=10, oldest_first=True):
                    if msg.embeds:
                        for emb in msg.embeds:
                            desc = emb.description or ""
                            for line in desc.splitlines():
                                if "Roblox Username:" in line:
                                    roblox_username = line.split("Roblox Username:")[-1].strip()
                                elif "Reason:" in line:
                                    reason = line.split("Reason:")[-1].strip()
                                elif "Case Type (Confirmed):" in line:
                                    confirmed_type = line.split("Case Type (Confirmed):")[-1].strip()
                                elif "Discord Username:" in line:
                                    discord_username = line.split("Discord Username:")[1].split("(")[0].strip()
                        break
            except Exception:
                pass
            try:
                member = guild.get_member(int(discord_id)) if discord_id.isdigit() else None
                if member and not discord_username:
                    discord_username = str(member)
            except Exception:
                pass
            record = {
                "case_id": case_id,
                "case_name": ch.name.replace("ticket-", "", 1),
                "case_type": raw_case_type,
                "discord_id": discord_id,
                "discord_username": discord_username,
                "roblox_username": roblox_username,
                "reason": reason,
                "confirmed_type": confirmed_type,
                "opened_at": ch.created_at.timestamp(),
                "opened_date": opened_date or ch.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "status": "open",
                "channel_id": str(ch.id),
                "messages": [],
            }
            save_ticket_record(case_id, record)
            added += 1
        return {"added": added, "skipped": skipped, "total_now": len(load_tickets())}

    future = asyncio.run_coroutine_threadsafe(do_backfill(), client.loop)
    try:
        result = future.result(timeout=30)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(result)


@app.route('/auth/roblox')
def auth_roblox():
    if not ROBLOX_CLIENT_ID or not ROBLOX_CLIENT_SECRET:
        return redirect("/dashboard?error=roblox_oauth_not_configured")
    state = secrets.token_urlsafe(24)
    verifier, challenge = _roblox_pkce_pair()
    nonce = secrets.token_urlsafe(24)
    _roblox_oauth_states[state] = {
        "verifier": verifier,
        "nonce": nonce,
        "ts": time.time(),
        "next": request.args.get("next", "/"),
    }
    try:
        return redirect(_roblox_oauth_url(state, challenge, nonce))
    except RuntimeError:
        _roblox_oauth_states.pop(state, None)
        return redirect("/dashboard?error=base_url_not_configured")


@app.route('/auth/roblox/callback')
def auth_roblox_callback():
    import urllib.parse as _up

    error = request.args.get("error", "")
    if error:
        return redirect(f"/login?error={_up.quote(error)}")

    state = request.args.get("state", "")
    flow = _roblox_oauth_states.pop(state, None)
    if not flow or (time.time() - flow["ts"]) > _ROBLOX_OAUTH_TTL:
        return redirect("/dashboard?error=invalid_state")

    code = request.args.get("code", "")
    if not code:
        return redirect("/dashboard?error=no_code")

    base = _get_base_url()
    if not base:
        return redirect("/dashboard?error=base_url_not_configured")
    redirect_uri = f"{base}/auth/roblox/callback"
    try:
        token_response = http_requests.post(
            "https://apis.roblox.com/oauth/v1/token",
            data={
                "client_id": ROBLOX_CLIENT_ID,
                "client_secret": ROBLOX_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": flow["verifier"],
                "redirect_uri": redirect_uri,
            },
            timeout=10,
        )
        token_response.raise_for_status()
        token_data = token_response.json()
    except Exception:
        return redirect("/dashboard?error=token_exchange_failed")

    access_token = token_data.get("access_token", "")
    if not access_token:
        return redirect("/dashboard?error=no_access_token")

    try:
        user_response = http_requests.get(
            "https://apis.roblox.com/oauth/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        user_response.raise_for_status()
        user = user_response.json()
    except Exception:
        return redirect("/dashboard?error=user_fetch_failed")

    roblox_id = str(user.get("sub", "")).strip()
    if not roblox_id:
        return redirect("/dashboard?error=user_fetch_failed")
    username = (
        user.get("preferred_username")
        or user.get("nickname")
        or user.get("name")
        or f"Roblox User {roblox_id}"
    )
    display_name = user.get("name") or username
    avatar = user.get("picture") or ""

    customer_token = secrets.token_urlsafe(32)
    customer_tokens[customer_token] = {
        "provider": "roblox",
        "roblox_id": roblox_id,
        "username": username,
        "display_name": display_name,
        "avatar": avatar,
        "login_ts": time.time(),
    }
    next_path = flow.get("next", "/")
    if not isinstance(next_path, str) or not next_path.startswith("/") or next_path.startswith("//"):
        next_path = "/"
    if next_path.split("?", 1)[0] == "/dashboard":
        linked, linked_discord_id, linked_member = _dashboard_linked_member(roblox_id)
        if not linked or not linked_discord_id or not linked_member:
            customer_tokens.pop(customer_token, None)
            return redirect("/dashboard?error=roblox_not_linked")
    if next_path.split("?", 1)[0] == "/staff":
        linked, discord_id, member = _dashboard_linked_member(roblox_id)
        if not linked or not discord_id or not member:
            customer_tokens.pop(customer_token, None)
            return redirect("/dashboard?error=roblox_not_linked")
        role_level, _role_name = _dashboard_role_access(member)
        if role_level < 2 or not _is_protected_roblox_staff_sync(roblox_id):
            customer_tokens.pop(customer_token, None)
            return redirect("/dashboard?error=no_staff_role")
        staff_token = secrets.token_hex(24)
        staff_name = member.display_name or str(member)
        staff_avatar = str(member.display_avatar.url)
        staff_tokens[staff_token] = {
            "name": staff_name,
            "discord_id": discord_id,
            "discord_username": str(member),
            "discord_avatar": staff_avatar,
            "avatar": staff_avatar or avatar,
            "roblox": f"https://www.roblox.com/users/{roblox_id}/profile",
            "roblox_username": username,
            "login_ts": time.time(),
        }
        customer_tokens.pop(customer_token, None)
        return redirect(
            f"/staff?token={_up.quote(staff_token)}"
            f"&name={_up.quote(staff_name)}&avatar={_up.quote(staff_avatar)}"
        )
    if next_path.split("?", 1)[0] in ("/portal", "/login", "/staff", "/"):
        linked = _dashboard_link_record(roblox_id)
        discord_id = str(
            (linked or {}).get("discordId")
            or (linked or {}).get("discord_id")
            or (linked or {}).get("discordUserId")
            or ""
        )
        guild = client.get_guild(int(NEXTECH_GUILD_ID))
        try:
            member = guild.get_member(int(discord_id)) if guild and discord_id else None
        except (TypeError, ValueError):
            member = None
        if not member:
            return redirect("/dashboard?error=roblox_not_linked")
        _chk_accts = load_portal_accounts()
        if _chk_accts.get(str(discord_id), {}).get("locked"):
            return redirect("/dashboard?error=account_locked")
        portal_token = secrets.token_urlsafe(32)
        portal_sessions[portal_token] = {
            "discord_id": discord_id,
            "username": str(member),
            "avatar": str(member.display_avatar.url),
            "roblox_id": roblox_id,
            "roblox_username": username,
            "ts": time.time(),
        }
        _record_portal_login(discord_id, str(member))
        return redirect(f"/dashboard?rt={_up.quote(portal_token)}")
    separator = "&" if "?" in next_path else "?"
    return redirect(f"{next_path}{separator}rt={_up.quote(customer_token)}")


@app.route('/auth/discord')
def auth_discord():
    return jsonify({"error": "Staff sign-in now uses Roblox through the NexTech Hub."}), 410
    if not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET:
        return "Discord OAuth not configured. Ask the developer to set DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET.", 503
    state = secrets.token_urlsafe(16)
    _discord_oauth_states[state] = time.time()
    try:
        return redirect(_discord_oauth_url("/auth/discord/callback", state))
    except RuntimeError as exc:
        return str(exc), 503


@app.route('/auth/discord/callback')
def auth_discord_callback():
    return jsonify({"error": "Staff sign-in now uses Roblox through the NexTech Hub."}), 410
    import urllib.request as _ur, urllib.parse as _up
    code = request.args.get("code", "")
    state = request.args.get("state", "")
    error = request.args.get("error", "")
    if error:
        return redirect(f"/staff?discord_error={_up.quote(error)}")
    if state not in _discord_oauth_states or (time.time() - _discord_oauth_states.pop(state, 0)) > 300:
        return redirect("/dashboard?error=invalid_state")
    if not code:
        return redirect("/dashboard?error=no_code")
    base = _get_base_url()
    if not base:
        return redirect("/dashboard?error=base_url_not_configured")
    redirect_uri = f"{base}/auth/discord/callback"
    # Exchange code for access token
    payload = _up.urlencode({
        "client_id": DISCORD_CLIENT_ID, "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
    }).encode()
    req = _ur.Request("https://discord.com/api/oauth2/token", data=payload,
                      headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with _ur.urlopen(req, timeout=10) as r:
            tok = json.loads(r.read())
    except Exception:
        return redirect("/dashboard?error=token_exchange_failed")
    access_token = tok.get("access_token", "")
    if not access_token:
        return redirect("/dashboard?error=no_access_token")
    # Get Discord user info
    req2 = _ur.Request("https://discord.com/api/users/@me",
                       headers={"Authorization": f"Bearer {access_token}"})
    try:
        with _ur.urlopen(req2, timeout=10) as r:
            user = json.loads(r.read())
    except Exception:
        return redirect("/dashboard?error=user_fetch_failed")
    discord_id = user.get("id", "")
    discord_username = user.get("username", "")
    avatar_hash = user.get("avatar", "")
    discord_avatar = f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.png" if avatar_hash else ""
    # Verify guild membership + staff role via bot cache
    guild = client.get_guild(int(NEXTECH_GUILD_ID))
    if guild is None:
        return redirect("/dashboard?error=bot_not_in_guild")
    member = guild.get_member(int(discord_id)) if discord_id else None
    if member is None:
        return redirect("/dashboard?error=not_in_server")
    staff_role_ids = {str(NEXTECH_STAFF_ROLE_ID), str(AGENT_STAFF_ROLE_ID)}
    if not any(str(r.id) in staff_role_ids for r in member.roles):
        return redirect("/dashboard?error=no_staff_role")
    # Store pending auth — step 2 will complete with Roblox + staff key
    temp = secrets.token_urlsafe(24)
    _discord_pending_auths[temp] = {
        "discord_id": discord_id, "discord_username": discord_username,
        "discord_avatar": discord_avatar, "ts": time.time(),
    }
    return redirect(f"/staff?discord_step2={temp}&duser={_up.quote(discord_username)}")


@app.route('/auth/discord/portal')
def auth_discord_portal():
    if not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET:
        return redirect("/dashboard?error=oauth_not_configured")
    state = secrets.token_urlsafe(16)
    _portal_oauth_states[state] = time.time()
    try:
        return redirect(_discord_oauth_url("/auth/discord/portal/callback", state))
    except RuntimeError:
        return redirect("/dashboard?error=base_url_not_configured")


@app.route('/auth/discord/portal/callback')
def auth_discord_portal_callback():
    import urllib.request as _ur, urllib.parse as _up
    code = request.args.get("code", "")
    state = request.args.get("state", "")
    error = request.args.get("error", "")
    if error:
        return redirect(f"/portal?error={_up.quote(error)}")
    if state not in _portal_oauth_states or (time.time() - _portal_oauth_states.pop(state, 0)) > 300:
        return redirect("/dashboard?error=invalid_state")
    if not code:
        return redirect("/dashboard?error=no_code")
    base = _get_base_url()
    if not base:
        return redirect("/dashboard?error=base_url_not_configured")
    redirect_uri = f"{base}/auth/discord/portal/callback"
    payload = _up.urlencode({
        "client_id": DISCORD_CLIENT_ID, "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
    }).encode()
    req = _ur.Request("https://discord.com/api/oauth2/token", data=payload,
                      headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with _ur.urlopen(req, timeout=10) as r:
            tok = json.loads(r.read())
    except Exception:
        return redirect("/dashboard?error=token_exchange_failed")
    access_token = tok.get("access_token", "")
    if not access_token:
        return redirect("/dashboard?error=no_access_token")
    req2 = _ur.Request("https://discord.com/api/users/@me",
                       headers={"Authorization": f"Bearer {access_token}"})
    try:
        with _ur.urlopen(req2, timeout=10) as r:
            user = json.loads(r.read())
    except Exception:
        return redirect("/dashboard?error=user_fetch_failed")
    discord_id = user.get("id", "")
    discord_username = user.get("username", "")
    avatar_hash = user.get("avatar", "")
    discord_avatar = (f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.png"
                      if avatar_hash else "")
    guild = client.get_guild(int(NEXTECH_GUILD_ID))
    if guild is None:
        return redirect("/dashboard?error=bot_not_in_guild")
    member = guild.get_member(int(discord_id)) if discord_id else None
    if member is None:
        return redirect("/dashboard?error=not_in_server")
    if not any(r.id == BUYER_ROLE_ID for r in member.roles):
        return redirect("/dashboard?error=no_buyer_role")
    _chk_accts = load_portal_accounts()
    if _chk_accts.get(str(discord_id), {}).get("locked"):
        return redirect("/dashboard?error=account_locked")
    token = secrets.token_urlsafe(32)
    portal_sessions[token] = {
        "discord_id": discord_id, "username": discord_username,
        "avatar": discord_avatar, "ts": time.time(),
    }
    _record_portal_login(discord_id, discord_username)
    return redirect(f"/portal?pt={_up.quote(token)}")


# ============================================================
# PRODUCT API
# ============================================================
@app.route('/api/products')
def api_products():
    return jsonify(load_products())


@app.route('/api/faq')
def api_faq():
    return jsonify(load_faq())


@app.route('/api/portal/me')
def api_portal_me():
    token = request.headers.get("X-Portal-Token") or request.args.get("pt")
    sess = portal_sessions.get(token)
    if not sess:
        return jsonify({"error": "Not authenticated"}), 401
    discord_id = sess["discord_id"]
    accts = load_portal_accounts()
    acct = accts.get(str(discord_id), {})
    account_no = "NX-" + str(discord_id)[-6:]
    guild = client.get_guild(int(NEXTECH_GUILD_ID))
    member = guild.get_member(int(discord_id)) if guild else None
    role_ids = {role.id for role in member.roles} if member else set()
    if STAFF_OVERRIDE_ROLE_ID in role_ids:
        role_level, role_name = 4, "Developer"
    elif MOD_ROLE_ID in role_ids:
        role_level, role_name = 3, "Moderator"
    elif role_ids.intersection({int(NEXTECH_STAFF_ROLE_ID), AGENT_STAFF_ROLE_ID}):
        role_level, role_name = 2, "Staff"
    else:
        role_level, role_name = 1, "Member"
    warnings = load_warnings()
    tickets = load_tickets()
    actions = load_bot_actions()
    is_staff = role_level >= 2
    if not is_staff:
        warnings = [
            item for item in warnings
            if str(item.get("user_id")) == str(discord_id)
        ]
        tickets = {
            key: value for key, value in tickets.items()
            if str(value.get("discord_id") or value.get("user_id")) == str(discord_id)
        }
        actions = [
            item for item in actions
            if str(
                item.get("user_id")
                or item.get("discord_id")
                or item.get("target_id")
                or item.get("actor_id")
            ) == str(discord_id)
        ]
    raw_purchases = acct.get("purchases", [])
    purchases = []
    if isinstance(raw_purchases, list):
        for purchase in raw_purchases:
            if not isinstance(purchase, dict):
                continue
            purchased_at = purchase.get("ts")
            purchases.append({
                "product_id": str(purchase.get("product_id") or ""),
                "product_name": str(purchase.get("product_name") or "NexTech product"),
                "ts": purchased_at
                if isinstance(purchased_at, (int, float))
                and math.isfinite(purchased_at)
                else None,
            })
    return jsonify({
        "discord_id": discord_id,
        "username": sess["username"],
        "avatar": sess["avatar"],
        "account_no": account_no,
        "role_name": role_name,
        "role_level": role_level,
        "is_staff": is_staff,
        "roblox_id": sess.get("roblox_id", ""),
        "roblox_username": sess.get("roblox_username") or acct.get("roblox_username", ""),
        "credits": acct.get("credits", 0),
        "first_login": acct.get("first_login"),
        "last_login": acct.get("last_login"),
        "purchases": purchases,
        "warnings": warnings[-500:][::-1],
        "tickets": tickets,
        "actions": actions[-500:][::-1],
    })


@app.route('/api/portal/logout', methods=['POST'])
def api_portal_logout():
    token = request.headers.get("X-Portal-Token")
    if token:
        portal_sessions.pop(token, None)
    return jsonify({"ok": True})


@app.route('/api/tickets/lookup')
def api_tickets_lookup():
    username = (request.args.get("username") or "").strip().lower()
    if not username:
        return jsonify({"error": "username required"}), 400
    data = load_tickets()
    matches = [
        t for t in data.values()
        if (t.get("roblox_username") or "").strip().lower() == username
    ]
    matches.sort(key=lambda x: x.get("opened_at", 0), reverse=True)
    safe = []
    for t in matches:
        safe.append({
            "case_id": t.get("case_id"),
            "case_type": t.get("case_type"),
            "reason": t.get("reason"),
            "confirmed_type": t.get("confirmed_type"),
            "roblox_username": t.get("roblox_username"),
            "discord_id": t.get("discord_id"),
            "opened_at": t.get("opened_at"),
            "opened_date": t.get("opened_date"),
            "status": t.get("status", "open"),
        })
    return jsonify({"tickets": safe})


@app.route('/api/tickets/messages/<case_id>')
def api_tickets_messages(case_id):
    username = (request.args.get("username") or "").strip().lower()
    if not username:
        return jsonify({"error": "username required"}), 400
    data = load_tickets()
    ticket = data.get(case_id)
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404
    if (ticket.get("roblox_username") or "").strip().lower() != username:
        return jsonify({"error": "Access denied"}), 403
    if ticket.get("status") == "closed":
        return jsonify({"messages": ticket.get("messages", []), "status": "closed"})
    channel_id = ticket.get("channel_id")
    if not channel_id or not client or not client.loop or client.loop.is_closed():
        return jsonify({"messages": ticket.get("messages", []), "status": ticket.get("status", "open")})
    async def fetch_msgs():
        ch = client.get_channel(int(channel_id))
        if not ch:
            return []
        msgs = []
        async for m in ch.history(limit=200):
            if m.content or m.embeds:
                msgs.append({
                    "author": m.author.display_name,
                    "author_id": str(m.author.id),
                    "is_bot": m.author.bot,
                    "content": m.content or (m.embeds[0].description if m.embeds and m.embeds[0].description else ""),
                    "ts": m.created_at.timestamp(),
                    "ts_str": m.created_at.strftime("%Y-%m-%d %H:%M UTC"),
                })
        msgs.reverse()
        return msgs
    future = asyncio.run_coroutine_threadsafe(fetch_msgs(), client.loop)
    try:
        msgs = future.result(timeout=8)
    except Exception:
        msgs = ticket.get("messages", [])
    return jsonify({"messages": msgs, "status": ticket.get("status", "open")})


@app.route('/api/portal/my-tickets')
def api_portal_my_tickets():
    token = request.headers.get("X-Portal-Token") or request.args.get("pt")
    sess = portal_sessions.get(token)
    if not sess:
        return jsonify({"error": "Not authenticated"}), 401
    discord_id = str(sess["discord_id"])
    data = load_tickets()
    user_tickets = [t for t in data.values() if t.get("discord_id") == discord_id]
    user_tickets.sort(key=lambda x: x.get("opened_at", 0), reverse=True)
    return jsonify({"tickets": user_tickets})


@app.route('/api/portal/ticket/<case_id>/messages')
def api_portal_ticket_messages(case_id):
    token = request.headers.get("X-Portal-Token") or request.args.get("pt")
    sess = portal_sessions.get(token)
    if not sess:
        return jsonify({"error": "Not authenticated"}), 401
    discord_id = str(sess["discord_id"])
    data = load_tickets()
    ticket = data.get(case_id)
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404
    if ticket.get("discord_id") != discord_id:
        return jsonify({"error": "Access denied"}), 403
    if ticket.get("status") == "closed":
        return jsonify({"messages": ticket.get("messages", []), "status": "closed"})
    channel_id = ticket.get("channel_id")
    if not channel_id or not client or not client.loop or client.loop.is_closed():
        return jsonify({"messages": ticket.get("messages", []), "status": ticket.get("status", "open")})
    async def fetch_msgs():
        ch = client.get_channel(int(channel_id))
        if not ch:
            return []
        msgs = []
        async for m in ch.history(limit=200):
            if m.content or m.embeds:
                msgs.append({
                    "author": m.author.display_name,
                    "author_id": str(m.author.id),
                    "is_bot": m.author.bot,
                    "content": m.content or (m.embeds[0].description if m.embeds and m.embeds[0].description else ""),
                    "ts": m.created_at.timestamp(),
                    "ts_str": m.created_at.strftime("%Y-%m-%d %H:%M UTC"),
                })
        msgs.reverse()
        return msgs
    future = asyncio.run_coroutine_threadsafe(fetch_msgs(), client.loop)
    try:
        msgs = future.result(timeout=8)
    except Exception:
        msgs = ticket.get("messages", [])
    return jsonify({"messages": msgs, "status": ticket.get("status", "open")})


@app.route('/api/portal/checkout', methods=['POST'])
def api_portal_checkout():
    body = request.get_json(silent=True) or {}
    token = body.get("token") or request.headers.get("X-Portal-Token")
    sess = portal_sessions.get(token)
    if not sess:
        return jsonify({"error": "Not authenticated"}), 401
    product_id = (body.get("product_id") or "").strip()
    products = load_products()
    product = next((p for p in products if str(p.get("id", "")).lower() == product_id.lower()), None)
    if not product:
        return jsonify({"error": "Product not found"}), 404
    discord_id = sess["discord_id"]
    if client and client.loop and not client.loop.is_closed():
        asyncio.run_coroutine_threadsafe(
            _dm_product_delivery(discord_id, product), client.loop
        )
    return jsonify({"ok": True, "message": "Purchase confirmed! Check your Discord DMs for your files."})


@app.route('/api/portal/verify-gamepass', methods=['POST'])
def api_portal_verify_gamepass():
    body = request.get_json(silent=True) or {}
    token = body.get("token") or request.headers.get("X-Portal-Token")
    sess = portal_sessions.get(token)
    if not sess:
        return jsonify({"error": "Not authenticated"}), 401
    product_id = (body.get("product_id") or "").strip()
    roblox_username = (body.get("roblox_username") or "").strip()
    if not roblox_username:
        return jsonify({"error": "Please enter your Roblox username."}), 400
    products = load_products()
    product = next((p for p in products if str(p.get("id", "")).lower() == product_id.lower()), None)
    if not product:
        return jsonify({"error": "Product not found"}), 404
    gamepass_id = str(product.get("gamepass_id", "")).strip()
    if not gamepass_id:
        return jsonify({"error": "This product has no Roblox Gamepass configured."}), 400
    try:
        r1 = http_requests.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [roblox_username], "excludeBannedUsers": False},
            timeout=8)
        data1 = r1.json()
        users_list = data1.get("data", [])
        if not users_list:
            return jsonify({"error": "Roblox username not found. Check the spelling and try again."}), 404
        roblox_user_id = users_list[0]["id"]
    except Exception:
        return jsonify({"error": "Could not reach Roblox API. Please try again."}), 503
    try:
        r2 = http_requests.get(
            f"https://inventory.roblox.com/v1/users/{roblox_user_id}/items/GamePass/{gamepass_id}",
            timeout=8)
        if r2.status_code == 200:
            items = r2.json().get("data", [])
            if items:
                discord_id = sess["discord_id"]
                _record_roblox_username(discord_id, roblox_username)
                if client and client.loop and not client.loop.is_closed():
                    asyncio.run_coroutine_threadsafe(
                        _dm_product_delivery(discord_id, product), client.loop)
                return jsonify({"ok": True})
            else:
                return jsonify({"error": "Gamepass not found in your Roblox inventory. Purchase it first, then verify."}), 403
        elif r2.status_code == 403:
            return jsonify({"error": "Your Roblox inventory is set to private. Make it public in Roblox settings and try again."}), 403
        else:
            return jsonify({"error": f"Roblox API returned an unexpected error ({r2.status_code}). Try again later."}), 503
    except Exception:
        return jsonify({"error": "Could not reach Roblox API. Please try again."}), 503


@app.route('/api/portal/resend', methods=['POST'])
def api_portal_resend():
    import time, uuid as _uuid
    body = request.get_json(silent=True) or {}
    token = body.get("token") or request.headers.get("X-Portal-Token")
    sess = portal_sessions.get(token)
    if not sess:
        return jsonify({"error": "Not authenticated"}), 401
    product_id = (body.get("product_id") or "").strip()
    if not product_id:
        return jsonify({"error": "No product specified."}), 400
    discord_id = sess["discord_id"]
    accts = load_portal_accounts()
    acct = accts.get(str(discord_id), {})
    purchases = acct.get("purchases", [])
    purchase = next((p for p in purchases if str(p.get("product_id", "")).lower() == product_id.lower()), None)
    if not purchase:
        return jsonify({"error": "You have not purchased this product."}), 403
    products = load_products()
    product = next((p for p in products if str(p.get("id", "")).lower() == product_id.lower()), None)
    file_url = ""
    if product:
        file_url = product.get("file_url") or product.get("file_path") or ""
    if not file_url:
        return jsonify({"error": "This product has no downloadable file attached yet. Contact staff."}), 404
    dl_token = _uuid.uuid4().hex
    expires_at = time.time() + 86400
    product_name = purchase.get("product_name") or (product.get("name") if product else product_id)
    download_tokens[dl_token] = {
        "discord_id": discord_id,
        "product_id": product_id,
        "product_name": product_name,
        "file_url": file_url,
        "expires_at": expires_at,
    }
    base = _get_base_url()
    dl_url = f"{base}/dl/{dl_token}"
    if client and client.loop and not client.loop.is_closed():
        asyncio.run_coroutine_threadsafe(
            _dm_download_link(discord_id, product_name, dl_url, expires_at), client.loop)
    return jsonify({"ok": True})


@app.route('/api/support/status')
def api_support_status():
    return jsonify({
        "staffOnline": len(staff_online) > 0,
        "staffCount": len(staff_online),
        "discordUrl": NEXTECH_DISCORD_INVITE,
    })


@app.route('/api/ai/chat', methods=['POST'])
def api_ai_chat():
    body = request.get_json(silent=True) or {}
    msg = (body.get("message") or "").strip().lower()
    if not msg:
        return jsonify({"reply": "Hi! I'm Techno, NexTech's product assistant. Tell me what you need — for example: 'I want a security script' or 'show me admin tools' — and I'll point you at the right product.", "products": []})
    products = load_products()
    greetings = ("hi", "hello", "hey", "yo", "sup", "hola")
    if msg in greetings or any(msg.startswith(g + " ") for g in greetings):
        return jsonify({"reply": "Hey! I'm Techno. What kind of product are you looking for? I can search by name, type, or what it does.", "products": []})
    thanks = ("thanks", "thank you", "ty", "thx")
    if any(t in msg for t in thanks):
        return jsonify({"reply": "Anytime! If you need a human, switch to the Live Staff tab and our team will help.", "products": []})
    if not products:
        return jsonify({"reply": "We don't have any products listed right now. Check back soon, or hop into our Discord for help.", "products": []})
    tokens = [t for t in __import__("re").findall(r"[a-z0-9]+", msg) if len(t) > 2]
    scored = []
    for p in products:
        hay = " ".join(str(p.get(k, "")) for k in ("name", "title", "description", "type", "category", "tags")).lower()
        score = sum(1 for t in tokens if t in hay)
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    matches = [p for _, p in scored[:3]]
    if matches:
        names = ", ".join(p.get("name") or p.get("title") or "Untitled" for p in matches)
        reply = f"I found {len(matches)} product(s) that might fit: {names}. Want more detail on any of them?"
    else:
        reply = "I couldn't match that to any product. Try describing what you want it to do, or switch to the Live Staff tab for a person to help."
    return jsonify({"reply": reply, "products": matches})


@app.route('/api/staff/logs')
def api_staff_logs():
    token = request.headers.get("X-Staff-Token") or request.args.get("token")
    if not token or not _get_authorized_staff_session(token):
        return jsonify({"error": "unauthorized"}), 401
    limit = min(int(request.args.get("limit", 100)), MAX_LOG_ENTRIES)
    out = []
    for e in action_log[:limit]:
        out.append({
            "timestamp": e["timestamp"].isoformat() if hasattr(e["timestamp"], "isoformat") else str(e["timestamp"]),
            "user_id": e.get("user_id"),
            "username": e.get("username"),
            "action_type": e.get("action_type"),
            "command_name": e.get("command_name"),
            "guild_name": e.get("guild_name"),
            "channel_name": e.get("channel_name"),
            "details": e.get("details"),
        })
    return jsonify({"logs": out, "total": len(action_log)})


@app.route('/api/staff/email', methods=['POST'])
def api_staff_email():
    token = request.headers.get("X-Staff-Token") or ""
    if not _get_authorized_staff_session(token):
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    session_id = (body.get("sessionId") or "").strip()
    to_email = (body.get("email") or "").strip()
    message = (body.get("message") or "").strip()
    if not session_id or not to_email or not message:
        return jsonify({"error": "sessionId, email and message are required."}), 400
    s = chat_sessions.get(session_id)
    if not s:
        return jsonify({"error": "Ticket not found."}), 404
    # Create a reply token valid for 7 days
    reply_token = secrets.token_urlsafe(32)
    email_reply_tokens[reply_token] = {
        "session_id": session_id, "to_email": to_email, "ts": time.time()
    }
    base = _get_base_url()
    reply_url = f"{base}/reply/{reply_token}" if base else f"/reply/{reply_token}"
    staff_info = staff_tokens[token]
    customer_name = s.get("robloxDisplay") or s.get("robloxUsername") or s.get("name") or "Customer"
    html_body = f"""
<div style="font-family:Verdana,Geneva,sans-serif;max-width:600px;margin:0 auto;background:#0b0d14;color:#e7ecf6;border-radius:14px;overflow:hidden;border:1px solid #222633">
  <div style="background:linear-gradient(90deg,#1a4fa0,#3b82f6);padding:24px 28px">
    <div style="font-size:20px;font-weight:900;color:#fff;font-family:Verdana,Geneva,sans-serif">NexTech Interactive Development LLC</div>
    <div style="font-size:12px;color:#dbeafe;margin-top:4px;font-family:Verdana,Geneva,sans-serif">Support message from {staff_info['name']}</div>
  </div>
  <div style="padding:28px;font-family:Verdana,Geneva,sans-serif">
    <p style="margin:0 0 18px;font-size:14px;color:#e7ecf6">Hi <b>{customer_name}</b>,</p>
    <div style="background:#11141d;border-left:4px solid #f4a830;border-radius:8px;padding:16px 18px;font-size:13px;line-height:1.6;color:#cbd5e1;margin-bottom:22px;white-space:pre-wrap;font-family:Verdana,Geneva,sans-serif">{message}</div>
    <a href="{reply_url}" style="display:inline-block;background:#f4a830;color:#000;font-weight:800;padding:13px 26px;border-radius:10px;text-decoration:none;font-size:13px;font-family:Verdana,Geneva,sans-serif">&#8617; Reply to this message</a>
    <p style="margin:22px 0 0;font-size:10.5px;color:#5b6373;font-family:Verdana,Geneva,sans-serif">This link lets you reply directly to the NexTech support team. It expires in 7 days. Do not reply directly to this email.</p>
  </div>
</div>"""
    text_body = (
        f"Hi {customer_name},\n\n{message}\n\n"
        f"Reply here: {reply_url}\n\n"
        f"-- {staff_info['name']} (NexTech Support)\n\n"
        "Do not reply directly to this email -- use the link above."
    )
    subject = f"[NexTech Support] Message from {staff_info['name']}"
    ok, err = send_email(to_email, subject, html_body, text_body)
    if not ok:
        return jsonify({"error": f"Failed to send email: {err}"}), 500
    # Log it in the session
    log_msg = {"id": str(uuid.uuid4()), "from": "system",
               "text": f"📧 Email sent to {to_email} by {staff_info['name']}.",
               "ts": time.time() * 1000}
    s["messages"].append(log_msg)
    socketio.emit("message", {"sessionId": session_id, "message": log_msg},
                  room=f"session:{session_id}")
    _broadcast_sessions()
    return jsonify({"ok": True})


@app.route('/api/email-reply/messages')
def api_email_reply_messages():
    token = (request.args.get("token") or "").strip()
    info = email_reply_tokens.get(token)
    if not info or (time.time() - info["ts"]) > _EMAIL_TOKEN_TTL:
        return jsonify({"error": "expired"}), 410
    s = chat_sessions.get(info["session_id"])
    if not s:
        return jsonify({"messages": [], "status": "closed"})
    return jsonify({"messages": s.get("messages", []), "status": s.get("status", "open")})


@app.route('/api/email-reply', methods=['POST'])
def api_email_reply():
    body = request.get_json(silent=True) or {}
    token = (body.get("token") or "").strip()
    message = (body.get("message") or "").strip()
    if not token or not message:
        return jsonify({"error": "token and message required."}), 400
    info = email_reply_tokens.get(token)
    if not info or (time.time() - info["ts"]) > _EMAIL_TOKEN_TTL:
        return jsonify({"error": "This reply link has expired."}), 410
    s = chat_sessions.get(info["session_id"])
    if not s:
        return jsonify({"error": "Ticket not found."}), 404
    display = s.get("robloxDisplay") or s.get("robloxUsername") or s.get("name") or "Customer"
    msg = {"id": str(uuid.uuid4()), "from": "customer",
           "text": f"📧 [Email reply from {display}]: {message}",
           "ts": time.time() * 1000}
    s["messages"].append(msg)
    socketio.emit("message", {"sessionId": s["id"], "message": msg},
                  room=f"session:{s['id']}")
    socketio.emit("session:update", _public_session(s), room="staff")
    _broadcast_sessions()
    return jsonify({"ok": True})


# ============================================================
# LIVE CHAT SOCKET HANDLERS
# ============================================================
@socketio.on("customer:start")
def on_customer_start(data):
    sid = request.sid
    name = (data.get("name") or "Guest")[:40]
    roblox = (data.get("roblox") or "")[:200]
    cuid = (data.get("cuid") or "")[:64]
    # Dedup: if an open session already exists for this client UID, rejoin it
    if cuid:
        for existing in list(chat_sessions.values()):
            if existing.get("cuid") == cuid and existing["status"] != "closed":
                existing["customer_sid"] = sid
                join_room(f"session:{existing['id']}")
                emit("session:started", {
                    "sessionId": existing["id"],
                    "staffOnline": bool(staff_online),
                    "discordUrl": NEXTECH_DISCORD_INVITE,
                })
                for m in existing["messages"]:
                    socketio.emit("message", {"sessionId": existing["id"], "message": m},
                                  room=f"session:{existing['id']}")
                return
    profile = fetch_roblox_profile(roblox)
    rb_username = profile["username"] if profile else None
    rb_display = profile["displayName"] if profile else None
    rb_avatar = profile["avatar"] if profile else None
    if profile:
        name = rb_display or rb_username or name
        roblox = profile["url"]
    s = {
        "id": str(uuid.uuid4()), "cuid": cuid, "customer_sid": sid, "name": name, "roblox": roblox,
        "robloxUsername": rb_username, "robloxDisplay": rb_display,
        "robloxAvatar": rb_avatar,
        "staff_sid": None, "staffName": None, "staffAvatar": None, "messages": [],
        "created": time.time(), "status": "waiting",
    }
    chat_sessions[s["id"]] = s
    join_room(f"session:{s['id']}")
    if staff_online:
        sys_text = (
            f"Connected as {rb_display or name} ({rb_username or 'unknown'}).\n"
            f"Roblox profile: {roblox}\nA staff member will join shortly."
        )
    else:
        sys_text = (
            f"Connected as {rb_display or name}.\n"
            f"No staff are currently online. Please join our Discord and our team will help you there.\n"
            f"Discord: {NEXTECH_DISCORD_INVITE_LINK}"
        )
    msg = {"id": str(uuid.uuid4()), "from": "system", "text": sys_text, "ts": time.time() * 1000}
    s["messages"].append(msg)
    emit("session:started", {
        "sessionId": s["id"], "staffOnline": bool(staff_online),
        "discordUrl": NEXTECH_DISCORD_INVITE,
    })
    socketio.emit("message", {"sessionId": s["id"], "message": msg}, room=f"session:{s['id']}")
    _broadcast_sessions()


@socketio.on("customer:message")
def on_customer_message(data):
    s = chat_sessions.get(data.get("sessionId"))
    if not s or s["customer_sid"] != request.sid or s["status"] == "closed":
        return
    msg = {"id": str(uuid.uuid4()), "from": "customer",
           "text": str(data.get("text", ""))[:2000], "ts": time.time() * 1000}
    s["messages"].append(msg)
    socketio.emit("message", {"sessionId": s["id"], "message": msg}, room=f"session:{s['id']}")
    socketio.emit("session:update", _public_session(s), room="staff")


@socketio.on("staff:auth")
def on_staff_auth(data):
    info = _get_authorized_staff_session(data.get("token") or "")
    if not info:
        emit("staff:auth:result", {"ok": False}); return
    staff_online[request.sid] = info
    join_room("staff")
    emit("staff:auth:result", {"ok": True, "name": info["name"], "avatar": info.get("avatar")})
    _broadcast_presence()
    _broadcast_sessions()


@socketio.on("staff:claim")
def on_staff_claim(data):
    staff = _get_authorized_staff_socket(request.sid)
    s = chat_sessions.get(data.get("sessionId"))
    if not (staff and s) or s["status"] == "closed":
        return
    reclaim = s["status"] == "active" and s.get("staffName") == staff["name"]
    s["staff_sid"] = request.sid
    s["staffName"] = staff["name"]
    s["staffAvatar"] = staff.get("avatar")
    s["status"] = "active"
    join_room(f"session:{s['id']}")
    if not reclaim:
        msg = {"id": str(uuid.uuid4()), "from": "system",
               "text": f"{staff['name']} joined the chat.", "ts": time.time() * 1000}
        s["messages"].append(msg)
        socketio.emit("message", {"sessionId": s["id"], "message": msg}, room=f"session:{s['id']}")
        socketio.emit("session:claimed",
                      {"sessionId": s["id"], "staffName": staff["name"],
                       "staffAvatar": staff.get("avatar")},
                      room=f"session:{s['id']}")
        _broadcast_sessions()


@socketio.on("staff:message")
def on_staff_message(data):
    staff = _get_authorized_staff_socket(request.sid)
    s = chat_sessions.get(data.get("sessionId"))
    if not (staff and s) or s["staff_sid"] != request.sid:
        return
    msg = {"id": str(uuid.uuid4()), "from": "staff",
           "text": str(data.get("text", ""))[:2000],
           "staffName": staff["name"],
           "staffAvatar": staff.get("avatar"),
           "ts": time.time() * 1000}
    s["messages"].append(msg)
    socketio.emit("message", {"sessionId": s["id"], "message": msg}, room=f"session:{s['id']}")
    socketio.emit("session:update", _public_session(s), room="staff")


@socketio.on("staff:history")
def on_staff_history(data):
    if not _get_authorized_staff_socket(request.sid):
        emit("staff:history:result", {"messages": []})
        return
    s = chat_sessions.get(data.get("sessionId"))
    emit("staff:history:result", {"messages": s["messages"] if s else []})


@socketio.on("staff:close")
def on_staff_close(data):
    staff = _get_authorized_staff_socket(request.sid)
    s = chat_sessions.get(data.get("sessionId"))
    if not (staff and s) or s["status"] == "closed":
        return
    s["status"] = "closed"
    msg = {"id": str(uuid.uuid4()), "from": "system",
           "text": f"This ticket was closed by {staff['name']}.",
           "ts": time.time() * 1000}
    s["messages"].append(msg)
    socketio.emit("message", {"sessionId": s["id"], "message": msg},
                  room=f"session:{s['id']}")
    socketio.emit("session:closed", {"sessionId": s["id"]},
                  room=f"session:{s['id']}")
    _broadcast_sessions()


@socketio.on("disconnect")
def on_socket_disconnect():
    sid = request.sid
    if sid in staff_online:
        del staff_online[sid]
        for s in chat_sessions.values():
            if s["staff_sid"] == sid and s["status"] == "active":
                s["staff_sid"] = None
                msg = {"id": str(uuid.uuid4()), "from": "system",
                       "text": f"{s['staffName']} disconnected. Waiting for another agent…",
                       "ts": time.time() * 1000}
                s["staffName"] = None
                s["status"] = "waiting"
                s["messages"].append(msg)
                socketio.emit("message", {"sessionId": s["id"], "message": msg},
                              room=f"session:{s['id']}")
        _broadcast_presence(); _broadcast_sessions()
    else:
        for s in chat_sessions.values():
            if s["customer_sid"] == sid and s["status"] != "closed":
                s["status"] = "closed"
        _broadcast_sessions()


# ============================================================
# WEB PAGES
# ============================================================
@app.route('/api/customer/register', methods=['POST'])
def api_customer_register():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()[:40]
    email = (body.get("email") or "").strip().lower()[:200]
    password = (body.get("password") or "").strip()
    if not username or not email or not password:
        return jsonify({"error": "All fields are required."}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters."}), 400
    if "@" not in email or "." not in email.split("@")[-1]:
        return jsonify({"error": "Please enter a valid email address."}), 400
    customers = _load_customers()
    if email in customers:
        return jsonify({"error": "An account with this email already exists."}), 409
    customers[email] = {
        "username": username,
        "password": _hash_password(password),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "email_verified": False,
    }
    verification_token = _set_customer_verification_token(customers[email])
    _save_customers(customers)
    sent, error = _send_customer_verification_email(
        email, username, verification_token
    )
    if not sent:
        app.logger.error("Could not send email verification to %s: %s", email, error)
        return jsonify({
            "error": "Your account was created, but the verification email could not be sent. Check your email settings or try resending the link.",
            "account_created": True,
            "verification_required": True,
            "email": email,
        }), 503
    return jsonify({"verification_required": True, "email": email}), 202


_CUSTOMER_EMAIL_VERIFICATION_TTL = 86400


def _set_customer_verification_token(account):
    token = secrets.token_urlsafe(32)
    account["email_verification_token_hash"] = hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()
    account["email_verification_expires_at"] = (
        time.time() + _CUSTOMER_EMAIL_VERIFICATION_TTL
    )
    return token


def _send_customer_verification_email(email, username, token):
    import html
    from urllib.parse import urlencode

    base_url = _get_base_url()
    if not base_url:
        return False, "SITE_BASE_URL is not configured."
    verification_url = (
        f"{base_url}/auth/verify-email?"
        f"{urlencode({'email': email, 'token': token})}"
    )
    safe_username = html.escape(username)
    safe_url = html.escape(verification_url, quote=True)
    html_body = (
        f"<p>Hello {safe_username},</p>"
        "<p>Confirm your email address to finish creating your NexTech account.</p>"
        f'<p><a href="{safe_url}">Verify my email</a></p>'
        "<p>This link expires in 24 hours and can only be used once. "
        "If you did not create this account, you can ignore this email.</p>"
    )
    text_body = (
        f"Hello {username},\n\n"
        "Confirm your email address to finish creating your NexTech account:\n"
        f"{verification_url}\n\n"
        "This link expires in 24 hours and can only be used once. "
        "If you did not create this account, you can ignore this email."
    )
    return send_email(
        email,
        "Verify your NexTech email address",
        html_body,
        text_body,
    )


@app.route('/api/customer/resend-verification', methods=['POST'])
def api_customer_resend_verification():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()[:200]
    if not email:
        return jsonify({"error": "Enter your email address."}), 400
    customers = _load_customers()
    account = customers.get(email)
    if account and not account.get("email_verified", True):
        token = _set_customer_verification_token(account)
        _save_customers(customers)
        sent, error = _send_customer_verification_email(
            email, account["username"], token
        )
        if not sent:
            app.logger.error(
                "Could not resend email verification to %s: %s", email, error
            )
            return jsonify({
                "error": "We could not send the verification email. Please try again later or contact support."
            }), 503
    return jsonify({
        "message": "If that email belongs to an account that still needs verification, a new link has been sent."
    })


@app.route('/auth/verify-email', methods=['GET', 'POST'])
def auth_verify_customer_email():
    email = (request.values.get("email") or "").strip().lower()[:200]
    token = (request.values.get("token") or "")[:128]
    customers = _load_customers()
    account = customers.get(email)
    candidate_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    stored_hash = account.get("email_verification_token_hash", "") if account else ""
    if not isinstance(stored_hash, str):
        stored_hash = ""
    expires_at = account.get("email_verification_expires_at", 0) if account else 0
    if (
        not account
        or account.get("email_verified", True)
        or not token
        or not secrets.compare_digest(stored_hash, candidate_hash)
        or not isinstance(expires_at, (int, float))
        or not expires_at > time.time()
    ):
        return redirect("/login?email_verification=invalid")
    if request.method == "GET":
        response = make_response(render_template_string(
            """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verify your email · NexTech</title>
<style>
*{box-sizing:border-box}body{min-height:100vh;display:grid;place-items:center;margin:0;padding:20px;background:#0b0b0c;color:#f1f2f4;font:15px/1.6 system-ui,sans-serif}
main{width:min(100%,480px);padding:32px;border:1px solid #303034;border-radius:12px;background:#151516}
h1{margin:0 0 12px;font-size:26px}p{color:#b6bac3}strong{color:#f1f2f4;overflow-wrap:anywhere}
button{width:100%;min-height:44px;margin-top:18px;border:1px solid #5277a8;border-radius:8px;background:#29415f;color:white;font:inherit;font-weight:750;cursor:pointer}
a{display:block;margin-top:16px;color:#a9cbff;text-align:center}
</style></head><body><main><h1>Confirm your email</h1>
<p>Confirm this email address to activate your NexTech account:</p>
<p><strong>{{ email }}</strong></p>
<form method="post" action="/auth/verify-email">
<input type="hidden" name="email" value="{{ email }}">
<input type="hidden" name="token" value="{{ token }}">
<button type="submit">Verify email address</button>
</form><a href="/login">Cancel and return to log in</a></main></body></html>""",
            email=email,
            token=token,
        ))
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    account["email_verified"] = True
    account["email_verified_at"] = time.strftime("%Y-%m-%d %H:%M:%S UTC")
    account.pop("email_verification_token_hash", None)
    account.pop("email_verification_expires_at", None)
    _save_customers(customers)
    return redirect("/login?email_verification=success")


@app.route('/password/forgot')
def customer_password_forgot_page():
    return Response(
        """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark">
<title>Reset password · NexTech</title><style>
*{box-sizing:border-box}body{min-height:100vh;display:grid;place-items:center;margin:0;padding:20px;background:#0b0b0c;color:#f1f2f4;font:14px/1.6 system-ui,sans-serif}
main{width:min(100%,440px);padding:30px;border:1px solid #303034;border-radius:12px;background:#151516}
h1{margin:0 0 8px;font-size:26px}p{color:#aeb4bf;font-size:12px}
label{display:block;margin:18px 0 6px;font-size:11px;font-weight:750}input{width:100%;min-height:42px;padding:10px 12px;border:1px solid #35353a;border-radius:7px;background:#101011;color:#fff;font:inherit}
button{width:100%;min-height:42px;margin-top:14px;border:1px solid #5277a8;border-radius:8px;background:#29415f;color:white;font:inherit;font-weight:800;cursor:pointer}
#status{min-height:18px;color:#cbd5e1;font-size:11px}a{display:block;margin-top:16px;color:#a9cbff;text-align:center}
</style></head><body><main><h1>Reset your password</h1>
<p>Enter your account email. If an eligible account exists, we’ll email a one-time reset link.</p>
<form id="request-form"><label for="email">Email address</label><input id="email" name="email" type="email" autocomplete="email" maxlength="200" required>
<div id="status" role="status" aria-live="polite"></div><button type="submit">Send reset link</button></form>
<a href="/login">Return to log in</a></main><script>
document.getElementById("request-form").addEventListener("submit",async event=>{event.preventDefault();const status=document.getElementById("status"),button=event.currentTarget.querySelector("button");button.disabled=true;status.textContent="Sending…";try{const response=await fetch("/api/customer/request-password-reset",{method:"POST",headers:{"Content-Type":"application/json","Accept":"application/json"},body:JSON.stringify({email:document.getElementById("email").value})});const data=await response.json();if(!response.ok)throw new Error(data.error||"Could not request a reset.");status.textContent=data.message;}catch(error){status.textContent=error.message;}finally{button.disabled=false;}});
</script></body></html>""",
        content_type="text/html; charset=utf-8",
    )


@app.route('/password/reset')
def customer_password_reset_page():
    email = (request.args.get("email") or "").strip().lower()[:200]
    token = (request.args.get("token") or "")[:128]
    response = make_response(render_template_string(
        """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark">
<title>Choose a password · NexTech</title><style>
*{box-sizing:border-box}body{min-height:100vh;display:grid;place-items:center;margin:0;padding:20px;background:#0b0b0c;color:#f1f2f4;font:14px/1.6 system-ui,sans-serif}
main{width:min(100%,440px);padding:30px;border:1px solid #303034;border-radius:12px;background:#151516}
h1{margin:0 0 8px;font-size:26px}p{color:#aeb4bf;font-size:12px}
label{display:block;margin:16px 0 6px;font-size:11px;font-weight:750}input{width:100%;min-height:42px;padding:10px 12px;border:1px solid #35353a;border-radius:7px;background:#101011;color:#fff;font:inherit}
button{width:100%;min-height:42px;margin-top:14px;border:1px solid #5277a8;border-radius:8px;background:#29415f;color:white;font:inherit;font-weight:800;cursor:pointer}
#status{min-height:18px;color:#ffb3b8;font-size:11px}a{display:block;margin-top:16px;color:#a9cbff;text-align:center}
</style></head><body><main><h1>Choose a password</h1><p>Set a new password for <strong>{{ email }}</strong>.</p>
<form id="reset-form"><input type="hidden" id="email" value="{{ email }}"><input type="hidden" id="token" value="{{ token }}">
<label for="password">New password</label><input id="password" type="password" autocomplete="new-password" minlength="6" maxlength="128" required>
<label for="confirmation">Confirm new password</label><input id="confirmation" type="password" autocomplete="new-password" minlength="6" maxlength="128" required>
<div id="status" role="status" aria-live="polite"></div><button type="submit">Save password</button></form>
<a href="/login">Return to log in</a></main><script>
document.getElementById("reset-form").addEventListener("submit",async event=>{event.preventDefault();const status=document.getElementById("status"),button=event.currentTarget.querySelector("button"),password=document.getElementById("password").value;if(password!==document.getElementById("confirmation").value){status.textContent="The passwords do not match.";return;}button.disabled=true;status.textContent="Saving…";try{const response=await fetch("/api/customer/reset-password",{method:"POST",headers:{"Content-Type":"application/json","Accept":"application/json"},body:JSON.stringify({email:document.getElementById("email").value,token:document.getElementById("token").value,password})});const data=await response.json();if(!response.ok)throw new Error(data.error||"Could not reset your password.");location.assign("/login?password_reset=success");}catch(error){status.textContent=error.message;}finally{button.disabled=false;}});
</script></body></html>""",
        email=email,
        token=token,
    ))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.route('/api/customer/request-password-reset', methods=['POST'])
def api_customer_request_password_reset():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()[:200]
    if not email:
        return jsonify({"error": "Enter your email address."}), 400
    customers = _load_customers()
    account = customers.get(email)
    if account and not account.get("suspended", False):
        token = _set_customer_password_reset_token(account)
        _save_customers(customers)
        sent, error = _send_customer_password_reset_email(
            email, account.get("username", "NexTech member"), token
        )
        if not sent:
            app.logger.error("Could not send a password reset email: %s", error)
    return jsonify({
        "message": "If an active account uses that email, a password reset link has been sent."
    })


@app.route('/api/customer/reset-password', methods=['POST'])
def api_customer_reset_password():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()[:200]
    token = (body.get("token") or "")[:128]
    password = body.get("password") or ""
    if not email or not token or not isinstance(password, str):
        return jsonify({"error": "A valid reset link and new password are required."}), 400
    if len(password) < 6 or len(password) > 128:
        return jsonify({"error": "Passwords must be between 6 and 128 characters."}), 400
    customers = _load_customers()
    account = customers.get(email)
    candidate_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    stored_hash = account.get("password_reset_token_hash", "") if account else ""
    if not isinstance(stored_hash, str):
        stored_hash = ""
    expires_at = account.get("password_reset_expires_at", 0) if account else 0
    if (
        not account
        or account.get("suspended", False)
        or not token
        or not secrets.compare_digest(stored_hash, candidate_hash)
        or not isinstance(expires_at, (int, float))
        or not expires_at > time.time()
    ):
        return jsonify({"error": "This password reset link is invalid or expired. Request a new link."}), 400
    account["password"] = _hash_password(password)
    account["email_verified"] = True
    account.pop("password_reset_token_hash", None)
    account.pop("password_reset_expires_at", None)
    _save_customers(customers)
    for session_token, session in list(customer_tokens.items()):
        if session.get("email") == email:
            customer_tokens.pop(session_token, None)
    return jsonify({"ok": True})


@app.route('/api/customer/login', methods=['POST'])
def api_customer_login():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = (body.get("password") or "").strip()
    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400
    customers = _load_customers()
    acct = customers.get(email)
    if not acct or not _verify_password(password, acct["password"]):
        return jsonify({"error": "Invalid email or password."}), 401
    if acct.get("suspended", False):
        return jsonify({"error": "This account is suspended. Contact NexTech Support for assistance."}), 403
    if not acct.get("email_verified", True):
        return jsonify({
            "error": "Please verify your email before logging in. You can request a new verification link below.",
            "verification_required": True,
        }), 403
    token = secrets.token_hex(24)
    customer_tokens[token] = {"email": email, "username": acct["username"], "login_ts": time.time()}
    return jsonify({"token": token, "username": acct["username"]})


def _get_customer_session(token):
    if not token:
        return None
    session = customer_tokens.get(token)
    if not session:
        return None
    if time.time() - session.get("login_ts", 0) > _CUSTOMER_TOKEN_TTL:
        customer_tokens.pop(token, None)
        return None
    return session


def _dashboard_datastore_record(store, key):
    if not HUB_CLOUD_API_KEY or not HUB_UNIVERSE_ID:
        return None
    try:
        response = http_requests.get(
            _ds_base(HUB_UNIVERSE_ID),
            params={"datastoreName": store, "entryKey": str(key)},
            headers={"x-api-key": HUB_CLOUD_API_KEY},
            timeout=8,
        )
        if response.status_code == 200:
            data = response.json()
            return data if isinstance(data, dict) else None
    except Exception:
        pass
    return None


def _dashboard_link_record(roblox_id):
    return _dashboard_datastore_record(LINKED_STORE_NAME, roblox_id)


def _dashboard_discord_index_record(discord_id):
    return _dashboard_datastore_record(DISCORD_INDEX_NAME, discord_id)


def _dashboard_profile_datastore_record(store, key):
    if not HUB_CLOUD_API_KEY or not HUB_UNIVERSE_ID:
        raise RuntimeError("Hub datastore is not configured")
    response = http_requests.get(
        _ds_base(HUB_UNIVERSE_ID),
        params={"datastoreName": store, "entryKey": str(key)},
        headers={"x-api-key": HUB_CLOUD_API_KEY},
        timeout=8,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    record = response.json()
    if not isinstance(record, dict):
        raise ValueError("Hub datastore returned a malformed link record")
    return record


def _dashboard_profile_codes_file():
    # Both code creation and redemption must resolve to the same persistent file.
    # Set DASHBOARD_PROFILE_CODES_FILE in Replit Secrets if the app uses a mounted
    # persistent directory; otherwise use the project directory beside bot.py.
    configured = os.environ.get("DASHBOARD_PROFILE_CODES_FILE", "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_profile_codes.json")


def _load_dashboard_profile_codes_from_disk():
    try:
        with open(_dashboard_profile_codes_file(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save_dashboard_profile_codes_to_disk():
    path = _dashboard_profile_codes_file()
    temp_path = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(_dashboard_profile_codes, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        return True
    except OSError:
        app.logger.exception("Could not persist dashboard sign-in codes to %s", path)
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        return False


def redeem_dashboard_profile_code(code, remote_addr=None):
    now = time.time()
    remote_key = str(remote_addr or "unknown")[:128]
    with _dashboard_profile_code_lock:
        for key, attempts in list(_dashboard_profile_login_attempts.items()):
            attempts = [
                attempted_at
                for attempted_at in attempts
                if now - attempted_at < _DASHBOARD_PROFILE_CODE_ATTEMPT_WINDOW
            ]
            if attempts:
                _dashboard_profile_login_attempts[key] = attempts
            else:
                _dashboard_profile_login_attempts.pop(key, None)
        attempts = _dashboard_profile_login_attempts.setdefault(remote_key, [])
        if len(attempts) >= _DASHBOARD_PROFILE_CODE_MAX_ATTEMPTS:
            return {
                "error": "Too many code attempts. Please wait 10 minutes and try again."
            }, 429
        attempts.append(now)

        normalized_code = re.sub(r"[\s-]+", "", str(code or "")).upper()
        if not re.fullmatch(r"[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{8}", normalized_code):
            return {"error": "The one-time code is invalid or expired."}, 401
        code_hash = hashlib.sha256(normalized_code.encode("ascii")).hexdigest()
        # The Flask dashboard may be running in a separate worker/process from
        # the Discord bot. Load the shared registry so both see the same code.
        disk_codes = _load_dashboard_profile_codes_from_disk()
        # Merge the on-disk registry into memory on every redemption. This avoids
        # stale process memory hiding a code written by another worker/process.
        if isinstance(disk_codes, dict):
            _dashboard_profile_codes.update(disk_codes)
        entry = _dashboard_profile_codes.get(code_hash)
        if not entry:
            app.logger.warning(
                "Dashboard profile code not found in registry (file=%s, entries=%d)",
                _dashboard_profile_codes_file(), len(_dashboard_profile_codes)
            )
        elif now - float(entry.get("issued_at", 0)) >= _DASHBOARD_PROFILE_CODE_TTL:
            app.logger.info("Dashboard profile code expired (issued_at=%s)", entry.get("issued_at"))
        if (
            not entry
            or now - float(entry.get("issued_at", 0)) >= _DASHBOARD_PROFILE_CODE_TTL
        ):
            _dashboard_profile_codes.pop(code_hash, None)
            disk_codes.pop(code_hash, None)
            try:
                with open(_dashboard_profile_codes_file() + ".tmp", "w", encoding="utf-8") as handle:
                    json.dump(disk_codes, handle)
                os.replace(_dashboard_profile_codes_file() + ".tmp", _dashboard_profile_codes_file())
            except OSError:
                pass
            return {"error": "The one-time code is invalid or expired."}, 401
        discord_id = entry["discord_id"]
        _dashboard_profile_codes.pop(code_hash, None)
        disk_codes.pop(code_hash, None)
        try:
            with open(_dashboard_profile_codes_file() + ".tmp", "w", encoding="utf-8") as handle:
                json.dump(disk_codes, handle)
            os.replace(_dashboard_profile_codes_file() + ".tmp", _dashboard_profile_codes_file())
        except OSError:
            pass

    if not HUB_CLOUD_API_KEY or not HUB_UNIVERSE_ID:
        return {"error": "Hub sign-in is temporarily unavailable."}, 503

    discord_index = _dashboard_profile_datastore_record(
        DISCORD_INDEX_NAME,
        discord_id,
    )
    roblox_id = str(
        (discord_index or {}).get("robloxUserId")
        or (discord_index or {}).get("roblox_id")
        or ""
    )
    if not roblox_id.isascii() or not roblox_id.isdecimal():
        return {"error": "This Discord account has no active NexTech Hub link."}, 401

    linked_record = _dashboard_profile_datastore_record(LINKED_STORE_NAME, roblox_id)
    linked_discord_id = str(
        (linked_record or {}).get("discordId")
        or (linked_record or {}).get("discord_id")
        or (linked_record or {}).get("discordUserId")
        or ""
    )
    if linked_discord_id != discord_id:
        return {"error": "This Discord account has no active NexTech Hub link."}, 401

    try:
        guild = client.get_guild(int(NEXTECH_GUILD_ID))
        member = guild.get_member(int(discord_id)) if guild else None
    except (TypeError, ValueError):
        member = None
    if not member or member.bot:
        return {"error": "This Discord account must be an active NexTech server member."}, 401

    username = str(
        (linked_record or {}).get("robloxUsername")
        or (linked_record or {}).get("roblox_username")
        or f"Roblox User {roblox_id}"
    )[:120]
    token = secrets.token_urlsafe(32)
    customer_tokens[token] = {
        "provider": "profile_code",
        "discord_id": discord_id,
        "roblox_id": roblox_id,
        "username": username,
        "display_name": member.display_name,
        "avatar": str(member.display_avatar.url),
        "login_ts": now,
    }
    return {"token": token, "username": username}


def _dashboard_linked_member(roblox_id):
    linked = _dashboard_link_record(roblox_id)
    discord_id = str(
        (linked or {}).get("discordId")
        or (linked or {}).get("discord_id")
        or (linked or {}).get("discordUserId")
        or ""
    )
    try:
        guild = client.get_guild(int(NEXTECH_GUILD_ID))
        member = guild.get_member(int(discord_id)) if guild and discord_id else None
    except (TypeError, ValueError):
        member = None
    return linked, discord_id, member


def _dashboard_verified_account_link(account):
    discord_id = str(account.get("discord_id") or "").strip()
    roblox_id = str(account.get("roblox_id") or "").strip()
    if (
        not discord_id.isascii()
        or not discord_id.isdecimal()
        or not roblox_id.isascii()
        or not roblox_id.isdecimal()
    ):
        return None
    linked, linked_discord_id, member = _dashboard_linked_member(roblox_id)
    reverse_link = _dashboard_discord_index_record(discord_id) or {}
    reverse_roblox_id = str(
        reverse_link.get("robloxUserId") or reverse_link.get("roblox_id") or ""
    )
    if (
        not linked
        or linked_discord_id != discord_id
        or reverse_roblox_id != roblox_id
        or member is None
        or member.bot
    ):
        return None
    return member


def _dashboard_role_access(member):
    role_ids = {role.id for role in member.roles}
    if STAFF_OVERRIDE_ROLE_ID in role_ids:
        return 4, "Developer"
    if MOD_ROLE_ID in role_ids:
        return 3, "Moderator"
    if role_ids.intersection({int(NEXTECH_STAFF_ROLE_ID), AGENT_STAFF_ROLE_ID}):
        return 2, "Staff"
    return 1, "Member"


def _dashboard_link_timestamp(discord_id, linked_record):
    for key in ("linkedAt", "linked_at", "linkedTimestamp", "linked_timestamp", "createdAt", "created_at"):
        value = (linked_record or {}).get(key)
        if value:
            try:
                numeric = float(value)
                return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(numeric))
            except (TypeError, ValueError, OverflowError):
                return str(value)
    for action in reversed(load_bot_actions()):
        if action.get("action_type") not in {"account_linked", "hub_link"}:
            continue
        action_discord_id = action.get("discord_id") or action.get("target_discord_id")
        if str(action_discord_id or "") == str(discord_id):
            return action.get("timestamp_str")
    return None


def dashboard_members(query, actor):
    query = (query or "").strip().lower()
    if len(query) < 2:
        return {"members": [], "query": query, "error": "Enter at least 2 characters to search."}

    local_accounts = load_portal_accounts()
    candidates = {}
    for guild in client.guilds:
        for member in guild.members:
            account = local_accounts.get(str(member.id), {})
            searchable = " ".join(
                str(value or "").lower()
                for value in (
                    member.name,
                    member.display_name,
                    member.global_name,
                    member.id,
                    account.get("roblox_username"),
                )
            )
            if query in searchable:
                candidates.setdefault(str(member.id), {"member": member, "servers": []})
            if str(member.id) in candidates:
                candidates[str(member.id)]["servers"].append(guild)

    results = []
    for discord_id, candidate in list(candidates.items())[:25]:
        member = candidate["member"]
        index_record = _dashboard_discord_index_record(discord_id) or {}
        roblox_id = index_record.get("robloxUserId") or index_record.get("roblox_id")
        linked_record = _dashboard_link_record(roblox_id) if roblox_id else {}
        account = local_accounts.get(discord_id, {})
        role_level, role_name = _dashboard_role_access(member)
        roles = sorted({role.name for role in member.roles if role.name != "@everyone"})
        servers = [
            {
                "id": str(guild.id),
                "name": guild.name,
                "roles": sorted({role.name for role in guild.get_member(int(discord_id)).roles if role.name != "@everyone"})
                if guild.get_member(int(discord_id))
                else [],
                "primary": str(guild.id) == str(NEXTECH_GUILD_ID),
            }
            for guild in candidate["servers"]
        ]
        roblox_username = (
            (linked_record or {}).get("robloxUsername")
            or (linked_record or {}).get("roblox_username")
            or account.get("roblox_username")
        )
        results.append(
            {
                "discord_id": discord_id,
                "discord_username": str(member),
                "display_name": member.display_name,
                "roblox_id": str(roblox_id) if roblox_id else None,
                "roblox_username": roblox_username,
                "linked": bool(roblox_id),
                "linked_at": _dashboard_link_timestamp(discord_id, linked_record),
                "roles": roles,
                "role_level": role_level,
                "role_name": role_name,
                "access": "Allowed" if role_level >= 1 else "Denied",
                "servers": servers,
            }
        )

    return {"members": results, "query": query}


def authorize_dashboard(token):
    customer = _get_customer_session(token)
    if not customer or not customer.get("roblox_id"):
        return None
    roblox_id = str(customer["roblox_id"])
    if not roblox_id.isascii() or not roblox_id.isdecimal():
        return None
    linked, discord_id, member = _dashboard_linked_member(roblox_id)
    if (
        not linked
        or not discord_id
        or not member
        or (
            customer.get("discord_id")
            and str(customer.get("discord_id")) != str(discord_id)
        )
    ):
        return None
    if customer.get("provider") in {"email", "profile_code"}:
        reverse_link = _dashboard_discord_index_record(discord_id) or {}
        reverse_roblox_id = str(
            reverse_link.get("robloxUserId") or reverse_link.get("roblox_id") or ""
        )
        if reverse_roblox_id != roblox_id:
            return None

    role_level, role_name = _dashboard_role_access(member)
    is_protected_staff = _is_protected_roblox_staff_sync(roblox_id)
    if not is_protected_staff:
        role_level, role_name = 1, "Member"
    roles = sorted({role.name for role in member.roles if role.name != "@everyone"})
    servers = []
    for guild in client.guilds:
        guild_member = guild.get_member(member.id)
        if guild_member:
            servers.append({
                "id": str(guild.id),
                "name": guild.name,
                "roles": sorted({role.name for role in guild_member.roles if role.name != "@everyone"}),
                "primary": str(guild.id) == str(NEXTECH_GUILD_ID),
            })
    linked_username = (
        (linked or {}).get("robloxUsername")
        or (linked or {}).get("roblox_username")
        or customer.get("username", "")
    )
    return {
        "discord_id": discord_id,
        "discord_username": str(member),
        "display_name": member.display_name,
        "roblox_id": roblox_id,
        "roblox_username": linked_username,
        "role_level": role_level,
        "role_name": role_name,
        "is_protected_staff": is_protected_staff,
        "roles": roles,
        "access": "Allowed",
        "linked_at": _dashboard_link_timestamp(discord_id, linked),
        "servers": servers,
    }


def dashboard_snapshot(actor):
    warnings = load_warnings()
    if actor.get("role_level", 0) < 3:
        warnings = [w for w in warnings if str(w.get("user_id")) == str(actor.get("discord_id"))]
    return {
        "warning_count": len(warnings),
        "warning_points": sum(int(w.get("points", 1)) for w in warnings),
        "ticket_count": len(load_tickets()),
        "action_count": len(load_bot_actions()),
    }


def dashboard_create_product(body, actor, image=None, product_file=None):
    name = body["name"]
    category = body["category"]
    currency = body["currency"]
    price = body["price"]
    product_id = "prod-" + secrets.token_hex(4)
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", product_id)[:80] or "product"
    product_dir = os.path.join(PRODUCT_FILES_DIR, safe_id)
    image_url = ""
    file_url = ""
    file_path = ""
    saved_assets = []
    os.makedirs(product_dir, exist_ok=True)
    try:
        for upload, prefix in ((image, "image"), (product_file, "file")):
            if not upload or not upload.filename:
                continue
            filename = secure_filename(upload.filename)
            if not filename:
                raise ValueError("The uploaded filename is invalid.")
            stored_name = f"{prefix}-{filename}"
            destination = os.path.join(product_dir, stored_name)
            upload.save(destination)
            saved_assets.append(destination)
            public_url = f"/static/product_files/{safe_id}/{stored_name}"
            if prefix == "image":
                image_url = public_url
            else:
                file_url = public_url
                file_path = destination

        robux_price = int(price) if currency == "Robux" else 0
        usd_price = float(price) if currency == "USD" else 0
        product = {
            "id": product_id,
            "name": name,
            "category": category,
            "description": body["description"],
            "image": image_url,
            "robux": robux_price,
            "currency": currency,
            "created": time.time(),
            "added_by": actor.get("display_name", "Dashboard"),
            "file_path": file_path,
            "file_url": file_url,
            "gamepass_id": "",
            "price_usd": usd_price,
        }
        products = load_products()
        products.append(product)
        save_products(products)
    except Exception:
        for asset_path in saved_assets:
            try:
                os.remove(asset_path)
            except FileNotFoundError:
                pass
        try:
            os.rmdir(product_dir)
        except OSError:
            pass
        raise
    record_bot_action(
        "product_created",
        product_id=product["id"],
        product_name=name,
        actor_id=actor.get("discord_id"),
        actor=actor.get("display_name"),
    )
    return product


def dashboard_customer_accounts(query, actor):
    needle = (query or "").strip().casefold()
    customers = _load_customers()
    accounts = []
    for email, account in customers.items():
        username = str(account.get("username") or "")
        if needle and needle not in email.casefold() and needle not in username.casefold():
            continue
        accounts.append({
            "email": email,
            "username": username,
            "email_verified": bool(account.get("email_verified", True)),
            "suspended": bool(account.get("suspended", False)),
            "created_at": str(account.get("created_at") or ""),
            "discord_id": str(account.get("discord_id") or ""),
            "roblox_id": str(account.get("roblox_id") or ""),
        })
    accounts.sort(key=lambda item: item["created_at"], reverse=True)
    return {"accounts": accounts[:200], "total": len(accounts)}


def dashboard_create_customer_account(body, actor):
    username = str(body.get("username") or "").strip()[:40]
    email = str(body.get("email") or "").strip().lower()[:200]
    if not username or not email:
        return {"error": "Username and email are required."}, 400
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return {"error": "Enter a valid email address."}, 400
    customers = _load_customers()
    if email in customers:
        return {"error": "An account with that email already exists."}, 409
    account = {
        "username": username,
        "password": _hash_password(secrets.token_urlsafe(32)),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "email_verified": False,
        "created_by_staff": str(actor.get("discord_id") or ""),
        "suspended": False,
    }
    setup_token = _set_customer_password_reset_token(account)
    customers[email] = account
    _save_customers(customers)
    record_bot_action(
        "customer_account_created",
        target_email=email,
        target_username=username,
        actor_id=actor.get("discord_id"),
        actor=actor.get("display_name"),
    )
    sent, send_error = _send_customer_password_reset_email(
        email, username, setup_token, account_setup=True
    )
    if not sent:
        app.logger.error("Could not send account setup email: %s", send_error)
        return {
            "error": "The account was created, but the setup email could not be sent. Review SMTP settings and use Send recovery link.",
            "account_created": True,
            "account": {
                "email": email,
                "username": username,
                "email_verified": False,
                "suspended": False,
                "created_at": account["created_at"],
            },
        }, 503
    return {
        "ok": True,
        "message": "Account created. A password setup link was sent to the user.",
        "account": {
            "email": email,
            "username": username,
            "email_verified": False,
            "suspended": False,
            "created_at": account["created_at"],
        },
    }


def dashboard_customer_account_action(body, actor):
    email = str(body.get("email") or "").strip().lower()[:200]
    action = str(body.get("action") or "").strip().lower()
    if not email or action not in {"suspend", "restore", "recover", "send-reset", "link"}:
        return {"error": "Choose a valid account and action."}, 400
    customers = _load_customers()
    account = customers.get(email)
    if not account:
        return {"error": "Website account not found."}, 404
    if action == "link":
        discord_id = str(body.get("discord_id") or "").strip()
        roblox_id = str(body.get("roblox_id") or "").strip()
        if (
            not discord_id.isascii()
            or not discord_id.isdecimal()
            or not 17 <= len(discord_id) <= 20
            or not roblox_id.isascii()
            or not roblox_id.isdecimal()
            or len(roblox_id) > 20
        ):
            return {"error": "Enter valid Discord and Roblox user IDs."}, 400
        if _dashboard_verified_account_link(
            {"discord_id": discord_id, "roblox_id": roblox_id}
        ) is None:
            return {
                "error": "Those IDs do not match a current Discord ↔ Roblox link in NexTech Hub."
            }, 409
        account["discord_id"] = discord_id
        account["roblox_id"] = roblox_id
        account.pop("dashboard_login_token_hash", None)
        account.pop("dashboard_login_expires_at", None)
        _save_customers(customers)
        for token, session in list(customer_tokens.items()):
            if session.get("email") == email:
                customer_tokens.pop(token, None)
        record_bot_action(
            "customer_account_hub_linked",
            target_email=email,
            target_discord_id=discord_id,
            target_roblox_id=roblox_id,
            actor_id=actor.get("discord_id"),
            actor=actor.get("display_name"),
        )
        return {
            "ok": True,
            "message": "The website email is linked to the matching Discord and Roblox Hub accounts.",
        }
    if action == "suspend":
        reason = str(body.get("reason") or "").strip()[:500]
        if not reason:
            return {"error": "Enter a reason before suspending an account."}, 400
        account["suspended"] = True
        account["suspension_reason"] = reason
        account["suspended_at"] = time.strftime("%Y-%m-%d %H:%M:%S UTC")
        account["suspended_by"] = str(actor.get("discord_id") or "")
        for token, session in list(customer_tokens.items()):
            if session.get("email") == email:
                customer_tokens.pop(token, None)
        _save_customers(customers)
        record_bot_action(
            "customer_account_suspended",
            target_email=email,
            reason=reason,
            actor_id=actor.get("discord_id"),
            actor=actor.get("display_name"),
        )
        return {"ok": True, "suspended": True}

    if action in {"restore", "recover"}:
        account["suspended"] = False
        account.pop("suspension_reason", None)
        account.pop("suspended_at", None)
        account.pop("suspended_by", None)

    if action in {"recover", "send-reset"}:
        if account.get("suspended", False):
            return {"error": "Restore the suspended account before sending a recovery link."}, 409
        reset_token = _set_customer_password_reset_token(account)
        _save_customers(customers)
        sent, send_error = _send_customer_password_reset_email(
            email,
            account.get("username", "NexTech member"),
            reset_token,
        )
        record_bot_action(
            "customer_account_recovery_sent",
            target_email=email,
            actor_id=actor.get("discord_id"),
            actor=actor.get("display_name"),
        )
        if not sent:
            app.logger.error("Could not send account recovery email: %s", send_error)
            return {
                "error": "The account is available, but the recovery email could not be sent. Check SMTP settings and try again.",
                "account_restored": action == "recover",
            }, 503
        return {
            "ok": True,
            "suspended": False,
            "message": "A password recovery link was sent to the account email.",
        }

    _save_customers(customers)
    record_bot_action(
        "customer_account_restored",
        target_email=email,
        actor_id=actor.get("discord_id"),
        actor=actor.get("display_name"),
    )
    return {"ok": True, "suspended": False}


def _load_team_profiles():
    try:
        with open(TEAM_PROFILES_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("The team profile store must be a JSON object.")
    return data


def _save_team_profiles(profiles):
    with open(TEAM_PROFILES_FILE, "w", encoding="utf-8") as file:
        json.dump(profiles, file, indent=2, ensure_ascii=False)


def dashboard_team_profiles(actor):
    with TEAM_PROFILES_LOCK:
        profiles = _load_team_profiles()
    guild = _nextech_guild()
    result = []
    for discord_id, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        member = guild.get_member(int(discord_id)) if guild and str(discord_id).isdecimal() else None
        mark = str(profile.get("verification_mark") or "formal_staff")
        label, icon = TEAM_VERIFICATION_MARKS.get(
            mark, TEAM_VERIFICATION_MARKS["formal_staff"]
        )
        result.append({
            **profile,
            "discord_id": str(discord_id),
            "display_name": member.display_name if member else str(discord_id),
            "verification_mark": mark,
            "verification_label": label,
            "verification_icon": icon,
        })
    result.sort(key=lambda item: item["display_name"].casefold())
    return {"profiles": result}


def dashboard_save_team_profile(body, actor):
    discord_id = str(body.get("discord_id") or "").strip()
    mark = str(body.get("verification_mark") or "").strip()
    title = str(body.get("title") or "").strip()[:80]
    bio = str(body.get("bio") or "").strip()[:500]
    team_join_date = str(body.get("team_join_date") or "").strip()
    if (
        not discord_id.isascii()
        or not discord_id.isdecimal()
        or not 17 <= len(discord_id) <= 20
    ):
        return {"error": "Enter a valid Discord user ID."}, 400
    if mark not in TEAM_VERIFICATION_MARKS:
        return {"error": "Choose a recognized NexTech verification mark."}, 400
    if not title:
        return {"error": "A public team title is required."}, 400
    if team_join_date:
        try:
            datetime.strptime(team_join_date, "%Y-%m-%d")
        except ValueError:
            return {"error": "Enter the team join date as YYYY-MM-DD."}, 400

    index = _dashboard_discord_index_record(discord_id) or {}
    roblox_id = str(index.get("robloxUserId") or index.get("roblox_id") or "")
    member = _dashboard_verified_account_link({
        "discord_id": discord_id,
        "roblox_id": roblox_id,
    })
    if member is None:
        return {
            "error": "This person must be in the NexTech Discord server and have an active matching Roblox Hub link before appearing in the verified team directory."
        }, 409

    profile = {
        "discord_id": discord_id,
        "verification_mark": mark,
        "title": title,
        "bio": bio,
        "team_join_date": team_join_date,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": str(actor.get("discord_id") or ""),
    }
    with TEAM_PROFILES_LOCK:
        profiles = _load_team_profiles()
        profiles[discord_id] = profile
        _save_team_profiles(profiles)
    record_bot_action(
        "team_profile_updated",
        target_discord_id=discord_id,
        verification_mark=mark,
        title=title,
        actor_id=actor.get("discord_id"),
        actor=actor.get("display_name"),
    )
    return {"ok": True, "message": f"{member.display_name}'s verified team profile was saved."}


def dashboard_remove_team_profile(discord_id, actor):
    discord_id = str(discord_id or "").strip()
    if not discord_id.isascii() or not discord_id.isdecimal():
        return {"error": "Enter a valid Discord user ID."}, 400
    with TEAM_PROFILES_LOCK:
        profiles = _load_team_profiles()
        if discord_id not in profiles:
            return {"error": "Managed team profile not found."}, 404
        profiles.pop(discord_id)
        _save_team_profiles(profiles)
    record_bot_action(
        "team_profile_removed",
        target_discord_id=discord_id,
        actor_id=actor.get("discord_id"),
        actor=actor.get("display_name"),
    )
    return {"ok": True}


def request_dashboard_email_login(email, remote_addr=None):
    generic_result = {
        "ok": True,
        "message": (
            "If this is a verified NexTech email account linked to the same active "
            "Discord ↔ Roblox Hub link, a single-use sign-in link has been sent."
        ),
    }
    email = str(email or "").strip().lower()[:200]
    request_key = hashlib.sha256(
        f"{email}\0{remote_addr or ''}".encode("utf-8")
    ).hexdigest()
    now = time.time()
    for key, requested_at in list(_dashboard_email_login_requests.items()):
        if now - requested_at > 3600:
            _dashboard_email_login_requests.pop(key, None)
    if now - _dashboard_email_login_requests.get(request_key, 0) < 60:
        return generic_result
    _dashboard_email_login_requests[request_key] = now

    customers = _load_customers()
    account = customers.get(email)
    if (
        not isinstance(account, dict)
        or not account.get("email_verified", True)
        or account.get("suspended", False)
        or not _dashboard_verified_account_link(account)
    ):
        return generic_result

    base_url = _get_base_url()
    if not base_url:
        _dashboard_email_login_requests.pop(request_key, None)
        app.logger.error("Cannot send dashboard email sign-in: SITE_BASE_URL is not configured.")
        return {"error": "The dashboard email sign-in service is not configured yet."}, 503

    token = secrets.token_urlsafe(32)
    account["dashboard_login_token_hash"] = hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()
    account["dashboard_login_expires_at"] = now + _DASHBOARD_EMAIL_LOGIN_TTL
    _save_customers(customers)

    from urllib.parse import urlencode
    import html

    login_url = f"{base_url}/dashboard?{urlencode({'email_token': token})}"
    safe_name = html.escape(str(account.get("username") or "NexTech member")[:80])
    safe_url = html.escape(login_url, quote=True)
    html_body = (
        f"<p>Hello {safe_name},</p><p>Use this single-use link to sign in to your "
        "NexTech dashboard. It expires in 15 minutes.</p>"
        f'<p><a href="{safe_url}">Sign in to NexTech</a></p>'
        "<p>If you did not request this, you can ignore this email.</p>"
    )
    text_body = (
        f"Hello {account.get('username') or 'NexTech member'},\n\n"
        "Use this single-use link to sign in to your NexTech dashboard. "
        "It expires in 15 minutes.\n\n"
        f"{login_url}\n\nIf you did not request this, you can ignore this email."
    )
    sent, send_error = send_email(
        email, "Your NexTech dashboard sign-in link", html_body, text_body
    )
    if not sent:
        _dashboard_email_login_requests.pop(request_key, None)
        account.pop("dashboard_login_token_hash", None)
        account.pop("dashboard_login_expires_at", None)
        _save_customers(customers)
        app.logger.error("Could not send a dashboard email sign-in link: %s", send_error)
        return {"error": "The sign-in email could not be sent. Check the mail service and try again."}, 503
    return generic_result


def verify_dashboard_email_login(token):
    token_hash = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
    customers = _load_customers()
    account_email = next(
        (
            email
            for email, account in customers.items()
            if isinstance(account, dict)
            and secrets.compare_digest(
                str(account.get("dashboard_login_token_hash") or ""),
                token_hash,
            )
        ),
        None,
    )
    if account_email is None:
        return {"error": "The sign-in link is invalid or expired."}, 401

    account = customers[account_email]
    try:
        expires_at = float(account.get("dashboard_login_expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    account.pop("dashboard_login_token_hash", None)
    account.pop("dashboard_login_expires_at", None)
    _save_customers(customers)
    if not math.isfinite(expires_at) or expires_at <= time.time():
        return {"error": "The sign-in link is invalid or expired."}, 401
    if account.get("suspended", False):
        return {"error": "This account is suspended. Contact NexTech Support."}, 403
    if not account.get("email_verified", True):
        return {"error": "Verify your account email before signing in."}, 403
    member = _dashboard_verified_account_link(account)
    if member is None:
        return {
            "error": "The account no longer matches an active Discord ↔ Roblox Hub link. Contact NexTech staff."
        }, 403

    session_token = secrets.token_urlsafe(32)
    customer_tokens[session_token] = {
        "provider": "email",
        "email": account_email,
        "username": str(account.get("username") or member.display_name),
        "roblox_id": str(account["roblox_id"]),
        "discord_id": str(account["discord_id"]),
        "login_ts": time.time(),
    }
    record_bot_action(
        "dashboard_email_login",
        actor_id=str(account["discord_id"]),
        actor=member.display_name,
    )
    return {"ok": True, "token": session_token}


async def _dashboard_ticket_history_async(channel_id):
    channel = client.get_channel(int(channel_id))
    if channel is None:
        channel = await client.fetch_channel(int(channel_id))
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        raise RuntimeError("The ticket channel is unavailable.")
    messages = []
    async for message in channel.history(limit=100):
        content = message.content or ""
        if not content and message.embeds:
            content = message.embeds[0].description or message.embeds[0].title or ""
        if content:
            messages.append({
                "author": message.author.display_name,
                "is_staff": bool(
                    getattr(message.author, "roles", None)
                    and _dashboard_role_access(message.author)[0] >= 2
                ),
                "content": content[:3000],
                "created_at": message.created_at.isoformat(),
            })
    messages.reverse()
    return channel, messages


def dashboard_ticket_messages(case_id, actor):
    tickets = load_tickets()
    ticket = tickets.get(case_id)
    if not ticket:
        return {"error": "Ticket not found."}, 404
    channel_id = str(ticket.get("channel_id") or "")
    if not channel_id.isdecimal():
        return {"error": "This ticket is not connected to a replyable Discord channel."}, 503
    if not client.loop or client.loop.is_closed() or not client.is_ready():
        return {"error": "The NexTech Discord bot is not connected."}, 503
    try:
        future = asyncio.run_coroutine_threadsafe(
            _dashboard_ticket_history_async(channel_id), client.loop
        )
        _channel, messages = future.result(timeout=12)
    except Exception:
        app.logger.exception("Could not read ticket channel %s", channel_id)
        return {"error": "Could not load the ticket conversation."}, 503
    summary_keys = (
        "case_id", "case_name", "case_type", "discord_id", "discord_username",
        "roblox_username", "reason", "opened_at", "opened_date", "status", "source",
    )
    return {
        "ticket": {key: ticket[key] for key in summary_keys if key in ticket},
        "messages": messages,
    }


async def _dashboard_send_ticket_reply_async(channel_id, message):
    channel = client.get_channel(int(channel_id))
    if channel is None:
        channel = await client.fetch_channel(int(channel_id))
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        raise RuntimeError("The ticket channel is unavailable.")
    return await channel.send(
        message,
        allowed_mentions=discord.AllowedMentions.none(),
    )


def dashboard_reply_ticket(case_id, message, actor):
    tickets = load_tickets()
    ticket = tickets.get(case_id)
    if not ticket:
        return {"error": "Ticket not found."}, 404
    if str(ticket.get("status") or "").casefold() in {"closed", "resolved", "deleted"}:
        return {"error": "This ticket is closed and cannot be replied to."}, 409
    channel_id = str(ticket.get("channel_id") or "")
    if not channel_id.isdecimal():
        return {"error": "This ticket is not connected to a replyable Discord channel."}, 503
    if not client.loop or client.loop.is_closed() or not client.is_ready():
        return {"error": "The NexTech Discord bot is not connected."}, 503
    try:
        future = asyncio.run_coroutine_threadsafe(
            _dashboard_send_ticket_reply_async(channel_id, message), client.loop
        )
        sent = future.result(timeout=15)
    except Exception:
        app.logger.exception("Could not send dashboard reply to ticket %s", case_id)
        return {"error": "Could not send the reply to Discord."}, 503
    entry = {
        "author": str(actor.get("display_name") or "NexTech Staff"),
        "content": message[:3000],
        "created_at": sent.created_at.isoformat(),
        "discord_message_id": str(sent.id),
    }
    ticket.setdefault("messages", []).append(entry)
    save_tickets(tickets)
    record_bot_action(
        "dashboard_ticket_reply",
        case_id=case_id,
        actor_id=actor.get("discord_id"),
        actor=actor.get("display_name"),
    )
    return {"ok": True, "message": entry}


async def _create_web_ticket_async(body, actor):
    guild = client.get_guild(int(NEXTECH_GUILD_ID))
    if not guild:
        return {"error": "NexTech Discord server is not available"}, 503
    try:
        member = guild.get_member(int(actor["discord_id"])) or await guild.fetch_member(int(actor["discord_id"]))
    except (discord.NotFound, discord.HTTPException, ValueError):
        return {"error": "Your linked Discord account is no longer in the server"}, 403

    category = guild.get_channel(SUPPORT_CATEGORY_ID)
    if not category or not isinstance(category, discord.CategoryChannel):
        return {"error": "Support category is not configured"}, 503

    tickets = load_tickets()
    for ticket in tickets.values():
        if (
            str(ticket.get("discord_id")) == str(member.id)
            and ticket.get("status") not in ("closed", "deleted")
        ):
            return {
                "error": f"You already have an open ticket: Case {ticket.get('case_id', 'unknown')}"
            }, 409

    case_type = (body.get("case_type") or "Other Support").strip()[:80]
    roblox_username = (body.get("roblox_username") or actor.get("roblox_username") or "").strip()[:80]
    discord_username = (body.get("discord_username") or str(member)).strip()[:120]
    date_timezone = (body.get("date_timezone") or "Not provided").strip()[:160]
    past_case = (body.get("past_case") or "No Inquiry").strip()[:300]
    confirmed_type = (body.get("confirmed_type") or case_type).strip()[:120]
    reason = (body.get("reason") or "").strip()[:2000]
    if not roblox_username or not reason:
        return {"error": "Roblox username and a case description are required"}, 400
    if not date_timezone or not past_case or not confirmed_type:
        return {"error": "The support intake is incomplete. Please finish the AI questions."}, 400
    if case_type not in {"Moderation", "Product Inquiry"}:
        return {"error": "The AI assistant must confirm the final case type first."}, 400

    case_id = "".join(random.choices(string.digits, k=10))
    case_name = f"{case_type.replace(' ', '-')}-{case_id}"
    opened_date = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    staff_role = guild.get_role(AGENT_STAFF_ROLE_ID)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    ticket_channel = await category.create_text_channel(
        name=f"ticket-{case_name}",
        overwrites=overwrites,
        topic=f"{case_type} Case | User: {member.mention} | Case ID: {case_id} | Opened: {opened_date}",
    )
    case_info_embed = discord.Embed(
        title="📄 Web Support Case",
        description=(
            f"**Discord Username:** {discord_username} ({member.mention})\n"
            f"**Roblox Username:** {roblox_username}\n"
            f"**Date & Timezone:** {date_timezone}\n"
            f"**Past Case:** {past_case}\n"
            f"**Case Type:** {case_type}\n"
            f"**Confirmed Type:** {confirmed_type}\n"
            f"**Reason:** {reason}\n"
            f"**Case ID:** {case_id}\n"
            f"**Opened:** {opened_date}\n"
            "**Source:** NexTech Support website"
        ),
        color=discord.Color.green(),
    )
    additional_info_embed = discord.Embed(
        title="🛡️ Additional Information",
        description=(
            "This ticket was opened from the NexTech website.\n\n"
            "Please provide further details in this channel and avoid sharing passwords, "
            "cookies, or API keys. Tickets are monitored and recorded."
        ),
        color=discord.Color.blue(),
    )
    staff_ping = f"<@&{AGENT_STAFF_ROLE_ID}>" if staff_role else None
    await ticket_channel.send(content=staff_ping, embed=case_info_embed)
    await ticket_channel.send(embed=additional_info_embed)
    save_ticket_record(case_id, {
        "case_id": case_id,
        "case_name": case_name,
        "case_type": case_type,
        "discord_id": str(member.id),
        "discord_username": str(member),
        "roblox_username": roblox_username,
        "date_timezone": date_timezone,
        "past_case": past_case,
        "reason": reason,
        "confirmed_type": confirmed_type,
        "opened_at": time.time(),
        "opened_date": opened_date,
        "status": "open",
        "channel_id": str(ticket_channel.id),
        "messages": [],
        "source": "website",
    })
    try:
        await member.send(
            f"✅ Your NexTech support ticket **{case_id}** has been created: "
            f"{ticket_channel.mention}\nStaff will assist you shortly."
        )
    except (discord.Forbidden, discord.HTTPException):
        pass
    record_bot_action(
        "ticket_created",
        case_id=case_id,
        channel_id=str(ticket_channel.id),
        source="website",
        discord_id=str(member.id),
        discord_username=str(member),
        case_type=case_type,
        actor=actor.get("display_name"),
    )
    return {"case_id": case_id, "channel_id": str(ticket_channel.id)}


def create_web_ticket(body, actor):
    if not client or not client.loop or client.loop.is_closed():
        return {"error": "Discord bot is not ready"}, 503
    future = asyncio.run_coroutine_threadsafe(
        _create_web_ticket_async(body, actor),
        client.loop,
    )
    try:
        return future.result(timeout=30)
    except TimeoutError:
        return {"error": "Discord took too long to create the ticket"}, 504
    except Exception:
        return {"error": "Discord could not create the ticket"}, 502


async def _create_web_report_async(body, actor):
    guild = client.get_guild(int(NEXTECH_GUILD_ID))
    if not guild:
        return {"error": "NexTech Discord server is not available"}, 503
    try:
        member = guild.get_member(int(actor["discord_id"])) or await guild.fetch_member(int(actor["discord_id"]))
    except (discord.NotFound, discord.HTTPException, ValueError):
        return {"error": "Your linked Discord account is no longer in the server"}, 403

    channel = guild.get_channel(REPORT_PORTAL_CHANNEL_ID)
    if not channel or not isinstance(channel, discord.TextChannel):
        return {"error": "The report channel is not configured"}, 503
    category = (body.get("category") or "Other").strip()[:80]
    target = (body.get("target") or "").strip()[:160]
    details = (body.get("details") or "").strip()[:3000]
    if not target or len(details) < 10:
        return {"error": "A report target and at least 10 characters of details are required"}, 400

    report_id = f"RPT-{random.randint(100000, 999999)}"
    role_ping = f"<@&{REPORT_PORTAL_ROLE_ID}>" if REPORT_PORTAL_ROLE_ID else None
    embed = discord.Embed(
        title="⚠️ Website Report Portal",
        description=details,
        color=discord.Color.orange(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Report ID", value=f"`{report_id}`", inline=True)
    embed.add_field(name="Category", value=category, inline=True)
    embed.add_field(name="Reported Target", value=target, inline=False)
    embed.add_field(
        name="Reporter",
        value=f"{member.mention} · {member}\nRoblox: {actor.get('roblox_username') or 'Linked account'}",
        inline=False,
    )
    embed.set_footer(text="Submitted through the NexTech Report Portal")
    await channel.send(
        content=role_ping,
        embed=embed,
        allowed_mentions=discord.AllowedMentions(roles=True),
    )
    record_bot_action(
        "web_report_created",
        report_id=report_id,
        channel_id=str(channel.id),
        category=category,
        target=target,
        reporter_id=str(member.id),
        reporter=actor.get("display_name"),
    )
    return {"report_id": report_id, "channel_id": str(channel.id)}


def create_web_report(body, actor):
    if not client or not client.loop or client.loop.is_closed():
        return {"error": "Discord bot is not ready"}, 503
    future = asyncio.run_coroutine_threadsafe(
        _create_web_report_async(body, actor),
        client.loop,
    )
    try:
        return future.result(timeout=30)
    except TimeoutError:
        return {"error": "Discord took too long to receive the report"}, 504
    except Exception:
        return {"error": "Discord could not receive the report"}, 502


def support_ai_reply(messages, actor):
    if not OPENAI_API_KEY:
        return {"error": "The AI support assistant is not configured"}, 503
    if not isinstance(messages, list) or len(messages) > 24:
        return {"error": "Invalid support conversation"}, 400

    cleaned = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
            continue
        content = str(message.get("content") or "").strip()[:2000]
        if content:
            cleaned.append({"role": message["role"], "content": content})
    if not cleaned or cleaned[-1]["role"] != "user":
        return {"error": "Send a support message first"}, 400

    system_prompt = """You are NexTech Support Assistant, a professional and friendly website support agent.
Your job is to collect the exact information required by the existing /case-support process before a Discord ticket is opened.
Ask only one question at a time and keep replies concise. You may acknowledge what the user says, but do not skip missing fields.
Collect these fields:
1. discord_username: the user's Discord username or mention
2. roblox_username: their Roblox username, not display name
3. date_timezone: the relevant date and timezone
4. past_case: a previous case reference, or "No Inquiry"
5. reason: the reason and useful details for the case
6. confirmed_type: ask the user to choose Reporting Staff, Reporting Customers, Other Support, Product Support,
   Staff Questions, Appeal, Verification Issue, or Service Consultation
7. final_case_type: ask whether this is a Moderation or Product Inquiry case
After all fields are present, summarize the intake and ask: "Would you like me to open this Discord ticket?"
Only set ready_to_create to true after the user clearly confirms they want the ticket opened.
Never invent missing details. Never ask for passwords, cookies, API keys, or other secrets.
Return JSON only with this shape:
{"reply":"message to show the user","ready_to_create":false,
"intake":{"discord_username":"","roblox_username":"","date_timezone":"","past_case":"","reason":"",
"confirmed_type":"","final_case_type":""}}"""
    messages_for_model = [{"role": "system", "content": system_prompt}]
    messages_for_model.extend(cleaned)
    try:
        response = http_requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": messages_for_model,
                "temperature": 0.25,
                "max_tokens": 700,
                "response_format": {"type": "json_object"},
            },
            timeout=30,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        result = json.loads(content)
    except (http_requests.RequestException, KeyError, IndexError, TypeError, json.JSONDecodeError):
        return {"error": "The AI support assistant is temporarily unavailable"}, 502

    intake = result.get("intake") if isinstance(result.get("intake"), dict) else {}
    allowed = {
        "discord_username", "roblox_username", "date_timezone",
        "past_case", "reason", "confirmed_type", "final_case_type",
    }
    result["intake"] = {key: str(intake.get(key) or "").strip()[:2000] for key in allowed}
    result["reply"] = str(result.get("reply") or "Please tell me more about your support request.")[:3000]
    result["ready_to_create"] = bool(result.get("ready_to_create"))
    result["actor_roblox_username"] = actor.get("roblox_username", "")
    record_bot_action(
        "support_ai_message",
        actor_id=actor.get("discord_id"),
        actor=actor.get("display_name"),
        ready_to_create=result["ready_to_create"],
    )
    return result


@app.route('/api/customer/me')
def api_customer_me():
    token = request.headers.get("X-Customer-Token") or request.args.get("token")
    session = _get_customer_session(token)
    if not session:
        return jsonify({"error": "Not authenticated"}), 401
    result = {
        "provider": session.get("provider", "email"),
        "roblox_id": session.get("roblox_id"),
        "username": session.get("username", ""),
        "display_name": session.get("display_name") or session.get("username", ""),
        "avatar": session.get("avatar", ""),
        "email": session.get("email"),
    }
    if result["provider"] == "email":
        account_email = str(session.get("email") or "").strip().lower()
        account = _load_customers().get(account_email)
        result["email_verified"] = bool(
            account.get("email_verified", True) if isinstance(account, dict) else False
        )
    return jsonify(result)


@app.route('/api/customer/logout', methods=['POST'])
def api_customer_logout():
    body = request.get_json(silent=True) or {}
    token = request.headers.get("X-Customer-Token") or body.get("token")
    if token:
        customer_tokens.pop(token, None)
    return jsonify({"ok": True})


def run_flask(port=5000):
    import logging
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    if port == 5000:
        socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True, log_output=False)
    else:
        # Secondary port serves the same site (no websockets)
        app.run(host='0.0.0.0', port=port, use_reloader=False, threaded=True)


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True
intents.voice_states = True

class AClient(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.synced = False
        self.current_status = discord.Status.online
        self.current_activity = discord.CustomActivity(name="Watching Server and Roblox group")
        self._presence_lock = asyncio.Lock()
        self._voice_reconnect_lock = asyncio.Lock()
        self._last_voice_reconnect = 0.0
        self._last_voice_warning = 0.0

    async def setup_hook(self):
        self.loop.create_task(self.status_task())
        self.loop.create_task(self.vc_watchdog_task())

    async def on_ready(self):
        await self.wait_until_ready()
        if not self.synced:
            await tree.sync()
            self.synced = True
        print(f"Logged in as {self.user}")
        try:
            async with self._presence_lock:
                await self.change_presence(
                    status=self.current_status,
                    activity=self.current_activity
                )
            print(f"[STATUS] Restored configured status: {self.current_status.name}")
        except Exception as e:
            print(f"[STATUS] Could not restore configured status: {type(e).__name__}: {e}")
        try:
            record_bot_log(
                "bot_ready",
                details=f"Logged in as {self.user} · {len(self.guilds)} guild(s)",
            )
        except Exception:
            pass
        # Auto-join the designated VC on startup
        await self._auto_join_vc()

    async def _auto_join_vc(self):
        """Keep the bot in the configured voice channel without overlapping reconnect attempts."""
        await self.wait_until_ready()
        async with self._voice_reconnect_lock:
            now = asyncio.get_running_loop().time()
            if now - self._last_voice_reconnect < 20:
                return
            # A deliberate /vc-join selection takes priority until the bot is
            # disconnected/kicked from that channel. The voice-state event clears
            # this override when the bot is removed, allowing the watchdog to
            # return it to AUTO_JOIN_VC_ID.
            main_channel = self.get_channel(AUTO_JOIN_VC_ID)
            if main_channel is None or not isinstance(main_channel, discord.VoiceChannel):
                if now - self._last_voice_warning > 300:
                    print(f"[AUTO-VC] Configured channel {AUTO_JOIN_VC_ID} was not found or is not a voice channel.")
                    self._last_voice_warning = now
                return

            guild = main_channel.guild
            if guild.id in _TEMP_VC_OVERRIDE_GUILDS:
                return
            channel = main_channel
            vc = guild.voice_client
            if vc and vc.is_connected():
                if vc.channel and vc.channel.id != AUTO_JOIN_VC_ID:
                    self._last_voice_reconnect = now
                    try:
                        await vc.move_to(channel)
                        print(f"[AUTO-VC] Moved to #{channel.name}")
                    except Exception as e:
                        if now - self._last_voice_warning > 120:
                            print(f"[AUTO-VC] Move failed: {type(e).__name__}: {e}")
                            self._last_voice_warning = now
                return

            self._last_voice_reconnect = now
            try:
                # If a stale voice client exists, clean it up before reconnecting.
                if vc:
                    try:
                        await vc.disconnect(force=True)
                    except Exception:
                        pass
                await channel.connect(timeout=20, reconnect=False)
                print(f"[AUTO-VC] Connected to #{channel.name}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if now - self._last_voice_warning > 120:
                    print(f"[AUTO-VC] Voice connection attempt failed: {type(e).__name__}: {e}")
                    self._last_voice_warning = now

    async def vc_watchdog_task(self):
        """Check periodically, but never spam overlapping voice reconnect attempts."""
        await self.wait_until_ready()
        while not self.is_closed():
            await asyncio.sleep(60)
            try:
                channel = self.get_channel(AUTO_JOIN_VC_ID)
                if channel is None or not isinstance(channel, discord.VoiceChannel):
                    continue
                vc = channel.guild.voice_client
                if vc is None or not vc.is_connected():
                    await self._auto_join_vc()
                elif vc.channel is None:
                    # A connected voice client with no channel is stale; recover once via the lock.
                    await self._auto_join_vc()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                now = asyncio.get_running_loop().time()
                if now - self._last_voice_warning > 120:
                    print(f"[AUTO-VC] Watchdog check failed: {type(e).__name__}: {e}")
                    self._last_voice_warning = now

    async def status_task(self):
        while not self.is_closed():
            if self.is_ready():
                total_members = sum(guild.member_count or 0 for guild in self.guilds)
                number_of_hubs = len(self.guilds)
                activity_texts = [
                    "Watching Server and Roblox group",
                    f"Watching over {total_members} members",
                    f"Watching in {number_of_hubs} Hubs",
                    "Watching use /help for commands",
                    "Watching for assistance use /case-support",
                    "Watching checking Server Moderation"
                ]
                next_activity = discord.CustomActivity(name=random.choice(activity_texts))
                try:
                    async with self._presence_lock:
                        self.current_activity = next_activity
                        selected_status = getattr(self, "current_status", discord.Status.online)
                        await self.change_presence(
                            status=selected_status,
                            activity=next_activity
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    print(f"[STATUS] Failed to refresh activity: {e}")
                await asyncio.sleep(random.randint(900, 1800))
            else:
                await asyncio.sleep(5)


action_log = []
MAX_LOG_ENTRIES = 500

def record_log(user, action_type, command_name=None, guild_name=None, channel_name=None, details=None):
    record_bot_action(
        action_type,
        details=details,
        user_id=str(user.id),
        username=str(user),
        command_name=command_name,
        guild_name=guild_name,
        channel_name=channel_name,
    )
    action_log.insert(0, {
        "timestamp": datetime.now(timezone.utc),
        "user_id": str(user.id),
        "username": str(user),
        "action_type": action_type,
        "command_name": command_name,
        "guild_name": guild_name,
        "channel_name": channel_name,
        "details": details,
    })
    if len(action_log) > MAX_LOG_ENTRIES:
        action_log.pop()


def record_bot_log(action_type, details=None, command_name=None, guild_name=None, channel_name=None):
    """Log an action performed by the bot itself (no Discord user)."""
    bot_user_id = str(client.user.id) if client and client.user else "bot"
    bot_username = f"🤖 {client.user}" if client and client.user else "🤖 NexTech Bot"
    record_bot_action(
        action_type,
        details=details,
        user_id=bot_user_id,
        username=bot_username,
        command_name=command_name,
        guild_name=guild_name,
        channel_name=channel_name,
    )
    action_log.insert(0, {
        "timestamp": datetime.now(timezone.utc),
        "user_id": bot_user_id,
        "username": bot_username,
        "action_type": action_type,
        "command_name": command_name,
        "guild_name": guild_name,
        "channel_name": channel_name,
        "details": details,
    })
    if len(action_log) > MAX_LOG_ENTRIES:
        action_log.pop()

# Define the command tree outside the class
client = AClient()
tree = app_commands.CommandTree(client)


def _nextech_guild():
    return client.get_guild(int(NEXTECH_GUILD_ID))


def _public_discord_member(member):
    status = getattr(member, "status", discord.Status.offline)
    return {
        "id": str(member.id),
        "display_name": str(member.display_name or member.name)[:100],
        "avatar": str(member.display_avatar.url),
        "online": status in {discord.Status.online, discord.Status.idle, discord.Status.dnd},
    }


def get_public_community_members(query=""):
    guild = _nextech_guild()
    if guild is None or not client.is_ready():
        raise RuntimeError("The NexTech Discord server is not available.")
    normalized_query = str(query or "").strip().casefold()[:80]
    members = [
        _public_discord_member(member)
        for member in guild.members
        if not member.bot
        and (
            not normalized_query
            or normalized_query in member.display_name.casefold()
            or normalized_query in member.name.casefold()
        )
    ]
    members.sort(key=lambda member: member["display_name"].casefold())
    return {"members": members[:250], "total": len(members), "truncated": len(members) > 250}


def get_public_community_member(discord_id):
    if not str(discord_id or "").isdecimal():
        return None
    guild = _nextech_guild()
    if guild is None or not client.is_ready():
        raise RuntimeError("The NexTech Discord server is not available.")
    member = guild.get_member(int(discord_id))
    if member is None or member.bot:
        return None
    return _public_discord_member(member)


def get_public_community_staff():
    guild = _nextech_guild()
    if guild is None or not client.is_ready():
        raise RuntimeError("The NexTech Discord server is not available.")

    with TEAM_PROFILES_LOCK:
        profiles = _load_team_profiles()
    role_titles = {
        int(NEXTECH_STAFF_ROLE_ID): "NexTech staff",
        int(AGENT_STAFF_ROLE_ID): "Operations staff",
        int(MOD_ROLE_ID): "Moderation",
        int(STAFF_OVERRIDE_ROLE_ID): "Staff leadership",
    }
    directory = []
    for member in guild.members:
        if member.bot:
            continue
        member_role_ids = {role.id for role in member.roles}
        default_title = next(
            (
                role_title
                for role_id, role_title in role_titles.items()
                if role_id in member_role_ids
            ),
            None,
        )
        profile = profiles.get(str(member.id))
        if not default_title and not isinstance(profile, dict):
            continue
        mark = (
            str(profile.get("verification_mark") or "formal_staff")
            if isinstance(profile, dict)
            else "orange" if MOD_ROLE_ID in member_role_ids
            else "silver" if AGENT_STAFF_ROLE_ID in member_role_ids
            else "formal_staff"
        )
        if mark not in TEAM_VERIFICATION_MARKS:
            mark = "formal_staff"
        label, icon = TEAM_VERIFICATION_MARKS[mark]
        directory.append({
            **_public_discord_member(member),
            "role": str(
                profile.get("title") if isinstance(profile, dict) else default_title
            ),
            "verification_mark": mark,
            "verification_label": label,
            "verification_icon": icon,
            "bio": str(profile.get("bio") or "")[:500] if isinstance(profile, dict) else "",
            "team_join_date": str(profile.get("team_join_date") or "")[:10] if isinstance(profile, dict) else "",
        })
    directory.sort(key=lambda item: item["display_name"].casefold())
    return directory[:250]


def get_public_community_summary():
    guild = _nextech_guild()
    if guild is None or not client.is_ready():
        raise RuntimeError("The NexTech Discord server is not available.")
    members = [member for member in guild.members if not member.bot]
    online_statuses = {discord.Status.online, discord.Status.idle, discord.Status.dnd}
    return {
        "member_count": guild.member_count if guild.member_count is not None else len(members),
        "online_count": sum(
            getattr(member, "status", discord.Status.offline) in online_statuses
            for member in members
        ),
        "server_name": guild.name,
        "available": True,
    }


def get_live_community_messages():
    if _nextech_guild() is None or not client.is_ready():
        raise RuntimeError("The NexTech Discord server is not available.")
    with COMMUNITY_LIVE_LOCK:
        return list(reversed(COMMUNITY_LIVE_MESSAGES))


def get_community_session(token):
    session = community_sessions.get(str(token or ""))
    if not session or session.get("expires_at", 0) <= time.time():
        community_sessions.pop(str(token or ""), None)
        return None
    guild = _nextech_guild()
    member = guild.get_member(int(session["discord_id"])) if guild else None
    if member is None:
        community_sessions.pop(str(token), None)
        return None
    return {
        "discord_id": str(member.id),
        "display_name": str(member.display_name or member.name)[:100],
        "avatar": str(member.display_avatar.url),
    }


def get_member_community_notifications(discord_id):
    return load_community_notifications(discord_id)


def mark_member_community_notifications_read(discord_id, notification_ids=None):
    return mark_community_notifications_read(discord_id, notification_ids)


async def mirror_public_discord_message(message):
    if message.guild is None or str(message.guild.id) != NEXTECH_GUILD_ID:
        return
    if message.author.bot or not isinstance(
        message.channel, (discord.TextChannel, discord.Thread)
    ):
        return
    if isinstance(message.channel, discord.Thread) and message.channel.is_private():
        return
    if getattr(message.channel, "is_nsfw", lambda: False)():
        return
    public_permissions = message.channel.permissions_for(message.guild.default_role)
    if not public_permissions.view_channel:
        return
    content = (message.content or "").strip()
    if not content:
        return

    # Let the bot's moderation listener remove prohibited pings before they reach the site.
    await asyncio.sleep(1.25)
    if message.id in ghost_ping_cache:
        return
    try:
        message = await message.channel.fetch_message(message.id)
    except discord.NotFound:
        return
    except discord.HTTPException:
        app.logger.exception("Could not verify a Discord message before public mirroring")
        return
    content = (message.content or "").strip()
    if not content or message.author.bot:
        return
    payload = {
        "id": str(message.id),
        "channel": str(message.channel.name)[:100],
        "author": str(message.author.display_name or message.author.name)[:100],
        "avatar": str(message.author.display_avatar.url),
        "content": content[:1200],
        "created_at": message.created_at.isoformat(),
    }
    with COMMUNITY_LIVE_LOCK:
        COMMUNITY_LIVE_MESSAGES.append(payload)

    recipients = {
        str(member.id)
        for member in message.mentions
        if not member.bot and member.id != message.author.id
    }
    role_labels = {}
    for role in message.role_mentions:
        role_labels[role.id] = role.name[:80]
        recipients.update(
            str(member.id)
            for member in role.members
            if not member.bot and member.id != message.author.id
        )
    channel_name = str(message.channel.name)[:80]
    notice_message = f"A message from {payload['author']} in #{channel_name} mentioned you or one of your roles."
    notice_type = "role_mention" if message.role_mentions else "direct_mention"
    notification_entries = [
        {
            "discord_id": discord_id,
            "type": notice_type,
            "title": "You were mentioned on Discord",
            "message": notice_message,
            "source_id": message.id,
            "href": "/community",
        }
        for discord_id in recipients
    ]
    if notification_entries:
        add_community_notifications(notification_entries)


async def update_public_discord_message(before, after):
    if after.guild is None or str(after.guild.id) != NEXTECH_GUILD_ID:
        return
    with COMMUNITY_LIVE_LOCK:
        for item in COMMUNITY_LIVE_MESSAGES:
            if item["id"] == str(after.id):
                item["content"] = (after.content or "").strip()[:1200]
                break


async def remove_deleted_public_discord_message(message):
    if message.guild is None or str(message.guild.id) != NEXTECH_GUILD_ID:
        return
    with COMMUNITY_LIVE_LOCK:
        retained = [
            item for item in COMMUNITY_LIVE_MESSAGES
            if item["id"] != str(message.id)
        ]
        COMMUNITY_LIVE_MESSAGES.clear()
        COMMUNITY_LIVE_MESSAGES.extend(retained)


@app.route("/auth/discord/community")
def auth_discord_community():
    if not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET:
        return redirect("/?discord_login_error=oauth_not_configured")
    next_path = request.args.get("next", "/")
    if (
        not isinstance(next_path, str)
        or not next_path.startswith("/")
        or next_path.startswith("//")
        or "#" in next_path
    ):
        next_path = "/"
    now = time.time()
    for old_state, value in list(_community_oauth_states.items()):
        if now - value.get("created_at", 0) > 300:
            _community_oauth_states.pop(old_state, None)
    state = secrets.token_urlsafe(24)
    _community_oauth_states[state] = {"created_at": now, "next": next_path}
    try:
        return redirect(_discord_oauth_url("/auth/discord/community/callback", state))
    except RuntimeError:
        return redirect("/?discord_login_error=base_url_not_configured")


@app.route("/auth/discord/community/callback")
def auth_discord_community_callback():
    import urllib.parse as _up
    import urllib.request as _ur

    error = request.args.get("error", "")
    if error:
        return redirect("/?discord_login_error=access_denied")
    state = request.args.get("state", "")
    state_data = _community_oauth_states.pop(state, None)
    if not isinstance(state_data, dict) or time.time() - state_data.get("created_at", 0) > 300:
        return redirect("/?discord_login_error=invalid_state")
    code = request.args.get("code", "")
    base = _get_base_url()
    if not code or not base:
        return redirect("/?discord_login_error=oauth_not_configured")

    redirect_uri = f"{base}/auth/discord/community/callback"
    payload = _up.urlencode({
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }).encode()
    token_request = _ur.Request(
        "https://discord.com/api/oauth2/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with _ur.urlopen(token_request, timeout=10) as response:
            oauth_data = json.loads(response.read())
        access_token = oauth_data.get("access_token")
        if not access_token:
            return redirect("/?discord_login_error=token_exchange_failed")
        user_request = _ur.Request(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with _ur.urlopen(user_request, timeout=10) as response:
            user = json.loads(response.read())
    except (OSError, ValueError, json.JSONDecodeError):
        app.logger.exception("Discord community login failed while fetching the user")
        return redirect("/?discord_login_error=user_fetch_failed")

    discord_id = str(user.get("id") or "")
    if not discord_id.isdecimal():
        return redirect("/?discord_login_error=user_fetch_failed")
    guild = _nextech_guild()
    if guild is None:
        return redirect("/?discord_login_error=bot_not_in_guild")
    member = guild.get_member(int(discord_id))
    if member is None:
        return redirect("/?discord_login_error=not_in_server")

    token = secrets.token_urlsafe(32)
    community_sessions[token] = {
        "discord_id": discord_id,
        "username": str(member.display_name or member.name),
        "expires_at": time.time() + 7 * 24 * 60 * 60,
    }
    next_path = state_data.get("next", "/")
    return redirect(f"{next_path}#discord_token={_up.quote(token)}")
@tree.interaction_check
async def global_command_check(interaction: discord.Interaction) -> bool:
    # Whitelisted IDs bypass role/permission gating implemented by this global
    # interaction check. Disabled commands remain disabled for everyone.
    if interaction.command and interaction.command.name in disabled_commands:
        await interaction.response.send_message(
            "❌ This command is currently **disabled** by the developer.", ephemeral=True)
        return False
    return True

FULL_ACCESS_USER_IDS = {
    1251996949536706565,
    385171080979021826,
    1315976768787578908,
    961724248718717008,
    961719954674163773,
}

def has_full_access(user) -> bool:
    return user is not None and int(getattr(user, "id", 0)) in FULL_ACCESS_USER_IDS

SETSTATUS_ROLE_ID = 1258549984387665943

@tree.command(name="setstatus", description="Change the bot's status (Online, DND, Idle, Invisible).")
@app_commands.describe(status="Choose the new status")
@app_commands.choices(status=[
    app_commands.Choice(name="Online ✅", value="online"),
    app_commands.Choice(name="Do Not Disturb ⛔", value="dnd"),
    app_commands.Choice(name="Idle 🌙", value="idle"),
    app_commands.Choice(name="Invisible ❌", value="invisible"),
])
async def setstatus(interaction: discord.Interaction, status: app_commands.Choice[str]):
    # This command changes the bot account's presence, so require a server member.
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True,
        )
        return

    # Allow the configured bot owner, explicitly whitelisted user IDs, or the
    # designated role. The ID whitelist works independently of Discord roles.
    try:
        is_bot_owner = await interaction.client.is_owner(interaction.user)
    except Exception:
        is_bot_owner = False

    is_authorized = (
        is_bot_owner
        or has_full_access(interaction.user)
        or any(role.id == SETSTATUS_ROLE_ID for role in interaction.user.roles)
    )
    if not is_authorized:
        await interaction.response.send_message(
            "❌ You do not have permission to use this command. Add your Discord user ID to FULL_ACCESS_USER_IDS to grant access.",
            ephemeral=True,
        )
        return

    status_map = {
        "online": discord.Status.online,
        "dnd": discord.Status.dnd,
        "idle": discord.Status.idle,
        "invisible": discord.Status.invisible,
    }
    new_status = status_map.get(status.value)
    if new_status is None:
        await interaction.response.send_message(
            "❌ That status is not supported.",
            ephemeral=True,
        )
        return

    # Use the one primary bot client that owns the command tree.
    bot_client = client
    if not bot_client.is_ready() or bot_client.is_closed():
        await interaction.response.send_message(
            "❌ The bot is not ready to update its status right now. Please try again shortly.",
            ephemeral=True,
        )
        return

    # Defer first so Discord does not time out the interaction while the presence
    # update is being sent. The status task also reads current_status, so it will
    # preserve DND/Idle/Invisible when it refreshes the rotating activity.
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        async with bot_client._presence_lock:
            bot_client.current_status = new_status
            await bot_client.change_presence(
                status=new_status,
                activity=getattr(bot_client, "current_activity", None),
            )
        print(f"[SETSTATUS] Presence update applied: status={new_status.name}")
    except Exception as exc:
        print(f"[SETSTATUS] Presence update failed: {type(exc).__name__}: {exc}")
        await interaction.followup.send(
            f"❌ I couldn't change my Discord status: {type(exc).__name__}: {exc}",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        f"✅ Bot status set to **{status.name}**.",
        ephemeral=True,
    )



class HelpView(discord.ui.View):
    MAX_FIELD_LENGTH = 1024  # Discord limit

    def __init__(self, author, commands_list, page=0, per_page=10, sub_page=0):
        super().__init__(timeout=180)
        self.author = author
        self.commands_list = commands_list
        self.page = page
        self.per_page = per_page
        self.current_group: discord.app_commands.Group | None = None
        self.sub_page = sub_page

        self.total_commands = len(self.commands_list)
        self.total_pages = (self.total_commands - 1) // per_page + 1

        self.update_buttons()

    # -------------------------------
    # Pagination Data
    # -------------------------------
    def get_page_data(self):
        start = self.page * self.per_page
        end = start + self.per_page
        return self.commands_list[start:end]

    # -------------------------------
    # Dynamic subcommand page
    # -------------------------------
    def get_sub_page_data(self):
        if not self.current_group:
            return []

        subcommands = self.current_group.commands
        pages = []
        current_page = []
        length = 0

        for sub in subcommands:
            formatted = self.format_command(sub, parent=self.current_group.name)
            padded_len = len(formatted) + 10
            if length + padded_len > self.MAX_FIELD_LENGTH and current_page:
                pages.append(current_page)
                current_page = [sub]
                length = padded_len
            else:
                current_page.append(sub)
                length += padded_len

        if current_page:
            pages.append(current_page)

        # Safe return
        if self.sub_page >= len(pages):
            self.sub_page = len(pages) - 1
        return pages[self.sub_page] if pages else []

    def total_sub_pages(self):
        if not self.current_group:
            return 1
        subcommands = self.current_group.commands
        pages = []
        current_page = []
        length = 0

        for sub in subcommands:
            formatted = self.format_command(sub, parent=self.current_group.name)
            padded_len = len(formatted) + 10
            if length + padded_len > self.MAX_FIELD_LENGTH and current_page:
                pages.append(current_page)
                current_page = [sub]
                length = padded_len
            else:
                current_page.append(sub)
                length += padded_len

        if current_page:
            pages.append(current_page)
        return max(1, len(pages))

    # -------------------------------
    # Format command with parameters
    # -------------------------------
    def format_command(self, command, parent=""):
        usage = f"/{parent} {command.name}".strip()
        description = getattr(command, "description", "No description provided.")
        if isinstance(command, discord.app_commands.Command) and command.parameters:
            params = " ".join(f"<{p.name}>" for p in command.parameters)
            usage += " " + params
        return f"**Description:** {description}\n**Usage:** `{usage}`"

    # -------------------------------
    # Format Embed
    # -------------------------------
    def format_embed(self, interaction: discord.Interaction):
        embed = discord.Embed(color=discord.Color.blue())

        if self.current_group:
            embed.title = f"Subcommands of /{self.current_group.name} (Page {self.sub_page+1}/{self.total_sub_pages()})"
            for sub in self.get_sub_page_data():
                embed.add_field(
                    name=sub.name,
                    value=self.format_command(sub, parent=self.current_group.name)[:self.MAX_FIELD_LENGTH],
                    inline=False
                )
        else:
            embed.title = f"Available Commands (Page {self.page+1}/{self.total_pages})"
            embed.description = f"Total Slash Commands: {self.total_commands}"
            for command in self.get_page_data():
                embed.add_field(
                    name=command.name,
                    value=self.format_command(command)[:self.MAX_FIELD_LENGTH],
                    inline=False
                )

        return embed

    # -------------------------------
    # Update Buttons
    # -------------------------------
    def update_buttons(self):
        self.clear_items()

        if self.current_group:
            # Subcommand pagination buttons
            prev_sub = discord.ui.Button(
                label="⬅ Prev Subcommands",
                style=discord.ButtonStyle.primary,
                disabled=self.sub_page == 0
            )
            prev_sub.callback = self.previous_sub_page
            self.add_item(prev_sub)

            next_sub = discord.ui.Button(
                label="Next Subcommands ➡",
                style=discord.ButtonStyle.primary,
                disabled=self.sub_page >= self.total_sub_pages() - 1
            )
            next_sub.callback = self.next_sub_page
            self.add_item(next_sub)

            # Back button
            back_button = discord.ui.Button(
                label="⬅ Back to Commands",
                style=discord.ButtonStyle.secondary
            )
            back_button.callback = self.back_to_main
            self.add_item(back_button)
        else:
            # Main command pagination
            prev_button = discord.ui.Button(
                label="Previous",
                style=discord.ButtonStyle.primary,
                disabled=self.page == 0
            )
            prev_button.callback = self.previous_page
            self.add_item(prev_button)

            next_button = discord.ui.Button(
                label="Next",
                style=discord.ButtonStyle.primary,
                disabled=self.page >= self.total_pages - 1
            )
            next_button.callback = self.next_page
            self.add_item(next_button)

            # Show subcommand buttons for groups
            for command in self.get_page_data():
                if isinstance(command, discord.app_commands.Group):
                    # Fix lambda binding using default argument
                    sub_button = discord.ui.Button(
                        label=f"Show /{command.name} Subcommands",
                        style=discord.ButtonStyle.secondary
                    )
                    sub_button.callback = self.create_show_sub_callback(command)
                    self.add_item(sub_button)

        # Total commands
        total_button = discord.ui.Button(
            label="📊 Total Commands",
            style=discord.ButtonStyle.secondary
        )
        total_button.callback = self.show_total
        self.add_item(total_button)

        # About Bot
        about_button = discord.ui.Button(
            label="🔗 About Bot",
            style=discord.ButtonStyle.link,
            url="https://futures-organization-1.gitbook.io/nextechs-development-support/how-do-i-make-a-ticket-inquiry/nextechs-utility-bot-infomataion"
        )
        self.add_item(about_button)

    # -------------------------------
    # Fix lambda binding for subcommand buttons
    # -------------------------------
    def create_show_sub_callback(self, command_group):
        async def callback(interaction: discord.Interaction):
            await self.show_subcommands(interaction, command_group)
        return callback

    # -------------------------------
    # Interaction check
    # -------------------------------
    async def interaction_check(self, interaction: discord.Interaction):
        return interaction.user.id == self.author.id

    # -------------------------------
    # Main Pagination Callbacks
    # -------------------------------
    async def previous_page(self, interaction: discord.Interaction):
        self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.format_embed(interaction), view=self)

    async def next_page(self, interaction: discord.Interaction):
        self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.format_embed(interaction), view=self)

    # -------------------------------
    # Subcommand Pagination Callbacks
    # -------------------------------
    async def previous_sub_page(self, interaction: discord.Interaction):
        self.sub_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.format_embed(interaction), view=self)

    async def next_sub_page(self, interaction: discord.Interaction):
        self.sub_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.format_embed(interaction), view=self)

    # -------------------------------
    # Show Subcommands
    # -------------------------------
    async def show_subcommands(self, interaction: discord.Interaction, group: discord.app_commands.Group):
        self.current_group = group
        self.sub_page = 0
        self.update_buttons()
        await interaction.response.edit_message(embed=self.format_embed(interaction), view=self)

    # -------------------------------
    # Back to main commands
    # -------------------------------
    async def back_to_main(self, interaction: discord.Interaction):
        self.current_group = None
        self.sub_page = 0
        self.update_buttons()
        await interaction.response.edit_message(embed=self.format_embed(interaction), view=self)

    # -------------------------------
    # Show total
    # -------------------------------
    async def show_total(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"This bot currently has {self.total_commands} slash commands.", ephemeral=True)

# ================= HELP COMMAND =================
@tree.command(name="help", description="List available commands")
async def show_help(interaction: discord.Interaction):
    commands_list = sorted(tree.get_commands(), key=lambda c: c.name)
    view = HelpView(author=interaction.user, commands_list=commands_list)
    await interaction.response.send_message(embed=view.format_embed(interaction), view=view, ephemeral=True)









@tree.command(name='report-bug', description='Report a bug with the bot')
@app_commands.checks.cooldown(1, 60)
async def report_bug(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user = interaction.user

    await interaction.followup.send("Please check your DMs to describe the bug you are experiencing with the bot.", ephemeral=True)

    try:
        await user.send("Please describe the bug you are experiencing with the bot.")
    except discord.Forbidden:
        await interaction.followup.send("I cannot send you a DM. Please ensure your DMs are open and try again.", ephemeral=True)
        return

    def check(msg):
        return msg.author == user and isinstance(msg.channel, discord.DMChannel)

    try:
        bug_description_msg = await client.wait_for('message', check=check, timeout=300.0)
        bug_description = bug_description_msg.content

        await user.send("If you have a photo, please upload it now. If not, please provide a link to the photo or leave this blank.")
        photo_msg = await client.wait_for('message', check=check, timeout=300.0)

        if photo_msg.attachments:
            photo_url = photo_msg.attachments[0].url
            photo_display = f"[Attached Photo]({photo_url})"
        else:
            photo_link = photo_msg.content
            if photo_link:
                photo_display = f"[Photo Link]({photo_link})"
            else:
                photo_display = 'No photo provided.'

        channel = client.get_channel(1277393868870127689)

        if channel:
            initial_message = await channel.send(
                f"**Bug Report by {user}**\n"
                f"**Bug Description:** {bug_description}\n"
                f"**Photo:** {photo_display}"
            )

            thread = await initial_message.create_thread(
                name=f"Bug Report by {user}",
                auto_archive_duration=60
            )

            # Notify the user about the thread
            await user.send(f"Thank you for your bug report! A thread has been created to discuss it further: {thread.mention}")

        else:
            await user.send("Failed to find the reporting channel. Please contact support.")

    except discord.Forbidden:
        await interaction.followup.send("I cannot send you DMs. Please ensure your DMs are open and try again.", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.followup.send("There was an error processing your report. Please try again later.", ephemeral=True)
        print(f"HTTP Error: {e}")
    except Exception as e:
        await interaction.followup.send("There was an error processing your report. Please try again later.", ephemeral=True)
        print(f"General Error: {e}")







@tree.command(name='product-bug', description='Report a bug with the product')
@app_commands.checks.cooldown(1, 60)
async def product_bug(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user = interaction.user

    await interaction.followup.send("Please check your DMs to describe the bug you are experiencing with the product.", ephemeral=True)

    try:
        await user.send("Please describe the bug you are experiencing with the product.")
    except discord.Forbidden:
        await interaction.followup.send("I cannot send you a DM. Please ensure your DMs are open and try again.", ephemeral=True)
        return

    def check(msg):
        return msg.author == user and isinstance(msg.channel, discord.DMChannel)

    try:
        bug_description_msg = await client.wait_for('message', check=check, timeout=300.0)
        bug_description = bug_description_msg.content

        await user.send("If you have a photo, please upload it now. If not, please provide a link to the photo or leave this blank.")
        photo_msg = await client.wait_for('message', check=check, timeout=300.0)

        if photo_msg.attachments:
            photo_url = photo_msg.attachments[0].url
            photo_display = f"[Attached Photo]({photo_url})"
        else:
            photo_link = photo_msg.content
            if photo_link:
                photo_display = f"[Photo Link]({photo_link})"
            else:
                photo_display = 'No photo provided.'

        channel = client.get_channel(1277393868870127689)

        if channel:
            initial_message = await channel.send(
                f"**Bug Report by {user}**\n"
                f"**Bug Description:** {bug_description}\n"
                f"**Photo:** {photo_display}"
            )

            thread = await initial_message.create_thread(
                name=f"Bug Report by {user}",
                auto_archive_duration=60
            )

            # Notify the user about the thread
            await user.send(f"Thank you for your product bug report! A thread has been created to discuss it further: {thread.mention}")

        else:
            await user.send("Failed to find the reporting channel. Please contact support.")

    except discord.Forbidden:
        await interaction.followup.send("I cannot send you DMs. Please ensure your DMs are open and try again.", ephemeral=True)
    except discord.HTTPException as e:
        await user.send("There was an error processing your report. Please try again later.")
        print(f"HTTP Error: {e}")
    except Exception as e:
        await user.send("There was an error processing your report. Please try again later.")
        print(f"General Error: {e}")




@tree.command(name='close-thread', description='close a report')
async def close_thread(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Sorry, this command has been disabled/discontinued. Please refer to our command list. Thank you!",
        ephemeral=True
    )

@tree.command(name='tester', description='testing')
@app_commands.checks.cooldown(1, 300)
async def slash2(interaction: discord.Interaction, name: str):
    embed = create_custom_embed("Test Result", f"I am working! {name}")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name='mod-support', description='Open a case and get case name and ID')
async def mod_support(interaction: discord.Interaction):
    await interaction.response.send_message(
        "This command is currently being updated. Please use `/case-support` instead.",
        ephemeral=True
    )


@tree.command(name='data', description='Checks logs')
async def data_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        "This command has been replaced by `/logs`. Please use that instead.",
        ephemeral=True
    )




@tree.command(name='user-case', description='Show user case information')
async def user_case(interaction: discord.Interaction, user: discord.User):
    await interaction.response.send_message(
        f"Feature coming soon! Checking history for {user.mention}.",
        ephemeral=True
    )


@tree.command(name='license-center', description='Get the link to our product license center')
async def license_center(interaction: discord.Interaction):
    await interaction.response.send_message(
        "License Center: [Coming Soon]",
        ephemeral=True
    )


@tree.command(name='pod-support', description='Get help with py Pod')
async def pod_support(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Py Pod Support is currently handled via tickets. Use `/case-support`.",
        ephemeral=True
    )


@tree.command(name='py-pod-update', description='Py pod update')
async def py_pod_update(interaction: discord.Interaction):
    await interaction.response.send_message(
        "No updates currently available for Py Pod.",
        ephemeral=True
    )


@tree.command(name='plans', description='get py pod plans')
async def plans_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Plans are currently being finalized. Stay tuned!",
        ephemeral=True
    )


@tree.command(name='checkbot', description='Check if Py pod is getting issues')
async def checkbot(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Bot Status: Operational ✅",
        ephemeral=True
    )


@tree.command(name='reportissue', description='Check if Py pod is having issues')
async def reportissue(interaction: discord.Interaction):
    await interaction.response.send_message(
        "To report an issue, please use `/report-bug`.",
        ephemeral=True
    )

@tree.command(name='self-help', description='self support desk')
async def self_help(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Self-Help Desk: [Link Coming Soon]",
        ephemeral=True
    )

@tree.command(name='ticket', description='Opens a support ticket for users')
async def ticket_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Please use `/case-support` to open a ticket.",
        ephemeral=True
    )

@tree.command(name='join', description='Allows a user to join a ticket thread')
async def join_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Please use `/join-ticket` instead.",
        ephemeral=True
    )

@tree.command(name='end', description='Closes a ticket thread')
async def end_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Please use `/end-ticket` instead.",
        ephemeral=True
    )


@tree.command(name='delete', description='Delete the ticket channel')
async def delete_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Use `/end-ticket` to close and archive tickets.",
        ephemeral=True
    )


@tree.command(name='inquiries-transcript', description='Retrieve transcripts for cases/inquiries.')
async def inquiries_transcript(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Transcripts are currently being archived. Contact an Admin for access.",
        ephemeral=True
    )


@tree.command(name='product-wheel', description='Spin the wheel for a chance to win product!')
async def product_wheel(interaction: discord.Interaction):
    await interaction.response.send_message(
        "The Product Wheel is currently offline for maintenance.",
        ephemeral=True
    )


@tree.command(name='techno', description='Open a support channel for help')
async def techno_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Techno Support: Please use `/case-support` for technical assistance.",
        ephemeral=True
    )


@tree.command(name='payment', description='Get the link to our Payment center')
async def payment_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Payment Center: [Link Coming Soon]",
        ephemeral=True
    )


@tree.command(name="rank-verify", description="Start the rank verification process")
async def rank_verify(interaction: discord.Interaction):
    await interaction.response.send_message(
        "🔐 **Rank Verification Process Initiated**\n"
        "Please follow the verification instructions provided.\n\n"
        "Staff Notice: If you encounter any issues during verification, please contact the **Chief of Staff** for further assistance.",
        ephemeral=True
    )


@tree.command(name='group', description='Get the link to our Roblox group')
async def roblox_group(interaction: discord.Interaction):
    embed = create_custom_embed("Roblox Group Links", "Here are the links to our Roblox groups:")
    embed.add_field(name="F-T-D NexTech Development", value="https://www.roblox.com/groups/11146129/F-T-D-NexTech-Development#!/about")
    embed.add_field(name="NexTech Development Game Administrator", value="https://www.roblox.com/groups/6419702/NexTech-Development-Game-administrator#!/about")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name='reviewed-app', description='Mark an app approved')
async def reviewed_app(interaction: discord.Interaction):
    await interaction.response.send_message(
        "This command is no longer active. Please run `/help` to see the available commands.",
        ephemeral=True
    )


@tree.command(name='pending-app', description='Mark an app pending')
async def pending_app(interaction: discord.Interaction):
    await interaction.response.send_message(
        "This command is no longer active. Please run `/help` to see the available commands.",
        ephemeral=True
    )


@tree.command(name='genkey', description='Generate an API key')
async def generate_api_key(interaction: discord.Interaction):
    await interaction.response.send_message(
        "This command is no longer active. Please run `/help` to see the available commands.",
        ephemeral=True
    )


@tree.command(name='gethub', description='get hub file')
async def get_rbxm_file(interaction: discord.Interaction):
    await interaction.response.send_message(
        "This command is no longer active. Please run `/help` to see the available commands.",
        ephemeral=True
    )


@tree.command(name='cookie', description='Send a cookie via DM')
async def send_cookie_dm(interaction: discord.Interaction):
    user = interaction.user

    embed = discord.Embed(
        title="Cookie Sent!",
        description="I sent you a cookie, you'll like it, it's fresh from the oven I made 🍪",
        color=discord.Color.blue()
    )
    embed.set_footer(text="Made by NexTech")

    await user.send(embed=embed)
    await interaction.response.send_message("Cookie sent to your DMs!", ephemeral=True)

@tree.command(name='setup', description='Check if the bot has an admin role')
async def setup(interaction: discord.Interaction):
    has_admin_role = False

    for role in interaction.guild.roles:
        if role.permissions.administrator:
            has_admin_role = True
            break

    if has_admin_role:
        embed = create_custom_embed("Administrator Role Detected", "I have an administrator role. Setup is complete!")
    else:
        embed = create_custom_embed("Administrator Role Missing", "I need an admin role to set up!")

    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name='pyarmor', description='Test obfuscated')
async def pyarmor(interaction: discord.Interaction):
    embed = create_custom_embed("Obfuscated Pyarmor File", "Sorry, I don't have an obfuscated Pyarmor file for your Roblox hub nor an API Key.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name='adminsetup', description='Set up admin roles and permissions')
async def admin_setup(interaction: discord.Interaction):
    guild = interaction.guild
    role = await guild.create_role(name="admin", permissions=discord.Permissions(administrator=True))

    embed = create_custom_embed("Admin Roles and Permissions", "Admin roles and permissions have been set up!")
    await interaction.response.send_message(embed=embed, ephemeral=True)



class FeatureRequestModal(discord.ui.Modal, title="Submit a Feature Request"):
    suggestion = discord.ui.TextInput(
        label="Feature Suggestion",
        style=discord.TextStyle.paragraph,
        placeholder="Describe your feature suggestion here...",
        required=True,
        max_length=1024
    )

    async def on_submit(self, interaction: discord.Interaction):
        feature_request_channel = client.get_channel(FEATURE_REQUEST_CHANNEL_ID)

        if feature_request_channel:
            embed = discord.Embed(
                title="Feature Request",
                description=self.suggestion.value,
                color=discord.Color.blue()
            )

            user_info = f"Requested by: {interaction.user.name} (User ID: {interaction.user.id})"
            embed.add_field(name="User Info", value=user_info, inline=False)

            # Footer with a simple message
            embed.set_footer(text="You want to add notes/give more feedback")

            # Send the embed to the channel
            message = await feature_request_channel.send(embed=embed)

            # Add reactions for Like (👍) and Dislike (👎)
            await message.add_reaction("👍")
            await message.add_reaction("👎")

            await interaction.response.send_message("Your feature request has been submitted.", ephemeral=True)
        else:
            await interaction.response.send_message("Feature request channel not found. Please contact the bot owner.", ephemeral=True)

@tree.command(name='feature', description='Submit a feature for the product')
async def feature_request(interaction: discord.Interaction):
    modal = FeatureRequestModal()
    await interaction.response.send_modal(modal)




@tree.command(name='staff-application', description='Apply for staff')
async def staff_application(interaction: discord.Interaction):
    view = View()
    button = Button(style=discord.ButtonStyle.link, label="Staff Application", url="Cooming soon")
    view.add_item(button)

    await interaction.response.send_message("Apply for staff using the following link:", ephemeral=True, view=view)

@tree.command(name='staff-interview', description='Staff interview')
async def staff_interview(interaction: discord.Interaction):
    view = View()
    button = Button(style=discord.ButtonStyle.link, label="Staff Interview", url="Coming soon")
    view.add_item(button)

    await interaction.response.send_message("Access the staff interview using the following link:", ephemeral=True, view=view)


@tree.command(name='support', description='Open a support ticket')
async def support_cmd(interaction: discord.Interaction):
    embed = create_custom_embed(
        'Coming-soon',
        'Sorry, this does not work yet. Check back later or open a ticket if you need support. '
        'We are working on supporting this soon. To open a case, use `/case-support`.'
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name='solve', description='Mark a case as solved')
async def solve_case(interaction: discord.Interaction):
    embed = create_custom_embed(
        "Coming-soon",
        "Sorry, this does not work yet. Check back later or open a ticket if you need support. "
        "To open a case, use `/end-ticket`."
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name='donte', description='Get the link to our Donation center')
async def donte_cmd(interaction: discord.Interaction):
    embed = create_custom_embed(
        "NexTech Development Donation center",
        "Here's the link to our NexTech Development server:"
    )
    embed.add_field(name="N.T.D's Donation center", value="Coming soon — refer to `/payment-hub` command")
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def page_cmd(interaction: discord.Interaction):
    embed = create_custom_embed(
        "NexTech Website Link",
        "Here's the link to our page:"
    )
    embed.add_field(
        name="Our Website",
        value="https://futures-organization-1.gitbook.io/nextechs-development-support/",
        inline=False
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name='invite', description='Get the link to our discord server')
async def invite_cmd(interaction: discord.Interaction):
    embed = create_custom_embed(
        "NexTech Link",
        "Here's the link to NexTech Development:"
    )
    embed.add_field(name="N.T.D's Server", value="https://discord.com/invite/KZzZKWkmWZ")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name='hub-security', description='Get link to our hub for code for PY pod')
async def hub_security_cmd(interaction: discord.Interaction):
    embed = create_custom_embed(
        "NexTech security hub",
        "Sorry, I don't have a link to NexTech's Security hub for you. "
        "For your Obfuscated or whitelist file/Roblox script, reach out to the support team for more info."
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)



# -----------------------------
# IDs and constants
# -----------------------------
AGENT_STAFF_ROLE_ID = 1268998581000601651
SUPPORT_CATEGORY_ID = 1277982068466516044
HISTORY_CHANNEL_ID = 1277984617403777024
ROBLOX_UNIVERSE_ID = 6281946099
ROBLOX_CLOUD_API_KEY = os.environ.get("ROBLOX_CLOUD_API_KEY", "")
MOD_ROLE_ID = 1306344294310875207
MOD_LOG_CHANNEL_ID = 1517544865720697053
PROTECTED_GROUP_ID = 11146129
PROTECTED_ROBLOX_RANK = 210

PROTECTED_DISCORD_ROLE_ID = 1306344294310875207
STAFF_OVERRIDE_ROLE_ID = 1258549984333135995

# -----------------------------
# Hub Link constants
# -----------------------------
HUB_PLACE_ID        = os.getenv("HUB_PLACE_ID", "75888659690482")
HUB_UNIVERSE_ID     = os.getenv("HUB_UNIVERSE_ID", "")
HUB_CLOUD_API_KEY   = os.getenv("HUB_CLOUD_API_KEY", "")
LINK_LOG_CHANNEL_ID = int(os.getenv("LINK_LOG_CHANNEL_ID", "0") or 0)

PENDING_STORE_NAME = "PendingLinks"
LINKED_STORE_NAME  = "LinkedPlayers"
DISCORD_INDEX_NAME = "DiscordIndex"
CODE_EXPIRY_SECS   = 20  # 20 seconds

# -----------------------------
# Question view for options
# -----------------------------
class QuestionView(discord.ui.View):
    def __init__(self, options, interaction):
        super().__init__()
        self.interaction = interaction
        for option in options:
            self.add_item(discord.ui.Button(label=option, style=discord.ButtonStyle.primary, custom_id=option))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user == self.interaction.user

# -----------------------------
# /case-support command
# -----------------------------
@tree.command(name='case-support', description='Open a case and create a ticket')
async def case_support(interaction: discord.Interaction):
    if interaction.guild is None:
        return await interaction.response.send_message(
            "⚠️ This command cannot be run in DMs. Please return to the server and try again.",
            ephemeral=True
        )

    category = interaction.guild.get_channel(SUPPORT_CATEGORY_ID)
    if not category or not isinstance(category, discord.CategoryChannel):
        return await interaction.response.send_message("⚠️ Support category not found. Contact staff.", ephemeral=True)

    for ch in category.text_channels:
        if ch.topic and f"User: {interaction.user.mention}" in ch.topic:
            return await interaction.response.send_message(
                f"⚠️ You already have an open ticket: {ch.mention}",
                ephemeral=True
            )

    if interaction.user.id in _case_support_active:
        return await interaction.response.send_message(
            "⚠️ You already have a case in progress. Please check your DMs and complete it first.",
            ephemeral=True
        )

    # DM Welcome
    try:
        await interaction.user.send(
            "👋 Welcome to **N.T.D | NexTech Development Support**!\n\n"
            "Do you need support? Reply with **Yes support** or **No support**."
        )
    except discord.Forbidden:
        return await interaction.response.send_message(
            "❌ I can't DM you. Enable DMs and try again.",
            ephemeral=True
        )

    _case_support_active.add(interaction.user.id)
    await interaction.response.send_message("✅ Case process started! Check your DMs. 📬", ephemeral=True)

    def dm_check(msg: discord.Message):
        return msg.author == interaction.user and msg.guild is None

    try:
        reply = await client.wait_for("message", check=dm_check, timeout=120)
        if reply.content.lower().strip() != "yes support":
            _case_support_active.discard(interaction.user.id)
            return await interaction.user.send("❌ Case process canceled.")
    except asyncio.TimeoutError:
        _case_support_active.discard(interaction.user.id)
        return await interaction.user.send("⏰ Timeout. Start again with `/case-support`.")

    # Questions
    questions = [
        ("What is your Discord username? (Ping it here)", []),
        ("What is your Roblox username? (NOT DISPLAY NAME)", []),
        ("Please provide the date and your timezone.", []),
        ("Do you have a past case? If not, say 'No Inquiry' or paste your past case.", []),

        ("What is your reason for this case?", [
            "Reporting Staff",
            "Reporting Customers",
            "Other Support",
            "Product Support",
            "Staff Questions",
            "Appeal to be Removed from Blacklist or similar matters",
            "Verification Issue",
            "Service Consultation"
        ]),

        ("To confirm your case with our support team, please choose from the following options:", [
            "Reporting Staff",
            "Reporting Customers",
            "Requesting to talk to an executive",
            "Other Support",
            "Product Support",
            "Staff Questions",
            "Appeal to be Removed from Blacklist or similar matters",
            "Verification Issue",
            "Service Consultation"
        ])
    ]

    answers = []

    for question, options in questions:
        if options:
            embed = discord.Embed(title="Question", description=question, color=discord.Color.yellow())
            view = QuestionView(options, interaction)
            message = await interaction.user.send(embed=embed, view=view)

            response = await client.wait_for(
                'interaction',
                check=lambda i: i.user == interaction.user and i.data['custom_id'] in options,
                timeout=300
            )

            answers.append(response.data['custom_id'])
            await response.response.defer()
            await message.edit(view=None)

        else:
            embed = discord.Embed(description=f"{question}\n\nReply to this message.", color=discord.Color.yellow())
            await interaction.user.send(embed=embed)

            try:
                user_answer = await client.wait_for('message', check=dm_check, timeout=300)
                answers.append(user_answer.content)
                await interaction.user.send("✅ Answer received.")
            except asyncio.TimeoutError:
                _case_support_active.discard(interaction.user.id)
                return await interaction.user.send("⏰ Timeout. Start again with `/case-support`.")

    # Final Case Type
    final_embed = discord.Embed(
        title="Final Question",
        description="Is this a Moderation or Product Inquiry case?",
        color=discord.Color.orange()
    )

    view = QuestionView(["Moderation", "Product Inquiry"], interaction)
    message = await interaction.user.send(embed=final_embed, view=view)

    response = await client.wait_for(
        'interaction',
        check=lambda i: i.user == interaction.user and i.data['custom_id'] in ["Moderation", "Product Inquiry"],
        timeout=300
    )

    case_type = response.data['custom_id']
    await response.response.defer()
    await message.edit(view=None)

    # -----------------------------
    # CREATE TICKET (FIXED - ONLY ONCE)
    # -----------------------------
    case_id = ''.join(random.choices(string.digits, k=10))
    case_name = f"{case_type.replace(' ', '-')}-{case_id}"
    opened_date = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        interaction.guild.get_role(AGENT_STAFF_ROLE_ID): discord.PermissionOverwrite(view_channel=True, send_messages=True)
    }

    ticket_channel = await category.create_text_channel(
        name=f"ticket-{case_name}",
        overwrites=overwrites,
        topic=f"{case_type} Case | User: {interaction.user.mention} | Case ID: {case_id} | Opened: {opened_date}"
    )

    case_info_embed = discord.Embed(
        title="📄 Case Information",
        description=(
            f"**Discord Username:** {answers[0]} ({interaction.user.mention})\n"
            f"**Roblox Username:** {answers[1]}\n"
            f"**Date & Timezone:** {answers[2]}\n"
            f"**Past Case:** {answers[3]}\n"
            f"**Reason:** {answers[4]}\n"
            f"**Case Type (Confirmed):** {answers[5]}\n"
            f"**Final Case Type:** {case_type}\n"
            f"**Case ID:** {case_id}\n"
            f"**Opened:** {opened_date}"
        ),
        color=discord.Color.green()
    )

    additional_info_embed = discord.Embed(
        title="🛡️ Additional Information",
        description=(
            "Please provide further details in this channel.\n\n"
            "**Guidelines:**\n"
            "- Avoid sharing sensitive information.\n"
            "- Staff will assist you shortly.\n\n"
            "⚠️ Tickets are monitored and recorded for legal & staff training purposes."
        ),
        color=discord.Color.blue()
    )

    await ticket_channel.send(content=f"<@&{AGENT_STAFF_ROLE_ID}>", embed=case_info_embed)
    await ticket_channel.send(embed=additional_info_embed)

    save_ticket_record(case_id, {
        "case_id": case_id,
        "case_name": case_name,
        "case_type": case_type,
        "discord_id": str(interaction.user.id),
        "discord_username": str(interaction.user),
        "roblox_username": answers[1],
        "reason": answers[4],
        "confirmed_type": answers[5],
        "opened_at": time.time(),
        "opened_date": opened_date,
        "status": "open",
        "channel_id": str(ticket_channel.id),
        "messages": [],
    })

    await interaction.user.send(
        f"✅ Your support ticket has been created: {ticket_channel.mention}\n"
        "Staff will assist you shortly. Please avoid pinging staff unnecessarily.\n"
        "⚠️ Tickets are monitored and recorded for legal & staff training purposes."
    )
    _case_support_active.discard(interaction.user.id)

# -----------------------------
# /end-ticket command
# -----------------------------
@tree.command(name='end-ticket', description='Close and delete a ticket, send summary and history')
async def end_ticket(interaction: discord.Interaction):
    if not has_full_access(interaction.user) and AGENT_STAFF_ROLE_ID not in [role.id for role in interaction.user.roles]:
        return await interaction.response.send_message(
            "You do not have permission to close tickets.", ephemeral=True
        )

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel) or not channel.name.startswith("ticket-"):
        return await interaction.response.send_message(
            "This command can only be used in a ticket channel.", ephemeral=True
        )

    topic = channel.topic or ""
    case_type = "Unknown"
    case_id = "Unknown"
    user_mention = None

    if "Case ID:" in topic and "User:" in topic:
        try:
            case_type = topic.split(" Case")[0].strip()
            case_id = topic.split("Case ID:")[1].strip()
            user_mention = topic.split("User:")[1].split("|")[0].strip()
        except Exception:
            pass

    messages = [msg async for msg in channel.history(limit=200)]
    message_count = len(messages)

    opened_date = channel.created_at.strftime("%Y-%m-%d %H:%M:%S")
    closed_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    review_end_date = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    saved_messages = []
    for msg in reversed(messages):
        if msg.content or msg.embeds:
            saved_messages.append({
                "author": msg.author.display_name,
                "author_id": str(msg.author.id),
                "is_bot": msg.author.bot,
                "content": msg.content,
                "ts": msg.created_at.timestamp(),
                "ts_str": msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
            })
    close_ticket_record(case_id, saved_messages)

    safe_case_id = re.sub(r'[\\/*?:"<>|]', "_", case_id)
    file_path = f"NexTech Ticket System - Case {safe_case_id}.txt"

    with open(file_path, "w", encoding="utf-8") as f:
        for msg in reversed(messages):
            timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}] {msg.author}: {msg.content}\n")

    embed = discord.Embed(
        title="Ticket Closed",
        description=(
            f"Closed by: {interaction.user.mention}\n\n"
            "This ticket has been resolved.\n"
            "Staff commands in ticket were:\n"
            "- /case-support\n"
            "- /end_ticket\n"
            "- /join_ticket\n\n"
            "Chat history logged; ticket will be deleted."
        ),
        color=discord.Color.red()
    )
    await channel.send(embed=embed)

    detailed_embed = discord.Embed(
        title="Ticket Summary",
        description=(
            f"**Case Type:** {case_type}\n"
            f"**Case ID:** {case_id}\n"
            f"**Opened Date:** {opened_date}\n"
            f"**Closed Date:** {closed_date}\n"
            f"**Messages Count:** {message_count}\n"
            f"**Closed by:** {interaction.user.display_name}\n"
            f"**Ticket will no longer be supported after:** {review_end_date}\n\n"
            "We would love to hear how the agent did too! 😊"
        ),
        color=discord.Color.blue()
    )

    file_channel = interaction.guild.get_channel(HISTORY_CHANNEL_ID)
    if file_channel:
        try:
            with open(file_path, "rb") as f:
                await file_channel.send(file=discord.File(f, filename=file_path), embed=detailed_embed)
        except Exception:
            pass

    if user_mention:
        try:
            user_id = int(user_mention.strip("<@!>"))
            user = await interaction.guild.fetch_member(user_id)
            await user.send(
                f"Thank you for reaching out to us. Your feedback is valuable.\n\n"
                f"**Ticket Type:** {case_type}\n"
                f"**Case ID:** {case_id}\n"
                f"**Closed by:** {interaction.user.display_name}\n"
                f"Your inquiry can be revisited until **{review_end_date}**. "
                "After this, it will no longer be supported.\n\n"
                "To provide feedback, run `/feedback`. To open a new ticket, run `/case-support`.\n\n"
                "We would love to hear how the agent did, too. It helps us improve our support! 😊\n\n"
                "Supported cases: https://futures-organization-1.gitbook.io/nextechs-development-support/nextech-utility-bot/supported-cases"
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "⚠️ Sorry, I can't DM the user because their DMs are off. "
                "Please ask them to check their Discord privacy settings. 🙂",
                ephemeral=True
            )
        except Exception:
            pass

    os.remove(file_path)

    try:
        await channel.delete()
    except Exception:
        pass

  # -----------------------------
  # /mod command — Roblox Open Cloud ban/kick/unban/termination
  # -----------------------------
async def _fetch_all_group_members(session, group_id: str) -> list:
      members = []
      cursor = ""
      while True:
          url = f"https://groups.roblox.com/v1/groups/{group_id}/users?sortOrder=Asc&limit=100"
          if cursor:
              url += f"&cursor={cursor}"
          try:
              async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                  if r.status != 200:
                      break
                  data = await r.json()
                  for entry in data.get("data", []):
                      u = entry.get("user", {})
                      members.append({
                          "userId": u.get("userId"),
                          "username": u.get("username", "Unknown"),
                          "displayName": u.get("displayName", "Unknown"),
                      })
                  cursor = data.get("nextPageCursor") or ""
                  if not cursor:
                      break
          except Exception:
              break
      return members


@tree.command(name='mod-action', description='Ban, kick, unban, or record termination for a Roblox player')
@app_commands.describe(action="Action to take", username="Roblox username", reason="Reason for the action (shown to player)")
@app_commands.choices(action=[
      app_commands.Choice(name="🔨 Ban (permanent)", value="ban"),
      app_commands.Choice(name="👢 Kick", value="kick"),
      app_commands.Choice(name="✅ Unban", value="unban"),
      app_commands.Choice(name="⚠ Recorded Termination", value="termination"),
  ])
async def mod_action(
      interaction: discord.Interaction,
      action: app_commands.Choice[str],
      username: str = None,
      reason: str = "No reason provided"
  ):
      # Explicit deny-list for /mod-action. These users receive the owner-contact message
      # even if they have a moderation role; the bot's full-access list does not override it.
      MOD_ACTION_DENIED_USER_IDS = {
          1258549984429871171,
          1478149951811878952,
          1478150097304027166,
          1501986579474939965,
          1558593915009900768,
      }
      if int(getattr(interaction.user, "id", 0)) in MOD_ACTION_DENIED_USER_IDS:
          return await interaction.response.send_message(
              "❌ Invalid permission. Please contact the bot owner.", ephemeral=True
          )

      # Permit full-access IDs or members with either configured moderation role.
      member_roles = getattr(interaction.user, "roles", [])
      allowed_role_ids = {MOD_ROLE_ID, 1258549984429871171}
      if not has_full_access(interaction.user) and not any(r.id in allowed_role_ids for r in member_roles):
          return await interaction.response.send_message(
              "❌ Invalid permission. Please contact the bot owner.", ephemeral=True
          )

      if not ROBLOX_CLOUD_API_KEY:
          return await interaction.response.send_message(
              "❌ Roblox Cloud API key is not configured. Contact the developer.", ephemeral=True
          )

      act = action.value

      if not username:
          return await interaction.response.send_message(
              "❌ You must provide a **Roblox username** for this action.", ephemeral=True
          )

      await interaction.response.defer(ephemeral=True)

      api_headers = {
          "x-api-key": ROBLOX_CLOUD_API_KEY,
          "Content-Type": "application/json",
      }

      async with aiohttp.ClientSession() as session:

          # ── LOOKUP USER ────────────────────────────────
          try:
              async with session.post(
                  "https://users.roblox.com/v1/usernames/users",
                  json={"usernames": [username.strip()], "excludeBannedUsers": False},
                  timeout=aiohttp.ClientTimeout(total=10)
              ) as r:
                  data = await r.json()
                  users = data.get("data", [])
                  if not users:
                      return await interaction.followup.send(
                          f"❌ Could not find a Roblox user with the username **{username}**.",
                          ephemeral=True
                      )
                  roblox_user = users[0]
                  user_id = roblox_user["id"]
                  display_name = roblox_user.get("displayName", username)

                  # Protect these Roblox accounts from all /mod-action operations.
                  # Compare the canonical Roblox username returned by Roblox, case-insensitively.
                  MOD_ACTION_PROTECTED_ROBLOX_USERNAMES = {
                      "Militarynoob562",
                      "doomkingtv_yt",
                      "Roseroblox0012",
                      "family_tree0246",
                      "AgentMrCoolFTD",
                  }
                  canonical_username = str(roblox_user.get("name", username)).strip().casefold()
                  protected_usernames = {name.casefold() for name in MOD_ACTION_PROTECTED_ROBLOX_USERNAMES}
                  if canonical_username in protected_usernames:
                      return await interaction.followup.send(
                          "❌ Invalid permission. This Roblox user is protected from `/mod-action`. Please contact the bot owner.",
                          ephemeral=True
                      )
          except Exception as e:
              return await interaction.followup.send(
                  f"❌ Failed to look up Roblox user: `{e}`",
                  ephemeral=True
              )

          url = f"https://apis.roblox.com/cloud/v2/universes/{ROBLOX_UNIVERSE_ID}/user-restrictions/{user_id}"
          profile_url = f"https://www.roblox.com/users/{user_id}/profile"

          # ── ACTION PAYLOADS ────────────────────────────────
          if act == "ban":
              payload = {"gameJoinRestriction": {
                  "active": True,
                  "displayReason": reason,
                  "privateReason": f"Banned by {interaction.user} via /mod",
                  "excludeAltAccounts": False,
              }}

          elif act == "termination":
              payload = {"gameJoinRestriction": {
                  "active": True,
                  "displayReason": f"⚠ Recorded Termination: {reason}",
                  "privateReason": f"Recorded Termination by {interaction.user} via /mod",
                  "excludeAltAccounts": True,
              }}

          else:  # unban
              payload = {"gameJoinRestriction": {"active": False}}

          # ── APPLY TO ROBLOX ────────────────────────────────
          if act == "kick":
              # Kick uses a separate endpoint (removes user from current session only)
              kick_url = f"https://apis.roblox.com/cloud/v2/universes/{ROBLOX_UNIVERSE_ID}/users/{user_id}/kick"
              kick_body = {"displayReason": reason}
              try:
                  async with session.post(
                      kick_url, headers=api_headers, json=kick_body,
                      timeout=aiohttp.ClientTimeout(total=10)
                  ) as resp:
                      resp_data = await resp.json()
                      if resp.status not in (200, 204):
                          err_msg = resp_data.get("message") or resp_data.get("error") or str(resp_data)
                          return await interaction.followup.send(
                              f"❌ Roblox API error: `{err_msg}`",
                              ephemeral=True
                          )
              except Exception as e:
                  return await interaction.followup.send(
                      f"❌ Failed to contact Roblox API: `{e}`",
                      ephemeral=True
                  )
          else:
              try:
                  async with session.patch(
                      url, headers=api_headers, json=payload,
                      timeout=aiohttp.ClientTimeout(total=10)
                  ) as resp:
                      resp_data = await resp.json()
                      if resp.status not in (200, 204):
                          err_msg = resp_data.get("message") or resp_data.get("error") or str(resp_data)
                          return await interaction.followup.send(
                              f"❌ Roblox API error: `{err_msg}`",
                              ephemeral=True
                          )
              except Exception as e:
                  return await interaction.followup.send(
                      f"❌ Failed to contact Roblox API: `{e}`",
                      ephemeral=True
                  )

          # ── GROUP CHECK ────────────────────────────────
          NEXTECH_GROUPS = {
              11146129: "F.T.D NexTech Development",
              35272985: "Northgate County California",
              6419702: "NexTech Development Game Administrator",
          }

          group_lines = []
          try:
              async with session.get(
                  f"https://groups.roblox.com/v1/users/{user_id}/groups/roles",
                  timeout=aiohttp.ClientTimeout(total=10)
              ) as gr:
                  if gr.status == 200:
                      gr_data = await gr.json()
                      for entry in gr_data.get("data", []):
                          gid = entry.get("group", {}).get("id")
                          if gid in NEXTECH_GROUPS:
                              gname = NEXTECH_GROUPS[gid]
                              role = entry.get("role", {}).get("name", "Unknown")
                              group_lines.append(f"**{gname}** — {role}")
          except Exception:
              pass

      # ── UI LABELS ────────────────────────────────
      action_labels = {
          "ban": "🔨 Banned",
          "kick": "👢 Kicked",
          "unban": "✅ Unbanned",
          "termination": "⚠ Recorded Termination"
      }

      action_colors = {
          "ban": discord.Color.red(),
          "kick": discord.Color.orange(),
          "unban": discord.Color.green(),
          "termination": discord.Color.dark_red()
      }

      embed = discord.Embed(
          title=f"{action_labels[act]} — {display_name}",
          color=action_colors[act]
      )

      embed.add_field(name="Roblox Username", value=f"`{username}`", inline=True)
      embed.add_field(name="Roblox User ID", value=f"`{user_id}`", inline=True)
      embed.add_field(name="Action", value=action.name, inline=True)
      embed.add_field(name="Reason", value=reason, inline=False)
      embed.add_field(name="Profile", value=f"[View Profile]({profile_url})", inline=True)
      embed.add_field(name="Moderator", value=f"{interaction.user.mention} (`{interaction.user}`)", inline=True)

      if group_lines:
          embed.add_field(name="NexTech Group Memberships", value="\n".join(group_lines), inline=False)
      else:
          embed.add_field(name="NexTech Group Memberships", value="Not in any monitored groups", inline=False)

      embed.set_footer(text=f"Universe ID: {ROBLOX_UNIVERSE_ID} · NexTech Moderation")
      embed.timestamp = discord.utils.utcnow()

      log_ch = interaction.guild.get_channel(MOD_LOG_CHANNEL_ID) if interaction.guild else None
      if log_ch:
          await log_ch.send(embed=embed)

      await interaction.followup.send(
          f"✅ Done — check <#{MOD_LOG_CHANNEL_ID}> for the full log.",
          ephemeral=True
      )

      save_mod_log({
          "ts": discord.utils.utcnow().timestamp(),
          "ts_str": discord.utils.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
          "action": act,
          "roblox_username": username,
          "roblox_display_name": display_name,
          "roblox_user_id": user_id,
          "profile_url": profile_url,
          "reason": reason,
          "moderator": str(interaction.user),
          "moderator_id": str(interaction.user.id),
          "universe_id": ROBLOX_UNIVERSE_ID,
      })
      record_bot_action(
          "roblox_moderation",
          details=reason,
          action=act,
          roblox_username=username,
          roblox_display_name=display_name,
          roblox_user_id=user_id,
          profile_url=profile_url,
          moderator=str(interaction.user),
          moderator_id=str(interaction.user.id),
          guild_id=str(interaction.guild.id) if interaction.guild else None,
          universe_id=ROBLOX_UNIVERSE_ID,
      )


  # -----------------------------
  # /mod-logs command
  # -----------------------------
@tree.command(name='mod-logs', description='View recent moderation actions taken via /mod-action')
@app_commands.describe(limit="How many recent entries to show (default 10, max 25)")
async def mod_logs(interaction: discord.Interaction, limit: int = 10):
      if not has_full_access(interaction.user) and not any(r.id == MOD_ROLE_ID for r in interaction.user.roles):
          return await interaction.response.send_message(
              "❌ You do not have permission to use this command.", ephemeral=True
          )

      limit = max(1, min(limit, 25))
      logs = load_mod_logs()

      if not logs:
          return await interaction.response.send_message(
              "📋 No moderation actions have been logged yet.", ephemeral=True
          )

      recent = logs[-limit:][::-1]

      action_icons = {"ban": "🔨", "kick": "👢", "unban": "✅"}
      lines = []
      for e in recent:
          icon = action_icons.get(e.get("action", ""), "❓")
          profile = e.get("profile_url")
          profile_str = f" · [Profile]({profile})" if profile else ""
          group_str = f" · Group `{e.get('group_id')}`" if e.get("group_id") else ""
          lines.append(
              f"{icon} **{e.get('action','?').upper()}** — `{e.get('roblox_username','?')}` "
              f"(ID: `{e.get('roblox_user_id','?')}`){profile_str}{group_str}\n"
              f"  📝 {e.get('reason','—')}  ·  👮 {e.get('moderator','?')}  ·  🕐 {e.get('ts_str','?')}"
          )

      embed = discord.Embed(
          title=f"🗒️ Mod Log — Last {len(recent)} action{'s' if len(recent)!=1 else ''}",
          description="\n\n".join(lines),
          color=discord.Color.blurple()
      )
      embed.set_footer(text=f"Total logged actions: {len(logs)} · Universe ID: {ROBLOX_UNIVERSE_ID}")
      await interaction.response.send_message(embed=embed, ephemeral=True)


# -----------------------------
# Hub Link helpers
# -----------------------------

def _md5_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.md5(data).digest()).decode()


def _ds_base(universe_id: str) -> str:
    return f"https://apis.roblox.com/datastores/v1/universes/{universe_id}/standard-datastores/datastore/entries/entry"


async def _ds_read(session: aiohttp.ClientSession, store: str, key: str):
    """Read a DataStore entry via Open Cloud. Returns the parsed JSON value, or None if missing/error."""
    async with session.get(
        _ds_base(HUB_UNIVERSE_ID),
        params={"datastoreName": store, "entryKey": key},
        headers={"x-api-key": HUB_CLOUD_API_KEY},
        timeout=aiohttp.ClientTimeout(total=10),
    ) as r:
        if r.status == 404:
            return None
        if r.status != 200:
            return None
        text = await r.text()
        try:
            return json.loads(text)
        except Exception:
            return None


async def _poll_for_code(session: aiohttp.ClientSession, store: str, key: str, max_wait: float = 20.0, interval: float = 2.0):
    """Poll DataStore until the key appears or max_wait is reached. Returns the value (dict) or None."""
    start = time.time()
    while time.time() - start < max_wait:
        value = await _ds_read(session, store, key)
        if value is not None:
            return value
        await asyncio.sleep(interval)
    return None


async def _ds_write(session: aiohttp.ClientSession, store: str, key: str, value: dict) -> bool:
    """Write a JSON value to a DataStore entry via Open Cloud."""
    import logging
    logger = logging.getLogger("bot")

    body_bytes = json.dumps(value).encode()
    url = _ds_base(HUB_UNIVERSE_ID)

    params = {
        "datastoreName": store,
        "entryKey": key
    }

    headers = {
        "x-api-key": HUB_CLOUD_API_KEY,
        "Content-Type": "application/json",
        "content-md5": _md5_b64(body_bytes),
    }

    logger.info("[DS_WRITE] store=%s key=%s url=%s", store, key, url)
    logger.info("[DS_WRITE] params=%s headers_md5=%s", params, headers["content-md5"])

    async with session.post(
        url,
        params=params,
        headers=headers,
        data=body_bytes,
        timeout=aiohttp.ClientTimeout(total=10),
    ) as r:
        body = await r.text()
        logger.info("[DS_WRITE] status=%s body=%s", r.status, body[:200])
        return r.status in (200, 201)


async def _ds_delete(session: aiohttp.ClientSession, store: str, key: str) -> bool:
    """Delete a DataStore entry (clean up used code)."""
    async with session.delete(
        _ds_base(HUB_UNIVERSE_ID),
        params={"datastoreName": store, "entryKey": key},
        headers={"x-api-key": HUB_CLOUD_API_KEY},
        timeout=aiohttp.ClientTimeout(total=10),
    ) as r:
        return r.status in (200, 204)


async def _publish_link_verified(session: aiohttp.ClientSession, roblox_user_id: int, code: str, discord_id: str) -> bool:
    """Publish to MessagingService 'LinkVerified' — triggers teleport in game."""
    message_str = json.dumps({
        "userId": roblox_user_id,
        "code": code,
        "discordId": discord_id
    })

    body = json.dumps({"message": message_str}).encode()

    async with session.post(
        f"https://apis.roblox.com/messaging-service/v1/universes/{HUB_UNIVERSE_ID}/topics/LinkVerified",
        headers={
            "x-api-key": HUB_CLOUD_API_KEY,
            "Content-Type": "application/json"
        },
        data=body,
        timeout=aiohttp.ClientTimeout(total=10),
    ) as r:
        return r.status in (200, 204)


async def _ds_read_entry(session: aiohttp.ClientSession, store: str, key: str) -> dict | None:
    """Read a single DataStore entry by key."""
    try:
        async with session.get(
            _ds_base(HUB_UNIVERSE_ID),
            params={"datastoreName": store, "entryKey": key},
            headers={"x-api-key": HUB_CLOUD_API_KEY},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status == 200:
                text = await r.text()
                return json.loads(text)
    except Exception:
        pass
    return None


async def _get_roblox_username(session: aiohttp.ClientSession, user_id: int) -> str:
    """Resolve a Roblox user ID to their display name."""
    try:
        async with session.get(
            f"https://users.roblox.com/v1/users/{user_id}",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status == 200:
                data = await r.json()
                return data.get("displayName") or data.get("name", str(user_id))
    except Exception:
        pass
    return str(user_id)


async def _publish_account_unlinked(session: aiohttp.ClientSession, roblox_user_id: int, discord_id: str) -> bool:
    """Notify Roblox game that a user has been unlinked."""
    message_str = json.dumps({
        "userId": roblox_user_id,
        "discordId": discord_id
    })

    body = json.dumps({"message": message_str}).encode()

    async with session.post(
        f"https://apis.roblox.com/messaging-service/v1/universes/{HUB_UNIVERSE_ID}/topics/AccountUnlinked",
        headers={
            "x-api-key": HUB_CLOUD_API_KEY,
            "Content-Type": "application/json"
        },
        data=body,
        timeout=aiohttp.ClientTimeout(total=10),
    ) as r:
        return r.status in (200, 204)


# -----------------------------
# 🔒 PROTECTED ROBLOX STAFF CHECK (NEW)
# -----------------------------

async def _is_protected_roblox_staff(
    session: aiohttp.ClientSession,
    user_id: int
) -> bool:
    """
    Returns True if the user is in the protected Roblox group
    and rank is 210 or higher.
    """
    try:
        async with session.get(
            f"https://groups.roblox.com/v2/users/{user_id}/groups/roles",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:

            if r.status != 200:
                return False

            data = await r.json()
            return _has_protected_roblox_group_role(data)

    except Exception:
        pass

    return False


def _has_protected_roblox_group_role(data) -> bool:
    for entry in data.get("data", []) if isinstance(data, dict) else []:
        if not isinstance(entry, dict):
            continue
        group = entry.get("group")
        role = entry.get("role")
        if not isinstance(group, dict) or not isinstance(role, dict):
            continue
        try:
            group_id = int(group.get("id", 0))
            rank = int(role.get("rank", 0))
        except (TypeError, ValueError):
            continue
        if group_id == PROTECTED_GROUP_ID and rank >= PROTECTED_ROBLOX_RANK:
            return True
    return False


def _is_protected_roblox_staff_sync(user_id: str) -> bool:
    user_id = str(user_id)
    if not user_id.isascii() or not user_id.isdecimal():
        return False
    now = time.time()
    with _dashboard_profile_code_lock:
        cached = _protected_roblox_role_cache.get(user_id)
        if cached and now - cached["checked_at"] < _PROTECTED_ROLE_CACHE_TTL:
            return cached["allowed"]

    allowed = False
    try:
        response = http_requests.get(
            f"https://groups.roblox.com/v2/users/{user_id}/groups/roles",
            timeout=8,
        )
        response.raise_for_status()
        allowed = _has_protected_roblox_group_role(response.json())
    except Exception:
        app.logger.exception("Could not verify protected Roblox group role for user %s", user_id)

    with _dashboard_profile_code_lock:
        _protected_roblox_role_cache[user_id] = {
            "checked_at": now,
            "allowed": allowed,
        }
    return allowed


  # ════════════════════════════════════════════════════════════════
  # /hub-link COMMAND
  # ════════════════════════════════════════════════════════════════
@tree.command(name='hub-link', description='Enter the code shown in the Roblox Hub game to link your Discord account')
@app_commands.describe(code="The 9-character code shown to you inside the Roblox Hub game")
async def hub_link(interaction: discord.Interaction, code: str):
      await interaction.response.defer(ephemeral=True)

      code = code.strip().upper()

      if not HUB_CLOUD_API_KEY or not HUB_UNIVERSE_ID:
          return await interaction.followup.send(
              "❌ Bot is not fully configured for hub linking. Contact the developer.", ephemeral=True
          )

      if len(code) != 9 or not code.isalnum():
          return await interaction.followup.send(
              "❌ That doesn't look like a valid code. It should be **9 characters** "
              "(letters and numbers), exactly as shown in the Roblox Hub game.",
              ephemeral=True,
          )

      async with aiohttp.ClientSession() as session:

          # ── 1. LOOK UP CODE IN DATASTORE (poll up to 20 sec) ───────────
          pending = await _poll_for_code(session, PENDING_STORE_NAME, code)

          if pending is None:
              hub_url = (
                  f"https://www.roblox.com/games/{HUB_PLACE_ID}" if HUB_PLACE_ID
                  else "https://www.roblox.com"
              )
              return await interaction.followup.send(
                  f"❌ Code **`{code}`** not found.\n\n"
                  f"Make sure you:\n"
                  f"1. [Joined the Hub game]({hub_url})\n"
                  f"2. Opened the **Menu** → **Verify Discord**\n"
                  f"3. Copied the code **exactly** as shown\n\n"
                  f"Codes expire after **{CODE_EXPIRY_SECS // 60} minutes** — "
                  f"if yours expired, re-open the verify screen for a fresh code.",
                  ephemeral=True,
              )

          # ── 2. CHECK EXPIRY ─────────────────────────────────────────────
          issued_at = pending.get("timestamp", 0)
          if issued_at and (time.time() - issued_at) > CODE_EXPIRY_SECS:
              await _ds_delete(session, PENDING_STORE_NAME, code)
              return await interaction.followup.send(
                  f"❌ That code has **expired** (codes last {CODE_EXPIRY_SECS // 60} minutes).\n"
                  f"Go back into the Hub game and open **Verify Discord** to get a fresh code.",
                  ephemeral=True,
              )

          roblox_user_id = pending.get("userId") or pending.get("robloxUserId")
          if not roblox_user_id:
              return await interaction.followup.send(
                  "❌ The code data is invalid. Please get a new code from the Hub game.",
                  ephemeral=True,
              )

          roblox_user_id = int(roblox_user_id)
          display_name   = await _get_roblox_username(session, roblox_user_id)
          profile_url    = f"https://www.roblox.com/users/{roblox_user_id}/profile"

          # ── 3. PUBLISH INSTANT NOTIFICATION ─────────────────────────────
          await _publish_link_verified(session, roblox_user_id, code, str(interaction.user.id))

          # ── 4. CLEAN UP USED CODE ────────────────────────────────────────
          await _ds_delete(session, PENDING_STORE_NAME, code)

      # ── 5. SUCCESS REPLY ─────────────────────────────────────────────────
      embed = discord.Embed(
          title="✅ Account Linked!",
          description=(
              f"Your Discord is now linked to **{display_name}** on Roblox.\n"
              f"Head back to the Hub — you'll be teleported to the main game automatically."
          ),
          color=discord.Color.green(),
      )
      embed.add_field(name="Roblox Account", value=f"[{display_name}]({profile_url})", inline=True)
      embed.add_field(name="Roblox User ID", value=f"`{roblox_user_id}`", inline=True)
      embed.set_footer(text="NexTech Interactive Development · Hub Link")
      embed.timestamp = discord.utils.utcnow()
      await interaction.followup.send(embed=embed, ephemeral=True)

      record_bot_action(
          "account_linked",
          details=f"{interaction.user} linked Roblox account {display_name}",
          discord_id=str(interaction.user.id),
          roblox_id=str(roblox_user_id),
          roblox_username=display_name,
          actor_id=str(interaction.user.id),
          actor=str(interaction.user),
      )

      # ── 6. LOG ───────────────────────────────────────────────────────────
      if LINK_LOG_CHANNEL_ID and interaction.guild:
          log_ch = interaction.guild.get_channel(LINK_LOG_CHANNEL_ID)
          if log_ch:
              log = discord.Embed(
                  title="🔗 Account Linked",
                  color=discord.Color.green(),
              )
              log.add_field(
                  name="Discord",
                  value=f"{interaction.user.mention} (`{interaction.user}`)",
                  inline=True,
              )
              log.add_field(
                  name="Roblox",
                  value=f"[{display_name}]({profile_url}) (`{roblox_user_id}`)",
                  inline=True,
              )
              log.set_footer(text=f"Universe: {HUB_UNIVERSE_ID} · NexTech")
              log.timestamp = discord.utils.utcnow()
              await log_ch.send(embed=log)


  # ════════════════════════════════════════════════════════════════
  # /check-link COMMAND
  # ════════════════════════════════════════════════════════════════
@tree.command(name='check-link', description='Check if your Discord is linked to a Roblox account')
async def check_link(interaction: discord.Interaction):
      await interaction.response.defer(ephemeral=True)
      discord_id = str(interaction.user.id)

      async with aiohttp.ClientSession() as session:
          # Try to read from DiscordIndex
          index_data = await _ds_read_entry(session, "DiscordIndex", discord_id)

          if index_data and index_data.get("robloxUserId"):
              roblox_user_id = int(index_data["robloxUserId"])
              display_name = await _get_roblox_username(session, roblox_user_id)
              profile_url = f"https://www.roblox.com/users/{roblox_user_id}/profile"

              embed = discord.Embed(
                  title="✅ You Are Linked",
                  description=f"Your Discord is linked to **{display_name}** on Roblox.",
                  color=discord.Color.green(),
              )
              embed.add_field(name="Roblox Account", value=f"[{display_name}]({profile_url})", inline=True)
              embed.add_field(name="Roblox User ID", value=f"`{roblox_user_id}`", inline=True)
              embed.set_footer(text="NexTech Interactive Development · Hub Link")
              embed.timestamp = discord.utils.utcnow()
              await interaction.followup.send(embed=embed, ephemeral=True)
          else:
              embed = discord.Embed(
                  title="❌ Not Linked",
                  description=(
                      "Your Discord is **not** linked to any Roblox account.\n\n"
                      "Join the [Hub game](https://www.roblox.com/games/" + str(HUB_PLACE_ID) + ") "
                      "and open **Verify Discord** to get a linking code."
                  ),
                  color=discord.Color.red(),
              )
              embed.set_footer(text="NexTech Interactive Development · Hub Link")
              embed.timestamp = discord.utils.utcnow()
              await interaction.followup.send(embed=embed, ephemeral=True)


def _create_dashboard_profile_code(discord_id: str):
    now = time.time()
    with _dashboard_profile_code_lock:
        # Keep the in-memory registry synchronized with codes created by another
        # process before writing this process's updated registry to disk.
        disk_codes = _load_dashboard_profile_codes_from_disk()
        if isinstance(disk_codes, dict):
            _dashboard_profile_codes.update(disk_codes)
        for user_id, issued_at in list(_dashboard_profile_code_issuance.items()):
            if now - issued_at >= _DASHBOARD_PROFILE_CODE_ISSUE_COOLDOWN:
                _dashboard_profile_code_issuance.pop(user_id, None)
        for digest, entry in list(_dashboard_profile_codes.items()):
            if now - entry["issued_at"] >= _DASHBOARD_PROFILE_CODE_TTL:
                _dashboard_profile_codes.pop(digest, None)
        previous_issue = _dashboard_profile_code_issuance.get(discord_id, 0)
        if now - previous_issue < _DASHBOARD_PROFILE_CODE_ISSUE_COOLDOWN:
            return None
        for digest, entry in list(_dashboard_profile_codes.items()):
            if entry["discord_id"] == discord_id:
                _dashboard_profile_codes.pop(digest, None)

        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        while True:
            code = "".join(secrets.choice(alphabet) for _ in range(8))
            digest = hashlib.sha256(code.encode("ascii")).hexdigest()
            if digest not in _dashboard_profile_codes:
                break
        _dashboard_profile_codes[digest] = {
            "discord_id": discord_id,
            "issued_at": now,
        }
        if not _save_dashboard_profile_codes_to_disk():
            _dashboard_profile_codes.pop(digest, None)
            return None
        _dashboard_profile_code_issuance[discord_id] = now
        return code


@tree.command(name='profile', description='Check a Hub profile or create a dashboard sign-in code')
@app_commands.describe(member="The Discord user whose Hub link you want to check (leave blank for your sign-in code)")
async def profile(
    interaction: discord.Interaction,
    member: discord.Member | None = None,
):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message(
            "❌ This command can only be used in the server.", ephemeral=True
        )
    if member is None:
        if not HUB_CLOUD_API_KEY or not HUB_UNIVERSE_ID:
            return await interaction.response.send_message(
                "❌ Hub linking is not configured. Contact the developer.", ephemeral=True
            )
        code = _create_dashboard_profile_code(str(interaction.user.id))
        if not code:
            return await interaction.response.send_message(
                "Please wait a minute before generating another dashboard code.",
                ephemeral=True,
            )
        return await interaction.response.send_message(
            f"Your dashboard sign-in code is `{code}`. It expires in 5 minutes and can only be used once. "
            "Enter it on the dashboard sign-in screen; do not share it.",
            ephemeral=True,
        )
    if not _is_staff(interaction.user):
        return await interaction.response.send_message(
            "❌ Only staff members can use `/profile`.", ephemeral=True
        )
    if not HUB_CLOUD_API_KEY or not HUB_UNIVERSE_ID:
        return await interaction.response.send_message(
            "❌ Hub linking is not configured. Contact the developer.", ephemeral=True
        )

    user_warnings = [
        entry for entry in load_warnings()
        if str(entry.get("user_id")) == str(member.id)
    ]
    bot_warning_count = len(user_warnings)
    warning_points = sum(int(entry.get("points", 1)) for entry in user_warnings)

    await interaction.response.defer(ephemeral=True)
    async with aiohttp.ClientSession() as session:
        index_data = await _ds_read_entry(session, DISCORD_INDEX_NAME, str(member.id))

        if not index_data or not index_data.get("robloxUserId"):
            embed = discord.Embed(
                title="🔎 Roblox Hub Profile",
                description=f"{member.mention} is **not linked** to a Roblox Hub account.",
                color=discord.Color.orange(),
            )
            embed.add_field(name="Discord User", value=f"{member} (`{member.id}`)", inline=False)
            embed.add_field(name="Hub Status", value="❌ Not linked", inline=True)
            embed.add_field(name="Bot Warnings", value=str(bot_warning_count), inline=True)
            embed.add_field(
                name="Warning Points",
                value=f"{warning_points}/{WARNING_POINT_LIMIT}",
                inline=True,
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text="NexTech Interactive Development · Staff Profile Check")
            return await interaction.followup.send(embed=embed, ephemeral=True)

        try:
            roblox_user_id = int(index_data["robloxUserId"])
        except (TypeError, ValueError):
            return await interaction.followup.send(
                "❌ The stored Hub link contains an invalid Roblox user ID.", ephemeral=True
            )
        display_name = await _get_roblox_username(session, roblox_user_id)

    profile_url = f"https://www.roblox.com/users/{roblox_user_id}/profile"
    embed = discord.Embed(
        title="🔎 Roblox Hub Profile",
        description=f"{member.mention} is **linked** to a Roblox Hub account.",
        color=discord.Color.green(),
    )
    embed.add_field(name="Discord User", value=f"{member} (`{member.id}`)", inline=False)
    embed.add_field(name="Hub Status", value="✅ Linked", inline=True)
    embed.add_field(name="Roblox Account", value=f"[{display_name}]({profile_url})", inline=True)
    embed.add_field(name="Roblox User ID", value=f"`{roblox_user_id}`", inline=True)
    embed.add_field(name="Bot Warnings", value=str(bot_warning_count), inline=True)
    embed.add_field(
        name="Warning Points",
        value=f"{warning_points}/{WARNING_POINT_LIMIT}",
        inline=True,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="NexTech Interactive Development · Staff Profile Check")
    embed.timestamp = discord.utils.utcnow()
    await interaction.followup.send(embed=embed, ephemeral=True)


  # ════════════════════════════════════════════════════════════════
  # /unlink COMMAND
  # ════════════════════════════════════════════════════════════════
@tree.command(name='unlink',description='Unlink a Discord account from its Roblox account')
@app_commands.describe(member="Leave blank to unlink yourself")
async def unlink(
  interaction: discord.Interaction,
  member: discord.Member | None = None
):
  await interaction.response.defer(ephemeral=True)

  target = member or interaction.user
  discord_id = str(target.id)

  if not HUB_CLOUD_API_KEY or not HUB_UNIVERSE_ID:
      return await interaction.followup.send(
          "❌ Bot is not fully configured for hub linking. Contact the developer.",
          ephemeral=True
      )

  async with aiohttp.ClientSession() as session:

      index_data = await _ds_read_entry(session, DISCORD_INDEX_NAME, discord_id)

      if not index_data or not index_data.get("robloxUserId"):
          return await interaction.followup.send(
              f"❌ {target.mention} is not linked to any Roblox account.",
              ephemeral=True
          )

      roblox_user_id = int(index_data["robloxUserId"])
      roblox_user_id_str = str(roblox_user_id)

      # Can executor bypass protection?
      can_override = any(
          r.id == STAFF_OVERRIDE_ROLE_ID
          for r in interaction.user.roles
      )

      # Is target protected in Discord?
      discord_staff = any(
          r.id == PROTECTED_DISCORD_ROLE_ID
          for r in target.roles
      )

      # Is target protected in Roblox?
      roblox_staff = await _is_protected_roblox_staff(
          session,
          roblox_user_id
      )

      if (discord_staff or roblox_staff) and not can_override:
          return await interaction.followup.send(
              "❌ This account belongs to a protected staff member.\n\n"
              "Sorry, this account cannot be unlinked by members.\n"
              "Please contact a developer or support if you need assistance.\n\n"
              "Thank you.",
              ephemeral=True
          )

      # Delete both entries
      await _ds_delete(session, DISCORD_INDEX_NAME, discord_id)
      await _ds_delete(session, LINKED_STORE_NAME, roblox_user_id_str)

      # Notify the game server
      await _publish_account_unlinked(
          session,
          roblox_user_id,
          discord_id
      )

  embed = discord.Embed(
      title="🔗 Account Unlinked",
      description=f"{target.mention}'s Discord account has been unlinked from its Roblox account.",
      color=discord.Color.orange(),
  )

  embed.add_field(
      name="Roblox User ID",
      value=f"`{roblox_user_id}`",
      inline=True
  )

  embed.add_field(
      name="Performed By",
      value=interaction.user.mention,
      inline=True
  )

  embed.set_footer(text="NexTech Interactive Development · Hub Link")
  embed.timestamp = discord.utils.utcnow()

  await interaction.followup.send(embed=embed, ephemeral=True)

  if LINK_LOG_CHANNEL_ID and interaction.guild:
      log_ch = interaction.guild.get_channel(LINK_LOG_CHANNEL_ID)

      if log_ch:
          log = discord.Embed(
              title="🔗 Account Unlinked",
              color=discord.Color.orange(),
          )

          log.add_field(
              name="Discord",
              value=f"{target.mention} (`{target}`)",
              inline=True
          )

          log.add_field(
              name="Roblox User ID",
              value=f"`{roblox_user_id}`",
              inline=True
          )

          log.add_field(
              name="Performed By",
              value=interaction.user.mention,
              inline=True
          )

          log.timestamp = discord.utils.utcnow()

          await log_ch.send(embed=log)



# -----------------------------
# /vc-join and /vc-leave commands
# -----------------------------
@tree.command(name='vc-join', description='Make the bot join a selected voice channel')
@app_commands.describe(channel="Voice channel to join; leave blank to join your current channel")
async def vc_join(interaction: discord.Interaction, channel: Optional[discord.VoiceChannel] = None):
    if not has_full_access(interaction.user) and not any(r.id == MOD_ROLE_ID for r in interaction.user.roles):
        return await interaction.response.send_message(
            "❌ You do not have permission to use this command.", ephemeral=True
        )
    if interaction.guild is None:
        return await interaction.response.send_message("❌ Use this command in a server.", ephemeral=True)
    if channel is None:
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message(
                "❌ Select a voice channel or join one yourself first.", ephemeral=True
            )
        channel = interaction.user.voice.channel
    if channel.guild.id != interaction.guild.id:
        return await interaction.response.send_message("❌ Select a channel in this server.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        # Set the override before moving so the watchdog doesn't immediately
        # drag the bot back to the main channel.
        _TEMP_VC_OVERRIDE_GUILDS.add(interaction.guild.id)
        vc = interaction.guild.voice_client
        if vc and vc.is_connected():
            await vc.move_to(channel)
        else:
            if vc:
                try:
                    await vc.disconnect(force=True)
                except Exception:
                    pass
            await channel.connect()
        await interaction.followup.send(
            f"🔊 Joined **{channel.name}** and will stay there until disconnected. "
            "If the bot is kicked/disconnected, it will return to its main voice channel.", ephemeral=True
        )
    except Exception as e:
        print(f"[VC-JOIN ERROR] {e}")
        await interaction.followup.send(
            f"❌ Failed to join the voice channel: `{e}`", ephemeral=True
        )


@tree.command(name='vc-leave', description='Make the bot leave the voice channel')
async def vc_leave(interaction: discord.Interaction):
    if not has_full_access(interaction.user) and not any(r.id == MOD_ROLE_ID for r in interaction.user.roles):
        return await interaction.response.send_message(
            "❌ You do not have permission to use this command.", ephemeral=True
        )
    if not interaction.guild.voice_client:
        return await interaction.response.send_message(
            "❌ I'm not currently in a voice channel.", ephemeral=True
        )
    await interaction.response.defer(ephemeral=True)
    vc = interaction.guild.voice_client
    channel_name = vc.channel.name
    try:
        goodbye_text = "I am leaving the voice channel. Have a good day, from the NexTech Interactive Development LLC team."
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp_path = tmp.name
        tmp.close()
        communicate = edge_tts.Communicate(goodbye_text, voice="en-US-AriaNeural")
        await communicate.save(tmp_path)
        done = asyncio.Event()
        def _after(e):
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            done.set()
        if vc.is_playing():
            vc.stop()
            await asyncio.sleep(0.3)
        source = await discord.FFmpegOpusAudio.from_probe(tmp_path)
        vc.play(source, after=_after)
        await asyncio.wait_for(done.wait(), timeout=15)
        await vc.disconnect()
        _TEMP_VC_OVERRIDE_GUILDS.discard(interaction.guild.id)
        main_channel = client.get_channel(AUTO_JOIN_VC_ID)
        if isinstance(main_channel, discord.VoiceChannel):
            try:
                await main_channel.connect(timeout=20, reconnect=False)
                await interaction.followup.send(f"🔇 Left **{channel_name}** and returned to **{main_channel.name}**.", ephemeral=True)
                return
            except Exception as reconnect_error:
                print(f"[VC-LEAVE RETURN ERROR] {reconnect_error}")
        await interaction.followup.send(f"🔇 Left **{channel_name}**. The bot will try to return to its main voice channel.", ephemeral=True)
    except Exception as e:
        print(f"[VC-LEAVE ERROR] {e}")
        try:
            await vc.disconnect()
        except Exception:
            pass
        _TEMP_VC_OVERRIDE_GUILDS.discard(interaction.guild.id)
        await interaction.followup.send(f"🔇 Left **{channel_name}**. The bot will try to return to its main voice channel.", ephemeral=True)


# -----------------------------
# /vc-move command
# -----------------------------
@tree.command(name='vc-move', description='Move a member to the bot\'s current voice channel')
@app_commands.describe(member="The member to move")
async def vc_move(interaction: discord.Interaction, member: discord.Member):
    if not has_full_access(interaction.user) and not any(r.id == MOD_ROLE_ID for r in interaction.user.roles):
        return await interaction.response.send_message(
            "❌ You do not have permission to use this command.", ephemeral=True
        )
    if not interaction.guild.voice_client or not interaction.guild.voice_client.is_connected():
        return await interaction.response.send_message(
            "❌ I'm not in a voice channel. Use `/vc-join` first.", ephemeral=True
        )
    if not member.voice or not member.voice.channel:
        return await interaction.response.send_message(
            f"❌ **{member.display_name}** is not in any voice channel.", ephemeral=True
        )
    target_channel = interaction.guild.voice_client.channel
    if member.voice.channel.id == target_channel.id:
        return await interaction.response.send_message(
            f"❌ **{member.display_name}** is already in **{target_channel.name}**.", ephemeral=True
        )
    await interaction.response.defer(ephemeral=True)
    try:
        await member.move_to(target_channel)
        await interaction.followup.send(
            f"✅ Moved **{member.display_name}** to **{target_channel.name}**.", ephemeral=True
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ I don't have permission to move that member.", ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Failed: `{e}`", ephemeral=True)


# -----------------------------
# /transfer-ticket command
# -----------------------------
@tree.command(name='transfer-ticket', description='Transfer a ticket to another staff member')
@app_commands.describe(staff_member="Select the staff member to transfer this ticket to")
async def transfer_ticket(interaction: discord.Interaction, staff_member: discord.Member):

    # Permission check
    if not has_full_access(interaction.user) and AGENT_STAFF_ROLE_ID not in [role.id for role in interaction.user.roles]:
        return await interaction.response.send_message(
            "⚠️ You do not have permission to transfer tickets.", ephemeral=True
        )

    channel = interaction.channel

    # Ensure it's a ticket channel
    if not isinstance(channel, discord.TextChannel) or not channel.name.startswith("ticket-"):
        return await interaction.response.send_message(
            "⚠️ This command can only be used in a ticket channel.", ephemeral=True
        )

    # Extract user + case ID
    topic = channel.topic or ""
    user_mention = None
    case_id = "Unknown"

    if "User:" in topic:
        user_mention = topic.split("User:")[1].split("|")[0].strip()

    if "Case ID:" in topic:
        case_id = topic.split("Case ID:")[1].split("|")[0].strip()

    # -----------------------------
    # GRAMMAR-FIXED TEXT (BLUE PANEL)
    # -----------------------------
    transfer_embed = discord.Embed(
        title="🔄 Ticket Transferred",
        description=(
            "Please wait while the newly assigned support agent reviews your previous messages and case details. "
            "You will receive a response shortly.\n\n"
            "Due to increased demand, it may take longer than usual for us to respond. "
            "We appreciate your patience and understanding during these extended wait times.\n\n"
            "**Estimated Wait Time:** 24–72 hours\n"
            "We are currently experiencing a high volume of inquiries."
        ),
        color=discord.Color.blue()
    )

    # Send message + ping new staff
    await channel.send(
        content=f"{staff_member.mention}",
        embed=transfer_embed
    )

    # DM the user
    if user_mention:
        try:
            user_id = int(user_mention.strip("<@!>"))
            user = await interaction.guild.fetch_member(user_id)

            await user.send(
                f"🔄 Your ticket (Case ID: {case_id}) has been transferred.\n"
                f"👤 New Agent: {staff_member.display_name}\n\n"
                "Please allow some time for your new agent to review your case."
            )
        except:
            pass

    # Confirmation
    await interaction.response.send_message(
        f"✅ Ticket successfully transferred to {staff_member.mention}",
        ephemeral=True
    )

# -----------------------------
# /join-ticket command
# -----------------------------
@tree.command(name='join-ticket', description='Staff join a ticket and notify the user')
async def join_ticket(interaction: discord.Interaction):
    if not has_full_access(interaction.user) and AGENT_STAFF_ROLE_ID not in [role.id for role in interaction.user.roles]:
        return await interaction.response.send_message("⚠️ You do not have permission to join tickets.", ephemeral=True)

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel) or not channel.name.startswith("ticket-"):
        return await interaction.response.send_message("⚠️ This command can only be used in a ticket channel.", ephemeral=True)

    topic = channel.topic or ""
    user_mention = None
    if "User:" in topic:
        user_mention = topic.split("User:")[1].split("|")[0].strip()

    if user_mention:
        try:
            user_id = int(user_mention.strip("<@!>"))
            user = await interaction.guild.fetch_member(user_id)
            await user.send(f"👋 {interaction.user} has joined your ticket {channel.mention}. They will assist you.")
            await channel.send(f"{user_mention} 👋 A staff member ({interaction.user.mention}) has joined this ticket.")
        except:
            pass

    await interaction.response.send_message(f"✅ You have joined the ticket: {channel.mention}", ephemeral=True)























@tree.command(name='staff-notes', description='Submit staff notes')
async def staff_notes(interaction: discord.Interaction):
    # Check if the user has the required role
    user_roles = [role.id for role in interaction.user.roles]
    if not has_full_access(interaction.user) and 1268998581000601651 not in user_roles:
        return await interaction.response.send_message(
            "You do not have the required role to use this command.", ephemeral=True
        )

    # Welcome message
    await interaction.user.send("Welcome to N.T.D | NexTech Staff Notes Submission!")
    await interaction.user.send("Please enter your Discord user (ping it):")

    # Wait for Discord user input
    def check(message):
        return message.author == interaction.user and message.guild is None

    try:
        user_response = await client.wait_for('message', check=check, timeout=300)
        discord_user = user_response.content
    except asyncio.TimeoutError:
        return await interaction.user.send("Timeout. Please start the process again.")

    # Ask for photo of the issue
    await interaction.user.send("Please provide a photo/screenshot of the issue (attach it here):")
    try:
        photo_response = await client.wait_for('message', check=check, timeout=300)
        photo_link = photo_response.attachments[0].url if photo_response.attachments else "No photo provided"
    except asyncio.TimeoutError:
        return await interaction.user.send("Timeout. Please start the process again.")

    # Ask for the mod's Discord user
    await interaction.user.send("Please provide the mod's Discord user:")
    try:
        mod_response = await client.wait_for('message', check=check, timeout=300)
        mod_user = mod_response.content
    except asyncio.TimeoutError:
        return await interaction.user.send("Timeout. Please start the process again.")

    # Ask for Roblox username
    await interaction.user.send("Please provide the user's Roblox username:")
    try:
        roblox_response = await client.wait_for('message', check=check, timeout=300)
        roblox_user = roblox_response.content
    except asyncio.TimeoutError:
        return await interaction.user.send("Timeout. Please start the process again.")

    # Ask for additional notes
    await interaction.user.send("Please provide any additional notes or comments:")
    try:
        notes_response = await client.wait_for('message', check=check, timeout=300)
        additional_notes = notes_response.content
    except asyncio.TimeoutError:
        return await interaction.user.send("Timeout. Please start the process again.")

    # Format and store the data
    notes_info = (
        f"Discord User: {discord_user}\n"
        f"Photo Link: {photo_link}\n"
        f"Mod User: {mod_user}\n"
        f"Roblox User: {roblox_user}\n"
        f"Additional Notes: {additional_notes}"
    )

    # Send the notes to a specific channel with a red embed
    alert_channel = client.get_channel(1258557815144845472)  # Replace with your actual channel ID
    if alert_channel:
        embed = discord.Embed(
            title="🚨 Staff Notes Alert! 🚨",
            description=notes_info,
            color=discord.Color.red()
        )
        embed.set_footer(text=f"Submitted by: {interaction.user.name}")
        await alert_channel.send(embed=embed)
        await interaction.user.send("Staff notes submitted successfully!")
    else:
        await interaction.user.send("Error: Could not find the alert channel. Please contact an admin.")





# Replace 'RoleID' with the actual ID of the role
ROLE_ID = 1258549984429871171 # Example role ID

@tree.command(name='version-update', description='Create a version update')
async def version_update(interaction: discord.Interaction, title: str):
    member = interaction.user
    if member is None:
        await interaction.response.send_message("User not found. Please try again later.", ephemeral=True)
        return

    # Check if the user has the required role
    if not has_full_access(interaction.user) and not any(role.id == 1258549984429871171 for role in member.roles):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    # Send an initial response to acknowledge the command
    await interaction.response.send_message("Processing your request. Please check your DMs for further instructions.", ephemeral=True)

    try:
        # DM the user to gather additional information
        await member.send(f'Title received: {title}\nWhat do you want to post?')
        await member.send("Please provide the body of the update:")

        def check(message):
            return message.author == member and message.guild is None

        # Get the body of the update
        try:
            body_message = await interaction.client.wait_for('message', check=check, timeout=300)
        except asyncio.TimeoutError:
            await member.send("Timeout. Please try again later.")
            return

        await member.send("Please provide the channel ID:")

        # Get the channel ID
        try:
            channel_id_message = await interaction.client.wait_for('message', check=check, timeout=300)
            channel_id = int(channel_id_message.content)
        except asyncio.TimeoutError:
            await member.send("Timeout. Please try again later.")
            return
        except ValueError:
            await member.send("Invalid channel ID. Please enter a valid number.")
            return

        # Get the channel and send the update
        channel = interaction.client.get_channel(channel_id)
        if channel is None:
            await member.send(f"Channel with ID {channel_id} not found.")
            return

        # Create the embed and send it to the channel
        embed = discord.Embed(title=title, description=body_message.content, color=discord.Color.blue())
        embed.set_footer(text="NTD Staff Team")

        message = await channel.send(embed=embed)

        # Inform the user via DM
        await member.send(f"Update posted: {message.jump_url}")

    except discord.Forbidden:
        await interaction.followup.send('I cannot send you a DM. Please check your privacy settings.', ephemeral=True)
    except discord.HTTPException as e:
        print(f"An error occurred: {e}")
        await interaction.followup.send('An unexpected error occurred. Please try again later.', ephemeral=True)








# Your existing commands with the custom embed
@tree.command(name='beta-test', description='testing thinking')
@app_commands.checks.cooldown(1, 60)
async def beta_command(interaction: discord.Interaction, name: str):
    # Check if the initial response has been done
    if not interaction.response.is_done():
        # Defer the initial response
        await interaction.response.defer()
        await asyncio.sleep(2)

    # Send a follow-up message with the specified text
    await interaction.followup.send('Done Thinking! run /help for more info')
    # Create the response embed
    embed = create_custom_embed("Test Result", f"I am working! {name}")

    # Send a follow-up message with the embed
    await interaction.followup.send(embed=embed, ephemeral=True)






class AnswerButton(discord.ui.View):
    def __init__(self, case_name, case_id, question, user, staff_role_id, log_channel_id):
        super().__init__(timeout=None)  # Remove timeout
        self.case_name = case_name
        self.case_id = case_id
        self.question = question
        self.user = user
        self.staff_role_id = staff_role_id
        self.log_channel_id = log_channel_id

    @discord.ui.button(label="Answer to question", style=discord.ButtonStyle.green)
    async def answer_button_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_full_access(interaction.user) and self.staff_role_id not in [role.id for role in interaction.user.roles]:
            await interaction.response.send_message("You do not have permission to answer this question.", ephemeral=True)
            return

        answer_modal = discord.ui.Modal(title="Answer the Question")
        answer_input = discord.ui.TextInput(
            label="Your Answer",
            style=discord.TextStyle.paragraph,
            placeholder="Enter your answer...",
            required=True,
        )
        answer_modal.add_item(answer_input)

        async def answer_modal_callback(answer_interaction: discord.Interaction):
            answer = answer_input.value
            answer_user = answer_interaction.user

            dm_embed = discord.Embed(
                title="Staff Replied to Your Q-A",
                description=f"**Case Name:** {self.case_name}\n**Case ID:** {self.case_id}\n**Question:** {self.question}\n**Answer:** {answer}",
                color=discord.Color.blue()
            )
            await self.user.send(embed=dm_embed)
            await answer_interaction.response.send_message(f"Question answered by {answer_user.name}#{answer_user.discriminator}", ephemeral=True)

            log_embed = discord.Embed(
                title="Question Answered",
                description=f"**Asked by:** {self.user.name}#{self.user.discriminator}\n**Answered by:** {answer_user.name}#{answer_user.discriminator}\n**Case Name:** {self.case_name}\n**Case ID:** {self.case_id}\n**Question:** {self.question}\n**Answer:** {answer}",
                color=discord.Color.green()
            )
            log_channel = interaction.client.get_channel(self.log_channel_id)
            await log_channel.send(embed=log_embed)

        answer_modal.on_submit = answer_modal_callback
        await interaction.response.send_modal(answer_modal)

@tree.command(name='question', description='Ask a question')
async def question_command(interaction: discord.Interaction):
    modal = discord.ui.Modal(title="Need help?")

    case_name_input = discord.ui.TextInput(
        label="Case Name",
        style=discord.TextStyle.short,
        placeholder="Enter the case name...",
        required=True,
    )
    case_id_input = discord.ui.TextInput(
        label="Case ID",
        style=discord.TextStyle.short,
        placeholder="Enter the case ID...",
        required=True,
    )
    question_input = discord.ui.TextInput(
        label="What is your question?",
        style=discord.TextStyle.paragraph,
        placeholder="Enter your question...",
        required=True,
    )

    modal.add_item(case_name_input)
    modal.add_item(case_id_input)
    modal.add_item(question_input)

    async def modal_callback(modal_interaction: discord.Interaction):
        case_name = case_name_input.value
        case_id = case_id_input.value
        question = question_input.value
        user = modal_interaction.user

        embed = discord.Embed(
            title="Help Request",
            description=f"**Asked by:** {user.name}#{user.discriminator}\n**Case Name:** {case_name}\n**Case ID:** {case_id}\n**Question:** {question}",
            color=discord.Color.yellow()
        )

        view = AnswerButton(case_name, case_id, question, user, STAFF_ROLE_ID, TARGET_CHANNEL_ID)

        log_channel = interaction.client.get_channel(TARGET_CHANNEL_ID)
        await log_channel.send(embed=embed, view=view)

        user_embed = discord.Embed(
            title="Your Help Request",
            description=f"**Case Name:** {case_name}\n**Case ID:** {case_id}\n**Question:** {question}",
            color=discord.Color.blue()
        )
        user_embed.set_footer(text="We will reply to you soon. We will not leave your question unanswered also you can check our support page for more Q-As that is relatable to your requst of Help!")
        await modal_interaction.user.send(embed=user_embed)
        await modal_interaction.response.send_message(f"Help request submitted by {modal_interaction.user.name}", ephemeral=True)

    modal.on_submit = modal_callback

    TARGET_CHANNEL_ID = 1258557816998854812 # Replace with your channel ID
    STAFF_ROLE_ID =   1261391359647154187   # Replace with your staff role ID

    await interaction.response.send_modal(modal)




COOKIE = os.getenv("ROBLOX_COOKIE")
GROUP_ID = 11146129

headers = {"User-Agent": "Mozilla/5.0"}


# ---------------- ROBLOX ----------------

async def fetch_all():
    cookies = {".ROBLOSECURITY": COOKIE}

    async with httpx.AsyncClient(cookies=cookies, headers=headers) as client:

        group = (await client.get(f"https://groups.roblox.com/v1/groups/{GROUP_ID}")).json()
        roles = (await client.get(f"https://groups.roblox.com/v1/groups/{GROUP_ID}/roles")).json()["roles"]
        user = (await client.get("https://users.roblox.com/v1/users/authenticated")).json()
        funds = (await client.get(f"https://economy.roblox.com/v1/groups/{GROUP_ID}/currency")).json().get("robux", 0)

        return group, roles, user, funds


# ---------------- PAGINATION ----------------

def chunk_roles(roles, size=5):
    for i in range(0, len(roles), size):
        yield roles[i:i + size]


# ---------------- VIEW ----------------

class DashboardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.page = 0
        self.mode = "info"
        self.roles_pages = []
        self.data = None

    # ---------------- DROPDOWN ----------------

    @discord.ui.select(
        placeholder="Select Dashboard View",
        options=[
            discord.SelectOption(label="Group Info", emoji="📊"),
            discord.SelectOption(label="Group Funds", emoji="💰"),
            discord.SelectOption(label="Roles", emoji="👥"),
            discord.SelectOption(label="Viewing Account", emoji="👤"),
        ]
    )
    async def select(self, interaction: discord.Interaction, select: discord.ui.Select):

        self.mode = select.values[0]
        self.page = 0

        await self.update(interaction)

    # ---------------- BUTTONS ----------------

    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.gray)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        await self.update(interaction)

    @discord.ui.button(label="➡️", style=discord.ButtonStyle.gray)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < len(self.roles_pages) - 1:
            self.page += 1
        await self.update(interaction)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.green, emoji="🔁")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update(interaction, force_refresh=True)

    # ---------------- UPDATE ----------------

    async def update(self, interaction: discord.Interaction, force_refresh=False):

        if force_refresh or self.data is None:
            self.data = await fetch_all()

            group, roles, user, funds = self.data

            self.roles_pages = list(chunk_roles(roles, 5))

        group, roles, user, funds = self.data

        embed = discord.Embed(
            title="Roblox Group Dashboard",
            color=discord.Color.green()
        )

        # ---------------- INFO ----------------
        if self.mode == "Group Info":

            embed.add_field(name="📊 Group Name", value=group["name"], inline=False)
            embed.add_field(name="👑 Owner", value=group["owner"]["username"], inline=False)
            embed.add_field(name="👥 Members", value=f"{group['memberCount']:,}", inline=False)

        # ---------------- FUNDS ----------------
        elif self.mode == "Group Funds":

            embed.add_field(name="💰 Funds", value=f"{funds:,} Robux", inline=False)
            embed.add_field(name="📦 Pending", value="Unavailable (Roblox API limitation)", inline=False)

        # ---------------- ROLES (PAGED) ----------------
        elif self.mode == "Roles":

            page_roles = self.roles_pages[self.page]

            text = ""
            for r in page_roles:
                text += f"**{r['name']}** (Rank {r['rank']}) — {r.get('memberCount', 0)} members\n"

            embed.add_field(
                name=f"👥 Roles Page {self.page + 1}/{len(self.roles_pages)}",
                value=text,
                inline=False
            )

        # ---------------- ACCOUNT ----------------
        elif self.mode == "Viewing Account":

            embed.add_field(name="👤 Username", value=user["name"], inline=False)
            embed.add_field(name="Display Name", value=user["displayName"], inline=False)
            embed.add_field(name="User ID", value=user["id"], inline=False)

        embed.set_footer(text="Live Roblox Dashboard • Refresh anytime")

        await interaction.response.edit_message(embed=embed, view=self)


# ---------------- COMMAND ----------------

@tree.command(name="group-dashboard", description="Roblox group dashboard (dropdown + pagination)")
async def groupdashboard(interaction: discord.Interaction):

    await interaction.response.defer(ephemeral=True)

    if not COOKIE:
        await interaction.followup.send("Missing ROBLOX_COOKIE", ephemeral=True)
        return

    view = DashboardView()

    group, roles, user, funds = await fetch_all()
    view.data = (group, roles, user, funds)
    view.roles_pages = list(chunk_roles(roles, 5))

    embed = discord.Embed(
        title="Roblox Group Dashboard",
        description="Use the dropdown to view data",
        color=discord.Color.green()
    )

    await interaction.followup.send(embed=embed, view=view, ephemeral=True)









# ================== CONFIG ==================
GROUP_IDS = [11146129, 6419702, 35272985]

GROUP_LINKS = {
    11146129: "F.T.D NexTech Development",
    6419702: "NexTech Development Game Administrator",
    35272985: "Northgate County California",
}

VERIFIED_ROLE_ID = 1258549984387665941
SECONDARY_ROLE_ID = 1311257043348492298
LOG_CHANNEL_ID = 1440249563293286410

BIO_CODE = "whitelistmember"
GAME_CODE_API_URL = "https://nex-tech-bot--develpoer0023.replit.app"  # Replit API URL

# ================== MEMORY STORAGE ==================
roblox_links = {}       # Discord user linked to Roblox ID
game_codes_used = {}    # Tracks used game codes {roblox_user_id: discord_id}

# ================== ROBLOX API FUNCTIONS ==================
async def get_roblox_user_profile(user_id):
    url = f"https://users.roblox.com/v1/users/{user_id}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                return {"username": data["name"], "bio": data.get("description", "")}
    return None

async def get_user_groups(user_id):
    url = f"https://groups.roblox.com/v1/users/{user_id}/groups/roles"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                return [g["group"]["id"] for g in data.get("data", [])]
    return []

async def verify_game_code(user_id, code):
    """Checks with API if the code is valid for this Roblox user."""
    async with aiohttp.ClientSession() as session:
        try:
            url = f"{GAME_CODE_API_URL}/verify_code/{user_id}/{code}"
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("success", False)
        except:
            return False
    return False

# ================== MODALS ==================
class RobloxBioModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Roblox Bio Verification")
        self.roblox_profile_id = discord.ui.TextInput(
            label="Roblox User ID",
            placeholder="Enter your Roblox User ID",
            required=True,
            max_length=20
        )
        self.add_item(self.roblox_profile_id)

    async def on_submit(self, interaction: discord.Interaction):
        roblox_profile_id = self.roblox_profile_id.value.strip()
        await interaction.response.defer(ephemeral=True)

        if roblox_profile_id in roblox_links:
            await interaction.followup.send(
                "⚠️ This Roblox account is already linked to another Discord user.",
                ephemeral=True
            )
            return

        profile = await get_roblox_user_profile(roblox_profile_id)
        if not profile:
            await interaction.followup.send("❌ Could not fetch Roblox account.", ephemeral=True)
            return

        if BIO_CODE.lower() not in profile["bio"].lower():
            await interaction.followup.send(
                f"❌ Verification Failed. Please add `{BIO_CODE}` to your Roblox bio and try again.",
                ephemeral=True
            )
            return

        user_groups = await get_user_groups(roblox_profile_id)
        matched_groups = [gid for gid in GROUP_IDS if gid in user_groups]
        if not matched_groups:
            await interaction.followup.send(
                "❌ You must join one of the required NexTech groups before verifying.",
                ephemeral=True
            )
            return

        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            try:
                member = await interaction.guild.fetch_member(interaction.user.id)
            except:
                await interaction.followup.send(
                    "❌ Could not find your Discord profile in this server.",
                    ephemeral=True
                )
                return

        primary_role = interaction.guild.get_role(VERIFIED_ROLE_ID)
        secondary_role = interaction.guild.get_role(SECONDARY_ROLE_ID)
        roles_given = []

        try:
            if primary_role:
                await member.add_roles(primary_role)
                roles_given.append(primary_role)
            if secondary_role:
                await member.add_roles(secondary_role)
                roles_given.append(secondary_role)
        except:
            pass

        try:
            await member.edit(nick=profile["username"])
        except:
            pass

        roblox_links[roblox_profile_id] = interaction.user.id

        _record_roblox_username(str(interaction.user.id), profile["username"])

        role_mentions = ", ".join([r.mention for r in roles_given]) if roles_given else "None"
        group_names = [GROUP_LINKS[gid] for gid in matched_groups]
        timestamp = datetime.utcnow().strftime("%B %d, %Y %I:%M %p UTC")

        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(
                f"**Verification Log**\n"
                f"Discord User: {interaction.user.mention} ({interaction.user.id})\n"
                f"Roblox User ID: {roblox_profile_id}\n"
                f"Roblox Username: {profile['username']}\n"
                f"Roles Assigned: {role_mentions}\n"
                f"Groups User is In: {', '.join(group_names)}\n"
                f"Verification Method: BIO\n"
                f"Verification Status: SUCCESSFUL\n"
                f"Timestamp: {timestamp}"
            )

        base = _get_base_url()
        portal_login_url = f"{base}/auth/roblox?next=/portal"

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="Login to Customer Portal",
            style=discord.ButtonStyle.link,
            url=portal_login_url,
            emoji="🌐"
        ))

        await interaction.followup.send(
            f"✅ **Verification Successful! You now have the @Whitelisted Group Member role.**\n\n"
            f"🎮 Roblox Username: **{profile['username']}**\n"
            f"🏷️ Roles Given: {role_mentions}\n"
            f"✔️ Method: Bio Verification\n\n"
            f"**You can now log in to the NexTech Customer Portal** using your Discord account. "
            f"Click the button below or run `/portal-login` anytime.",
            view=view,
            ephemeral=True
        )
# ================== SLASH COMMAND ==================
@tree.command(name="roblox-group", description="Verify Roblox group membership.")
async def roblox_group(interaction: discord.Interaction):

    if not interaction.guild:
        await interaction.response.send_message(
            "⚠️ You must run this command inside a server.",
            ephemeral=True
        )
        return

    group_urls = {
        11146129: "https://www.roblox.com/groups/11146129/F-T-D-NexTech-Development",
        6419702: "https://www.roblox.com/groups/6419702/NexTech-Development-Game-Administrator",
        35272985: "https://www.roblox.com/groups/35272985/Northgate-County-California",
    }

    group_text = ""
    for gid in GROUP_IDS:
        name = GROUP_LINKS[gid]
        url = group_urls.get(gid, "")
        group_text += f"• [{name}]({url})\n"

    embed = discord.Embed(
        title="NexTech Roblox Verification",
        description=(
            "**Choose a verification method below.**\n\n"

            "## 1️⃣ Bio Verification\n"
            "Add the following code to your Roblox profile's **About Me** section:\n"
            f"```{BIO_CODE}```\n"
            "After saving your profile, click **Verify with Bio**.\n\n"

            "## 2️⃣ Game Code Verification\n"
            "Generate a verification code in the NexTech verification game and enter it when prompted.\n\n"

            "**Required Groups**\n"
            f"{group_text}"
        ),
        color=discord.Color.blue()
    )

    embed.add_field(
        name="Bio Code",
        value=f"```{BIO_CODE}```",
        inline=False
    )

    embed.add_field(
        name="Verification Steps",
        value=(
            "1. Join one of the required groups.\n"
            "2. Add the Bio Code to your Roblox profile.\n"
            "3. Click **Verify with Bio**.\n"
            "4. Enter your Roblox User ID.\n"
            "5. Submit the form.\n"
            "6. Receive your Discord roles automatically."
        ),
        inline=False
    )

    embed.set_footer(
        text="NexTech Interactive Development • Roblox Verification System"
    )

    view = discord.ui.View(timeout=None)

    # Bio Verification Button
    bio_button = discord.ui.Button(
        label="Verify with Bio",
        emoji="📝",
        style=discord.ButtonStyle.green
    )

    async def bio_callback(i: discord.Interaction):
        await i.response.send_modal(RobloxBioModal())

    bio_button.callback = bio_callback
    view.add_item(bio_button)

    # Help Button
    help_button = discord.ui.Button(
        label="Help / Verification Guide",
        emoji="📖",
        style=discord.ButtonStyle.link,
        url="https://futures-organization-1.gitbook.io/nextechs-development-support/nextech-utility-bot/supported-cases/group-verification"
    )
    view.add_item(help_button)

    await interaction.response.send_message(
        embed=embed,
        view=view,
        ephemeral=True
    )
















# Global error handler
@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"❗ You are on cooldown. Please wait `{int(error.retry_after)} seconds` before using this command again.",
            ephemeral=True
        )
    else:
        try:
            embed = discord.Embed(
                title="❌ Server or Hub is Down",
                description=(
                    "We are sorry! Our systems must be down or offline for maintenance.\n"
                    "Please check back later today or within 24 hours.\n\n"
                    "We thank you for your support.\n\n– NTD Support Team"
                ),
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except:
            pass




# ------------------------------
# Role IDs
# ------------------------------
PING_ROLE_ID = 1268998581000601651
EXEMPT_ROLE_ID = 1268998581000601651
PING_BLACKLISTED_ID = 1258549984387665949
SECOND_BLACKLIST_ID = 1412682029216174100
LOG_CHANNEL_ID = 1258549985738363007

# ------------------------------
# SYSTEM VARIABLES
# ------------------------------
HI_TRIGGERS = ["hi"]
SUPPORT_TRIGGERS = ["support"]

user_warnings = {}
recent_pings = {}
ghost_ping_cache = {}

anti_raid_mode = True

SERVER_INVITE = "https://discord.gg/KZzZKWkmWZ"
NEW_ACCOUNT_AGE_LIMIT = 3

# ------------------------------
# TYPING HELPERS
# ------------------------------
async def reply_with_typing(message, content=None, embed=None):
    try:
        async with message.channel.typing():
            await asyncio.sleep(1)
        if content:
            await message.reply(content, mention_author=True)
        elif embed:
            await message.reply(embed=embed, mention_author=True)
    except:
        pass

async def send_with_typing(destination, content=None, embed=None):
    try:
        async with destination.typing():
            await asyncio.sleep(1)
        if content:
            await destination.send(content)
        elif embed:
            await destination.send(embed=embed)
    except:
        pass

# ------------------------------
# WARNING EMBED
# ------------------------------
def create_warning_embed(user_name, description, footer_text):
    embed = discord.Embed(
        title="⚠️⚠️ BOT WARNING ⚠️⚠️",
        description=f"{user_name}, you have received a verbal warning.",
        color=discord.Color.red()
    )
    embed.add_field(name="Reason", value=description, inline=False)
    embed.set_footer(text=footer_text)
    return embed

# ------------------------------
# BOT PING TRIGGER SYSTEM
# ------------------------------
async def handle_bot_ping_triggers(message, member):
    if client.user not in message.mentions:
        return

    content = message.content.lower()

    mention1 = f"<@{client.user.id}>"
    mention2 = f"<@!{client.user.id}>"

    content = content.replace(mention1, "").replace(mention2, "").strip()

    if content in HI_TRIGGERS:
        embed = discord.Embed(
            title="👋 Greetings!",
            description=f"Hi {member.mention}! How can NexTech assist you today?",
            color=discord.Color.blue()
        )
        embed.set_footer(text="Made by NexTech Development 👋")
        await reply_with_typing(message, embed=embed)

    elif content in SUPPORT_TRIGGERS:
        embed = discord.Embed(
            title="📩 NexTech Support",
            description=(
                "Need help? Visit our support center below:\n\n"
                "📖 https://futures-organization-1.gitbook.io/nextechs-development-support\n\n"
                "🔗 Ban Appeal Center:\n"
                "https://www.roblox.com/games/75888659690482/NexTech-Ban-Appeal-Center\n\n"
                "📧 Email: nextechdevassist@gmail.com"
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text="NexTech Development Support")
        await reply_with_typing(message, embed=embed)

# ------------------------------
# MESSAGE EVENT
# ------------------------------
@client.event
async def on_message(message):

    if message.author.bot or message.guild is None:
        return

    asyncio.create_task(mirror_public_discord_message(message))
    guild = message.guild
    member = message.author

    ping_role = guild.get_role(PING_ROLE_ID)
    exempt_role = guild.get_role(EXEMPT_ROLE_ID)
    ping_blacklist_role = guild.get_role(PING_BLACKLISTED_ID)
    second_blacklist_role = guild.get_role(SECOND_BLACKLIST_ID)
    log_channel = client.get_channel(LOG_CHANNEL_ID)

    await handle_bot_ping_triggers(message, member)

    # ------------------------------
    # 🚨 MASS PING = PERMA BAN
    # ------------------------------
    if (
        message.mention_everyone
        or len(message.mentions) >= 5
        or len(message.role_mentions) >= 3
    ):
        if exempt_role not in member.roles:
            try:
                await message.delete()
                await member.ban(reason="Mass Ping Detected")
                if log_channel:
                    await log_channel.send(f"🚨 {member.mention} permanently banned for MASS PING.")
            except:
                pass
            return

    # ------------------------------
    # STAFF PING WARNING SYSTEM
    # ------------------------------
    if ping_role:

        staff_pinged = False

        if ping_role in message.role_mentions:
            staff_pinged = True

        for m in message.mentions:
            if isinstance(m, discord.Member):
                if ping_role in m.roles:
                    staff_pinged = True
                    break

        is_exempt = exempt_role in member.roles if exempt_role else False

        if staff_pinged and not is_exempt:

            ghost_ping_cache[message.id] = member.id

            try:
                await message.delete()
            except:
                pass

            user_id = member.id
            now = time.time()

            if user_id not in recent_pings:
                recent_pings[user_id] = []

            recent_pings[user_id].append(now)

            SPAM_LIMIT = 3
            SPAM_INTERVAL = 15
            recent_pings[user_id] = [t for t in recent_pings[user_id] if now - t <= SPAM_INTERVAL]

            if len(recent_pings[user_id]) >= SPAM_LIMIT:
                try:
                    await member.timeout(timedelta(minutes=10))
                except:
                    pass
                recent_pings[user_id] = []
                return

            previous = user_warnings.get(user_id, 0)
            warnings_count = previous + 1
            user_warnings[user_id] = warnings_count

            embed = create_warning_embed(
                member.display_name,
                (
                    "⚠️ Do not ping staff members directly. "
                    "They read the chat. Warning is still active to all users. "
                    "Read the rules, ask questions, and have fun! "
                    "Just don't ping by @user or by replying; when replying turn off @! "
                    "Use /case-support command to open a ticket. "
                    "⚠️ Warning is still active if you are staff and you are pinged "
                    "or a role is sending this warning to you reach out to NexTech Developers via ping @Developer support specialist. "
                    "⚠️ Thank you!"
                ),
                "Made by NexTech Development 👋"
            )
            await send_with_typing(message.channel, embed=embed)

            if warnings_count >= 4:
                if ping_blacklist_role:
                    await member.add_roles(ping_blacklist_role)
                if second_blacklist_role:
                    await member.add_roles(second_blacklist_role)

            if warnings_count > 10:
                try:
                    await member.ban(reason="Exceeded warning limit")
                except:
                    pass

# ------------------------------
# GHOST PING DETECTION
# ------------------------------
@client.event
async def on_message_delete(message):

    if message.id in ghost_ping_cache:
        guild = message.guild
        member = guild.get_member(ghost_ping_cache[message.id])
        log_channel = client.get_channel(LOG_CHANNEL_ID)

        try:
            await member.ban(reason="Ghost Ping Detected")
            if log_channel:
                await log_channel.send(f"👻 {member.mention} permanently banned for GHOST PING.")
        except:
            pass

        del ghost_ping_cache[message.id]






































ALLOWED_ROLE_ID = 1295298122951426092  # Replace with your actual role ID

class LogsSetupView(discord.ui.View):
    def __init__(self):
        super().__init__()

    @discord.ui.button(label="Logging", style=discord.ButtonStyle.primary)
    async def setup_logging(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.setup_channels(interaction, ["bot-case", "other-case", "action-notes", "bot-warnings", "bot-q-a", "mod-case"], "Logging")

    @discord.ui.button(label="Channels", style=discord.ButtonStyle.primary)
    async def setup_channels_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.setup_channels(interaction, ["general", "off-topic", "announcements"], "Channels")

    @discord.ui.button(label="Overwrites/Edit", style=discord.ButtonStyle.secondary)
    async def overwrite_edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Prompt the user to input channel information for editing
        modal = OverwriteEditModal()
        await interaction.response.send_modal(modal)

    async def setup_channels(self, interaction: discord.Interaction, channel_names: list, category_name: str):
        guild = interaction.guild
        existing_channels = [channel.name for channel in guild.channels]

        if any(channel_name in existing_channels for channel_name in channel_names):
            embed = create_custom_embed(
                "Error",
                f"Sorry, I can't create channels because one or more of the specified channels already exist in {category_name}."
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Create roles with all permissions and admin permissions
        all_perms_role = await guild.create_role(name="All Permissions", permissions=discord.Permissions.all())
        admin_role = await guild.create_role(name="Admin", permissions=discord.Permissions(administrator=True))

        category = await guild.create_category(category_name)
        created_channels = []
        for channel_name in channel_names:
            text_channel = await guild.create_text_channel(channel_name, category=category)
            created_channels.append(text_channel)

        # Return a summary of the setup
        channel_names_str = ', '.join([f"{channel.name} (ID: {channel.id})" for channel in created_channels])
        embed = create_custom_embed(
            "Setup Complete",
            f"Created roles '{all_perms_role.name}' and '{admin_role.name}', category '{category.name}' and channels:\n{channel_names_str}"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class OverwriteEditModal(Modal):
    def __init__(self):
        super().__init__(title="Overwrite/Edit Channel Info")

    channel_id = TextInput(label="Channel ID", placeholder="Enter Channel ID", required=True)
    category_id = TextInput(label="Category ID", placeholder="Enter Category ID", required=True)
    channel_name = TextInput(label="New Channel Name", placeholder="Enter New Channel Name", required=True)
    create_webhook = TextInput(label="Create Webhook? (yes/no)", placeholder="Enter 'yes' or 'no'", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        channel_id = self.channel_id.value
        category_id = self.category_id.value
        channel_name = self.channel_name.value
        create_webhook = self.create_webhook.value.lower()

        # Validate if the IDs are valid (optional)
        guild = interaction.guild
        channel = discord.utils.get(guild.channels, id=int(channel_id))
        category = discord.utils.get(guild.categories, id=int(category_id))

        if not channel or not category:
            embed = create_custom_embed(
                "Error",
                "Invalid channel ID or category ID. Please check the IDs provided."
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Overwrite/edit the channel name
        await channel.edit(name=channel_name)

        embed = create_custom_embed(
            "Success",
            f"Channel {channel_name} (ID: {channel.id}) has been updated successfully."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

        # Create a webhook if needed
        if create_webhook == 'yes':
            webhook = await channel.create_webhook(name=f"{channel_name} Webhook")
            webhook_link = webhook.url

            webhook_info = create_custom_embed(
                "Webhook Created",
                f"A webhook has been created for {channel_name}. You can use the following link:\n{webhook_link}"
            )
            await interaction.followup.send(embed=webhook_info, ephemeral=True)

def create_custom_embed(title: str, description: str) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=discord.Color.blue())
    return embed

@tree.command(name='logs', description='Set up logging channels')
@app_commands.checks.cooldown(1, 10)  # 1 use per 10 seconds
async def logs(interaction: discord.Interaction):
    # Check if the user has the allowed role
    user_roles = [role.id for role in interaction.user.roles]
    if not has_full_access(interaction.user) and ALLOWED_ROLE_ID not in user_roles:
        embed = create_custom_embed(
            "Permission Denied",
            "You do not have the required role to use this command."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return



































# Replace with your actual role and channel IDs
STAFF_ROLE_ID = 1268998581000601651
BLACKLIST_ROLE_ID = 1258549984387665949
LOG_CHANNEL_ID = 1273569868096213022
@tree.command(name='blacklist', description='Ping user to issue blacklist')
async def blacklist(interaction: discord.Interaction, user: discord.Member, reason: str):
    staff_role = discord.utils.get(interaction.guild.roles, id=STAFF_ROLE_ID)

    if staff_role in interaction.user.roles:
        blacklist_role = discord.utils.get(interaction.guild.roles, id=BLACKLIST_ROLE_ID)

        if blacklist_role:
            await user.add_roles(blacklist_role)
            appeal_link = "https://futures-organization-1.gitbook.io/nextechs-development-support/how-do-i-make-a-ticket-inquiry/appeal-to-be-removed-from-blacklist-or-similar-matters"

            # Create the embed for the user notification
            embed = discord.Embed(
                title="Blacklist Notification",
                description=f"You have been blacklisted by a staff member for the following reason:\n\n"
                            f"**Reason:** {reason}\n"
                            f"Check our page Appeal to be Removed from Blacklist or similar matters({appeal_link}).",
                color=discord.Color.red()
            )
            await user.send(embed=embed)

            # Log the blacklist action
            log_channel = client.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                log_message = (
                    f"⛔ **Bot issued Blacklist** ⛔\n\n"
                    f"**User Blacklisted**: {user.mention}\n"
                    f"**Reason**: {reason}\n"
                    f"**Issued by**: {interaction.user.mention}\n"
                )

                log_embed = discord.Embed(
                    title="Bot Action Log",
                    description=log_message,
                    color=discord.Color.red()
                )
                await log_channel.send(embed=log_embed)
            else:
                await interaction.response.send_message("Log channel not found.", ephemeral=True)
        else:
            await interaction.response.send_message("Blacklist role not found.", ephemeral=True)
    else:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)


# Ensure product_submissions is a global dictionary
product_submissions = {}

# -----------------------------
# CONFIG
# -----------------------------
ADMIN_HUB_ROLE = 1258549984333135994
HUB_ROLE_1 = 1494073408512721089
HUB_ROLE_2 = 1311257043348492298
PRODUCT_LIST_CHANNEL = 1259535698265116813

@tree.command(name="hub-info", description="Submit product info for N.T.D | NexTech products.")
async def hub_info(interaction: discord.Interaction):
    global product_submissions

    # 🔒 STAFF ONLY CHECK
    if not has_full_access(interaction.user) and (not isinstance(interaction.user, discord.Member) or not any(role.id == ADMIN_HUB_ROLE for role in interaction.user.roles)):
        return await interaction.response.send_message(
            "❌ This command is for staff only.",
            ephemeral=True
        )

    await interaction.response.send_message(
        "Welcome to N.T.D | NexTech Product Notes Submission!",
        ephemeral=True
    )

    client = interaction.client

    def check(message):
        return message.author == interaction.user and message.guild is None

    # -----------------------------
    # PRODUCT ID
    # -----------------------------
    await interaction.user.send("🆔 Enter a Product ID (example: NTD-001):")

    try:
        id_response = await client.wait_for('message', check=check, timeout=300)
        product_id = id_response.content.strip()

        if not product_id:
            return await interaction.user.send("❌ Product ID cannot be empty.")

        if product_id in product_submissions:
            return await interaction.user.send("❌ This Product ID already exists. Use a different one.")

    except asyncio.TimeoutError:
        return await interaction.user.send("Timeout. Please start the process again.")

    # -----------------------------
    # PRICE
    # -----------------------------
    await interaction.user.send("💰 Enter product price (example: 1000 Robux or $5):")

    try:
        price_response = await client.wait_for('message', check=check, timeout=300)
        product_price = price_response.content.strip()

        if not product_price:
            return await interaction.user.send("❌ Price cannot be empty.")

    except asyncio.TimeoutError:
        return await interaction.user.send("Timeout. Please start the process again.")

    # -----------------------------
    # PHOTO
    # -----------------------------
    await interaction.user.send("Please provide a photo/screenshot of the product (attach it here):")

    try:
        photo_response = await client.wait_for('message', check=check, timeout=300)
        photo_link = photo_response.attachments[0].url if photo_response.attachments else None
    except asyncio.TimeoutError:
        return await interaction.user.send("Timeout. Please start the process again.")

    # -----------------------------
    # PRODUCT NAME
    # -----------------------------
    await interaction.user.send("Please provide the product's name:")

    try:
        product_name_response = await client.wait_for('message', check=check, timeout=300)
        product_name = product_name_response.content.strip()
    except asyncio.TimeoutError:
        return await interaction.user.send("Timeout. Please start the process again.")

    # -----------------------------
    # NOTES
    # -----------------------------
    await interaction.user.send("Please provide any additional notes or comments:")

    try:
        notes_response = await client.wait_for('message', check=check, timeout=300)
        additional_notes = notes_response.content.strip()
    except asyncio.TimeoutError:
        return await interaction.user.send("Timeout. Please start the process again.")

    # -----------------------------
    # STORE DATA
    # -----------------------------
    product_submissions[product_id] = {
        "product_name": product_name,
        "price": product_price,
        "additional_notes": additional_notes,
        "photo_link": photo_link
    }

    # -----------------------------
    # EMBED (SHOP STYLE)
    # -----------------------------
    embed = discord.Embed(
        title=f"🛒 {product_name}",
        description="Available now in the NexTech Shop",
        color=discord.Color.green()
    )

    embed.add_field(name="🆔 Product ID", value=product_id, inline=True)
    embed.add_field(name="💰 Price", value=product_price, inline=True)

    embed.add_field(
        name="📝 Details",
        value=additional_notes or "No details provided.",
        inline=False
    )

    embed.add_field(
        name="🔗 Access",
        value="[Join Hub](https://www.roblox.com/games/14242175066/NexTech-Development-Hub)\n"
              "[Group Link](https://www.roblox.com/share/g/11146129)",
        inline=False
    )

    if photo_link:
        embed.set_image(url=photo_link)

    embed.set_footer(text="NexTech Interactive Development LLC Team")

    # -----------------------------
    # SEND TO CHANNEL + ROLE PINGS
    # -----------------------------
    alert_channel = client.get_channel(PRODUCT_LIST_CHANNEL)

    if alert_channel:
        await alert_channel.send(
            content=f"📢 New Product Available!\n<@&{HUB_ROLE_1}> <@&{HUB_ROLE_2}>",
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=True)
        )

    # -----------------------------
    # CONFIRM USER
    # -----------------------------
    await interaction.user.send(
        f"✅ Product added successfully!\n🆔 Product ID: **{product_id}**"
    )




@tree.command(name='command-status', description='Check the command status')
async def roblox_group(interaction: discord.Interaction):
    embed = create_custom_embed("Check the command status Link", "Here's the link to our Check the command status:")
    embed.add_field(name="status/update", value="https://futures-organization-1.gitbook.io/nextechs-development-support/how-do-i-make-a-ticket-inquiry/update-logs")
    await interaction.response.send_message(embed=embed, ephemeral=True)




# Replace with your actual role and channel IDs
STAFF_ROLE_ID = 1261391359647154187
BLACKLIST_ROLE_ID = 1258549984387665949
LOG_CHANNEL_ID = 1273569868096213022

@tree.command(name='unblacklist', description='Remove the blacklist from a user')
async def unblacklist(interaction: discord.Interaction, user: discord.Member):
    staff_role = discord.utils.get(interaction.guild.roles, id=STAFF_ROLE_ID)

    if staff_role in interaction.user.roles:
        blacklist_role = discord.utils.get(interaction.guild.roles, id=BLACKLIST_ROLE_ID)

        if blacklist_role:
            await user.remove_roles(blacklist_role)
            appeal_link = "https://futures-organization-1.gitbook.io/nextechs-development-support/how-do-i-make-a-ticket-inquiry/appeal-to-be-removed-from-blacklist-or-similar-matters"

            # Create the embed for the user notification
            embed = discord.Embed(
                title="Blacklist Removal Notification",
                description=f"You have been removed from the blacklist by a staff member. If you believe this is in error or if you need further assistance, please visit our appeal page here: {appeal_link}.",
                color=discord.Color.green()
            )
            await user.send(embed=embed)

            # Log the unblacklist action
            log_channel = client.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                log_message = (
                    f"✅ **Bot issued Unblacklist** ✅\n\n"
                    f"**User Unblacklisted**: {user.mention}\n"
                    f"**Issued by**: {interaction.user.mention}\n"
                )

                log_embed = discord.Embed(
                    title="Bot Action Log",
                    description=log_message,
                    color=discord.Color.green()
                )
                await log_channel.send(embed=log_embed)
            else:
                await interaction.response.send_message("Log channel not found.", ephemeral=True)
        else:
            await interaction.response.send_message("Blacklist role not found.", ephemeral=True)
    else:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)

@tree.command(name='resources', description='Coming soon')
async def resources(interaction: discord.Interaction):
    embed = create_custom_embed("NexTech resources", "Sorry, I don't have a link to resources reach out to support team for more infomation Thank you!.")
    await interaction.response.send_message(embed=embed, ephemeral=True)




#--------------------------------------------------feedback/other----------------------------------------------------------------------------------------------

class FeedbackModal(discord.ui.Modal, title="Submit Feedback"):
    agent_name = discord.ui.TextInput(
        label="Agent Name",
        placeholder="Enter the agent's name",
        required=True,
        max_length=100
    )

    support_topic = discord.ui.TextInput(
        label="Support Topic",
        placeholder="Enter the topic of support",
        required=True,
        max_length=100
    )

    agent_actions = discord.ui.TextInput(
        label="What the Agent Did",
        style=discord.TextStyle.paragraph,
        placeholder="Describe what the agent did",
        required=True,
        max_length=1024
    )

    experience = discord.ui.TextInput(
        label="Your Experience",
        style=discord.TextStyle.paragraph,
        placeholder="Describe your experience",
        required=True,
        max_length=1024
    )

    rating = discord.ui.TextInput(
        label="Rating (1-5)",
        placeholder="Rate the support from 1 to 5",
        required=True,
        max_length=1
    )

    async def on_submit(self, interaction: discord.Interaction):
        feedback_channel = client.get_channel(1258549985079853133)  # Replace with your feedback channel ID

        if feedback_channel:
            embed = discord.Embed(
                title="Support Feedback",
                color=discord.Color.blue()
            )
            embed.add_field(name="Agent Name", value=self.agent_name.value, inline=False)
            embed.add_field(name="Support Topic", value=self.support_topic.value, inline=False)
            embed.add_field(name="What the Agent Did", value=self.agent_actions.value, inline=False)
            embed.add_field(name="Experience", value=self.experience.value, inline=False)
            embed.add_field(name="Rating", value=self.rating.value, inline=False)

            user_info = f"Feedback by: {interaction.user.name} (User ID: {interaction.user.id})"
            embed.add_field(name="User Info", value=user_info, inline=False)

            # Footer with a simple message
            embed.set_footer(text="Thank you for your feedback. If you have made a complaint, you can reach out to our team. Feedback helps us improve our support!   -NexTech staff")

            # Send the embed to the feedback channel
            message = await feedback_channel.send(embed=embed)

            # Add reactions for Like (👍) and Dislike (👎)
            await message.add_reaction("👍")
            await message.add_reaction("👎")

            await interaction.response.send_message("Your feedback has been submitted.", ephemeral=True)
        else:
            await interaction.response.send_message("Feedback channel not found. Please contact the bot owner.", ephemeral=True)

@tree.command(name='feedback', description='Submit feedback on the support you received')
async def feedback_command(interaction: discord.Interaction):
    modal = FeedbackModal()
    await interaction.response.send_modal(modal)


# Replace with your actual role membership and channel IDs
STAFF_ROLE_ID = 1261391359647154187
MEMBERSHIP_ROLE_ID = 1311257043348492298  # Replace with actual Membership Role ID
LOG_CHANNEL_ID = 1340181098604265472

# Command to assign membership role to a user
@tree.command(name='give-membership', description='Assign membership role to a user')
async def give_membership(interaction: discord.Interaction, user: discord.Member):
    if has_full_access(interaction.user) or STAFF_ROLE_ID in [role.id for role in interaction.user.roles]:
        membership_role = discord.utils.get(interaction.guild.roles, id=MEMBERSHIP_ROLE_ID)

        if membership_role:
            await user.add_roles(membership_role)

            embed = discord.Embed(
                title="Membership Role Assigned",
                description=(
                    "You have been given the membership role.\n\n"
                    "**What is Membership & How to make a ticket**\n"
                    "For tickets, use this link: [Ticket Information](https://futures-organization-1.gitbook.io/nextechs-development-support/how-do-i-make-a-ticket-inquiry/nextechs-utility-bot-infomataion/tickets)\n"
                    "For membership details, use this link: [What is Membership](https://futures-organization-1.gitbook.io/nextechs-development-support/how-do-i-make-a-ticket-inquiry/nextechs-utility-bot-infomataion/tickets/what-is-membership-at-ntd)"
                ),
                color=discord.Color.green()
            )
            try:
                await user.send(embed=embed)
            except discord.Forbidden:
                await interaction.followup.send(f"Could not DM {user.mention}, but role was assigned.")

            log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                log_message = (
                    f"✅ **Membership Role Assigned** ✅\n\n"
                    f"**User**: {user.mention}\n"
                    f"**Assigned by**: {interaction.user.mention}\n"
                )

                log_embed = discord.Embed(
                    title="Membership Role Assignment Log",
                    description=log_message,
                    color=discord.Color.green()
                )
                await log_channel.send(embed=log_embed)

            await interaction.response.send_message(f"Membership role assigned to {user.mention}.", ephemeral=True)
        else:
            await interaction.response.send_message("Membership role not found.", ephemeral=True)
    else:
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)


# Define the check_user_warnings function to check for warnings based on the User ID
async def check_user_warnings(interaction: discord.Interaction, user_id: int):
    # Fetch the channel where the warnings are logged
    log_channel = interaction.guild.get_channel(1258549985738363007)  # Update with the correct channel ID

    # Ensure that the bot has permission to read message history
    if not log_channel.permissions_for(interaction.guild.me).read_message_history:
        await interaction.response.send_message("I don't have permission to read the message history in the log channel.", ephemeral=True)
        return

    # Initialize a dictionary to store found warnings by year
    found_warnings = {}

    # Get the message history from the channel (increase limit if necessary)
    async for message in log_channel.history(limit=200):  # Adjust the limit as needed
        # Check if the message is an embed (Verbal Warning Log)
        if message.embeds:
            for embed in message.embeds:
                description = embed.description
                if description and "⛔ **Bot sent Verbal warning** ⛔" in description:
                    if f"<@{user_id}>" in description:  # Check if the user is mentioned in the warning
                        # Calculate the warning year (for grouping)
                        warning_year = message.created_at.year
                        if warning_year not in found_warnings:
                            found_warnings[warning_year] = []
                        found_warnings[warning_year].append(message)

    # If warnings are found
    if found_warnings:
        # Get the username of the user
        user = interaction.guild.get_member(user_id)
        username = user.name if user else "Unknown User"

        # Prepare the result string to send back
        total_warnings = sum(len(warnings) for warnings in found_warnings.values())
        warning_pages = []

        # Format the warnings and split into pages (each page with a max of 2000 characters)
        current_page = f"**Warnings for {username} (ID: {user_id})**\n**Total Warnings: {total_warnings}**\n\n"  # Add total warnings count at the top
        for year, warnings in found_warnings.items():
            year_count = len(warnings)
            page_content = f"**{year}: {year_count} warnings**\n"
            for warn in warnings:
                warning_detail = f" - **Message**: {warn.content}\n"
                warning_detail += f"   **By**: {warn.author}\n"
                warning_detail += f"   **Date**: {warn.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
                warning_detail += f"   **Message Link**: [Jump to Message]({warn.jump_url})\n"
                warning_detail += "\n"

                # Add the warning detail to the current page, checking if it exceeds the character limit
                if len(current_page) + len(page_content) + len(warning_detail) > 2000:
                    warning_pages.append(current_page)
                    current_page = warning_detail
                else:
                    current_page += page_content + warning_detail

        if current_page:
            warning_pages.append(current_page)  # Add the last page if it has content

        # Send the first page with navigation buttons
        await send_warning_page(interaction, warning_pages, 0, len(warning_pages), username)

    else:
        # If no warnings were found, notify the user
        await interaction.response.send_message(f"No warnings found for <@{user_id}>.", ephemeral=True)


async def send_warning_page(interaction: discord.Interaction, warning_pages: list, page_index: int, total_pages: int, username: str):
    # Send the current page with the "Previous" and "Next" buttons
    page_content = warning_pages[page_index]

    embed = discord.Embed(
        title=f"Warnings for {username}",
        description=f"{page_content}\n\nPage {page_index + 1} of {total_pages}",
        color=discord.Color.red()
    )

    # Create the navigation buttons
    previous_button = Button(label="Previous", custom_id="previous", disabled=page_index == 0)
    next_button = Button(label="Next", custom_id="next", disabled=page_index == len(warning_pages) - 1)

    # Define the view with the buttons
    view = View()
    view.add_item(previous_button)
    view.add_item(next_button)

    # Send the embed with the buttons
    message = await interaction.response.send_message(embed=embed, view=view)

    # Wait for the user's interaction with the buttons
    def check(interaction: discord.Interaction):
        return interaction.user == interaction.user and interaction.message.id == message.id

    interaction_response = await interaction.client.wait_for("button_click", check=check)

    # Handle the button clicks
    if interaction_response.custom_id == "previous" and page_index > 0:
        await send_warning_page(interaction, warning_pages, page_index - 1, total_pages, username)
    elif interaction_response.custom_id == "next" and page_index < len(warning_pages) - 1:
        await send_warning_page(interaction, warning_pages, page_index + 1, total_pages, username)

    # Disable buttons after navigating
    await interaction_response.response.edit_message(view=None)

WARNING_POINT_LIMIT = 20


def _can_issue_warning(member: discord.Member) -> bool:
    return (
        member.guild_permissions.moderate_members
        or member.guild_permissions.manage_guild
        or any(role.id == MOD_ROLE_ID for role in member.roles)
    )


@tree.command(name="warn-point", description="Warn a user and add one moderation point")
@app_commands.describe(user="The Discord user to warn", reason="Why this warning is being issued")
async def warn_user(interaction: discord.Interaction, user: discord.Member, reason: str):
    if not interaction.guild:
        return await interaction.response.send_message(
            "❌ This command can only be used in a server.", ephemeral=True
        )
    if not _can_issue_warning(interaction.user):
        return await interaction.response.send_message(
            "❌ You do not have permission to issue warnings.", ephemeral=True
        )
    if user.bot:
        return await interaction.response.send_message(
            "❌ Bots cannot receive warnings.", ephemeral=True
        )
    if user.id == interaction.user.id:
        return await interaction.response.send_message(
            "❌ You cannot warn yourself.", ephemeral=True
        )
    if any(role.id == PROTECTED_DISCORD_ROLE_ID for role in user.roles):
        return await interaction.response.send_message(
            "❌ This user has a protected role and cannot receive bot warnings.",
            ephemeral=True,
        )
    if user.top_role >= interaction.user.top_role and interaction.user != interaction.guild.owner:
        return await interaction.response.send_message(
            "❌ You can only warn members below your highest role.", ephemeral=True
        )

    reason = reason.strip()[:1000] or "No reason provided"
    warnings = load_warnings()
    user_warnings = [entry for entry in warnings if str(entry.get("user_id")) == str(user.id)]
    previous_points = sum(int(entry.get("points", 1)) for entry in user_warnings)
    points = previous_points + 1
    warnings.append({
        "user_id": str(user.id),
        "username": str(user),
        "reason": reason,
        "points": 1,
        "moderator_id": str(interaction.user.id),
        "moderator": str(interaction.user),
        "guild_id": str(interaction.guild.id),
        "timestamp": time.time(),
        "timestamp_str": discord.utils.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    })
    save_warnings(warnings)
    record_bot_action(
        "warning_issued",
        details=reason,
        user_id=str(user.id),
        username=str(user),
        moderator_id=str(interaction.user.id),
        moderator=str(interaction.user),
        guild_id=str(interaction.guild.id),
        points_added=1,
        total_points=points,
        automatic_ban=points >= WARNING_POINT_LIMIT,
    )

    threshold_reached = points >= WARNING_POINT_LIMIT
    ban_succeeded = False
    if threshold_reached:
        try:
            await user.ban(
                reason=f"Reached {WARNING_POINT_LIMIT} warning points: {reason}",
                delete_message_days=0,
            )
            ban_succeeded = True
        except (discord.Forbidden, discord.HTTPException):
            pass

    try:
        dm_text = (
            f"You received a warning in **{interaction.guild.name}**.\n"
            f"**Reason:** {reason}\n"
            f"**Points:** {points}/{WARNING_POINT_LIMIT}"
        )
        if threshold_reached:
            dm_text += "\nYou reached the warning limit and have been banned from the server."
        await user.send(dm_text)
    except (discord.Forbidden, discord.HTTPException):
        pass

    log_channel = interaction.guild.get_channel(MOD_LOG_CHANNEL_ID)
    if log_channel:
        log_embed = discord.Embed(
            title="⚠️ User Warning",
            color=discord.Color.red() if threshold_reached else discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        log_embed.add_field(name="User", value=f"{user.mention} (`{user}`)", inline=True)
        log_embed.add_field(name="Points", value=f"{points}/{WARNING_POINT_LIMIT}", inline=True)
        log_embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        log_embed.add_field(name="Reason", value=reason, inline=False)
        if threshold_reached:
            outcome = (
                "✅ User banned automatically."
                if ban_succeeded
                else "❌ Limit reached, but I could not ban the user."
            )
            log_embed.add_field(name="Threshold action", value=outcome, inline=False)
        await log_channel.send(embed=log_embed)

    if threshold_reached and ban_succeeded:
        response = (
            f"✅ {user.mention} received a warning and now has "
            f"**{points}/{WARNING_POINT_LIMIT}** points. They were automatically banned."
        )
    elif threshold_reached:
        response = (
            f"⚠️ {user.mention} reached **{points}/{WARNING_POINT_LIMIT}** points, "
            "but Discord did not allow the automatic ban."
        )
    else:
        response = (
            f"✅ Warning issued to {user.mention}. They now have "
            f"**{points}/{WARNING_POINT_LIMIT}** points."
        )
    await interaction.response.send_message(response, ephemeral=True)


# Define the modal class to request the user ID
class WarningCheckModal(Modal):
    def __init__(self, interaction):
        super().__init__(title="Check User Warnings")
        self.interaction = interaction

        # Add a text input field for user ID
        self.add_item(TextInput(label="Enter User ID", placeholder="123456789012345678"))

    async def on_submit(self, interaction: discord.Interaction):
        # Get the value entered in the input field
        user_id = self.children[0].value

        # Ensure the input is a valid number
        if not user_id.isdigit():
            await interaction.response.send_message("Invalid User ID. Please enter a valid number.", ephemeral=True)
            return

        # Convert to integer
        user_id = int(user_id)

        # Call the check_user_warnings function with the specified channel check
        await check_user_warnings(interaction, user_id)

# Command to open the modal
@tree.command(name="ping-warnings", description="Check warnings of a user")
async def ping_warnings(interaction: discord.Interaction):
    # Open the modal to enter the user ID
    modal = WarningCheckModal(interaction)
    await interaction.response.send_modal(modal)


CHANNEL_ID = 1513879987491704902
ROLE_ID = 1268998581000601651


class ReturningToWorkModal(discord.ui.Modal, title="Returning To Work Form"):

    roblox_username = discord.ui.TextInput(
        label="Roblox Username",
        placeholder="Enter your Roblox username",
        required=True,
        max_length=50
    )

    reason = discord.ui.TextInput(
        label="Reason For Time Off",
        style=discord.TextStyle.paragraph,
        placeholder="Explain your reason for time off",
        required=True,
        max_length=1000
    )

    off_date = discord.ui.TextInput(
        label="Off Date (MM-DD-YY)",
        placeholder="01-05-26",
        required=True,
        max_length=20
    )

    return_date = discord.ui.TextInput(
        label="Return Date (MM-DD-YY)",
        placeholder="01-12-26",
        required=True,
        max_length=20
    )

    department = discord.ui.TextInput(
        label="Board / Team Department",
        placeholder="Example: Human Resources",
        required=True,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):

        try:
            off_date_obj = datetime.strptime(
                self.off_date.value,
                "%m-%d-%y"
            )

            return_date_obj = datetime.strptime(
                self.return_date.value,
                "%m-%d-%y"
            )

            formatted_off_date = off_date_obj.strftime("%b %d, %Y")
            formatted_return_date = return_date_obj.strftime("%b %d, %Y")

        except ValueError:
            formatted_off_date = "Invalid Date Format"
            formatted_return_date = "Invalid Date Format"

        embed = discord.Embed(
            title="📋 Returning To Work Notice",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )

        embed.add_field(
            name="Roblox Username",
            value=f"**{self.roblox_username.value}**",
            inline=False
        )

        embed.add_field(
            name="Discord User",
            value=f"**{interaction.user}** (`{interaction.user.id}`)",
            inline=False
        )

        embed.add_field(
            name="Reason For Time Off",
            value=f"**{self.reason.value}**",
            inline=False
        )

        embed.add_field(
            name="Off Date",
            value=f"**{formatted_off_date}**",
            inline=False
        )

        embed.add_field(
            name="Return Date",
            value=f"**{formatted_return_date}**",
            inline=False
        )

        embed.add_field(
            name="Board / Team Department",
            value=f"**{self.department.value}**",
            inline=False
        )

        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        embed.set_footer(
            text=f"Submitted by {interaction.user}",
            icon_url=interaction.user.display_avatar.url
        )

        channel = interaction.client.get_channel(CHANNEL_ID)

        if channel:
            await channel.send(
                content=f"<@&{ROLE_ID}>",
                embed=embed
            )

        await interaction.response.send_message(
            f"✅ Your Returning To Work form has been submitted.\n\n"
            f"Return Date: **{formatted_return_date}**",
            ephemeral=True
        )

CHANNEL_ID = 1513879987491704902  # Replace with your actual channel ID
ROLE_ID = 1268998581000601651  # Replace with the required role ID

@tree.command(name="returning", description="Submit a time off and returning to work form")
async def returning_to_work(interaction: discord.Interaction):
    # The configured full-access user IDs bypass role-based restrictions.
    if not has_full_access(interaction.user):
        if not has_full_access(interaction.user) and (not isinstance(interaction.user, discord.Member) or not any(role.id == ROLE_ID for role in interaction.user.roles)):
            await interaction.response.send_message(
                "❌ You do not have permission to use this command.", ephemeral=True
            )
            return

    # Send a message asking for the Roblox username
    await interaction.response.send_message("I will ask you for some information to submit your time off and return details. Please respond to each question in this DM.", ephemeral=True)

    # Send a DM to the user and start collecting responses
    dm_channel = await interaction.user.create_dm()

    # Ask questions one by one
    await dm_channel.send("Please provide your Roblox Username:")

    def check(m):
        return m.author == interaction.user and isinstance(m.channel, discord.DMChannel)

    roblox_user_msg = await interaction.client.wait_for('message', check=check)
    roblox_user = roblox_user_msg.content

    await dm_channel.send("Please provide your Discord Username:")

    discord_user_msg = await interaction.client.wait_for('message', check=check)
    discord_user = discord_user_msg.content

    await dm_channel.send("Please provide your reason for time off:")

    reason_msg = await interaction.client.wait_for('message', check=check)
    reason = reason_msg.content

    # Ask for the "Off Date"
    await dm_channel.send("Please provide your Off Date (MM-DD-YY) Like format 1-5-24:")

    off_date_msg = await interaction.client.wait_for('message', check=check)
    off_date_str = off_date_msg.content

    # Ask for the "Return Date"
    await dm_channel.send("Please provide your Return Date (MM-DD-YY) Like format 1-2-24:")

    return_date_msg = await interaction.client.wait_for('message', check=check)
    return_date_str = return_date_msg.content

    await dm_channel.send("Please provide the full name of your board/team department:")

    department_msg = await interaction.client.wait_for('message', check=check)
    department = department_msg.content

    try:
        off_date_obj = datetime.strptime(off_date_str, "%m-%d-%y")
        return_date_obj = datetime.strptime(return_date_str, "%m-%d-%y")
        formatted_off_date = off_date_obj.strftime("%b %d, %Y")  # Jan 01, 2025 format
        formatted_return_date = return_date_obj.strftime("%b %d, %Y")
    except ValueError:
        formatted_off_date = "Invalid format"
        formatted_return_date = "Invalid format"

    # Create embed for submission
    embed = discord.Embed(title="Returning to Work Notice", color=discord.Color.green())
    embed.add_field(name="Roblox Username", value=f"**{roblox_user}**", inline=False)
    embed.add_field(name="Discord Username", value=f"**{discord_user}**", inline=False)
    embed.add_field(name="Reason for Time Off", value=f"**{reason}**", inline=False)
    embed.add_field(name="Off Date", value=f"**{formatted_off_date}**", inline=False)
    embed.add_field(name="Return Date", value=f"**{formatted_return_date}**", inline=False)
    embed.add_field(name="Board/Team Department", value=f"**{department}**", inline=False)

    # Send the embed to the user (in DM) with all the information
    await dm_channel.send(embed=embed)

    # Send the information to the designated channel
    channel = interaction.client.get_channel(CHANNEL_ID)
    if channel:
        await channel.send(embed=embed)

    # Confirmation message in DMs (not in the channel)
    await dm_channel.send(f"Your time off and return details have been submitted. See you on: **{formatted_return_date}**!")


# ----------------- Config -----------------
SOFTBAN_FILE = "softbans.json"
LOG_CHANNEL_ID = 1258549985738363007  # Replace with your log channel ID
STAFF_ROLE_ID = 1479915487713427456 # Replace with your staff role ID
PRIMARY_SERVER_ID = 1258549984325009468  # Replace with your server ID
# ----------------- Helpers -----------------
def load_softbans():
    """Load softbans from disk, tolerating an empty/corrupt/missing file."""
    if not os.path.exists(SOFTBAN_FILE):
        return []
    try:
        with open(SOFTBAN_FILE, "r") as f:
            content = f.read().strip()
            if not content:
                return []
            data = json.loads(content)
            if not isinstance(data, list):
                return []
            return data
    except (json.JSONDecodeError, OSError) as e:
        print(f"[softbans] Failed to load {SOFTBAN_FILE}: {e}. Resetting to empty list.")
        try:
            # Back up the bad file so the user can inspect it later
            if os.path.exists(SOFTBAN_FILE):
                os.replace(SOFTBAN_FILE, SOFTBAN_FILE + ".corrupt")
        except OSError:
            pass
        return []

def save_softbans(data):
    with open(SOFTBAN_FILE, "w") as f:
        json.dump(data, f, indent=4)

def parse_duration(duration_str):
    if not duration_str:
        return None
    match = re.match(r"(\d+)([dhm])", duration_str.lower())
    if not match:
        return None
    value, unit = match.groups()
    value = int(value)
    if unit == "d":
        return timedelta(days=value)
    elif unit == "h":
        return timedelta(hours=value)
    elif unit == "m":
        return timedelta(minutes=value)
    return None

def generate_case_id():
    return str(random.randint(1000000, 9999999))

def is_staff(member: discord.abc.User):
    # Whitelisted full-access users do not need a staff role.
    if has_full_access(member):
        return True
    if not isinstance(member, discord.Member):
        return False
    return any(role.id == STAFF_ROLE_ID for role in member.roles)

# ----------------- Softban -----------------
@tree.command(name="softban", description="Softban a user")
@app_commands.describe(user="User to softban", reason="Reason", duration="10d / 2h / 30m")
async def softban(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided", duration: Optional[str] = None):
    if interaction.guild is None or interaction.guild.id != PRIMARY_SERVER_ID:
        return await interaction.response.send_message("❌ Only usable in main server.", ephemeral=True)
    if not is_staff(interaction.user):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)

    softbans = load_softbans()
    user_id = str(user.id)
    if user_id in [entry["user_id"] for entry in softbans]:
        return await interaction.response.send_message("⚠ Already softbanned.", ephemeral=True)

    duration_td = parse_duration(duration)
    expires_at = (discord.utils.utcnow() + duration_td).isoformat() if duration_td else None
    case_id = generate_case_id()

    try:
        await user.send(f"You were softbanned from {interaction.guild.name}\nReason: {reason}")
    except:
        pass

    await interaction.guild.ban(user, reason=reason, delete_message_seconds=0)
    await interaction.guild.unban(user, reason="Softban")

    softbans.append({
        "case_id": case_id,
        "user_id": user_id,
        "reason": reason,
        "banned_at": discord.utils.utcnow().isoformat(),
        "expires_at": expires_at,
        "guild_id": str(interaction.guild.id),
        "permanent": not duration_td,
    })
    save_softbans(softbans)

    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    if isinstance(log_channel, discord.TextChannel):
        embed = discord.Embed(
            title="🔨 Softban Issued",
            description=f"Case: `{case_id}`\nUser: {user.mention}\nBy: {interaction.user.mention}\nReason: {reason}",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )
        await log_channel.send(embed=embed)

    await interaction.response.send_message("✅ Softban complete.", ephemeral=True)

# ----------------- Unsoftban -----------------
@tree.command(name="unsoftban", description="Remove a softban")
@app_commands.describe(user="User to unsoftban")
async def unsoftban(interaction: discord.Interaction, user: discord.User):
    if interaction.guild is None or interaction.guild.id != PRIMARY_SERVER_ID:
        return await interaction.response.send_message("❌ Only usable in main server.", ephemeral=True)
    if not is_staff(interaction.user):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)

    softbans = load_softbans()
    updated = [b for b in softbans if b["user_id"] != str(user.id)]
    save_softbans(updated)

    try:
        await interaction.guild.unban(discord.Object(id=user.id), reason="Manual unsoftban")
    except discord.NotFound:
        pass
    except Exception as e:
        return await interaction.response.send_message(f"❌ Failed: {e}", ephemeral=True)

    await interaction.response.send_message("✅ Unsoftbanned.", ephemeral=True)

# ----------------- Softban List -----------------
class SoftbanSelect(discord.ui.Select):
    def __init__(self, entries):
        options = []
        for entry in entries[:25]:  # Discord limit: 25 options per select
            user_id = entry.get("user_id", "?")
            case_id = entry.get("case_id", "?")
            reason = (entry.get("reason") or "No reason")[:80]
            options.append(
                discord.SelectOption(
                    label=f"User {user_id}",
                    description=f"Case {case_id} — {reason}",
                    value=str(user_id),
                )
            )
        super().__init__(
            placeholder="Pick a user to unsoftban…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        if interaction.guild is None:
            return await interaction.response.send_message("❌ Guild only.", ephemeral=True)

        user_id = self.values[0]
        softbans = load_softbans()
        updated = [b for b in softbans if b["user_id"] != user_id]
        save_softbans(updated)

        try:
            await interaction.guild.unban(discord.Object(id=int(user_id)), reason="Unsoftban via list")
        except discord.NotFound:
            pass
        except Exception as e:
            return await interaction.response.send_message(f"❌ Failed: {e}", ephemeral=True)

        await interaction.response.send_message(f"✅ Unsoftbanned user `{user_id}`.", ephemeral=True)


class SoftbanListView(discord.ui.View):
    def __init__(self, entries):
        super().__init__(timeout=120)
        self.add_item(SoftbanSelect(entries))


@tree.command(name="softban-list", description="List all softbanned users")
async def softban_list(interaction: discord.Interaction):
    if interaction.guild is None or interaction.guild.id != PRIMARY_SERVER_ID:
        return await interaction.response.send_message("❌ Only usable in main server.", ephemeral=True)
    if not is_staff(interaction.user):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)

    softbans = load_softbans()
    filtered = [b for b in softbans if b.get("guild_id") == str(interaction.guild.id)]
    if not filtered:
        return await interaction.response.send_message("No softbans.", ephemeral=True)

    lines = [f"Softbanned Users ({len(filtered)}):"]
    for entry in filtered:
        lines.append(
            f"- Case: {entry.get('case_id')} | User ID: {entry.get('user_id')} | Reason: {entry.get('reason')}"
        )
    message = "\n".join(lines)
    if len(message) > 1900:
        message = message[:1900] + "\n... (truncated)"

    view = SoftbanListView(filtered)
    await interaction.response.send_message(message, view=view, ephemeral=True)

# ----------------- Kick -----------------
@tree.command(name="server-kick", description="Kick user")
@app_commands.describe(user="User", reason="Reason")
async def server_kick(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason"):
    if interaction.guild is None or interaction.guild.id != PRIMARY_SERVER_ID:
        return await interaction.response.send_message("❌ Only usable in main server.", ephemeral=True)
    if not is_staff(interaction.user):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)

    case_id = generate_case_id()
    try:
        await user.send(f"You were kicked from {interaction.guild.name}\nReason: {reason}")
    except:
        pass
    await user.kick(reason=reason)
    record_bot_action(
        "staff_kick",
        details=reason,
        target_discord_id=str(user.id),
        target_username=str(user),
        moderator_id=str(interaction.user.id),
        moderator=str(interaction.user),
        guild_id=str(interaction.guild.id),
        case_id=case_id,
    )

    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    if isinstance(log_channel, discord.TextChannel):
        embed = discord.Embed(
            title="👢 User Kicked",
            description=f"Case: `{case_id}`\nUser: {user.mention}\nBy: {interaction.user.mention}\nReason: {reason}",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )
        await log_channel.send(embed=embed)

    await interaction.response.send_message("✅ User kicked.", ephemeral=True)

# ----------------- Ban -----------------
@tree.command(name="server-ban", description="Ban user")
@app_commands.describe(user="User", reason="Reason")
async def server_ban(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason"):
    if interaction.guild is None or interaction.guild.id != PRIMARY_SERVER_ID:
        return await interaction.response.send_message("❌ Only usable in main server.", ephemeral=True)
    if not is_staff(interaction.user):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)

    case_id = generate_case_id()
    try:
        await user.send(f"You were banned from {interaction.guild.name}\nReason: {reason}")
    except:
        pass
    await interaction.guild.ban(user, reason=reason)
    record_bot_action(
        "staff_ban",
        details=reason,
        target_discord_id=str(user.id),
        target_username=str(user),
        moderator_id=str(interaction.user.id),
        moderator=str(interaction.user),
        guild_id=str(interaction.guild.id),
        case_id=case_id,
    )

    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    if isinstance(log_channel, discord.TextChannel):
        embed = discord.Embed(
            title="🔨 User Banned",
            description=f"Case: `{case_id}`\nUser: {user.mention}\nBy: {interaction.user.mention}\nReason: {reason}",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )
        await log_channel.send(embed=embed)

    await interaction.response.send_message("✅ User banned.", ephemeral=True)

# ----------------- Unban -----------------
class BanListSelect(discord.ui.Select):
    def __init__(self, ban_entries):
        options = []
        for entry in ban_entries[:25]:  # Discord limit: 25 options per select
            user = entry.user
            reason = (entry.reason or "No reason")[:80]
            options.append(
                discord.SelectOption(
                    label=f"{user.name} ({user.id})"[:100],
                    description=f"Reason: {reason}",
                    value=str(user.id),
                )
            )
        super().__init__(
            placeholder="Pick a user to unban…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        if interaction.guild is None:
            return await interaction.response.send_message("❌ Guild only.", ephemeral=True)

        user_id = self.values[0]
        case_id = generate_case_id()
        try:
            user = await interaction.client.fetch_user(int(user_id))
            await interaction.guild.unban(user, reason="Unban via list")
        except discord.NotFound:
            return await interaction.response.send_message("❌ That user isn't banned anymore.", ephemeral=True)
        except Exception as e:
            return await interaction.response.send_message(f"❌ Failed: {e}", ephemeral=True)

        # Also clean up softban entry if present
        softbans = load_softbans()
        updated = [b for b in softbans if b["user_id"] != user_id]
        if len(updated) != len(softbans):
            save_softbans(updated)

        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if isinstance(log_channel, discord.TextChannel):
            embed = discord.Embed(
                title="🔓 User Unbanned",
                description=f"Case: `{case_id}`\nUser: {user.name} (`{user.id}`)\nBy: {interaction.user.mention}",
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow()
            )
            await log_channel.send(embed=embed)

        await interaction.response.send_message(f"✅ Unbanned `{user.name}` (`{user.id}`).", ephemeral=True)


class BanListView(discord.ui.View):
    def __init__(self, ban_entries):
        super().__init__(timeout=120)
        self.add_item(BanListSelect(ban_entries))


@tree.command(name="server-banlist", description="List banned users and pick one to unban")
async def server_banlist(interaction: discord.Interaction):
    if interaction.guild is None or interaction.guild.id != PRIMARY_SERVER_ID:
        return await interaction.response.send_message("❌ Only usable in main server.", ephemeral=True)
    if not is_staff(interaction.user):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not interaction.guild.me.guild_permissions.ban_members:
        return await interaction.response.send_message("❌ I need the 'Ban Members' permission.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    try:
        bans = [b async for b in interaction.guild.bans(limit=25)]
    except Exception as e:
        return await interaction.followup.send(f"❌ Failed to fetch bans: {e}", ephemeral=True)

    if not bans:
        return await interaction.followup.send("No banned users.", ephemeral=True)

    lines = [f"Banned Users ({len(bans)}):"]
    for entry in bans:
        reason = entry.reason or "No reason"
        lines.append(f"- {entry.user.name} (`{entry.user.id}`) — {reason}")
    message = "\n".join(lines)
    if len(message) > 1900:
        message = message[:1900] + "\n... (truncated)"

    view = BanListView(bans)
    await interaction.followup.send(message, view=view, ephemeral=True)


@tree.command(name="server-unban", description="Unban user")
@app_commands.describe(user_id="User ID")
async def server_unban(interaction: discord.Interaction, user_id: str):
    if interaction.guild is None or interaction.guild.id != PRIMARY_SERVER_ID:
        return await interaction.response.send_message("❌ Only usable in main server.", ephemeral=True)
    if not is_staff(interaction.user):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)

    case_id = generate_case_id()
    try:
        user = await interaction.client.fetch_user(int(user_id))
        await interaction.guild.unban(user)
    except Exception as e:
        return await interaction.response.send_message(f"❌ Failed: {e}", ephemeral=True)

    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    if isinstance(log_channel, discord.TextChannel):
        embed = discord.Embed(
            title="🔓 User Unbanned",
            description=f"Case: `{case_id}`\nUser: {user.name}\nBy: {interaction.user.mention}",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        await log_channel.send(embed=embed)

    await interaction.response.send_message("✅ User unbanned.", ephemeral=True)

# ----------------- Timeout -----------------
@tree.command(name="server-timeout", description="Timeout a user")
@app_commands.describe(user="User to timeout", duration="10d / 2h / 30m", reason="Reason for timeout")
async def timeout(interaction: discord.Interaction, user: discord.Member, duration: str, reason: str = "No reason provided"):
    if interaction.guild is None or interaction.guild.id != PRIMARY_SERVER_ID:
        return await interaction.response.send_message("❌ Only usable in main server.", ephemeral=True)
    if not is_staff(interaction.user):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not interaction.guild.me.guild_permissions.moderate_members:
        return await interaction.response.send_message("❌ I need the 'Moderate Members' permission.", ephemeral=True)

    duration_td = parse_duration(duration)
    if not duration_td:
        return await interaction.response.send_message("❌ Invalid duration format. Use 10d, 2h, or 30m.", ephemeral=True)

    timeout_until = discord.utils.utcnow() + duration_td
    try:
        await user.send(f"You have been timed out in {interaction.guild.name} for {duration}. Reason: {reason}")
    except:
        pass

    try:
        await user.edit(timed_out_until=timeout_until, reason=reason)
    except Exception as e:
        return await interaction.response.send_message(f"❌ Failed to timeout: {e}", ephemeral=True)

    case_id = generate_case_id()
    record_bot_action(
        "staff_timeout",
        details=f"{duration}: {reason}",
        target_discord_id=str(user.id),
        target_username=str(user),
        moderator_id=str(interaction.user.id),
        moderator=str(interaction.user),
        guild_id=str(interaction.guild.id),
        case_id=case_id,
    )
    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    if isinstance(log_channel, discord.TextChannel):
        embed = discord.Embed(
            title="⏱ User Timed Out",
            description=f"Case: `{case_id}`\nUser: {user.mention}\nBy: {interaction.user.mention}\nDuration: {duration}\nReason: {reason}",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )
        await log_channel.send(embed=embed)

    await interaction.response.send_message(f"✅ {user.mention} has been timed out for {duration}.", ephemeral=True)

# ----------------- Untimeout -----------------
@tree.command(name="server-untimeout", description="Remove a timeout from a user")
@app_commands.describe(user="User to remove timeout")
async def untimeout(interaction: discord.Interaction, user: discord.Member):
    if interaction.guild is None or interaction.guild.id != PRIMARY_SERVER_ID:
        return await interaction.response.send_message("❌ Only usable in main server.", ephemeral=True)
    if not is_staff(interaction.user):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not interaction.guild.me.guild_permissions.moderate_members:
        return await interaction.response.send_message("❌ I need the 'Moderate Members' permission.", ephemeral=True)

    try:
        await user.edit(timed_out_until=None, reason="Manual untimeout")
    except Exception as e:
        return await interaction.response.send_message(f"❌ Failed to remove timeout: {e}", ephemeral=True)

    case_id = generate_case_id()
    record_bot_action(
        "staff_untimeout",
        details="Timeout removed",
        target_discord_id=str(user.id),
        target_username=str(user),
        moderator_id=str(interaction.user.id),
        moderator=str(interaction.user),
        guild_id=str(interaction.guild.id),
        case_id=case_id,
    )
    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    if isinstance(log_channel, discord.TextChannel):
        embed = discord.Embed(
            title="⏱ Timeout Removed",
            description=f"Case: `{case_id}`\nUser: {user.mention}\nBy: {interaction.user.mention}",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )
        await log_channel.send(embed=embed)

    await interaction.response.send_message(f"✅ Timeout removed for {user.mention}.", ephemeral=True)

# ----------------- Member Counter -----------------
@tree.command(name="member-counter", description="Show total humans & bots in the server.")
async def membercounter(interaction: discord.Interaction):
    guild = interaction.guild
    humans = sum(1 for member in guild.members if not member.bot)
    bots = sum(1 for member in guild.members if member.bot)
    total = guild.member_count

    embed = discord.Embed(title="📊 Member Count", color=discord.Color.blue())
    embed.add_field(name="Total Members", value=str(total), inline=False)
    embed.add_field(name="Humans", value=str(humans), inline=True)
    embed.add_field(name="Bots", value=str(bots), inline=True)
    embed.set_footer(text=f"Requested by {interaction.user}")

    await interaction.response.send_message(embed=embed)


# Store these in Replit Secrets; do not hardcode bot tokens in source files.
BOT_TOKENS = [
    os.getenv("SUB_BOT_TOKEN_1", ""),
    os.getenv("SUB_BOT_TOKEN_2", ""),
    os.getenv("SUB_BOT_TOKEN_3", ""),
]

# Statuses for each bot
BOT_STATUSES = [
    ["Watching Pod Hub", "Checking hubs", "Log portal"],
    ["Checking game reports", "Viewing", "Updating server"],
    ["In development", "Updating Pod Hub", "Server moderation"],
]
# To keep references to all clients
sub_clients = []

async def run_bot(index: int):
    if index >= len(BOT_TOKENS) or not BOT_TOKENS[index]:
        print(f"[SUB-BOT] SUB_BOT_TOKEN_{index + 1} is not configured in Replit Secrets.")
        return
    intents = discord.Intents.default()
    intents.guilds = True  # Enables access to bot's own info

    sub_client = discord.Client(intents=intents)

    @sub_client.event
    async def on_ready():
        print(f"{sub_client.user} is online.")
        while True:
            for status in BOT_STATUSES[index]:
                await sub_client.change_presence(
                    activity=discord.Activity(type=discord.ActivityType.watching, name=status),
                    status=discord.Status.online
                )
                await asyncio.sleep(10)

    sub_clients.append(sub_client)
    await sub_client.start(BOT_TOKENS[index])

@tree.command(name="server_reset", description="Start all sub-bots online with status loop.")
async def start_bots(interaction: discord.Interaction):
    await interaction.response.send_message("Turning on all bots...", ephemeral=True)

    for i in range(len(BOT_TOKENS)):
        asyncio.create_task(run_bot(i))




# -------------------------
# Role Check
# -------------------------
REQUIRED_ROLE_ID = 1306344294310875207

async def check_role(interaction: discord.Interaction):
    if has_full_access(interaction.user):
        return True
    if not interaction.user.guild:
        return False

    role_ids = [role.id for role in interaction.user.roles]

    if not has_full_access(interaction.user) and REQUIRED_ROLE_ID not in role_ids:
        await interaction.response.send_message(
            "❌ You do not have permission to use this command.",
            ephemeral=True
        )
        return False

    return True

# -------------------------
# /startsession
# -------------------------
@tree.command(name="startsession", description="Start a session with session info.")
@app_commands.describe(
    header="Text to show in the embed header",
    footer_text="Optional text to show in the footer",
    host="Host of the session",
    co_host="Co-Host",
    assistant="Assistant",
    time_="Time of session",  # renamed to avoid conflict with 'time' module
    trainer="Trainer",
    information="Additional information",
    channel="Text/voice channel for session",
    ping_role="Role to ping for session"
)
async def startsession(
    interaction: discord.Interaction,
    header: str,
    footer_text: str = None,
    host: str = None,
    co_host: str = None,
    assistant: str = None,
    time_: str = None,
    trainer: str = None,
    information: str = None,
    channel: discord.TextChannel = None,
    ping_role: discord.Role = None
):
    if not await check_role(interaction): return

    embed = discord.Embed(
        title="🟢 Session Started",
        color=discord.Color.green()
    )

    # Header text from user input
    embed.set_author(name=header)

    # Add optional fields
    if host: embed.add_field(name="Host", value=host, inline=True)
    if co_host: embed.add_field(name="Co-Host", value=co_host, inline=True)
    if assistant: embed.add_field(name="Assistant", value=assistant, inline=True)
    if time_: embed.add_field(name="Time", value=time_, inline=True)
    if trainer: embed.add_field(name="Trainer", value=trainer, inline=True)
    if information: embed.add_field(name="Information", value=information, inline=False)
    if channel: embed.add_field(name="Channel", value=channel.mention, inline=True)

    # Footer shows who started + optional footer text
    footer_display = f"Started by {interaction.user}"
    if footer_text:
        footer_display += f" | {footer_text}"
    embed.set_footer(text=footer_display)

    # Send message with ping role if provided
    if ping_role:
        await interaction.response.send_message(
            content=ping_role.mention,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=True)  # ping the role
        )
    else:
        await interaction.response.send_message(embed=embed)

    # Save data for /endsession
    original_msg = await interaction.original_response()
    interaction.client.session_message = original_msg
    interaction.client.session_ping_role = ping_role
    interaction.client.session_starter = interaction.user
    interaction.client.session_footer_text = footer_text

# -------------------------
# /endsession
# -------------------------
@tree.command(name="endsession", description="End the current session.")
async def endsession(interaction: discord.Interaction):
    if not await check_role(interaction): return

    if not hasattr(interaction.client, "session_message"):
        await interaction.response.send_message("❌ No active session found.", ephemeral=True)
        return

    message = interaction.client.session_message
    embed = message.embeds[0]

    # Keep the header text (author) the same
    header_text = embed.author.name if embed.author else "Session"

    # Update embed for ended session
    embed.color = discord.Color.red()
    embed.title = "🔴 Session Ended"
    embed.set_author(name=header_text)  # keep original header

    # Footer shows who ended, who started, and optional footer text
    starter = getattr(interaction.client, "session_starter", "Unknown")
    footer_text = getattr(interaction.client, "session_footer_text", "")
    footer_display = f"Ended by {interaction.user} | Started by {starter}"
    if footer_text:
        footer_display += f" | {footer_text}"
    embed.set_footer(text=footer_display)

    # Send ping again if role was provided
    ping_role = getattr(interaction.client, "session_ping_role", None)
    if ping_role:
        await message.edit(
            content=ping_role.mention,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=True)
        )
    else:
        await message.edit(embed=embed)

    await interaction.response.send_message(
        content="✅ Session marked as ended.",
        ephemeral=True
    )


# -------------------------
# /ping (no role check)
# -------------------------
@tree.command(name="ping-server", description="Check bot latency & uptime.")
async def ping(interaction: discord.Interaction):
    latency = round(client.latency * 1000)
    uptime = time.time() - start_time
    uptime_str = f"{int(uptime//3600)}h {int((uptime%3600)//60)}m {int(uptime%60)}s"

    await interaction.response.send_message(
        f"🏓 Pong! Latency: **{latency}ms**\n⏱ Uptime: **{uptime_str}**"
    )


# Optional: restrict command to staff
STAFF_ROLE_ID = 1258549984341528689 # Replace with your staff role ID
async def check_role(interaction: discord.Interaction):
    """Check if the user has the staff role in the server where command is run"""
    # Some interactions may not have a guild (DMs), so skip if none
    if interaction.guild:
        if has_full_access(interaction.user) or STAFF_ROLE_ID in [role.id for role in interaction.user.roles]:
            return True
    return False

@tree.command(name="ghost-whispers", description="test ghost user")
@app_commands.describe(user="The message", message="The message to send")
async def whisper(interaction: discord.Interaction, user: discord.User, message: str):

    # Check staff role (optional)
    if not await check_role(interaction):
        await interaction.response.send_message(
            "❌ You do not have permission to use this command.",
            ephemeral=True
        )
        return
    try:
        # Create the embed to send to the target user
        embed = discord.Embed(
            title="📩 Private Message",
            description=message,
            color=discord.Color.blurple()
        )
        embed.set_footer(text="Sent by: Staff Team")

        # DM the user
        await user.send(embed=embed)

        # Confirm to the staff member
        await interaction.response.send_message(
            f"✅ Message successfully sent to {user.mention}.",
            ephemeral=True
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I cannot DM this user. They may have DMs disabled.",
            ephemeral=True
        )
    except discord.HTTPException as e:
        await interaction.response.send_message(
            f"❌ Failed to send message: {e}",
            ephemeral=True
        )


# ============================================================
# CONFIG
# ============================================================

REQUIRED_ROLE_ID = 1268998581000601651

# ============================================================
# GLOBALS
# ============================================================

sniped_data = None
edited_data = None

URL_REGEX = re.compile(r"https?://[^\s]+")

# ============================================================
# HELPERS
# ============================================================

def has_required_role(member: discord.Member) -> bool:
    # Full-access users are allowed regardless of their server roles.
    return has_full_access(member) or (
        isinstance(member, discord.Member)
        and any(role.id == REQUIRED_ROLE_ID for role in member.roles)
    )

# ============================================================
# EVENTS
# ============================================================

@client.event
async def on_message_delete(message: discord.Message):
    global sniped_data

    if message.author.bot:
        return

    sniped_data = {
        "author": message.author,
        "channel": message.channel,
        "content": message.content or "[No Message Content]",
        "created": message.created_at,
        "attachments": [],
        "stickers": [],
        "links": URL_REGEX.findall(message.content),
        "reply": None,
    }

    for attachment in message.attachments:
        sniped_data["attachments"].append({
            "filename": attachment.filename,
            "url": attachment.url,
            "content_type": attachment.content_type,
            "size": attachment.size,
        })

    for sticker in message.stickers:
        sniped_data["stickers"].append(sticker.name)

    if message.reference and message.reference.resolved:
        if isinstance(message.reference.resolved, discord.Message):
            replied = message.reference.resolved

            sniped_data["reply"] = {
                "author": replied.author,
                "content": replied.content or "[No Message Content]"
            }


@client.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    await update_public_discord_message(before, after)
    global edited_data

    if before.author.bot:
        return

    if before.content == after.content:
        return

    edited_data = {
        "author": after.author,
        "channel": after.channel,
        "before": before.content or "[No Message Content]",
        "after": after.content or "[No Message Content]",
        "edited": datetime.utcnow(),
        "attachments": [],
        "links": URL_REGEX.findall(after.content),
    }

    for attachment in after.attachments:
        edited_data["attachments"].append({
            "filename": attachment.filename,
            "url": attachment.url,
            "content_type": attachment.content_type,
            "size": attachment.size,
        })

# ============================================================
# /SERVER-SNIPE
# ============================================================

@tree.command(name="server-snipe",description="Displays the most recently deleted message.")
async def server_snipe(interaction: discord.Interaction):

    member = interaction.user

    if not isinstance(member, discord.Member) or not has_required_role(member):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True
        )
        return

    if sniped_data is None:
        await interaction.response.send_message(
            "There are no deleted messages available.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="Last Deleted Message",
        color=discord.Color.red(),
        timestamp=sniped_data["created"]
    )

    embed.add_field(
        name="Author",
        value=sniped_data["author"].mention,
        inline=False
    )

    embed.add_field(
        name="Channel",
        value=sniped_data["channel"].mention,
        inline=False
    )

    embed.add_field(
        name="Message",
        value=sniped_data["content"][:1024],
        inline=False
    )

    if sniped_data["reply"]:
        embed.add_field(
            name="Replying To",
            value=f"{sniped_data['reply']['author'].mention}\n{sniped_data['reply']['content'][:900]}",
            inline=False
        )

    if sniped_data["links"]:
        embed.add_field(
            name="Links",
            value="\n".join(sniped_data["links"])[:1024],
            inline=False
        )

    if sniped_data["stickers"]:
        embed.add_field(
            name="Stickers",
            value="\n".join(sniped_data["stickers"]),
            inline=False
        )

    if sniped_data["attachments"]:

        attachment_text = ""

        for attachment in sniped_data["attachments"]:
            attachment_text += (
                f"[{attachment['filename']}]({attachment['url']})\n"
            )

        embed.add_field(
            name="Attachments",
            value=attachment_text[:1024],
            inline=False
        )

        first = sniped_data["attachments"][0]

        if (
            first["content_type"]
            and first["content_type"].startswith("image")
        ):
            embed.set_image(url=first["url"])

    embed.set_footer(
        text=f"User ID: {sniped_data['author'].id}"
    )

    await interaction.response.send_message(embed=embed)

# ============================================================
# /SERVER-EDIT
# ============================================================

@tree.command(name="server-edit",description="Displays the most recently edited message.")
async def server_edit(interaction: discord.Interaction):

    member = interaction.user

    if not isinstance(member, discord.Member) or not has_required_role(member):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True
        )
        return

    if edited_data is None:
        await interaction.response.send_message(
            "There are no edited messages available.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="Last Edited Message",
        color=discord.Color.orange(),
        timestamp=edited_data["edited"]
    )

    embed.add_field(
        name="Author",
        value=edited_data["author"].mention,
        inline=False
    )

    embed.add_field(
        name="Channel",
        value=edited_data["channel"].mention,
        inline=False
    )

    embed.add_field(
        name="Before",
        value=edited_data["before"][:1024],
        inline=False
    )

    embed.add_field(
        name="After",
        value=edited_data["after"][:1024],
        inline=False
    )

    if edited_data["links"]:
        embed.add_field(
            name="Links",
            value="\n".join(edited_data["links"])[:1024],
            inline=False
        )

    if edited_data["attachments"]:

        attachment_text = ""

        for attachment in edited_data["attachments"]:
            attachment_text += (
                f"[{attachment['filename']}]({attachment['url']})\n"
            )

        embed.add_field(
            name="Attachments",
            value=attachment_text[:1024],
            inline=False
        )

        first = edited_data["attachments"][0]

        if (
            first["content_type"]
            and first["content_type"].startswith("image")
        ):
            embed.set_image(url=first["url"])

    embed.set_footer(
        text=f"User ID: {edited_data['author'].id}"
    )

    await interaction.response.send_message(embed=embed)

#=========================================================================================================================================

VIEWLOGS_ROLE_ID = 1258549984387665943

@tree.command(name="view-logs", description="View the bot activity and user action logs")
@app_commands.describe(
    filter_type="Filter by action type",
    user="Filter logs by a specific user"
)
@app_commands.choices(filter_type=[
    app_commands.Choice(name="All", value="all"),
    app_commands.Choice(name="Slash Commands", value="slash_command"),
    app_commands.Choice(name="Message Deletes", value="message_delete"),
    app_commands.Choice(name="Message Edits", value="message_edit"),
    app_commands.Choice(name="Bot Actions", value="bot_action"),
])
async def view_logs(interaction: discord.Interaction, filter_type: app_commands.Choice[str] = None, user: discord.User = None):
    if not has_full_access(interaction.user) and not any(role.id == VIEWLOGS_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    filter_value = filter_type.value if filter_type else "all"
    filter_user_id = str(user.id) if user else None

    rows = [
        entry for entry in action_log
        if (filter_value == "all" or entry["action_type"] == filter_value)
        and (filter_user_id is None or entry["user_id"] == filter_user_id)
    ][:20]

    if not rows:
        await interaction.followup.send("No logs found matching those filters.", ephemeral=True)
        return

    embed = discord.Embed(
        title="📋 Activity Logs",
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc)
    )

    if filter_type:
        embed.set_footer(text=f"Filter: {filter_type.name}" + (f" | User: {user}" if user else ""))
    elif user:
        embed.set_footer(text=f"User: {user}")

    for row in rows:
        ts = row["timestamp"].strftime("%m/%d %H:%M")
        action = row["action_type"]
        name = row["username"] or "Unknown"
        cmd = f"`/{row['command_name']}`" if row["command_name"] else ""
        guild = row["guild_name"] or "DM"
        channel = f"#{row['channel_name']}" if row["channel_name"] else ""
        details = row["details"] or ""
        if len(details) > 80:
            details = details[:80] + "..."

        field_name = f"{ts} — {action} {cmd}"
        field_value = f"**User:** {name}\n**Server:** {guild} {channel}"
        if details:
            field_value += f"\n**Details:** {details}"

        embed.add_field(name=field_name, value=field_value, inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)


# Create group for fun commands
fun = app_commands.Group(name="fun", description="Fun commands and games")


# ---------------- JOKE ----------------
@fun.command(name="joke", description="Get a random joke")
async def joke(interaction: discord.Interaction):

    jokes = [
        "Why did the developer go broke? Because he used up all his cache.",
        "Why do programmers prefer dark mode? Because light attracts bugs.",
        "How many programmers does it take to change a light bulb? None, that's a hardware problem.",
        "What's a programmer's favorite place to hang out? The Foo Bar.",
        "A SQL query walks into a bar and asks two tables: Can I join you?"
    ]

    await interaction.response.send_message(random.choice(jokes))


# ---------------- ROLL ----------------
@fun.command(name="roll", description="Roll a number")
@app_commands.describe(max_num="Maximum number to roll")
async def roll(interaction: discord.Interaction, max_num: int = 100):

    if max_num < 1:
        return await interaction.response.send_message(
            "❌ Number must be greater than 0.",
            ephemeral=True
        )

    result = random.randint(1, max_num)

    await interaction.response.send_message(
        f"🎲 You rolled **{result}** (1-{max_num})"
    )


# ---------------- COIN FLIP ----------------
@fun.command(name="coinflip", description="Flip a coin")
async def coinflip(interaction: discord.Interaction):

    result = random.choice(["Heads", "Tails"])

    await interaction.response.send_message(
        f"🪙 The coin landed on **{result}**"
    )


# ---------------- CHOOSE ----------------
@fun.command(name="choose", description="Choose between options")
@app_commands.describe(options="Separate choices with commas")
async def choose(interaction: discord.Interaction, options: str):

    choices = [c.strip() for c in options.split(",") if c.strip()]

    if len(choices) < 2:
        return await interaction.response.send_message(
            "❌ Provide at least two options separated by commas.",
            ephemeral=True
        )

    selection = random.choice(choices)

    await interaction.response.send_message(
        f"🤔 I choose **{selection}**"
    )


# ---------------- RPS ----------------
@fun.command(name="rps", description="Play Rock Paper Scissors")
@app_commands.describe(choice="rock, paper, or scissors")
async def rps(interaction: discord.Interaction, choice: str):

    options = ["rock", "paper", "scissors"]
    choice = choice.lower()

    if choice not in options:
        return await interaction.response.send_message(
            "❌ Choose rock, paper, or scissors.",
            ephemeral=True
        )

    bot_choice = random.choice(options)

    if choice == bot_choice:
        result = "It's a tie!"
    elif (choice == "rock" and bot_choice == "scissors") or \
         (choice == "paper" and bot_choice == "rock") or \
         (choice == "scissors" and bot_choice == "paper"):
        result = "You win! 🎉"
    else:
        result = "I win! 😎"

    await interaction.response.send_message(
        f"🧍 You chose **{choice}**\n🤖 I chose **{bot_choice}**\n**{result}**"
    )


# ---------------- TRIVIA ----------------
@fun.command(name="trivia", description="Answer a trivia question")
async def trivia(interaction: discord.Interaction):

    questions = [
        ("What planet is known as the Red Planet?", "mars"),
        ("How many continents are there?", "7"),
        ("What language is primarily used for Discord bots?", "python"),
        ("Who painted the Mona Lisa?", "da vinci"),
        ("What is the largest ocean on Earth?", "pacific")
    ]

    question, answer = random.choice(questions)

    await interaction.response.send_message(f"🧠 Trivia Time!\n\n{question}")

    def check(m):
        return m.author == interaction.user and m.channel == interaction.channel

    try:
        msg = await interaction.client.wait_for("message", timeout=15, check=check)

        if msg.content.lower() == answer:
            await interaction.followup.send("✅ Correct!")
        else:
            await interaction.followup.send(f"❌ Wrong! The answer was **{answer}**.")

    except asyncio.TimeoutError:
        await interaction.followup.send(
            f"⏰ Time's up! The answer was **{answer}**."
        )


# ---------------- GUESS ----------------
@fun.command(name="guess", description="Guess the number (1-20)")
async def guess(interaction: discord.Interaction):

    number = random.randint(1, 20)

    await interaction.response.send_message(
        "🎯 Guess a number between **1 and 20**!"
    )

    def check(m):
        return m.author == interaction.user and m.channel == interaction.channel

    try:
        msg = await interaction.client.wait_for("message", timeout=15, check=check)

        if not msg.content.isdigit():
            return await interaction.followup.send("❌ That's not a number!")

        if int(msg.content) == number:
            await interaction.followup.send("🎉 Correct!")
        else:
            await interaction.followup.send(f"❌ Nope! The number was **{number}**.")

    except asyncio.TimeoutError:
        await interaction.followup.send(
            f"⏰ Time's up! The number was **{number}**."
        )


# ---------------- SHIP ----------------
@fun.command(name="ship", description="Check compatibility between two users")
@app_commands.describe(user1="First user", user2="Second user")
async def ship(interaction: discord.Interaction, user1: discord.Member, user2: discord.Member):

    percentage = random.randint(0, 100)

    await interaction.response.send_message(
        f"💖 {user1.mention} + {user2.mention}\nCompatibility: **{percentage}%**"
    )


# ---------------- ROAST ----------------
@fun.command(name="roast", description="Friendly roast")
async def roast(interaction: discord.Interaction):

    roasts = [
        "I'd explain it to you, but I left my crayons at home.",
        "You're not stupid, just buffering.",
        "You remind me of math… confusing.",
        "You have the confidence of a developer deploying on Friday."
    ]

    await interaction.response.send_message(random.choice(roasts))


# ---------------- COLOR ----------------
@fun.command(name="color", description="Generate a random color")
async def color(interaction: discord.Interaction):

    r = random.randint(0,255)
    g = random.randint(0,255)
    b = random.randint(0,255)

    embed = discord.Embed(
        title="🎨 Random Color",
        description=f"RGB: ({r}, {g}, {b})",
        color=discord.Color.from_rgb(r,g,b)
    )

    await interaction.response.send_message(embed=embed)


# ---------------- HOW OLD ----------------
@fun.command(name="howold", description="Find out how old you are (totally accurate)")
async def howold(interaction: discord.Interaction):

    age = random.randint(1,100)

    await interaction.response.send_message(
        f"🧓 You are **{age} years old**. Totally accurate."
    )


# ---------------- 8BALL ----------------
@fun.command(name="8ball", description="Ask the magic 8-ball a question")
@app_commands.describe(question="Your question")
async def eightball(interaction: discord.Interaction, question: str):

    responses = [
        "🎱 It is certain.",
        "🎱 Most likely.",
        "🎱 Outlook good.",
        "🎱 Ask again later.",
        "🎱 Don't count on it.",
        "🎱 Very doubtful."
    ]

    await interaction.response.send_message(
        f"❓ {question}\n{random.choice(responses)}"
    )


# REGISTER GROUP
tree.add_command(fun)


# ======== AUTO LOG SLASH COMMANDS ========
@client.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.application_command:
        record_log(
            user=interaction.user,
            action_type="slash_command",
            command_name=interaction.command.name if interaction.command else None,
            guild_name=interaction.guild.name if interaction.guild else None,
            channel_name=interaction.channel.name if interaction.channel and hasattr(interaction.channel, "name") else None,
        )
    elif interaction.type == discord.InteractionType.component:
        record_log(
            user=interaction.user,
            action_type="button_click",
            command_name=(interaction.data or {}).get("custom_id"),
            guild_name=interaction.guild.name if interaction.guild else None,
            channel_name=interaction.channel.name if interaction.channel and hasattr(interaction.channel, "name") else None,
        )


# ======== BOT ACTIVITY LOGGING ========
@client.event
async def on_connect():
    record_bot_log("bot_connect", details="Bot connected to Discord gateway.")


@client.event
async def on_disconnect():
    record_bot_log("bot_disconnect", details="Bot disconnected from Discord gateway.")


@client.event
async def on_resumed():
    record_bot_log("bot_resume", details="Bot session resumed.")


@client.event
async def on_member_join(member: discord.Member):
    record_bot_log(
        "member_join",
        details=f"{member} ({member.id}) joined.",
        guild_name=member.guild.name if member.guild else None,
    )
    channel = member.guild.get_channel(MEMBER_NOTIFICATION_CHANNEL_ID)
    if not channel:
        print(
            f"[MEMBER NOTIFICATION] Join channel {MEMBER_NOTIFICATION_CHANNEL_ID} "
            f"was not found in {member.guild.name}."
        )
        return
    embed = discord.Embed(
        title="WELCOME TO NEXTECH",
        description=(
            f"Thanks for joining, {member.mention}.\n\n"
            "Please read the server information before getting started."
        ),
        color=discord.Color.from_rgb(244, 168, 48),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_author(
        name=f"{member.display_name} joined the server",
        icon_url=member.display_avatar.url,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Member", value=member.mention, inline=True)
    embed.add_field(
        name="Members",
        value=str(member.guild.member_count or "—"),
        inline=True,
    )
    embed.set_footer(text="NexTech Development • Welcome")
    try:
        await channel.send(embed=embed)
    except discord.HTTPException as error:
        print(f"[MEMBER NOTIFICATION] Could not send join notification: {error}")


@client.event
async def on_member_remove(member: discord.Member):
    record_bot_log(
        "member_leave",
        details=f"{member} ({member.id}) left.",
        guild_name=member.guild.name if member.guild else None,
    )
    channel = member.guild.get_channel(MEMBER_NOTIFICATION_CHANNEL_ID)
    if not channel:
        print(
            f"[MEMBER NOTIFICATION] Leave channel {MEMBER_NOTIFICATION_CHANNEL_ID} "
            f"was not found in {member.guild.name}."
        )
        return
    embed = discord.Embed(
        title="MEMBER LEFT NEXTECH",
        description=(
            f"{member.mention} has left the server.\n\n"
            "We appreciate the time they spent with the NexTech community."
        ),
        color=discord.Color.from_rgb(59, 130, 246),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_author(
        name=f"{member.display_name} left the server",
        icon_url=member.display_avatar.url,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Member", value=str(member), inline=True)
    embed.add_field(
        name="Members",
        value=str(member.guild.member_count or "—"),
        inline=True,
    )
    embed.set_footer(text="NexTech Development • Member Update")
    try:
        await channel.send(embed=embed)
    except discord.HTTPException as error:
        print(f"[MEMBER NOTIFICATION] Could not send leave notification: {error}")


@client.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    record_bot_log(
        "member_ban",
        details=f"{user} ({user.id}) was banned.",
        guild_name=guild.name if guild else None,
    )


@client.event
async def on_member_unban(guild: discord.Guild, user: discord.User):
    record_bot_log(
        "member_unban",
        details=f"{user} ({user.id}) was unbanned.",
        guild_name=guild.name if guild else None,
    )


@client.event
async def on_message_delete(message: discord.Message):
    await remove_deleted_public_discord_message(message)
    if message.author and not message.author.bot:
        record_bot_log(
            "message_delete",
            details=f"Message by {message.author} deleted: {(message.content or '')[:120]}",
            guild_name=message.guild.name if message.guild else None,
            channel_name=message.channel.name if hasattr(message.channel, "name") else None,
        )


@client.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    # ── Handle the bot itself being disconnected or moved ──────────────────
    if member.id == client.user.id:
        bot_disconnected = before.channel is not None and after.channel is None
        bot_moved_away = (
            before.channel is not None
            and after.channel is not None
            and before.channel.id == AUTO_JOIN_VC_ID
            and after.channel.id != AUTO_JOIN_VC_ID
        )
        if bot_disconnected or bot_moved_away:
            # A temporary channel is only used while the bot remains connected
            # there. If it is kicked/disconnected, clear the override so the
            # watchdog returns it to the configured main VC.
            if before.channel and before.channel.id != AUTO_JOIN_VC_ID:
                _TEMP_VC_OVERRIDE_GUILDS.discard(before.channel.guild.id)
            print("[AUTO-VC] Voice state changed; watchdog will check the connection.")
        return

    # ── TTS announcements (existing behaviour) ─────────────────────────────
    vc = member.guild.voice_client
    if not vc or not vc.is_connected():
        return
    joined_bots_channel = (
        after.channel is not None
        and after.channel.id == vc.channel.id
        and (before.channel is None or before.channel.id != after.channel.id)
    )
    left_bots_channel = (
        before.channel is not None
        and before.channel.id == vc.channel.id
        and (after.channel is None or after.channel.id != before.channel.id)
    )
    if not joined_bots_channel and not left_bots_channel:
        return
    name = member.display_name
    if joined_bots_channel:
        if before.channel is not None:
            text = f"{name} has transferred over to {after.channel.name}."
        else:
            text = f"{name} has joined the channel."
    else:
        text = f"{name} has left the channel."

        # ── Auto-rejoin logic: if the bot is now alone, disconnect & rejoin ──
        if vc.channel.id == AUTO_JOIN_VC_ID:
            human_members = [m for m in vc.channel.members if not m.bot]
            if not human_members:
                print("[AUTO-VC] Bot is alone in AUTO_JOIN_VC — will disconnect and rejoin when someone arrives.")
                # Stay connected; the watchdog will keep it alive.
                # No action needed — staying in the VC is the desired behaviour.

    print(f"[VC TTS] Announcing: {text}")
    tmp_path = None
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp_path = tmp.name
        tmp.close()
        communicate = edge_tts.Communicate(text, voice="en-US-AriaNeural")
        await communicate.save(tmp_path)
        print(f"[VC TTS] Audio saved to {tmp_path}")
        if vc.is_playing():
            vc.stop()
            await asyncio.sleep(0.3)
        def _cleanup(e):
            if e:
                print(f"[VC TTS] Playback error: {e}")
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        source = await discord.FFmpegOpusAudio.from_probe(tmp_path)
        vc.play(source, after=_cleanup)
        print(f"[VC TTS] Playback started.")
    except Exception as e:
        print(f"[VC TTS ERROR] {e}")
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)



# ============================================================
# PRODUCT MANAGEMENT SLASH COMMANDS (staff only)
# ============================================================
def _is_staff(member: discord.Member) -> bool:
    try:
        return any(str(r.id) == NEXTECH_STAFF_ROLE_ID for r in member.roles)
    except Exception:
        return False


@tree.command(name="addproduct", description="Add a product to the website.")
@app_commands.describe(
    product_id="Unique product ID (e.g. NX-1001)",
    name="Product name",
    category="Category (e.g. Gear, Plugin, Service)",
    description="Short description",
    image_url="Image URL (optional, used if no image file attached)",
    robux_price="Price in Robux (optional)",
    image="Product image to upload (replaces image_url if provided)",
    product_file="Deliverable file sent to buyers via DM (e.g. .rbxm, .zip)",
    gamepass_id="Roblox Gamepass ID customers must own to receive files (optional)",
    price_usd="USD price for Stripe card checkout, e.g. 4.99 (optional)",
)
async def addproduct(interaction: discord.Interaction, product_id: str, name: str,
                     category: str, description: str,
                     image_url: str = "",
                     robux_price: int = 0,
                     image: discord.Attachment = None,
                     product_file: discord.Attachment = None,
                     gamepass_id: str = "",
                     price_usd: str = ""):
    if not isinstance(interaction.user, discord.Member) or not _is_staff(interaction.user):
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return
    products = load_products()
    if any(p.get("id", "").lower() == product_id.lower() for p in products):
        await interaction.response.send_message(
            f"❌ A product with ID `{product_id}` already exists. Use `/removeproduct` first.",
            ephemeral=True)
        return
    await interaction.response.defer(ephemeral=False)
    prod_dir = os.path.join(PRODUCT_FILES_DIR, product_id)
    os.makedirs(prod_dir, exist_ok=True)
    final_image = image_url
    final_file_path = ""
    final_file_url = ""
    if image:
        img_dest = os.path.join(prod_dir, image.filename)
        await image.save(img_dest)
        final_image = f"/static/product_files/{product_id}/{image.filename}"
    if product_file:
        file_dest = os.path.join(prod_dir, product_file.filename)
        await product_file.save(file_dest)
        final_file_path = file_dest
        final_file_url = f"/static/product_files/{product_id}/{product_file.filename}"
    price_usd_val = 0.0
    try:
        price_usd_val = float(price_usd) if price_usd else 0.0
    except ValueError:
        price_usd_val = 0.0
    products.append({
        "id": product_id, "name": name, "category": category,
        "description": description, "image": final_image,
        "robux": robux_price, "created": time.time(),
        "added_by": str(interaction.user),
        "file_path": final_file_path,
        "file_url": final_file_url,
        "gamepass_id": gamepass_id.strip() if gamepass_id else "",
        "price_usd": price_usd_val,
    })
    save_products(products)
    embed = discord.Embed(title="✅ Product Added", color=discord.Color.gold(),
                         description=f"**{name}** is now live on the website.")
    embed.add_field(name="ID", value=product_id, inline=True)
    embed.add_field(name="Category", value=category, inline=True)
    if robux_price:
        embed.add_field(name="Robux Price", value=f"R$ {robux_price:,}", inline=True)
    if price_usd_val:
        embed.add_field(name="USD Price (Stripe)", value=f"${price_usd_val:.2f}", inline=True)
    if gamepass_id:
        embed.add_field(name="Roblox Gamepass ID", value=f"`{gamepass_id.strip()}`", inline=True)
    if final_file_path:
        embed.add_field(name="Deliverable File", value=f"`{product_file.filename}`", inline=True)
    if final_image.startswith("/static"):
        embed.add_field(name="Image", value="Uploaded ✅", inline=True)
    elif final_image:
        embed.set_thumbnail(url=final_image)
    embed.set_footer(text=f"Added by {interaction.user}")
    await interaction.followup.send(embed=embed)


@tree.command(name="removeproduct", description="Remove a product from the website (staff only).")
@app_commands.describe(product_id="The product ID to remove")
async def removeproduct(interaction: discord.Interaction, product_id: str):
    if not isinstance(interaction.user, discord.Member) or not _is_staff(interaction.user):
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return
    products = load_products()
    new_products = [p for p in products if p.get("id", "").lower() != product_id.lower()]
    if len(new_products) == len(products):
        await interaction.response.send_message(
            f"❌ No product with ID `{product_id}` found.", ephemeral=True)
        return
    save_products(new_products)
    await interaction.response.send_message(
        f"🗑️ Removed product `{product_id}` from the website.")


@tree.command(name="addfaq", description="Add a Q&A article to the Help Center (staff only).")
@app_commands.describe(
    category="Category name (e.g. General, FAQ, Technical Support)",
    question="The question to display",
    answer="The answer text (use \\n for line breaks)",
)
async def addfaq(interaction: discord.Interaction, category: str, question: str, answer: str):
    if not isinstance(interaction.user, discord.Member) or not _is_staff(interaction.user):
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return
    faq = load_faq()
    faq_id = str(uuid.uuid4())[:8]
    faq.append({
        "id": faq_id,
        "category": category.strip(),
        "question": question.strip(),
        "answer": answer.strip(),
        "created": time.time(),
        "added_by": str(interaction.user),
    })
    save_faq(faq)
    embed = discord.Embed(title="✅ FAQ Article Added", color=discord.Color.gold(),
                          description="The article is now live on the Help Center.")
    embed.add_field(name="ID", value=f"`{faq_id}`", inline=True)
    embed.add_field(name="Category", value=category.strip(), inline=True)
    embed.add_field(name="Question", value=question.strip(), inline=False)
    embed.add_field(name="Answer", value=answer.strip()[:1000], inline=False)
    embed.set_footer(text=f"Added by {interaction.user} · Remove with /removefaq {faq_id}")
    await interaction.response.send_message(embed=embed)


@tree.command(name="removefaq", description="Remove a Q&A article from the Help Center (staff only).")
@app_commands.describe(faq_id="The 8-character FAQ entry ID (shown when added, or use /listfaq)")
async def removefaq(interaction: discord.Interaction, faq_id: str):
    if not isinstance(interaction.user, discord.Member) or not _is_staff(interaction.user):
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return
    faq = load_faq()
    new_faq = [f for f in faq if f.get("id") != faq_id.strip()]
    if len(new_faq) == len(faq):
        await interaction.response.send_message(
            f"❌ No FAQ entry found with ID `{faq_id}`. Use `/listfaq` to see all entries.", ephemeral=True)
        return
    save_faq(new_faq)
    await interaction.response.send_message(f"🗑️ FAQ entry `{faq_id}` removed from the Help Center.", ephemeral=True)


@tree.command(name="listfaq", description="List all Q&A articles on the Help Center (staff only).")
async def listfaq(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not _is_staff(interaction.user):
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return
    faq = load_faq()
    if not faq:
        await interaction.response.send_message(
            "📭 No FAQ articles yet. Use `/addfaq` to add one.", ephemeral=True)
        return
    lines = []
    for f in faq:
        q = f.get("question", "")
        short_q = q[:55] + "…" if len(q) > 55 else q
        lines.append(f"`{f['id']}` **[{f.get('category','?')}]** — {short_q}")
    embed = discord.Embed(
        title=f"📋 Help Center Articles ({len(faq)})",
        color=discord.Color.gold(),
        description="\n".join(lines[:25]))
    embed.set_footer(text="Use /removefaq <id> to remove an entry")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="my-account", description="View your NexTech portal account details.")
async def my_account_cmd(interaction: discord.Interaction):
    member = interaction.user
    if not isinstance(member, discord.Member):
        await interaction.response.send_message("❌ This command can only be used inside the NexTech Discord server.", ephemeral=True)
        return
    has_buyer = any(r.id == BUYER_ROLE_ID for r in member.roles)
    staff_ids = {str(NEXTECH_STAFF_ROLE_ID), str(AGENT_STAFF_ROLE_ID)}
    role_ids = {str(r.id) for r in member.roles}
    is_staff = bool(role_ids & staff_ids)
    if not has_buyer and not is_staff:
        embed = discord.Embed(
            title="❌ Not Verified Yet",
            description=(
                "You don't have the **@Whitelisted Group Member** role yet, so you don't have a portal account.\n\n"
                "**Complete verification to unlock your account:**\n"
                "1️⃣ Join one of the required NexTech Roblox groups\n"
                "2️⃣ Add `whitelistmember` to your Roblox profile bio\n"
                "3️⃣ Run `/roblox-group` in this server and click **Verify with Bio**\n"
                "4️⃣ Once verified, run `/my-account` again to see your account"
            ),
            color=discord.Color.red()
        )
        embed.add_field(
            name="Required Groups (join any one)",
            value=(
                "• [F.T.D NexTech Development](https://www.roblox.com/groups/11146129)\n"
                "• [NexTech Development Game Administrator](https://www.roblox.com/groups/6419702)\n"
                "• [Northgate County California](https://www.roblox.com/groups/35272985)"
            ),
            inline=False
        )
        embed.set_footer(text="NexTech Interactive Development LLC · Run /roblox-group to get verified")
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="Verification Guide",
            style=discord.ButtonStyle.link,
            url="https://futures-organization-1.gitbook.io/nextechs-development-support/nextech-utility-bot/supported-cases/group-verification",
            emoji="📖"
        ))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        return
    accts = load_portal_accounts()
    acct = accts.get(str(member.id), {})
    account_no = "NX-" + str(member.id)[-6:]
    credits = acct.get("credits", 0)
    roblox = acct.get("roblox_username", "")
    first_login = acct.get("first_login")
    last_login = acct.get("last_login")
    purchases = acct.get("purchases", [])
    products = load_products()
    if is_staff:
        rank = "⚡ Staff"
        rank_color = discord.Color.red()
    else:
        rank = "✅ Whitelisted Member"
        rank_color = discord.Color.gold()
    embed = discord.Embed(
        title=f"Your NexTech Account",
        description=f"Here's a summary of your portal account, {member.display_name}.",
        color=rank_color
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Account No.", value=f"`{account_no}`", inline=True)
    embed.add_field(name="Portal Rank", value=rank, inline=True)
    embed.add_field(name="Credits", value=f"**{credits}** credits", inline=True)
    embed.add_field(name="Roblox Username", value=f"`{roblox}`" if roblox else "Not linked — log into the portal to link", inline=False)
    if first_login:
        embed.add_field(name="Member Since", value=f"<t:{int(first_login)}:D> (<t:{int(first_login)}:R>)", inline=True)
    if last_login:
        embed.add_field(name="Last Login", value=f"<t:{int(last_login)}:R>", inline=True)
    if purchases:
        prod_map = {p.get("id"): p.get("name", p.get("id")) for p in products}
        lines = []
        for pur in purchases[-10:]:
            pname = prod_map.get(pur.get("product_id"), pur.get("product_name") or pur.get("product_id") or "Unknown")
            ts = pur.get("ts")
            lines.append(f"📦 **{pname}**" + (f" — <t:{int(ts)}:d>" if ts else ""))
        embed.add_field(name=f"Owned Products ({len(purchases)})", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Owned Products", value="*None yet — visit the portal to browse products.*", inline=False)
    if not acct:
        embed.set_footer(text="⚠ You haven't logged into the Customer Portal yet. Visit the portal to activate your account.")
    else:
        embed.set_footer(text=f"NexTech Portal · {account_no} · {len(purchases)} product(s) owned")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="portal-account", description="Look up a member's NexTech portal account (staff only).")
@app_commands.describe(member="The Discord member to look up")
async def portal_account_cmd(interaction: discord.Interaction, member: discord.Member):
    if not isinstance(interaction.user, discord.Member) or not _is_staff(interaction.user):
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return
    accts = load_portal_accounts()
    acct = accts.get(str(member.id), {})
    staff_ids = {str(NEXTECH_STAFF_ROLE_ID), str(AGENT_STAFF_ROLE_ID)}
    role_ids = {str(r.id) for r in member.roles}
    if role_ids & staff_ids:
        rank = "⚡ Staff"
        rank_color = discord.Color.red()
    elif any(r.id == BUYER_ROLE_ID for r in member.roles):
        rank = "✅ Whitelisted Member"
        rank_color = discord.Color.gold()
    else:
        rank = "👤 Guest"
        rank_color = discord.Color.light_grey()
    products = load_products()
    purchases = acct.get("purchases", [])
    roblox = acct.get("roblox_username", "")
    first_login = acct.get("first_login")
    last_login = acct.get("last_login")
    account_no = "NX-" + str(member.id)[-6:]
    credits = acct.get("credits", 0)
    embed = discord.Embed(
        title=f"Portal Account — {member.display_name}",
        color=rank_color
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Discord", value=f"{member}\n`{member.id}`", inline=True)
    embed.add_field(name="Account No.", value=f"`{account_no}`", inline=True)
    embed.add_field(name="Portal Rank", value=rank, inline=True)
    embed.add_field(name="Roblox Username", value=f"`{roblox}`" if roblox else "Not linked", inline=True)
    embed.add_field(name="Credits", value=f"**{credits}** credits", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)
    if first_login:
        embed.add_field(name="First Login", value=f"<t:{int(first_login)}:R>", inline=True)
    if last_login:
        embed.add_field(name="Last Login", value=f"<t:{int(last_login)}:R>", inline=True)
    if purchases:
        prod_map = {p.get("id"): p.get("name", p.get("id")) for p in products}
        lines = []
        for pur in purchases[-15:]:
            pname = prod_map.get(pur.get("product_id"), pur.get("product_name") or pur.get("product_id") or "Unknown")
            ts = pur.get("ts")
            lines.append(f"📦 **{pname}**" + (f" — <t:{int(ts)}:d>" if ts else ""))
        embed.add_field(name=f"Owned Products ({len(purchases)})", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Owned Products", value="*None on record*", inline=False)
    if not acct:
        embed.set_footer(text="⚠ This user has not logged into the portal yet — no account record found.")
    else:
        embed.set_footer(text=f"NexTech Portal · Account {account_no} · {len(purchases)} product(s) owned")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="listproducts", description="List all products on the website (staff only).")
async def listproducts(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not _is_staff(interaction.user):
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return
    products = load_products()
    if not products:
        await interaction.response.send_message(
            "📭 No products on the website yet. Use `/addproduct` to add one.",
            ephemeral=True)
        return
    embed = discord.Embed(title=f"🛒 Products ({len(products)})",
                         color=discord.Color.gold())
    for p in products[:25]:
        price = f"R$ {p.get('robux', 0):,}" if p.get("robux") else "—"
        embed.add_field(
            name=f"`{p.get('id', '?')}` · {p.get('name', '?')}",
            value=f"**{p.get('category', '—')}** · {price}\n{p.get('description', '')[:100]}",
            inline=False)
    if len(products) > 25:
        embed.set_footer(text=f"Showing first 25 of {len(products)}")
    await interaction.response.send_message(embed=embed, ephemeral=True)




# ============================================================
# /create-staffuser — staff registration command
# ============================================================

class StaffRegistrationModal(discord.ui.Modal, title="Staff Registration"):
    discord_username = discord.ui.TextInput(
        label="Your Discord Username",
        placeholder="e.g. johndoe or johndoe#1234",
        max_length=100,
    )
    roblox_url = discord.ui.TextInput(
        label="Your Roblox Profile URL",
        placeholder="https://www.roblox.com/users/123456/profile",
        max_length=200,
    )
    group_url = discord.ui.TextInput(
        label="Our Main Roblox Group Link",
        placeholder="https://www.roblox.com/groups/123456/groupname",
        max_length=200,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        reg_id = secrets.token_hex(8)
        staff_registrations.append({
            "id": reg_id,
            "discord_username": self.discord_username.value.strip(),
            "discord_id": str(interaction.user.id),
            "roblox_url": self.roblox_url.value.strip(),
            "group_url": self.group_url.value.strip(),
            "submitted_at": time.strftime("%Y-%m-%d %H:%M:%S UTC"),
        })
        record_log(
            user=interaction.user,
            action_type="staff_registration",
            command_name="create-staffuser",
            guild_name=interaction.guild.name if interaction.guild else None,
            channel_name=interaction.channel.name if interaction.channel else None,
            details=f"Discord: {self.discord_username.value.strip()} | Roblox: {self.roblox_url.value.strip()}",
        )
        await interaction.response.send_message(
            "✅ Your registration has been submitted! The developer will review it and create your account. "
            "You will receive your personal login key once approved.",
            ephemeral=True,
        )


async def create_staffuser(interaction: discord.Interaction):
    await interaction.response.send_modal(StaffRegistrationModal())


# ============================================================
# /portal-login — customer portal access command
# ============================================================
async def portal_login_cmd(interaction: discord.Interaction):
    member = interaction.user
    if not isinstance(member, discord.Member):
        await interaction.response.send_message(
            "❌ This command can only be used inside the NexTech Discord server.",
            ephemeral=True
        )
        return

    has_buyer = any(r.id == BUYER_ROLE_ID for r in member.roles)
    staff_ids = {str(NEXTECH_STAFF_ROLE_ID), str(AGENT_STAFF_ROLE_ID)}
    role_ids = {str(r.id) for r in member.roles}
    is_staff = bool(role_ids & staff_ids)

    if not has_buyer and not is_staff:
        embed = discord.Embed(
            title="❌ Not Verified Yet",
            description=(
                "You don't have the **@Whitelisted Group Member** role required to access the NexTech Customer Portal.\n\n"
                "**To get access, complete verification:**\n"
                "1️⃣ Join one of the required NexTech Roblox groups\n"
                "2️⃣ Add `whitelistmember` to your Roblox profile bio\n"
                "3️⃣ Run `/roblox-group` in this server and click **Verify with Bio**\n"
                "4️⃣ Once verified, come back and run `/portal-login` again\n\n"
                "Need help? Check the verification guide or contact a staff member."
            ),
            color=discord.Color.red()
        )
        embed.add_field(
            name="Required Groups (join any one)",
            value=(
                "• [F.T.D NexTech Development](https://www.roblox.com/groups/11146129)\n"
                "• [NexTech Development Game Administrator](https://www.roblox.com/groups/6419702)\n"
                "• [Northgate County California](https://www.roblox.com/groups/35272985)"
            ),
            inline=False
        )
        embed.set_footer(text="NexTech Interactive Development LLC · Run /roblox-group to verify")
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="Verification Guide",
            style=discord.ButtonStyle.link,
            url="https://futures-organization-1.gitbook.io/nextechs-development-support/nextech-utility-bot/supported-cases/group-verification",
            emoji="📖"
        ))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        return

    base = _get_base_url()
    portal_url = f"{base}/portal"
    login_url = f"{base}/auth/roblox?next=/portal"

    accts = load_portal_accounts()
    acct = accts.get(str(member.id), {})
    has_account = bool(acct)
    account_no = "NX-" + str(member.id)[-6:]
    credits = acct.get("credits", 0)
    roblox = acct.get("roblox_username", "")
    purchases = acct.get("purchases", [])
    first_login = acct.get("first_login")

    if has_account:
        embed = discord.Embed(
            title="🌐 NexTech Customer Portal",
            description=(
                f"Welcome back, **{member.display_name}**! Click the button below to log in to your portal account."
            ),
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Account No.", value=f"`{account_no}`", inline=True)
        embed.add_field(name="Credits", value=f"**{credits}** credits", inline=True)
        embed.add_field(name="Products Owned", value=str(len(purchases)), inline=True)
        if roblox:
            embed.add_field(name="Roblox", value=f"`{roblox}`", inline=True)
        if first_login:
            embed.add_field(name="Member Since", value=f"<t:{int(first_login)}:D>", inline=True)
        embed.set_footer(text="Your login is secured via Discord OAuth — no password needed.")
    else:
        embed = discord.Embed(
            title="🌐 NexTech Customer Portal",
            description=(
                f"Hey **{member.display_name}**! You have portal access but haven't logged in yet.\n\n"
                "Click the button below to create your account — it only takes a second using your Discord account."
            ),
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(
            name="What you get",
            value="• View & download your purchased products\n• Track your credits\n• Link your Roblox account\n• Access your order history",
            inline=False
        )
        embed.set_footer(text="Secured via Discord OAuth · NexTech Interactive Development LLC")

    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="Login to Portal" if has_account else "Create My Account",
        style=discord.ButtonStyle.link,
        url=login_url,
        emoji="🔐"
    ))
    view.add_item(discord.ui.Button(
        label="Visit Portal",
        style=discord.ButtonStyle.link,
        url=portal_url,
        emoji="🌐"
    ))

    record_log(
        user=member,
        action_type="portal_login_request",
        command_name="portal-login",
        guild_name=interaction.guild.name if interaction.guild else None,
        channel_name=interaction.channel.name if interaction.channel else None,
        details=f"Account exists: {has_account} | Buyer: {has_buyer}",
    )

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)













def dashboard_system_status():
    """Return public operational status for the dashboard status page."""
    bot_client = tree.client
    guild = bot_client.get_guild(int(NEXTECH_GUILD_ID))
    uptime_seconds = max(0, int(time.time() - start_time))
    return {
        "online": bool(bot_client.is_ready() and not bot_client.is_closed()),
        "uptime_seconds": uptime_seconds,
        "bot_name": str(bot_client.user) if bot_client.user else "NexTech Bot",
        "member_count": guild.member_count if guild else None,
        "server_count": len(bot_client.guilds),
        "server_name": guild.name if guild else "Primary Discord server",
    }


try:
    from dashboard import dashboard_bp, configure_dashboard
except ImportError as exc:
    dashboard_bp = None
    configure_dashboard = None
    print(f"[DASHBOARD] Dashboard import failed; bot will continue without dashboard routes: {exc}")

if dashboard_bp is not None and configure_dashboard is not None:
    try:
        configure_dashboard(
            authorize_dashboard=authorize_dashboard,
            dashboard_snapshot=dashboard_snapshot,
            dashboard_members=dashboard_members,
            load_bot_actions=load_bot_actions,
            load_warnings=load_warnings,
            load_mod_logs=load_mod_logs,
            load_tickets=load_tickets,
            load_products=load_products,
            save_products=save_products,
            load_faq=load_faq,
            save_faq=save_faq,
            dashboard_create_product=dashboard_create_product,
            dashboard_customer_accounts=dashboard_customer_accounts,
            dashboard_create_customer_account=dashboard_create_customer_account,
            dashboard_customer_account_action=dashboard_customer_account_action,
            dashboard_team_profiles=dashboard_team_profiles,
            dashboard_save_team_profile=dashboard_save_team_profile,
            dashboard_remove_team_profile=dashboard_remove_team_profile,
            request_dashboard_email_login=request_dashboard_email_login,
            verify_dashboard_email_login=verify_dashboard_email_login,
            redeem_dashboard_profile_code=redeem_dashboard_profile_code,
            dashboard_ticket_messages=dashboard_ticket_messages,
            dashboard_reply_ticket=dashboard_reply_ticket,
            create_web_ticket=create_web_ticket,
            create_web_report=create_web_report,
            # Keep the public website available even when report-list storage is not configured.
            # The dashboard module can replace these safe fallbacks with real storage callbacks.
            list_web_reports=lambda actor: [],
            list_all_web_reports=lambda actor: [],
            update_web_report_status=lambda report_id, status, public_update, actor: (
                {"error": "Report status updates are not configured on this server."}, 503
            ),
            support_ai_reply=support_ai_reply,
            record_bot_action=record_bot_action,
            system_status=dashboard_system_status,
            get_public_community_members=get_public_community_members,
            get_public_community_member=get_public_community_member,
            get_public_community_staff=get_public_community_staff,
            get_public_community_summary=get_public_community_summary,
            get_live_community_messages=get_live_community_messages,
            get_community_session=get_community_session,
            get_member_community_notifications=get_member_community_notifications,
            mark_member_community_notifications_read=mark_member_community_notifications_read,
            new_id=lambda: str(uuid.uuid4())[:8],
            now=time.time,
        )
        app.register_blueprint(dashboard_bp)
    except (ValueError, TypeError) as exc:
        # A dashboard module with an outdated service contract must not prevent
        # the Discord bot itself from starting. Dashboard routes stay disabled
        # until dashboard.py provides the required callables.
        print(f"[DASHBOARD] Dashboard setup skipped; bot will continue without dashboard routes: {exc}")


# Run the bot
if __name__ == '__main__':
    # Run one Flask/Socket.IO server only. Starting the same app on both
    # 5000 and 8080 caused avoidable port conflicts in Replit.
    dashboard_port = int(os.environ.get('PORT', '5000'))
    t = Thread(target=run_flask, args=(dashboard_port,), daemon=True)
    t.start()
    print(f"[DASHBOARD] Starting web server on port {dashboard_port}")
    time.sleep(1)
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is not configured bot offline.")
    try:
        client.run(DISCORD_BOT_TOKEN)
    except discord.LoginFailure:
        raise SystemExit(
            "[DISCORD LOGIN ERROR] Discord rejected DISCORD_BOT_TOKEN (401 Unauthorized). "
            "The token is invalid, reset, or the wrong value was copied. Update the "
            "DISCORD_BOT_TOKEN secret with the current Bot Token from Discord Developer "
            "Portal > Applications > your app > Bot, then restart. Do not use the "
            "Application ID, Public Key, or client secret, and never share the bot token."
        )