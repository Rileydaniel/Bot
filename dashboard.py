"""NexTech operations dashboard Blueprint.

Register ``dashboard_bp`` on the Flask app, then call ``configure_dashboard``
with the callbacks already wired at the bottom of bot.py. The dashboard uses
the bot's existing Roblox customer-token flow and does not add or replace
authentication endpoints.
"""

from __future__ import annotations

import html
import os
import re
from decimal import Decimal, InvalidOperation
from datetime import datetime
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, render_template, request
from werkzeug.datastructures import FileStorage

from supportchat import (
    DISCORD_INVITE,
    STAFF_ROLE,
    SUPPORT_EMAIL,
    SUPPORT_HOURS,
    SUGGESTED_QUESTIONS,
    answer_support_message,
)


dashboard_bp = Blueprint("dashboard", __name__)
_services: dict[str, Any] = {}

_REQUIRED_SERVICES = (
    "authorize_dashboard",
    "dashboard_snapshot",
    "dashboard_members",
    "load_bot_actions",
    "load_warnings",
    "load_mod_logs",
    "load_tickets",
    "load_products",
    "save_products",
    "load_faq",
    "save_faq",
    "dashboard_create_product",
    "dashboard_customer_accounts",
    "dashboard_create_customer_account",
    "dashboard_customer_account_action",
    "dashboard_team_profiles",
    "dashboard_save_team_profile",
    "dashboard_remove_team_profile",
    "request_dashboard_email_login",
    "verify_dashboard_email_login",
    "redeem_dashboard_profile_code",
    "dashboard_ticket_messages",
    "dashboard_reply_ticket",
    "create_web_ticket",
    "create_web_report",
    "list_web_reports",
    "list_all_web_reports",
    "update_web_report_status",
    "support_ai_reply",
    "record_bot_action",
    "system_status",
    "get_public_community_members",
    "get_public_community_member",
    "get_public_community_staff",
    "get_public_community_summary",
    "get_live_community_messages",
    "get_community_session",
    "get_member_community_notifications",
    "mark_member_community_notifications_read",
    "new_id",
    "now",
)


def configure_dashboard(**services: Any) -> None:
    """Supply the app's existing callbacks, with safe fallbacks for optional reports."""
    # Report routes should not prevent the entire website from starting when the
    # bot has not wired its report-storage callbacks yet.
    services.setdefault("list_web_reports", lambda actor: [])
    services.setdefault("list_all_web_reports", lambda actor: [])
    services.setdefault(
        "update_web_report_status",
        lambda report_id, status, public_update, actor: (
            {"error": "Report status updates are not configured on this server."}, 503
        ),
    )

    missing = [
        name for name in _REQUIRED_SERVICES
        if not callable(services.get(name))
    ]
    if missing:
        raise ValueError(
            "Dashboard setup is missing callable services: " + ", ".join(missing)
        )
    _services.update(services)


def _token_from_request() -> str:
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.headers.get("X-Customer-Token", "").strip()


def _auth_error(message: str, status: int):
    return jsonify({"error": message}), status


def _current_actor(minimum_role: int = 1):
    authorize = _services.get("authorize_dashboard")
    if not callable(authorize):
        return None, _auth_error("Dashboard authorization is not configured.", 503)

    token = _token_from_request()
    if not token:
        return None, _auth_error("Sign in to continue.", 401)

    try:
        actor = authorize(token)
    except Exception:
        current_app.logger.exception("Dashboard authorization callback failed")
        return None, _auth_error("Could not verify your dashboard access.", 503)

    if not isinstance(actor, dict):
        return None, _auth_error("Your sign-in has expired. Please sign in again.", 401)

    is_protected_staff = actor.get("is_protected_staff") is True
    if minimum_role >= 2 and not is_protected_staff:
        return None, _auth_error("Your Roblox account does not have the protected staff role.", 403)

    if not is_protected_staff:
        actor = dict(actor)
        actor["role_level"] = min(_role_level(actor), 1)
        if actor["role_level"] == 1:
            actor["role_name"] = "Member"

    try:
        role_level = int(actor.get("role_level", 0))
    except (TypeError, ValueError):
        role_level = 0

    if role_level < minimum_role:
        return None, _auth_error("Your NexTech role does not allow this action.", 403)
    return actor, None


def _current_community_member():
    get_session = _services["get_community_session"]
    token = request.headers.get("X-Discord-Community-Token", "").strip()
    if not token:
        return None, _auth_error("Sign in with Discord to view your notifications.", 401)
    try:
        member = get_session(token)
    except Exception:
        current_app.logger.exception("Discord community session lookup failed")
        return None, _auth_error("Discord sign-in is temporarily unavailable.", 503)
    if not isinstance(member, dict) or not str(member.get("discord_id", "")).isdecimal():
        return None, _auth_error("Your Discord sign-in expired. Sign in again.", 401)
    return member, None


def _call_service(name: str, *args: Any, **kwargs: Any):
    callback = _services.get(name)
    if not callable(callback):
        raise RuntimeError(f"Dashboard service {name!r} is not configured")
    return callback(*args, **kwargs)


def _json_result(result: Any, default_status: int = 200):
    status = default_status
    payload = result
    if isinstance(result, tuple) and len(result) == 2:
        payload, status = result
    if not isinstance(payload, (dict, list)):
        payload = {"result": payload}
    return jsonify(payload), int(status)


def _role_level(actor: dict[str, Any]) -> int:
    try:
        return int(actor.get("role_level", 0))
    except (TypeError, ValueError):
        return 0


def _public_actor(actor: dict[str, Any]) -> dict[str, Any]:
    return {
        "display_name": str(actor.get("display_name") or actor.get("roblox_username") or "NexTech member")[:120],
        "roblox_username": str(actor.get("roblox_username") or "")[:120],
        "discord_username": str(actor.get("discord_username") or "")[:120],
        "discord_id": str(actor.get("discord_id") or ""),
        "role_name": str(actor.get("role_name") or "Member")[:60],
        "role_level": _role_level(actor),
        "avatar": str(actor.get("avatar") or "")[:500],
    }


def _all_tickets() -> list[dict[str, Any]]:
    tickets = _call_service("load_tickets")
    if isinstance(tickets, dict):
        records = list(tickets.values())
    elif isinstance(tickets, list):
        records = tickets
    else:
        records = []
    return [record for record in records if isinstance(record, dict)]


def _visible_warnings(actor: dict[str, Any]) -> list[dict[str, Any]]:
    warnings = _call_service("load_warnings")
    if not isinstance(warnings, list):
        return []
    if _role_level(actor) >= 3:
        visible = warnings
    else:
        actor_id = str(actor.get("discord_id") or "")
        visible = [
            warning
            for warning in warnings
            if isinstance(warning, dict)
            and str(warning.get("user_id") or warning.get("discord_id") or "") == actor_id
        ]
    return [warning for warning in visible if isinstance(warning, dict)]


def _ticket_summary(ticket: dict[str, Any]) -> dict[str, Any]:
    # Ticket message history and internal Discord channel IDs stay server-side.
    keys = (
        "case_id",
        "case_name",
        "case_type",
        "discord_id",
        "discord_username",
        "roblox_username",
        "reason",
        "confirmed_type",
        "opened_at",
        "opened_date",
        "status",
        "source",
    )
    return {key: ticket[key] for key in keys if key in ticket}


def _visible_tickets(actor: dict[str, Any]) -> list[dict[str, Any]]:
    tickets = _all_tickets()
    if _role_level(actor) < 2:
        actor_id = str(actor.get("discord_id") or "")
        tickets = [
            ticket
            for ticket in tickets
            if str(ticket.get("discord_id") or "") == actor_id
        ]
    tickets.sort(
        key=lambda ticket: float(ticket.get("opened_at") or 0),
        reverse=True,
    )
    return [_ticket_summary(ticket) for ticket in tickets[:200]]


def _safe_products() -> list[dict[str, Any]]:
    products = _call_service("load_products")
    if not isinstance(products, list):
        return []
    allowed = (
        "id",
        "name",
        "category",
        "description",
        "image",
        "robux",
        "price_usd",
        "currency",
        "created",
        "added_by",
    )
    return [
        {key: product[key] for key in allowed if key in product}
        for product in products
        if isinstance(product, dict)
    ]


def _safe_faq() -> list[dict[str, Any]]:
    entries = _call_service("load_faq")
    if not isinstance(entries, list):
        return []
    allowed = ("id", "category", "question", "answer", "created", "added_by")
    return [
        {key: entry[key] for key in allowed if key in entry}
        for entry in entries
        if isinstance(entry, dict)
    ]


def _record_timestamp(record: dict[str, Any]) -> float | None:
    for key in ("timestamp", "ts", "created", "created_at"):
        value = record.get(key)
        if value is None:
            continue
        try:
            number = float(value)
            if number > 0:
                return number / 1000 if number > 100_000_000_000 else number
        except (TypeError, ValueError):
            pass
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.timestamp()
        except (TypeError, ValueError, OverflowError):
            continue
    return None


def _record_text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _public_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    warnings = _call_service("load_warnings")
    if isinstance(warnings, list):
        for warning in warnings:
            if not isinstance(warning, dict):
                continue
            timestamp = _record_timestamp(warning)
            try:
                points = max(1, int(warning.get("points", 1)))
            except (TypeError, ValueError):
                points = 1
            records.append({
                "type": "warning",
                "action": "warning",
                "label": "Warning",
                "source": "Discord",
                "target": _record_text(warning.get("username") or warning.get("display_name") or warning.get("user_id"), 120) or "NexTech member",
                "target_id": _record_text(warning.get("user_id") or warning.get("discord_id"), 40),
                "reason": _record_text(warning.get("reason") or warning.get("details") or "No reason recorded"),
                "moderator": _record_text(warning.get("moderator") or warning.get("staff_name") or "Not recorded", 120),
                "points": points,
                "timestamp": timestamp,
                "timestamp_str": _record_text(warning.get("timestamp_str") or warning.get("date"), 80),
                "case_id": _record_text(warning.get("case_id") or warning.get("id"), 60),
            })

    mod_logs = _call_service("load_mod_logs")
    if isinstance(mod_logs, list):
        for entry in mod_logs:
            if not isinstance(entry, dict):
                continue
            action = _record_text(entry.get("action"), 40).lower()
            if action not in {"ban", "termination"}:
                continue
            records.append({
                "type": "ban",
                "action": action,
                "label": "Permanent ban" if action == "ban" else "Recorded termination",
                "source": "Roblox",
                "target": _record_text(entry.get("roblox_display_name") or entry.get("roblox_username"), 120) or "Roblox user",
                "target_id": _record_text(entry.get("roblox_user_id"), 40),
                "reason": _record_text(entry.get("reason") or "No reason recorded"),
                "moderator": _record_text(entry.get("moderator") or "Not recorded", 120),
                "timestamp": _record_timestamp(entry),
                "timestamp_str": _record_text(entry.get("ts_str"), 80),
                "case_id": _record_text(entry.get("case_id") or entry.get("id"), 60),
                "profile_url": _record_text(entry.get("profile_url"), 300),
            })

    actions = _call_service("load_bot_actions")
    if isinstance(actions, list):
        for entry in actions:
            if not isinstance(entry, dict):
                continue
            event = _record_text(entry.get("action_type"), 60).lower()
            if event not in {"member_ban", "member_unban"}:
                continue
            details = _record_text(entry.get("details"), 500)
            match = re.match(r"(.+?)\s+\((\d+)\)\s+was\s+(banned|unbanned)\.?", details, re.IGNORECASE)
            target = _record_text(entry.get("target_username") or entry.get("username") or (match.group(1) if match else "Discord member"), 120)
            target_id = _record_text(entry.get("target_user_id") or (match.group(2) if match else ""), 40)
            action = "unban" if event == "member_unban" else "ban"
            records.append({
                "type": "community",
                "action": action,
                "label": "Server ban" if action == "ban" else "Server unban",
                "source": _record_text(entry.get("guild_name") or "NexTech Discord", 120),
                "target": target or "Discord member",
                "target_id": target_id,
                "reason": _record_text(entry.get("reason") or details or "No reason recorded"),
                "moderator": _record_text(entry.get("moderator") or "Not recorded in the ban event", 120),
                "timestamp": _record_timestamp(entry),
                "timestamp_str": _record_text(entry.get("timestamp_str"), 80),
                "case_id": _record_text(entry.get("case_id") or entry.get("id"), 60),
            })

    records.sort(key=lambda item: item.get("timestamp") or 0, reverse=True)
    return records


@dashboard_bp.get("/api/community/records")
def community_records_api():
    record_type = request.args.get("type", "ban").strip().lower()
    if record_type not in {"ban", "warning", "community"}:
        return jsonify({"error": "Choose ban, warning, or community records."}), 400
    query = request.args.get("q", "").strip()[:120].casefold()
    try:
        all_records = _public_records()
    except Exception:
        current_app.logger.exception("Could not load public moderation records")
        return jsonify({"error": "Public records are temporarily unavailable."}), 503

    def matches_type(record: dict[str, Any], selected: str) -> bool:
        if selected == "ban":
            return record["type"] == "ban" or (
                record["type"] == "community" and record["action"] == "ban"
            )
        return record["type"] == selected

    counts = {
        selected: sum(1 for record in all_records if matches_type(record, selected))
        for selected in ("ban", "warning", "community")
    }
    filtered = [
        record for record in all_records
        if matches_type(record, record_type)
        and (not query or query in " ".join(str(value) for value in record.values()).casefold())
    ]
    try:
        limit = min(100, max(1, int(request.args.get("limit", 50))))
    except (TypeError, ValueError):
        limit = 50
    return jsonify({
        "records": filtered[:limit],
        "counts": counts,
        "total": len(filtered),
        "type": record_type,
        "q": request.args.get("q", "")[:120],
    })


@dashboard_bp.get("/api/community/status")
def community_status_api():
    try:
        status = _call_service("system_status")
    except Exception:
        current_app.logger.exception("Could not load bot status")
        return jsonify({"error": "Bot status is temporarily unavailable."}), 503
    if not isinstance(status, dict):
        return jsonify({"error": "Bot status is temporarily unavailable."}), 503
    return jsonify(status)


@dashboard_bp.get("/api/community/live")
def community_live_api():
    try:
        messages = _services["get_live_community_messages"]()
        if not isinstance(messages, list):
            raise TypeError("The live Discord message service returned an invalid result.")
        return jsonify({"messages": messages[:50]})
    except Exception:
        current_app.logger.exception("Could not load live public Discord messages")
        return jsonify({"error": "Live Discord chat is temporarily unavailable."}), 503


@dashboard_bp.get("/api/community/members")
def community_members_api():
    query = (request.args.get("q") or "").strip()[:80]
    try:
        result = _services["get_public_community_members"](query)
        if not isinstance(result, dict) or not isinstance(result.get("members"), list):
            raise TypeError("The public Discord member service returned an invalid result.")
        return jsonify(result)
    except Exception:
        current_app.logger.exception("Could not load public Discord member profiles")
        return jsonify({"error": "Discord member profiles are temporarily unavailable."}), 503


@dashboard_bp.get("/api/community/members/<discord_id>")
def community_member_api(discord_id: str):
    if not discord_id.isdecimal():
        return jsonify({"error": "Member not found."}), 404
    try:
        member = _services["get_public_community_member"](discord_id)
        if member is None:
            return jsonify({"error": "Member not found."}), 404
        return jsonify({"member": member})
    except Exception:
        current_app.logger.exception("Could not load public Discord profile")
        return jsonify({"error": "Discord member profile is temporarily unavailable."}), 503


@dashboard_bp.get("/api/community/team")
def community_team_api():
    try:
        members = _services["get_public_community_staff"]()
        return jsonify({"members": members})
    except Exception:
        current_app.logger.exception("Could not load the public staff directory")
        return jsonify({"error": "The staff directory is temporarily unavailable."}), 503


@dashboard_bp.get("/api/community/summary")
def community_summary_api():
    try:
        return jsonify(_services["get_public_community_summary"]())
    except Exception:
        current_app.logger.exception("Could not load NexTech community statistics")
        return jsonify({"error": "Community statistics are temporarily unavailable."}), 503


@dashboard_bp.get("/api/community/notifications")
def community_notifications_api():
    member, error = _current_community_member()
    if error:
        return error
    try:
        notifications = _services["get_member_community_notifications"](
            member["discord_id"]
        )
        if not isinstance(notifications, list):
            raise TypeError("The community notification service returned an invalid result.")
        visible = [
            {
                "id": str(item.get("id") or ""),
                "type": str(item.get("type") or "notice")[:40],
                "title": str(item.get("title") or "NexTech notification")[:120],
                "message": str(item.get("message") or "")[:300],
                "href": item.get("href") if item.get("href") in {"/dashboard", "/community", "/messages"} else "/dashboard",
                "created_at": item.get("created_at"),
                "read": bool(item.get("read")),
            }
            for item in notifications
            if isinstance(item, dict)
        ]
        visible.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return jsonify({
            "member": {
                "display_name": str(member.get("display_name") or "Discord member")[:100],
                "avatar": str(member.get("avatar") or "")[:500],
            },
            "notifications": visible[:30],
            "unread_count": sum(not item["read"] for item in visible),
        })
    except Exception:
        current_app.logger.exception("Could not load member-specific notifications")
        return jsonify({"error": "Notifications are temporarily unavailable."}), 503


@dashboard_bp.post("/api/community/notifications/read")
def community_notifications_read_api():
    member, error = _current_community_member()
    if error:
        return error
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid notification update."}), 400
    mark_all = body.get("all") is True
    ids = body.get("ids", [])
    if not mark_all and (
        not isinstance(ids, list)
        or not ids
        or len(ids) > 200
        or any(not isinstance(item, str) or len(item) > 40 for item in ids)
    ):
        return jsonify({"error": "Choose one or more notifications to mark read."}), 400
    try:
        marked = _services["mark_member_community_notifications_read"](
            member["discord_id"], None if mark_all else ids
        )
        return jsonify({"marked_read": int(marked)})
    except Exception:
        current_app.logger.exception("Could not mark member notifications read")
        return jsonify({"error": "Notifications could not be updated."}), 503


@dashboard_bp.get("/api/community/wiki")
def community_wiki_api():
    try:
        entries = _safe_faq()
    except Exception:
        current_app.logger.exception("Could not load public wiki entries")
        return jsonify({"error": "Wiki entries are temporarily unavailable."}), 503
    public_entries = [
        {key: item.get(key) for key in ("id", "category", "question", "answer")}
        for item in entries
    ]
    return jsonify({"articles": public_entries})


@dashboard_bp.get("/")
def home_page():
    return _render_public_page("Home", HOME_PAGE_CONTENT, HOME_PAGE_SCRIPT)


@dashboard_bp.get("/login")
def login_page():
    return _render_public_page("Log in", LOGIN_PAGE_CONTENT, AUTH_PAGE_SCRIPT)


@dashboard_bp.get("/confirm-password")
def confirm_password_page():
    content = """
      <section class="hero"><div class="eyebrow">Account security</div><h1>Confirm your password</h1>
      <p>Sign in again to re-authenticate your NexTech account. After sign-in, you will return to your account settings.</p></section>
      <section class="panel soon-panel"><div class="soon-icon">⌑</div><h2>Re-authenticate to continue</h2>
      <p>Use the sign-in method attached to your account. If you forgot your email-account password, request a reset link instead. NexTech will never ask you to share your password with support.</p>
      <div class="cta-row" style="justify-content:center"><a class="cta primary" href="/login?next=/settings/account">Continue to sign in</a><a class="cta" href="/password/forgot">Reset a forgotten password</a></div></section>
    """
    return _render_public_page("Confirm password", content)


@dashboard_bp.get("/onboarding")
def onboarding_page():
    return _render_public_page("Create your account", ONBOARDING_PAGE_CONTENT, AUTH_PAGE_SCRIPT)


@dashboard_bp.get("/support")
def support_page():
    return _render_public_page("Support Hub", SUPPORT_PAGE_CONTENT, SUPPORT_PAGE_SCRIPT)


@dashboard_bp.get("/report-user")
def report_user_page():
    return _render_public_page("Report a user", REPORT_USER_PAGE_CONTENT)


@dashboard_bp.get("/profile")
def profile_page():
    return _render_public_page("Profile", PROFILE_PAGE_CONTENT, PROFILE_PAGE_SCRIPT)


@dashboard_bp.get("/users/<username>")
def public_user_profile_page(username: str):
    if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", username):
        return "Profile not found", 404
    selected_tab = (request.args.get("tab") or "overview").casefold()
    profile_tabs = {
        "overview": "Overview",
        "activity": "Activity",
        "gallery": "Gallery",
        "friends": "Friends",
        "achievements": "Achievements",
        "game": "Game record",
    }
    if selected_tab not in profile_tabs:
        selected_tab = "overview"
    safe_username = html.escape(username)
    tab_links = []
    for tab, label in profile_tabs.items():
        active = " active" if tab == selected_tab else ""
        current = ' aria-current="page"' if tab == selected_tab else ""
        href = f"/users/{safe_username}" + (f"?tab={tab}" if tab != "overview" else "")
        tab_links.append(
            f'<a class="profile-tab{active}" href="{href}"{current}>{label}</a>'
        )
    tabs = "".join(tab_links)
    content_by_tab = {
        "overview": (
            "<h2>About</h2><p>This public profile can show NexTech account details when "
            "the member links and publishes them. Account email and private sign-in details "
            "are never shown here.</p>"
        ),
        "activity": "<h2>Recent activity</h2><p>Public profile activity is not available yet.</p>",
        "gallery": "<h2>Gallery</h2><p>This member has not published a NexTech gallery.</p>",
        "friends": "<h2>Friends</h2><p>Friend lists are not available on NexTech profiles yet.</p>",
        "achievements": "<h2>Achievements</h2><p>Public achievements are not available yet.</p>",
        "game": "<h2>Game record</h2><p>Game activity is not available on NexTech profiles yet.</p>",
    }
    content = (
        '<section class="public-profile">'
        '<nav class="profile-breadcrumb" aria-label="Breadcrumb"><a href="/hub">Hub</a>'
        f'<span aria-hidden="true">›</span><span aria-current="page">{safe_username}</span></nav>'
        '<section class="public-profile-banner panel">'
        '<div class="public-profile-cover" aria-hidden="true"></div>'
        '<div class="public-profile-identity">'
        f'<div class="public-profile-avatar" aria-hidden="true">{html.escape(username[:1].upper())}</div>'
        '<div class="public-profile-title">'
        f'<h1>{safe_username}</h1><p>@{safe_username.casefold()} · NexTech community profile</p>'
        '</div>'
        '<div class="public-profile-actions">'
        f'<a class="cta" href="https://www.roblox.com/search/users?keyword={safe_username}" '
        'target="_blank" rel="noopener noreferrer">Search Roblox ↗</a>'
        '<a class="cta" href="/report-user">Report</a></div></div>'
        '<div class="public-profile-stats"><span>Public profile</span><span>Roblox account lookup</span></div>'
        '</section>'
        '<div class="public-profile-layout"><div class="public-profile-main">'
        f'<nav class="profile-tabs" aria-label="Profile sections">{tabs}</nav>'
        f'<section class="panel public-profile-panel">{content_by_tab[selected_tab]}</section>'
        '</div><aside class="public-profile-aside">'
        '<section class="panel"><h2>Highlights</h2><p>NexTech community profile</p></section>'
        '<section class="panel"><h2>About</h2><p>Public account details appear here when the member chooses to publish them.</p></section>'
        '<section class="panel"><h2>Privacy</h2><p>Private account, email, and sign-in details are not displayed on public profiles.</p></section>'
        '</aside></div></section>'
    )
    return _render_public_page(f"{username} · Profile", content)


@dashboard_bp.get("/members")
def members_page():
    return _render_public_page("Discord members", MEMBERS_PAGE_CONTENT, MEMBERS_PAGE_SCRIPT)


@dashboard_bp.get("/members/<discord_id>")
def discord_member_page(discord_id: str):
    if not discord_id.isdecimal():
        return "Member not found", 404
    selected_tab = (request.args.get("tab") or "overview").casefold()
    profile_tabs = {
        "overview": "Overview",
        "activity": "Activity",
        "gallery": "Gallery",
        "friends": "Friends",
        "achievements": "Achievements",
        "game": "Game record",
    }
    if selected_tab not in profile_tabs:
        selected_tab = "overview"
    tab_links = "".join(
        f'<a class="profile-tab{" active" if tab == selected_tab else ""}" '
        f'href="/members/{discord_id}' + (f'?tab={tab}' if tab != "overview" else "") + '"'
        f'{" aria-current=page" if tab == selected_tab else ""}>{label}</a>'
        for tab, label in profile_tabs.items()
    )
    profile_content = {
        "overview": "<h2>Community profile</h2><p>Discord display name, avatar, and current presence are shown when available.</p>",
        "activity": "<h2>Recent activity</h2><p>Public activity is not currently available for Discord member profiles.</p>",
        "gallery": "<h2>Gallery</h2><p>Public profile galleries are not currently available in NexTech.</p>",
        "friends": "<h2>Friends</h2><p>Friend lists are not provided by the NexTech community profile data.</p>",
        "achievements": "<h2>Achievements</h2><p>Achievement data is not currently connected to Discord member profiles.</p>",
        "game": "<h2>Game record</h2><p>Roblox game presence and moderation history are not currently connected to this Discord profile.</p>",
    }
    content = (
        '<section class="public-profile">'
        '<nav class="profile-breadcrumb" aria-label="Breadcrumb"><a href="/members">Community members</a>'
        '<span aria-hidden="true">›</span><span aria-current="page">Member profile</span></nav>'
        '<section class="public-profile-banner panel">'
        '<div class="public-profile-cover" aria-hidden="true"></div>'
        f'<div class="public-profile-identity" id="discord-profile" data-discord-id="{discord_id}" aria-live="polite">'
        '<div class="public-profile-avatar" id="discord-profile-avatar" aria-hidden="true">N</div>'
        '<div class="public-profile-title">'
        '<h1 id="discord-profile-name">Loading profile…</h1>'
        '<p id="discord-profile-status">Checking Discord presence…</p>'
        '<span class="member-profile-caption">NexTech community member</span>'
        '</div><div class="public-profile-actions"><a class="cta" href="/members">All members</a>'
        '<a class="cta" href="/report-user">Report</a></div></div>'
        '<div class="public-profile-stats"><span>Discord community profile</span><span>Presence updates from Discord</span></div>'
        '</section>'
        '<div class="public-profile-layout"><div class="public-profile-main">'
        f'<nav class="profile-tabs" aria-label="Profile sections">{tab_links}</nav>'
        f'<section class="panel public-profile-panel">{profile_content[selected_tab]}</section>'
        '</div><aside class="public-profile-aside">'
        '<section class="panel"><h2>Highlights</h2><p>Community member profile</p><p id="discord-profile-highlight">Discord presence loads with the profile.</p></section>'
        '<section class="panel"><h2>About</h2><p>This page shows public Discord profile details only.</p></section>'
        '<section class="panel"><h2>Privacy</h2><p>Private account information is not shown here.</p></section>'
        '</aside></div></section>'
    )
    return _render_public_page(
        "Community member profile", content, DISCORD_MEMBER_PAGE_SCRIPT
    )


@dashboard_bp.get("/messages")
def messages_page():
    return _render_public_page("Messages", MESSAGES_PAGE_CONTENT, MESSAGES_PAGE_SCRIPT)


@dashboard_bp.get("/support/tickets")
def support_tickets_page():
    return _render_public_page(
        "Support tickets", SUPPORT_TICKETS_PAGE_CONTENT, SUPPORT_TICKETS_PAGE_SCRIPT
    )


@dashboard_bp.get("/shop")
def shop_page():
    return _render_public_page("Marketplace", SHOP_PAGE_CONTENT, SHOP_PAGE_SCRIPT)


@dashboard_bp.get("/settings")
def settings_home_page():
    return settings_page("account")


@dashboard_bp.get("/settings/<section>")
def settings_page(section):
    page = SETTINGS_PAGE_INFO.get(section)
    if page is None:
        return "Settings page not found", 404
    return _render_public_page(
        f"{page[0]} settings",
        _settings_page_content(section, page[0], page[1]),
        SETTINGS_PAGE_SCRIPT,
    )


@dashboard_bp.get("/rewards")
def rewards_page():
    page = SETTINGS_PAGE_INFO["rewards"]
    return _render_public_page(
        page[0], _settings_page_content("rewards", page[0], page[1]), SETTINGS_PAGE_SCRIPT
    )


@dashboard_bp.get("/advertising")
def advertising_page():
    page = SETTINGS_PAGE_INFO["advertising"]
    return _render_public_page(
        page[0], _settings_page_content("advertising", page[0], page[1]), SETTINGS_PAGE_SCRIPT
    )


@dashboard_bp.get("/hub")
def hub_page():
    return _render_public_page("Hub", HUB_PAGE_CONTENT)


@dashboard_bp.get("/studio")
def studio_page():
    return _render_public_page("NexTech Studio", STUDIO_PAGE_CONTENT)


@dashboard_bp.get("/studio/<section>")
def studio_section_page(section):
    sections = {
        "titles": "Titles",
        "news": "News",
        "team": "Team",
        "careers": "Careers",
        "press": "Press",
    }
    title = sections.get(section)
    if title is None:
        return "Studio page not found", 404
    if section == "team":
        return _render_public_page("Staff directory", TEAM_PAGE_CONTENT, TEAM_PAGE_SCRIPT)
    if section == "careers":
        return _render_public_page("Careers", CAREERS_PAGE_CONTENT)
    return _render_public_page(title, _coming_soon_content(title))


@dashboard_bp.get("/team")
def team_page():
    return _render_public_page("Staff directory", TEAM_PAGE_CONTENT, TEAM_PAGE_SCRIPT)


@dashboard_bp.get("/careers")
def careers_page():
    return _render_public_page("Careers", CAREERS_PAGE_CONTENT)


@dashboard_bp.get("/policies")
def policies_page():
    return _render_public_page("Policies", POLICIES_PAGE_CONTENT)


@dashboard_bp.get("/club")
def club_page():
    return _render_public_page("NexTech Club", _coming_soon_content("NexTech Club"))


@dashboard_bp.post("/api/support/chat")
def support_chat_api():
    body = request.get_json(silent=True)
    message = body.get("message") if isinstance(body, dict) else None
    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "Enter a message for NexVisions."}), 400
    if len(message) > 1000:
        return jsonify({"error": "Messages must be 1,000 characters or fewer."}), 400
    return jsonify({"reply": answer_support_message(message.strip())})


@dashboard_bp.get("/community")
def community_home_page():
    return _render_public_page("Community", COMMUNITY_PAGE_CONTENT)


@dashboard_bp.get("/community/reports")
def community_reports_page():
    return _render_public_page(
        "Report a concern",
        _report_page_content(
            "Community report",
            "Report a concern",
            "Submit a community safety concern for staff review. Reports are sent to the configured Discord report channel and include your linked account identity. Do not include passwords or other account secrets.",
        ),
        REPORT_PAGE_SCRIPT,
    )


@dashboard_bp.get("/community/appeals")
def community_appeals_page():
    return _render_public_page(
        "Appeals",
        _report_page_content(
            "Moderation appeal",
            "Appeal a moderation action",
            "Ask staff to review a NexTech moderation action. Include the case ID, the account involved, and the context you want staff to consider.",
        ),
        REPORT_PAGE_SCRIPT,
    )


@dashboard_bp.get("/community/staff-abuse")
def community_staff_abuse_page():
    return _render_public_page(
        "Report staff abuse",
        _report_page_content(
            "Staff conduct report",
            "Report staff misconduct",
            "Report suspected abuse of staff permissions or inappropriate staff conduct. Reports go to the configured Discord report channel and include your linked account identity, so this form is not anonymous. Do not include passwords or other account secrets.",
        ),
        REPORT_PAGE_SCRIPT,
    )


@dashboard_bp.get("/community/submissions")
def community_report_submissions_page():
    return _render_public_page(
        "Track reports & appeals",
        REPORT_TRACKING_PAGE_CONTENT,
        REPORT_TRACKING_PAGE_SCRIPT,
    )


@dashboard_bp.get("/community/osp")
def community_osp_page():
    return _render_public_page("Online safety", OSP_PAGE_CONTENT)


@dashboard_bp.get("/community/apply")
def community_apply_page():
    return _render_public_page("Community applications", COMMUNITY_APPLY_PAGE_CONTENT)


@dashboard_bp.get("/community/records")
def community_records_page():
    return _render_public_page("Public Records", RECORDS_PAGE_CONTENT, RECORDS_PAGE_SCRIPT)


@dashboard_bp.get("/groups")
def groups_page():
    return _render_public_page("Groups", _coming_soon_content("Groups"))


@dashboard_bp.get("/wiki")
def wiki_page():
    return _render_public_page("Wiki", WIKI_PAGE_CONTENT, WIKI_PAGE_SCRIPT)


@dashboard_bp.get("/knowledge-base")
def knowledge_base_page():
    return _render_public_page(
        "Knowledge base", KNOWLEDGE_BASE_PAGE_CONTENT, WIKI_PAGE_SCRIPT
    )


@dashboard_bp.get("/feedback")
def feedback_page():
    return _render_public_page("Feedback", FEEDBACK_PAGE_CONTENT)


@dashboard_bp.get("/forums")
def forums_page():
    return _render_public_page("Forums", _coming_soon_content("Forums"))


@dashboard_bp.get("/status")
def system_status_page():
    return _render_public_page("System Status", STATUS_PAGE_CONTENT, STATUS_PAGE_SCRIPT)


@dashboard_bp.get("/dashboard")
def dashboard_page():
    return Response(DASHBOARD_HTML, content_type="text/html; charset=utf-8")


@dashboard_bp.get("/portal")
def customer_portal_page():
    return render_template("portal.html")


@dashboard_bp.get("/api/dashboard/me")
def dashboard_me():
    actor, error = _current_actor()
    if error:
        return error
    public_actor = _public_actor(actor)
    public_actor["is_staff"] = public_actor["role_level"] >= 2
    return jsonify({"actor": public_actor})


@dashboard_bp.get("/api/dashboard/overview")
def dashboard_overview():
    actor, error = _current_actor()
    if error:
        return error
    level = _role_level(actor)
    try:
        if level >= 2:
            stats = _call_service("dashboard_snapshot", actor)
        else:
            warnings = _visible_warnings(actor)
            tickets = _visible_tickets(actor)
            stats = {
                "warning_count": len(warnings),
                "warning_points": sum(
                    int(warning.get("points", 1) or 0) for warning in warnings
                ),
                "ticket_count": len(tickets),
                "action_count": 0,
            }
        if not isinstance(stats, dict):
            stats = {}
        recent = []
        if level >= 3:
            actions = _call_service("load_bot_actions")
            if isinstance(actions, list):
                recent = [
                    {
                        "timestamp_str": action.get("timestamp_str", ""),
                        "action_type": action.get("action_type", ""),
                        "actor": action.get("actor") or action.get("username") or "",
                        "details": action.get("details") or "",
                    }
                    for action in reversed(actions[-8:])
                    if isinstance(action, dict)
                ]
        return jsonify({"stats": stats, "recent_activity": recent})
    except Exception:
        current_app.logger.exception("Could not load dashboard overview")
        return _auth_error("Dashboard overview is temporarily unavailable.", 503)


@dashboard_bp.get("/api/dashboard/members")
def dashboard_member_search():
    actor, error = _current_actor(minimum_role=2)
    if error:
        return error
    query = (request.args.get("q") or "").strip()[:100]
    try:
        result = _call_service("dashboard_members", query, actor)
        status = 400 if isinstance(result, dict) and result.get("error") else 200
        return _json_result(result, status)
    except Exception:
        current_app.logger.exception("Dashboard member search failed")
        return _auth_error("Member search is temporarily unavailable.", 503)


@dashboard_bp.get("/api/dashboard/warnings")
def dashboard_warnings():
    actor, error = _current_actor()
    if error:
        return error
    try:
        warnings = _visible_warnings(actor)
        warnings.sort(
            key=lambda item: float(item.get("timestamp") or item.get("created_at") or 0),
            reverse=True,
        )
        return jsonify({"warnings": warnings[:500]})
    except Exception:
        current_app.logger.exception("Could not load dashboard warnings")
        return _auth_error("Warnings are temporarily unavailable.", 503)


@dashboard_bp.get("/api/dashboard/tickets")
def dashboard_tickets():
    actor, error = _current_actor()
    if error:
        return error
    try:
        return jsonify({"tickets": _visible_tickets(actor)})
    except Exception:
        current_app.logger.exception("Could not load dashboard tickets")
        return _auth_error("Tickets are temporarily unavailable.", 503)


@dashboard_bp.get("/api/dashboard/tickets/<case_id>")
def dashboard_ticket_detail(case_id: str):
    actor, error = _current_actor(minimum_role=2)
    if error:
        return error
    try:
        return _json_result(_call_service("dashboard_ticket_messages", case_id, actor))
    except Exception:
        current_app.logger.exception("Could not load dashboard ticket messages")
        return _auth_error("Ticket messages are temporarily unavailable.", 503)


@dashboard_bp.post("/api/dashboard/tickets/<case_id>/reply")
def dashboard_ticket_reply(case_id: str):
    actor, error = _current_actor(minimum_role=2)
    if error:
        return error
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _auth_error("Send the ticket reply as JSON.", 400)
    message = str(body.get("message") or "").strip()
    if not message or len(message) > 3000:
        return _auth_error("Replies must contain 1–3,000 characters.", 400)
    try:
        return _json_result(_call_service("dashboard_reply_ticket", case_id, message, actor))
    except Exception:
        current_app.logger.exception("Could not send dashboard ticket reply")
        return _auth_error("The reply could not be sent.", 503)


@dashboard_bp.get("/api/dashboard/accounts")
def dashboard_customer_accounts():
    actor, error = _current_actor(minimum_role=4)
    if error:
        return error
    query = (request.args.get("q") or "").strip()[:120]
    try:
        return _json_result(_call_service("dashboard_customer_accounts", query, actor))
    except Exception:
        current_app.logger.exception("Could not load website customer accounts")
        return _auth_error("Website accounts are temporarily unavailable.", 503)


@dashboard_bp.post("/api/dashboard/accounts")
def dashboard_customer_account_create():
    actor, error = _current_actor(minimum_role=4)
    if error:
        return error
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _auth_error("Send account details as JSON.", 400)
    try:
        return _json_result(_call_service("dashboard_create_customer_account", body, actor), 201)
    except Exception:
        current_app.logger.exception("Could not create website customer account")
        return _auth_error("The website account could not be created.", 503)


@dashboard_bp.post("/api/dashboard/accounts/action")
def dashboard_customer_account_action():
    actor, error = _current_actor(minimum_role=4)
    if error:
        return error
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _auth_error("Send an account action as JSON.", 400)
    try:
        return _json_result(_call_service("dashboard_customer_account_action", body, actor))
    except Exception:
        current_app.logger.exception("Could not update website customer account")
        return _auth_error("The website account could not be updated.", 503)


@dashboard_bp.get("/api/dashboard/team")
def dashboard_team_profiles():
    actor, error = _current_actor(minimum_role=4)
    if error:
        return error
    try:
        return _json_result(_call_service("dashboard_team_profiles", actor))
    except Exception:
        current_app.logger.exception("Could not load managed team profiles")
        return _auth_error("Team profiles are temporarily unavailable.", 503)


@dashboard_bp.post("/api/dashboard/team")
def dashboard_team_profile_save():
    actor, error = _current_actor(minimum_role=4)
    if error:
        return error
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _auth_error("Send team profile details as JSON.", 400)
    try:
        return _json_result(_call_service("dashboard_save_team_profile", body, actor))
    except Exception:
        current_app.logger.exception("Could not save managed team profile")
        return _auth_error("The team profile could not be saved.", 503)


@dashboard_bp.delete("/api/dashboard/team/<discord_id>")
def dashboard_team_profile_remove(discord_id: str):
    actor, error = _current_actor(minimum_role=4)
    if error:
        return error
    try:
        return _json_result(_call_service("dashboard_remove_team_profile", discord_id, actor))
    except Exception:
        current_app.logger.exception("Could not remove managed team profile")
        return _auth_error("The team profile could not be removed.", 503)


@dashboard_bp.post("/api/dashboard/email-login/request")
def dashboard_email_login_request():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _auth_error("Enter your NexTech account email.", 400)
    email = str(body.get("email") or "").strip().lower()[:200]
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return _auth_error("Enter a valid email address.", 400)
    try:
        result = _call_service("request_dashboard_email_login", email, request.remote_addr)
        if isinstance(result, tuple) and len(result) == 2:
            payload, status = result
            return _json_result(payload, int(status))
        return _json_result(result)
    except Exception:
        current_app.logger.exception("Could not request dashboard email sign-in")
        return _auth_error("The sign-in link could not be sent. Please try again later.", 503)


@dashboard_bp.post("/api/dashboard/email-login/verify")
def dashboard_email_login_verify():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _auth_error("The sign-in link is invalid or expired.", 400)
    token = str(body.get("token") or "").strip()
    if not token or len(token) > 256:
        return _auth_error("The sign-in link is invalid or expired.", 400)
    try:
        result = _call_service("verify_dashboard_email_login", token)
        return _json_result(result)
    except Exception:
        current_app.logger.exception("Could not verify dashboard email sign-in")
        return _auth_error("The sign-in link could not be verified. Please request a new one.", 503)


@dashboard_bp.post("/api/dashboard/profile-login")
def dashboard_profile_login():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _auth_error("Enter the one-time code from Discord.", 400)
    code = str(body.get("code") or "").strip()
    if not code or len(code) > 32:
        return _auth_error("The one-time code is invalid or expired.", 400)
    try:
        result = _call_service(
            "redeem_dashboard_profile_code",
            code,
            request.remote_addr or "",
        )
        return _json_result(result)
    except Exception:
        current_app.logger.exception("Could not verify dashboard profile code")
        return _auth_error("The one-time code could not be verified. Please try again.", 503)


@dashboard_bp.get("/api/dashboard/products")
def dashboard_products():
    actor, error = _current_actor()
    if error:
        return error
    try:
        return jsonify({"products": _safe_products()})
    except Exception:
        current_app.logger.exception("Could not load dashboard products")
        return _auth_error("Products are temporarily unavailable.", 503)


@dashboard_bp.post("/api/dashboard/products")
def dashboard_product_create():
    actor, error = _current_actor(minimum_role=4)
    if error:
        return error
    if request.content_length is not None and request.content_length > 32 * 1024 * 1024:
        return _auth_error("Product uploads must be 32 MB or smaller.", 413)
    if request.is_json:
        body = request.get_json(silent=True)
        image = None
        product_file = None
    elif request.mimetype == "multipart/form-data":
        body = request.form.to_dict()
        image = request.files.get("image")
        product_file = request.files.get("product_file")
    else:
        body = None
        image = None
        product_file = None
    if not isinstance(body, dict):
        return _auth_error("Send product details as JSON or a multipart form.", 400)
    legacy_unpriced_json = request.is_json and "price" not in body and "currency" not in body

    name = str(body.get("name") or "").strip()
    category = str(body.get("category") or "General").strip()
    description = str(body.get("description") or "").strip()
    currency = str(body.get("currency") or "Robux").strip()
    if not name or len(name) > 120:
        return _auth_error("Product name is required and must be 120 characters or fewer.", 400)
    if not category or len(category) > 80:
        return _auth_error("Product category is required and must be 80 characters or fewer.", 400)
    if len(description) > 1000:
        return _auth_error("Product description must be 1,000 characters or fewer.", 400)
    if currency not in ("Robux", "USD"):
        return _auth_error("Choose Robux or USD as the product price currency.", 400)
    if legacy_unpriced_json:
        price = Decimal(0)
    else:
        try:
            raw_price = body.get("price")
            if isinstance(raw_price, bool) or raw_price is None:
                raise InvalidOperation
            price = Decimal(str(raw_price).strip())
            if not price.is_finite() or price <= 0:
                raise InvalidOperation
            if currency == "Robux":
                if price != price.to_integral_value() or price > 100_000_000:
                    raise InvalidOperation
                price = price.to_integral_value()
            else:
                if price > Decimal("100000.00") or price != price.quantize(Decimal("0.01")):
                    raise InvalidOperation
        except (InvalidOperation, ValueError, TypeError):
            expected = "a positive whole number of Robux" if currency == "Robux" else "a USD amount greater than $0 and no more than $100,000.00"
            return _auth_error(f"Enter {expected}.", 400)

    allowed_extensions = {
        "image": {"png", "jpg", "jpeg", "gif", "webp"},
        "product_file": {"zip", "pdf", "rbxm", "rbx", "txt"},
    }
    for field_name, upload, max_size in (
        ("image", image, 5 * 1024 * 1024),
        ("product_file", product_file, 25 * 1024 * 1024),
    ):
        if not isinstance(upload, FileStorage) or not upload.filename:
            continue
        extension = os.path.splitext(upload.filename)[1].lstrip(".").lower()
        if extension not in allowed_extensions[field_name]:
            return _auth_error(f"Unsupported {field_name.replace('_', ' ')} file type.", 400)
        try:
            upload.stream.seek(0, os.SEEK_END)
            file_size = upload.stream.tell()
            upload.stream.seek(0)
        except (OSError, AttributeError):
            current_app.logger.exception("Could not validate uploaded product file size")
            return _auth_error("Could not validate the uploaded file.", 400)
        if file_size > max_size:
            return _auth_error(f"{field_name.replace('_', ' ').title()} files must be {max_size // (1024 * 1024)} MB or smaller.", 413)

    body = {
        "name": name,
        "category": category,
        "description": description,
        "currency": currency,
        "price": str(price),
    }
    try:
        if (image and image.filename) or (product_file and product_file.filename):
            result = _call_service("dashboard_create_product", body, actor, image, product_file)
        else:
            result = _call_service("dashboard_create_product", body, actor)
        return _json_result(result, 201)
    except Exception:
        current_app.logger.exception("Dashboard product creation failed")
        return _auth_error("Could not create the product.", 503)


@dashboard_bp.get("/api/dashboard/faq")
def dashboard_faq_list():
    actor, error = _current_actor()
    if error:
        return error
    try:
        return jsonify({"faq": _safe_faq()})
    except Exception:
        current_app.logger.exception("Could not load dashboard FAQ")
        return _auth_error("FAQ entries are temporarily unavailable.", 503)


@dashboard_bp.post("/api/dashboard/faq")
def dashboard_faq_create():
    actor, error = _current_actor(minimum_role=4)
    if error:
        return error
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _auth_error("Send FAQ details as JSON.", 400)
    category = str(body.get("category") or "").strip()[:80]
    question = str(body.get("question") or "").strip()[:300]
    answer = str(body.get("answer") or "").strip()[:4000]
    if not category or not question or not answer:
        return _auth_error("Category, question, and answer are required.", 400)
    try:
        entry = {
            "id": str(_call_service("new_id"))[:80],
            "category": category,
            "question": question,
            "answer": answer,
            "created": _call_service("now"),
            "added_by": str(actor.get("display_name") or "Dashboard")[:120],
        }
        entries = _call_service("load_faq")
        if not isinstance(entries, list):
            entries = []
        entries.append(entry)
        _call_service("save_faq", entries)
        _call_service(
            "record_bot_action",
            "faq_created",
            faq_id=entry["id"],
            actor_id=actor.get("discord_id"),
            actor=actor.get("display_name"),
        )
        return jsonify({"ok": True, "faq": entry}), 201
    except Exception:
        current_app.logger.exception("Dashboard FAQ creation failed")
        return _auth_error("Could not add the FAQ entry.", 503)


@dashboard_bp.delete("/api/dashboard/faq/<faq_id>")
def dashboard_faq_delete(faq_id: str):
    actor, error = _current_actor(minimum_role=4)
    if error:
        return error
    try:
        entries = _call_service("load_faq")
        if not isinstance(entries, list):
            entries = []
        updated = [
            entry
            for entry in entries
            if not isinstance(entry, dict) or str(entry.get("id")) != faq_id
        ]
        if len(updated) == len(entries):
            return _auth_error("FAQ entry not found.", 404)
        _call_service("save_faq", updated)
        _call_service(
            "record_bot_action",
            "faq_deleted",
            faq_id=faq_id,
            actor_id=actor.get("discord_id"),
            actor=actor.get("display_name"),
        )
        return jsonify({"ok": True})
    except Exception:
        current_app.logger.exception("Dashboard FAQ deletion failed")
        return _auth_error("Could not delete the FAQ entry.", 503)


@dashboard_bp.get("/api/dashboard/activity")
def dashboard_activity():
    actor, error = _current_actor(minimum_role=3)
    if error:
        return error
    try:
        actions = _call_service("load_bot_actions")
        if not isinstance(actions, list):
            actions = []
        try:
            limit = max(1, min(int(request.args.get("limit", "100")), 250))
        except ValueError:
            limit = 100
        return jsonify({"actions": list(reversed(actions[-limit:]))})
    except Exception:
        current_app.logger.exception("Could not load dashboard activity")
        return _auth_error("Activity logs are temporarily unavailable.", 503)


@dashboard_bp.post("/api/dashboard/support/ai")
def dashboard_support_ai():
    actor, error = _current_actor()
    if error:
        return error
    body = request.get_json(silent=True)
    messages = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(messages, list):
        return _auth_error("Send the support conversation as a messages list.", 400)
    try:
        return _json_result(_call_service("support_ai_reply", messages, actor))
    except Exception:
        current_app.logger.exception("Dashboard AI support request failed")
        return _auth_error("The support assistant is temporarily unavailable.", 503)


@dashboard_bp.post("/api/dashboard/support/ticket")
def dashboard_support_ticket():
    actor, error = _current_actor()
    if error:
        return error
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _auth_error("Send the completed support intake as JSON.", 400)
    try:
        return _json_result(_call_service("create_web_ticket", body, actor), 201)
    except Exception:
        current_app.logger.exception("Dashboard support ticket creation failed")
        return _auth_error("Could not create the support ticket.", 503)


@dashboard_bp.post("/api/dashboard/reports")
def dashboard_report_create():
    actor, error = _current_actor()
    if error:
        return error
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _auth_error("Send the report details as JSON.", 400)
    try:
        return _json_result(_call_service("create_web_report", body, actor), 201)
    except Exception:
        current_app.logger.exception("Dashboard report submission failed")
        return _auth_error("Could not submit the report.", 503)


@dashboard_bp.get("/api/dashboard/report-submissions")
def dashboard_report_submissions():
    actor, error = _current_actor()
    if error:
        return error
    try:
        return _json_result(_call_service("list_web_reports", actor))
    except Exception:
        current_app.logger.exception("Could not load the member's report submissions")
        return _auth_error("Report tracking is temporarily unavailable.", 503)


@dashboard_bp.get("/api/dashboard/staff/report-submissions")
def dashboard_staff_report_submissions():
    actor, error = _current_actor(minimum_role=2)
    if error:
        return error
    try:
        return _json_result(_call_service("list_all_web_reports", actor))
    except Exception:
        current_app.logger.exception("Could not load staff report submissions")
        return _auth_error("The staff report queue is temporarily unavailable.", 503)


@dashboard_bp.post("/api/dashboard/staff/report-submissions/<report_id>/status")
def dashboard_staff_update_report_status(report_id: str):
    actor, error = _current_actor(minimum_role=2)
    if error:
        return error
    if not re.fullmatch(r"RPT-\d{6}", report_id):
        return _auth_error("Enter a valid report reference.", 400)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _auth_error("Send the status update as JSON.", 400)
    status = str(body.get("status") or "").strip()
    public_update = str(body.get("public_update") or "").strip()
    if status not in {"under_review", "outcome", "closed"}:
        return _auth_error("Choose a valid report stage.", 400)
    if len(public_update) > 1000:
        return _auth_error("Public updates must be 1,000 characters or fewer.", 400)
    try:
        return _json_result(
            _call_service("update_web_report_status", report_id, status, public_update, actor)
        )
    except Exception:
        current_app.logger.exception("Could not update report submission status")
        return _auth_error("The report status could not be updated.", 503)


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="dark">
  <meta name="referrer" content="no-referrer">
  <title>NexTech Dashboard</title>
  <style>
    :root{color-scheme:dark;--bg:#090a0d;--panel:#111318;--panel2:#171a21;--line:#282d38;--text:#f4f6fb;--muted:#a2a9b7;--subtle:#747d8b;--blue:#4d7dff;--gold:#f4a830;--red:#f87171;--green:#4ade80}
    *{box-sizing:border-box}html{min-width:320px;background:var(--bg);scroll-behavior:smooth}body{margin:0;min-height:100vh;background:radial-gradient(ellipse at 45% -18%,#1a2d53 0,transparent 45%),var(--bg);color:var(--text);font:14px Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
    button,input,textarea,select{font:inherit}button,a{-webkit-tap-highlight-color:transparent}a{color:inherit;text-decoration:none}button{color:inherit}
    .shell{min-height:100vh;display:grid;grid-template-columns:248px minmax(0,1fr)}.sidebar{position:sticky;top:0;height:100vh;padding:21px 14px;background:rgba(13,15,20,.92);border-right:1px solid var(--line);display:flex;flex-direction:column;z-index:5}
    .brand{display:flex;align-items:center;gap:11px;padding:0 8px 23px;border-bottom:1px solid var(--line);font-weight:800;letter-spacing:-.02em}.brand-mark{width:35px;height:35px;display:grid;place-items:center;border-radius:11px;background:linear-gradient(145deg,#4d7dff,#315ad1);box-shadow:0 8px 24px #315ad144;font-size:18px}.brand small{display:block;margin-top:2px;color:var(--muted);font-size:10px;font-weight:650;letter-spacing:.08em;text-transform:uppercase}
    .nav-label{padding:22px 10px 8px;color:var(--subtle);font-size:9px;font-weight:800;letter-spacing:.15em;text-transform:uppercase}.nav{display:grid;gap:4px}.nav button{width:100%;min-height:39px;display:flex;align-items:center;gap:10px;padding:0 11px;border:0;border-radius:8px;background:transparent;color:#b9c0cc;text-align:left;cursor:pointer;font-size:12px;font-weight:650;transition:background .15s,color .15s}.nav button:hover{background:#1b1e26;color:#fff}.nav button.active{background:#1d2b4d;color:#d9e4ff}.nav .ico{width:18px;text-align:center;font-size:15px}.sidebar-foot{margin-top:auto;padding:12px 9px;color:var(--subtle);font-size:10px;line-height:1.6}
    .main{min-width:0}.topbar{height:66px;display:flex;align-items:center;gap:14px;padding:0 clamp(18px,4vw,40px);border-bottom:1px solid var(--line);background:#0c0e13c9}.topbar-title{flex:1;color:#d6dae2;font-weight:700;font-size:12px}.status-dot{display:inline-block;width:7px;height:7px;margin-right:7px;border-radius:50%;background:var(--green);box-shadow:0 0 12px #4ade8066}.user-chip{display:flex;align-items:center;gap:9px;padding:7px 10px;border:1px solid var(--line);border-radius:9px;background:var(--panel);font-size:11px}.user-avatar{width:26px;height:26px;display:grid;place-items:center;border-radius:8px;background:#263962;color:#cbd9ff;font-weight:800}.role-pill{margin-left:4px;padding:3px 7px;border-radius:99px;background:#202f4d;color:#adc2ff;font-size:9px;font-weight:800;text-transform:uppercase}
    .content{width:min(1320px,100%);margin:auto;padding:31px clamp(18px,4vw,40px) 58px}.page-head{display:flex;align-items:flex-end;justify-content:space-between;gap:18px;margin-bottom:23px}.eyebrow{color:#8facff;font-size:9px;font-weight:800;letter-spacing:.16em;text-transform:uppercase}.page-head h1{margin:7px 0 5px;font-size:clamp(25px,3vw,33px);letter-spacing:-.045em}.page-head p{margin:0;color:var(--muted);font-size:12px;line-height:1.6}.page-actions{display:flex;gap:8px;flex-wrap:wrap}
    .grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.stat,.panel{border:1px solid var(--line);border-radius:12px;background:linear-gradient(145deg,#14161c,#101216)}.stat{min-height:112px;padding:17px}.stat-label{color:var(--muted);font-size:10px;font-weight:700}.stat-value{margin-top:13px;font-size:27px;font-weight:800;letter-spacing:-.04em}.stat-note{margin-top:3px;color:var(--subtle);font-size:10px}.two-col{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(280px,.9fr);gap:13px;margin-top:13px}.panel{min-width:0;padding:18px}.panel-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}.panel-head h2{margin:0;font-size:13px;letter-spacing:-.01em}.panel-sub{margin-top:4px;color:var(--muted);font-size:10px}.panel-body{min-width:0}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:9px}table{width:100%;border-collapse:collapse;min-width:560px}th{padding:10px 12px;background:#0d1016;color:var(--subtle);font-size:9px;font-weight:800;letter-spacing:.1em;text-align:left;text-transform:uppercase;white-space:nowrap}td{padding:11px 12px;border-top:1px solid #222630;color:#d2d7e1;font-size:11px;line-height:1.5;vertical-align:top}td.muted{color:var(--muted)}.empty{padding:26px;color:var(--subtle);font-size:11px;text-align:center}.badge{display:inline-flex;align-items:center;padding:4px 7px;border:1px solid #34415a;border-radius:99px;background:#1b263d;color:#c5d5ff;font-size:9px;font-weight:800}.badge.good{border-color:#24543d;background:#10281d;color:#7de3a7}.badge.warn{border-color:#644a22;background:#302310;color:#f2c66f}.badge.bad{border-color:#603038;background:#321a20;color:#ffa1a8}
    .btn{min-height:34px;display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:0 12px;border:1px solid transparent;border-radius:8px;background:var(--blue);color:#fff;font-size:11px;font-weight:750;cursor:pointer;transition:filter .15s,border-color .15s}.btn:hover{filter:brightness(1.12)}.btn.secondary{border-color:#343a47;background:#191c23;color:#e8ebf0}.btn.danger{border-color:#603038;background:#321a20;color:#ffa1a8}.btn:disabled{opacity:.55;cursor:wait}
    .field{display:grid;gap:6px;margin-bottom:12px}.field label{color:#cbd0da;font-size:10px;font-weight:750}.input,textarea.input,select.input{width:100%;min-height:37px;padding:9px 11px;border:1px solid #333945;border-radius:7px;outline:0;background:#0b0d12;color:#f4f6fb;font-size:11px}.input:focus{border-color:#668bff;box-shadow:0 0 0 3px #4d7dff22}textarea.input{min-height:88px;resize:vertical;line-height:1.5}.form-row{display:grid;grid-template-columns:1fr 1fr;gap:10px}.inline-form{display:flex;gap:8px;margin-bottom:13px}.inline-form .input{flex:1}.notice{padding:11px 12px;border:1px solid #304064;border-radius:8px;background:#111c31;color:#aebee0;font-size:10px;line-height:1.55}.notice.error{border-color:#603038;background:#321a20;color:#ffb3b8}.notice.success{border-color:#24543d;background:#10281d;color:#9ae6b7}
    .chat{height:340px;display:flex;flex-direction:column;border:1px solid var(--line);border-radius:9px;background:#0d0f14;overflow:hidden}.chat-messages{flex:1;overflow:auto;padding:14px;display:flex;flex-direction:column;gap:9px}.bubble{max-width:86%;padding:10px 12px;border:1px solid #292e38;border-radius:11px;background:#171a21;color:#dce1ea;font-size:11px;line-height:1.55;white-space:pre-wrap}.bubble.user{align-self:flex-end;border-color:#344a7d;background:#1a2a4b}.bubble.assistant{align-self:flex-start}.chat-entry{display:flex;gap:8px;padding:10px;border-top:1px solid var(--line)}.chat-entry .input{flex:1}.ticket-conversation{max-height:440px;overflow:auto;display:grid;gap:9px;padding:4px 2px}.ticket-message{padding:11px 12px;border:1px solid #292e38;border-radius:9px;background:#0d0f14}.ticket-message>div{display:flex;justify-content:space-between;gap:12px;color:#e2e6ee;font-size:10px}.ticket-message time{color:var(--subtle);font-size:9px}.ticket-message p{margin:8px 0 0;color:#c8ced9;font-size:11px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere}.account-actions{display:flex;gap:6px;flex-wrap:wrap}.account-actions .btn{min-height:29px;padding:0 8px;font-size:10px}.account-link-details{margin-top:8px;color:#b8c8ea;font-size:10px}.account-link-details summary{cursor:pointer}.account-link-form{display:grid;gap:7px;margin-top:8px;padding:9px;border:1px solid var(--line);border-radius:8px;background:#101216}.account-link-form label{display:grid;gap:4px;color:var(--muted)}.verification-mark{display:inline-grid;place-items:center;min-width:17px;font-size:14px;font-weight:900}.mark-green{color:#4ade80}.mark-blue{color:#60a5fa}.mark-yellow{color:#facc15}.mark-orange{color:#fb923c}.mark-silver{color:#cbd5e1}.mark-purple{color:#c084fc}.mark-deep_blue{color:#2563eb}.mark-black{color:#09090b;text-shadow:0 0 1px #fff}.mark-formal_staff{color:#fb7185;text-shadow:1px 1px #facc15}.auth-message.success{color:#78dfa0}.hidden{display:none!important}.auth-screen{min-height:100vh;display:grid;place-items:center;padding:20px}.auth-card{width:min(430px,100%);padding:28px;border:1px solid var(--line);border-radius:14px;background:#12151b;box-shadow:0 30px 90px #0008}.auth-card h1{margin:18px 0 8px;font-size:24px;letter-spacing:-.04em}.auth-card p{color:var(--muted);font-size:12px;line-height:1.65}.auth-card .btn{width:100%;margin-top:12px;min-height:42px}.auth-logo{display:flex;align-items:center;gap:10px;font-weight:800}.auth-message{min-height:16px;margin-top:12px;color:#ffabb1;font-size:11px;line-height:1.5}
    @media(max-width:1000px){.shell{grid-template-columns:210px minmax(0,1fr)}.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.two-col{grid-template-columns:1fr}}
    @media(max-width:700px){.shell{display:block}.sidebar{position:fixed;left:-250px;top:0;width:230px;transition:left .2s;box-shadow:15px 0 36px #0007}.sidebar.open{left:0}.menu-toggle{display:inline-flex!important}.topbar{height:58px;padding:0 15px}.topbar-title{display:none}.content{padding:23px 15px 42px}.page-head{align-items:flex-start;flex-direction:column}.user-chip{font-size:10px}.user-chip .role-pill{display:none}}
    @media(max-width:480px){.grid{grid-template-columns:1fr 1fr;gap:8px}.stat{min-height:96px;padding:12px}.stat-value{font-size:23px}.stat-label{font-size:9px}.panel{padding:14px}.form-row{grid-template-columns:1fr}.content{padding-top:19px}}
    .menu-toggle{display:none}
     body.search-open{overflow:hidden}
     .search-trigger{width:min(290px,30vw);min-width:38px;min-height:35px;display:flex;align-items:center;gap:9px;padding:0 10px;border:1px solid #303238;border-radius:8px;background:#111214;color:#999da5;cursor:pointer;text-align:left}
     .search-trigger:hover{border-color:#4a4c52;color:#d4d5d8}
     .search-trigger .search-label{overflow:hidden;flex:1;font-size:10px;white-space:nowrap;text-overflow:ellipsis}
     .search-svg{width:16px;height:16px;flex:none;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round}
     .search-trigger kbd{padding:2px 5px;border:1px solid #303238;border-radius:4px;background:#191a1c;color:#8b8c90;font:10px ui-monospace,SFMono-Regular,monospace;white-space:nowrap}
     .search-overlay{position:fixed;inset:0;z-index:9999;background:#171717;color:#e4e4e4}
     .search-dialog{width:100%;height:100%;display:flex;flex-direction:column}
     .search-input-row{height:47px;min-height:47px;display:flex;align-items:center;gap:10px;padding:0 10px;border-bottom:1px solid #303030}
     .search-input-row .search-svg{color:#a1a1a1}
     .search-input{width:100%;height:100%;flex:1;border:0;outline:0;background:transparent;color:#ededed;font:inherit;font-size:13px}
     .search-input::placeholder{color:#999da6;opacity:1}
     .search-close{width:30px;height:30px;border:0;border-radius:6px;background:transparent;color:#a5a5a5;font-size:20px;cursor:pointer}
     .search-close:hover{background:#282828;color:#fff}
     .search-filters{min-height:45px;display:flex;align-items:center;gap:8px;padding:6px 9px;border-bottom:1px solid #303030;overflow-x:auto}
     .search-filters-label{flex:none;margin-right:4px;color:#aaa;font-size:12px}
     .search-chip{height:29px;flex:none;display:inline-flex;align-items:center;gap:6px;padding:0 9px;border:1px solid #202124;border-radius:7px;background:#1b1c1e;color:#e0e0e0;font-size:11px;cursor:pointer}
     .search-chip:hover,.search-chip.active{border-color:#3a3b3e;background:#252629}
     .search-chip .chip-icon{font-size:12px;color:#c5c5c5}
     .search-date-wrap{position:relative;flex:none}
     .search-date-menu{position:absolute;top:35px;left:0;z-index:2;width:160px;padding:5px;border:1px solid #383838;border-radius:8px;background:#202020;box-shadow:0 12px 36px #0008}
     .search-date-menu button{width:100%;padding:8px 9px;border:0;border-radius:5px;background:transparent;color:#ddd;font-size:11px;text-align:left;cursor:pointer}
     .search-date-menu button:hover,.search-date-menu button.active{background:#303030}
     .search-empty{flex:1;min-height:125px;display:grid;place-items:center;padding:26px;text-align:center;color:#a4a7ae;font-size:13px}
     .search-empty small{display:block;margin-top:10px;color:#767b85;font-size:11px}
     .search-results{width:min(900px,100%);margin:16px auto 30px;padding:0 16px;overflow:auto}
     .search-result{width:100%;display:flex;align-items:flex-start;gap:12px;padding:12px;border:0;border-bottom:1px solid #2a2a2a;background:transparent;color:#e8e8e8;text-align:left;cursor:pointer}
     .search-result:hover{background:#222}
     .search-result-icon{width:28px;height:28px;flex:none;display:grid;place-items:center;border:1px solid #343434;border-radius:7px;background:#202020;color:#bbb;font-size:12px}
     .search-result-copy{min-width:0;flex:1}
     .search-result-title{display:block;font-size:12px;font-weight:650}
     .search-result-summary{display:block;margin-top:5px;color:#999da5;font-size:11px;line-height:1.5}
     .search-result-type{display:inline-block;margin-top:7px;color:#8c929c;font-size:9px;text-transform:uppercase;letter-spacing:.08em}
     .search-hint{padding:30px 15px;color:#939393;font-size:12px;line-height:1.7;text-align:center}
     @media(max-width:700px){.search-trigger{width:38px;margin-left:auto;justify-content:center;padding:0}.search-trigger .search-label,.search-trigger kbd{display:none}.search-filters{gap:6px}.search-filters-label{font-size:11px}.search-chip{padding:0 8px}.search-results{padding:0 10px}}
    .public-profile-avatar img{display:block;width:100%;height:100%;border-radius:inherit;object-fit:cover}.public-profile-title #discord-profile-status{display:inline-flex;align-items:center;gap:7px}.public-profile-title #discord-profile-status::before{content:"";width:8px;height:8px;border-radius:50%;background:#555b63}.public-profile-title #discord-profile-status.online::before{background:#4ade80}.member-profile-caption{display:block;margin-top:7px;color:var(--muted);font-size:10px}
    @media(max-width:600px){.public-profile-heading{align-items:flex-start;gap:12px;padding:15px}.public-profile-avatar{width:60px;height:60px;border-radius:15px;font-size:24px}.public-profile-title h1{font-size:21px}.public-profile-panel{padding:16px}.profile-tab{padding:9px 10px}}
  </style>
</head>
<body>
  <section class="auth-screen hidden" id="auth-screen">
    <div class="auth-card">
      <div class="auth-logo"><span class="brand-mark">N</span><span>NexTech <small style="display:block;color:#a2a9b7;font-size:9px;letter-spacing:.12em;text-transform:uppercase">Operations Dashboard</small></span></div>
      <h1 id="auth-title">Sign in to the dashboard</h1>
      <p id="auth-copy">Sign in with your linked Roblox account, a verified email, or a one-time code from Discord. Staff tools are available only to authorized members with the protected Roblox group role.</p>
      <form id="dashboard-profile-login-form">
        <label class="field" for="dashboard-profile-code">Discord one-time code<input class="input" id="dashboard-profile-code" name="code" type="text" autocomplete="one-time-code" autocapitalize="characters" maxlength="8" pattern="[A-Za-z0-9]{8}" required></label>
        <button class="btn" type="submit">Sign in with Discord code</button>
        <small>Generate a code with <code>/profile</code> in the NexTech Discord server. It expires after 5 minutes.</small>
      </form>
      <div class="auth-message" id="profile-login-status" role="status" aria-live="polite"></div>
      <details class="auth-alternate">
        <summary>Use email sign-in instead</summary>
        <form id="dashboard-email-login-form">
          <label class="field" for="dashboard-login-email">Verified account email<input class="input" id="dashboard-login-email" name="email" type="email" autocomplete="email" maxlength="200" required></label>
          <button class="btn secondary" type="submit">Email me a sign-in link</button>
        </form>
        <div class="auth-message" id="email-login-status" role="status" aria-live="polite"></div>
      </details>
      <div class="auth-message" id="auth-message" role="status" aria-live="polite"></div>
      <a class="btn secondary" id="login-link" href="/auth/roblox?next=/dashboard">Continue with linked Roblox</a>
    </div>
  </section>
  <div class="shell hidden" id="app-shell">
    <aside class="sidebar" id="sidebar">
      <a class="brand" href="/" aria-label="NexTech home"><span class="brand-mark">N</span><span>NexTech<small>Operations dashboard</small></span></a>
      <div class="nav-label">Workspace</div>
      <nav class="nav" aria-label="Dashboard sections">
        <button class="active" data-page="overview"><span class="ico">▦</span>Overview</button>
        <button data-page="tickets"><span class="ico">▤</span><span id="tickets-nav-label">My tickets</span></button>
        <button data-page="warnings"><span class="ico">◇</span>Warnings</button>
        <button data-page="support"><span class="ico">✦</span>AI support</button>
        <button data-page="report"><span class="ico">⚑</span>Submit a report</button>
        <div class="staff-only hidden" id="staff-nav">
          <div class="nav-label">Staff tools</div>
          <button data-page="members"><span class="ico">♙</span>Members</button>
          <button data-page="products"><span class="ico">▣</span>Products</button>
          <button data-page="faq"><span class="ico">?</span>Help articles</button>
        </div>
        <div class="moderator-only hidden" id="moderator-nav">
          <div class="nav-label">Moderation</div>
          <button data-page="activity"><span class="ico">◷</span>Activity log</button>
        </div>
        <div class="developer-only hidden" id="admin-nav">
          <div class="nav-label">Administration</div>
          <button data-page="accounts"><span class="ico">♙</span>Accounts &amp; team</button>
        </div>
      </nav>
      <div class="sidebar-foot">NexTech Interactive Development LLC<br>Role-based access · Secure Roblox sign-in</div>
    </aside>
    <main class="main">
      <header class="topbar">
        <button class="btn secondary menu-toggle" id="menu-toggle" type="button" aria-label="Open menu">☰</button>
        <div class="topbar-title"><span class="status-dot"></span>Account and community operations</div>
        <button class="search-trigger" id="open-search" type="button" aria-label="Open site search"><svg class="search-svg" viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5.7"/><path d="m13 13 4 4"/></svg><span class="search-label">Search policies, posts, products, topics and members…</span><kbd>⌘ K</kbd></button>
        <div class="user-chip"><span class="user-avatar" id="user-initials">NX</span><span id="user-name">NexTech member</span><span class="role-pill" id="user-role">Member</span></div>
        <button class="btn secondary" id="logout-button" type="button">Sign out</button>
      </header>
      <div class="content">
        <section class="page-view" id="page-overview">
          <div class="page-head"><div><div class="eyebrow">Your NexTech account</div><h1>Dashboard</h1><p>Account activity, support, and community tools in one place.</p></div><div class="page-actions"><button class="btn secondary" id="refresh-overview">↻ Refresh</button></div></div>
          <div class="grid">
            <article class="stat"><div class="stat-label">Warnings on your account</div><div class="stat-value" id="stat-warnings">—</div><div class="stat-note">Visible according to your role</div></article>
            <article class="stat"><div class="stat-label">Support tickets</div><div class="stat-value" id="stat-tickets">—</div><div class="stat-note">Open and past cases</div></article>
            <article class="stat staff-metric hidden"><div class="stat-label">All bot actions</div><div class="stat-value" id="stat-actions">—</div><div class="stat-note">Staff overview</div></article>
            <article class="stat staff-metric hidden"><div class="stat-label">Warning points</div><div class="stat-value" id="stat-points">—</div><div class="stat-note">Visible according to your role</div></article>
          </div>
          <div class="two-col">
            <section class="panel"><div class="panel-head"><div><h2>Recent account activity</h2><div class="panel-sub">Latest actions you are allowed to view</div></div></div><div id="recent-activity" class="panel-body"><div class="empty">Loading activity…</div></div></section>
            <section class="panel"><div class="panel-head"><div><h2>Quick actions</h2><div class="panel-sub">Common NexTech tasks</div></div></div><div class="panel-body" style="display:grid;gap:9px"><button class="btn secondary" data-go="support">✦ Start support chat</button><button class="btn secondary" data-go="tickets">▤ View my tickets</button><button class="btn secondary" data-go="report">⚑ Submit a report</button><a class="btn secondary" href="/community/submissions">◷ Track reports &amp; appeals</a><a class="btn secondary" href="/portal">↗ Open customer portal</a></div></section>
          </div>
        </section>
        <section class="page-view hidden" id="page-tickets">
          <div class="page-head"><div><div class="eyebrow">Support history</div><h1 id="tickets-title">Tickets</h1><p id="tickets-description">Your support cases. Staff can review cases assigned to the NexTech team.</p></div><button class="btn" data-go="support">＋ Start a support request</button></div>
          <section class="panel"><div class="panel-head"><div><h2>Support cases</h2><div class="panel-sub" id="tickets-subtitle">Only tickets associated with your account are shown.</div></div><button class="btn secondary" id="refresh-tickets">↻ Refresh</button></div><div id="tickets-table"></div></section>
          <section class="panel hidden" id="ticket-detail-panel" style="margin-top:13px">
            <div class="panel-head"><div><h2 id="ticket-detail-title">Ticket conversation</h2><div class="panel-sub" id="ticket-detail-subtitle"></div></div><button class="btn secondary" id="close-ticket-detail" type="button">Close</button></div>
            <div id="ticket-conversation" class="ticket-conversation" aria-live="polite"></div>
            <form id="ticket-reply-form" class="hidden" style="margin-top:14px"><label class="field"><span>Reply as NexTech staff</span><textarea class="input" id="ticket-reply-input" maxlength="3000" required></textarea></label><button class="btn" type="submit">Send reply</button><div class="auth-message" id="ticket-reply-status" role="status" aria-live="polite"></div></form>
          </section>
        </section>
        <section class="page-view hidden" id="page-warnings">
          <div class="page-head"><div><div class="eyebrow">Community standing</div><h1>Warnings</h1><p>Review warnings visible for your account and role.</p></div><button class="btn secondary" id="refresh-warnings">↻ Refresh</button></div>
          <section class="panel"><div class="panel-head"><div><h2>Warning history</h2><div class="panel-sub">Moderators and developers can review community-wide records.</div></div></div><div id="warnings-table"></div></section>
        </section>
        <section class="page-view hidden" id="page-members">
          <div class="page-head"><div><div class="eyebrow">Staff tools</div><h1>Member lookup</h1><p>Search NexTech Discord members by name, ID, or linked Roblox username.</p></div></div>
          <section class="panel"><form class="inline-form" id="member-search-form"><input class="input" id="member-query" minlength="2" placeholder="Search members…" autocomplete="off"><button class="btn" type="submit">Search</button></form><div id="member-results"></div></section>
        </section>
        <section class="page-view hidden" id="page-products">
          <div class="page-head"><div><div class="eyebrow">Staff tools</div><h1>Products</h1><p>Review the NexTech catalog. Developers can add entries.</p></div><button class="btn secondary" id="refresh-products">↻ Refresh</button></div>
          <section class="panel developer-only hidden" id="product-form-panel"><div class="panel-head"><div><h2>Add a product</h2><div class="panel-sub">Publish a product with its sale currency and optional image or downloadable file.</div></div></div><form id="product-form" enctype="multipart/form-data"><div class="form-row"><div class="field"><label for="product-name">Name</label><input class="input" id="product-name" maxlength="120" required></div><div class="field"><label for="product-category">Category</label><input class="input" id="product-category" maxlength="80" value="General" required></div></div><div class="field"><label for="product-description">Description</label><textarea class="input" id="product-description" maxlength="1000"></textarea></div><div class="form-row"><div class="field"><label for="product-currency">Sale currency</label><select class="input" id="product-currency"><option value="Robux">Robux</option><option value="USD">USD (sold in USD)</option></select></div><div class="field"><label for="product-price" id="product-price-label">Price (Robux)</label><input class="input" id="product-price" type="number" min="1" max="100000000" step="1" required></div></div><div class="form-row"><div class="field"><label for="product-image">Product image (optional; PNG, JPG, GIF, or WebP, max 5 MB)</label><input class="input" id="product-image" type="file" accept=".png,.jpg,.jpeg,.gif,.webp,image/png,image/jpeg,image/gif,image/webp"></div><div class="field"><label for="product-file">Downloadable product file (optional; ZIP, PDF, RBXM, RBX, or TXT, max 25 MB)</label><input class="input" id="product-file" type="file" accept=".zip,.pdf,.rbxm,.rbx,.txt"></div></div><button class="btn" type="submit">Create product</button><div class="auth-message" id="product-message" aria-live="polite"></div></form></section>
          <section class="panel" style="margin-top:13px"><div id="products-grid"></div></section>
        </section>
        <section class="page-view hidden" id="page-faq">
          <div class="page-head"><div><div class="eyebrow">Staff tools</div><h1>Help articles</h1><p>Manage the FAQ entries shown across NexTech help tools.</p></div><button class="btn secondary" id="refresh-faq">↻ Refresh</button></div>
          <section class="panel developer-only hidden" id="faq-form-panel"><div class="panel-head"><div><h2>Add an FAQ entry</h2><div class="panel-sub">New entries are written to the bot’s existing FAQ store.</div></div></div><form id="faq-form"><div class="form-row"><div class="field"><label for="faq-category">Category</label><input class="input" id="faq-category" maxlength="80" required></div><div class="field"><label for="faq-question">Question</label><input class="input" id="faq-question" maxlength="300" required></div></div><div class="field"><label for="faq-answer">Answer</label><textarea class="input" id="faq-answer" maxlength="4000" required></textarea></div><button class="btn" type="submit">Add article</button><div class="auth-message" id="faq-message" aria-live="polite"></div></form></section>
          <section class="panel" style="margin-top:13px"><div id="faq-list"></div></section>
        </section>
        <section class="page-view hidden" id="page-activity">
          <div class="page-head"><div><div class="eyebrow">Moderation</div><h1>Activity log</h1><p>Recent recorded NexTech bot actions. Visible to moderators and developers.</p></div><button class="btn secondary" id="refresh-activity">↻ Refresh</button></div>
          <section class="panel"><div id="activity-table"></div></section>
        </section>
        <section class="page-view hidden" id="page-accounts">
          <div class="page-head"><div><div class="eyebrow">Administration · Developers only</div><h1>Accounts &amp; team</h1><p>Manage NexTech email/password accounts and public team profiles. Identity links must match the live Discord ↔ Roblox Hub link.</p></div><button class="btn secondary" id="refresh-accounts">↻ Refresh</button></div>
          <section class="panel"><div class="panel-head"><div><h2>Create an account</h2><div class="panel-sub">The user receives an email link to set their password and verify their email.</div></div></div>
            <form id="account-create-form"><div class="form-row"><label class="field">Username<input class="input" id="account-create-username" maxlength="40" required></label><label class="field">Email address<input class="input" id="account-create-email" type="email" maxlength="200" required></label></div><button class="btn" type="submit">Create and email setup link</button><div class="auth-message" id="account-create-status" role="status" aria-live="polite"></div></form>
          </section>
          <section class="panel" style="margin-top:13px"><div class="panel-head"><div><h2>Account directory</h2><div class="panel-sub">Password hashes and reset tokens are never exposed.</div></div></div>
            <form class="inline-form" id="account-search-form"><input class="input" id="account-query" type="search" placeholder="Search username or email…" autocomplete="off"><button class="btn" type="submit">Search</button></form><div id="accounts-table"></div>
          </section>
          <section class="panel" style="margin-top:13px">
            <div class="panel-head"><div><h2>Verified team directory</h2><div class="panel-sub">Publish a profile for a Discord member with an active, matching Roblox Hub link. A check mark identifies status; it does not grant permissions.</div></div><button class="btn secondary" id="refresh-team-admin" type="button">↻ Refresh</button></div>
            <form id="team-profile-form">
              <div class="form-row">
                <label class="field">Discord user ID<input class="input" id="team-discord-id" inputmode="numeric" pattern="[0-9]{17,20}" maxlength="20" required placeholder="Discord ID"></label>
                <label class="field">Official verification mark<select class="input" id="team-verification-mark" required><option value="formal_staff">Formal NexTech Staff</option><option value="green">Green · Whitelisted Group Member</option><option value="blue">Blue · Dedicated Member Supporter</option><option value="yellow">Yellow · Staff Volunteer</option><option value="orange">Orange · Community Moderation Staff</option><option value="silver">Silver · Hybrid &amp; Virtual Operations Staff</option><option value="purple">Purple · Director or Chief-Level</option><option value="deep_blue">Deep Blue · Owner</option><option value="black">Black · Suspended, Terminated, or Blacklisted</option></select></label>
              </div>
              <div class="form-row">
                <label class="field">Public team title<input class="input" id="team-title" maxlength="80" required placeholder="Role or department"></label>
                <label class="field">Team join date<input class="input" id="team-join-date" type="date"></label>
              </div>
              <label class="field">Public biography<textarea class="input" id="team-bio" maxlength="500" placeholder="Optional public staff introduction"></textarea></label>
              <button class="btn" type="submit">Add or update team profile</button><div class="auth-message" id="team-profile-status" role="status" aria-live="polite"></div>
            </form>
            <div id="team-profiles-table" style="margin-top:12px"></div>
          </section>
        </section>
        <section class="page-view hidden" id="page-support">
          <div class="page-head"><div><div class="eyebrow">NexTech support</div><h1>Support assistant</h1><p>Describe your issue. The assistant will collect the details needed before offering to open a private Discord case.</p></div><button class="btn secondary" id="clear-chat">Clear chat</button></div>
          <section class="panel"><div class="chat"><div class="chat-messages" id="chat-messages"><div class="bubble assistant">Hi — tell me what you need help with, and I’ll collect the information NexTech staff need.</div></div><div class="chat-entry"><input class="input" id="chat-input" maxlength="2000" placeholder="Write a message…"><button class="btn" id="chat-send">Send</button></div></div><div id="ticket-confirm" class="notice hidden" style="margin-top:12px"></div><div id="support-message" class="auth-message" role="status" aria-live="polite"></div></section>
        </section>
        <section class="page-view hidden" id="page-report">
          <div class="page-head"><div><div class="eyebrow">Safety and community</div><h1>Submit a report</h1><p>Reports are sent to the configured NexTech Discord staff channel for review.</p></div></div>
          <section class="panel"><form id="report-form"><div class="form-row"><div class="field"><label for="report-category">Category</label><select class="input" id="report-category"><option>Other</option><option>Community member</option><option>Staff member</option><option>Roblox</option><option>Discord</option><option>Safety concern</option></select></div><div class="field"><label for="report-target">Who or what are you reporting?</label><input class="input" id="report-target" maxlength="160" required></div></div><div class="field"><label for="report-details">What happened?</label><textarea class="input" id="report-details" minlength="10" maxlength="3000" required></textarea></div><button class="btn" type="submit">Submit report</button><div class="auth-message" id="report-message" role="status" aria-live="polite"></div></form></section>
        </section>
      </div>
    </main>
  </div>
  <section class="search-overlay hidden" id="search-overlay" role="dialog" aria-modal="true" aria-label="Search NexTech">
    <div class="search-dialog">
      <div class="search-input-row">
        <svg class="search-svg" viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5.7"/><path d="m13 13 4 4"/></svg>
        <input class="search-input" id="site-search-input" type="search" autocomplete="off" placeholder="Search policies, posts, products, topics and members…" aria-label="Search policies, posts, products, topics and members">
        <button class="search-close" id="close-search" type="button" aria-label="Close search">×</button>
      </div>
      <div class="search-filters" aria-label="Search filters">
        <span class="search-filters-label">Filter by:</span>
        <button class="search-chip" type="button" data-search-filter="policies"><span class="chip-icon">♧</span>Policies</button>
        <button class="search-chip" type="button" data-search-filter="posts"><span class="chip-icon">▤</span>Posts</button>
        <button class="search-chip" type="button" data-search-filter="products"><span class="chip-icon">▣</span>Products</button>
        <button class="search-chip" type="button" data-search-filter="topics"><span class="chip-icon">▱</span>Topics</button>
        <button class="search-chip" type="button" data-search-filter="members"><span class="chip-icon">♙</span>Members</button>
        <div class="search-date-wrap">
          <button class="search-chip" id="search-date-trigger" type="button" aria-haspopup="true" aria-expanded="false"><span class="chip-icon">▦</span><span id="search-date-label">Date Filters</span><span>⌄</span></button>
          <div class="search-date-menu hidden" id="search-date-menu">
            <button type="button" data-search-days="0" class="active">Any time</button>
            <button type="button" data-search-days="1">Past 24 hours</button>
            <button type="button" data-search-days="7">Past 7 days</button>
            <button type="button" data-search-days="30">Past 30 days</button>
            <button type="button" data-search-days="365">Past year</button>
          </div>
        </div>
      </div>
      <div class="search-empty" id="search-empty"><div>Start typing to search…<small>⌘ K to focus</small></div></div>
      <div class="search-results hidden" id="search-results" role="listbox" aria-label="Search results"></div>
    </div>
  </section>
  <script>
    (() => {
      const $ = (selector, root=document) => root.querySelector(selector);
      const $$ = (selector, root=document) => [...root.querySelectorAll(selector)];
      const state = {token:"", actor:null, page:"overview", messages:[], intake:null, ready:false, currentTicket:""};
      const searchState = {filter:"all",days:0,sequence:0,cache:{products:null,topics:null,policies:null}};
      const roleLevel = () => Number(state.actor?.role_level || 0);
      const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
      const setNotice = (node, text, kind="") => { if (!node) return; node.textContent=text; node.className=`auth-message ${kind}`; };
      const loginErrors = {
        roblox_not_linked:"Your Roblox account is not linked to a Discord member in the NexTech server.",
        roblox_oauth_not_configured:"Roblox sign-in is not configured yet. Contact the site administrator.",
        base_url_not_configured:"The public website URL is not configured for sign-in.",
        invalid_state:"Your sign-in session expired. Please try again.",
        token_exchange_failed:"Roblox sign-in could not be completed. Please try again.",
        user_fetch_failed:"NexTech could not load your Roblox profile.",
        access_denied:"Roblox sign-in was cancelled."
      };
      async function api(path, options={}) {
        const headers = new Headers(options.headers || {});
        headers.set("X-Customer-Token", state.token);
        if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type","application/json");
        const response = await fetch(path,{...options,headers,credentials:"same-origin"});
        let data={};
        try { data=await response.json(); } catch (_) {}
        if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
        return data;
      }
      function showLogin(message="") {
        $("#auth-screen").classList.remove("hidden");
        $("#app-shell").classList.add("hidden");
        $("#auth-message").textContent=message;
        $("#login-link").href="/auth/roblox?next=/dashboard";
      }
      function showApp(actor) {
        state.actor=actor;
        $("#auth-screen").classList.add("hidden");
        $("#app-shell").classList.remove("hidden");
        const name=actor.display_name || actor.roblox_username || "NexTech member";
        $("#user-name").textContent=name;
        $("#user-role").textContent=actor.role_name || "Member";
        $("#user-initials").textContent=name.split(/\s+/).slice(0,2).map(x=>x[0]||"").join("").toUpperCase() || "NX";
        if (actor.role_level >= 2) $("#staff-nav").classList.remove("hidden");
        if (actor.role_level >= 3) {
          $("#moderator-nav").classList.remove("hidden");
          $$(".staff-metric").forEach(node=>node.classList.remove("hidden"));
        }
        if (actor.role_level >= 4) {
          $("#admin-nav").classList.remove("hidden");
          $$(".developer-only").forEach(node=>node.classList.remove("hidden"));
        }
        if (actor.role_level >= 2) {
          $("#tickets-nav-label").textContent="Team tickets";
          $("#tickets-title").textContent="Team tickets";
          $("#tickets-description").textContent="Review NexTech support cases and reply directly in their Discord ticket channel.";
          $("#tickets-subtitle").textContent="Open a conversation to review messages and reply as staff.";
        }
      }
      function table(headers, rows, empty="No records to show.") {
        if (!rows.length) return `<div class="empty">${esc(empty)}</div>`;
        return `<div class="table-wrap"><table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
      }
      function row(cells) { return `<tr>${cells.map(cell=>`<td>${cell}</td>`).join("")}</tr>`; }
      function statusBadge(value) {
        const text=String(value||"unknown");
        const cls=/closed|resolved|complete|active|verified|restored/i.test(text)?"good":/suspend|blocked|failed/i.test(text)?"bad":/open|waiting|pending|unverified/i.test(text)?"warn":"";
        return `<span class="badge ${cls}">${esc(text)}</span>`;
      }
      const stamp = value => {
        if (!value) return "—";
        const date = typeof value === "number" ? new Date(value*1000) : new Date(value);
        return Number.isNaN(date.getTime()) ? esc(value) : esc(date.toLocaleString());
      };
      async function loadOverview() {
        const data=await api("/api/dashboard/overview");
        const stats=data.stats||{};
        $("#stat-warnings").textContent=stats.warning_count ?? 0;
        $("#stat-tickets").textContent=stats.ticket_count ?? 0;
        $("#stat-actions").textContent=stats.action_count ?? 0;
        $("#stat-points").textContent=stats.warning_points ?? 0;
        const items=data.recent_activity||[];
        $("#recent-activity").innerHTML=items.length
          ? `<div style="display:grid;gap:9px">${items.map(item=>`<div style="display:flex;gap:10px;padding:9px 0;border-bottom:1px solid #252a34"><span class="badge">${esc(item.action_type||"action")}</span><div style="min-width:0"><div style="font-size:11px;color:#e2e6ee">${esc(item.actor||"NexTech")}</div><div style="margin-top:3px;color:#858e9d;font-size:10px">${esc(item.details||"Recorded activity")} · ${esc(item.timestamp_str||"")}</div></div></div>`).join("")}</div>`
          : `<div class="empty">${roleLevel()<3?"Activity details are available to moderators and developers.":"No recent activity."}</div>`;
      }
      async function loadTickets() {
        const data=await api("/api/dashboard/tickets");
        const rows=(data.tickets||[]).map(t=>row([
          `<strong>${esc(t.case_id||t.case_name||"Case")}</strong><div style="color:#8d96a5;margin-top:3px">${esc(t.case_type||"Support")}</div>`,
          esc(t.discord_username||t.roblox_username||"NexTech member"),
          esc(t.reason||t.confirmed_type||"—"),
          statusBadge(t.status),
          stamp(t.opened_at||t.opened_date),
          roleLevel()>=2?`<button class="btn secondary ticket-open" data-case-id="${esc(t.case_id||"")}">Conversation</button>`:""
        ]));
        $("#tickets-table").innerHTML=table(roleLevel()>=2?["Case","Member","Summary","Status","Opened","Action"]:["Case","Member","Summary","Status","Opened"],rows,"No support cases found.");
      }
      async function loadTicketConversation(caseId) {
        state.currentTicket=caseId;
        $("#ticket-detail-panel").classList.remove("hidden");
        $("#ticket-detail-title").textContent=`Ticket ${caseId}`;
        $("#ticket-detail-subtitle").textContent="Loading conversation…";
        $("#ticket-conversation").innerHTML='<div class="empty">Loading ticket messages…</div>';
        $("#ticket-reply-form").classList.add("hidden");
        const data=await api(`/api/dashboard/tickets/${encodeURIComponent(caseId)}`);
        const ticket=data.ticket||{};
        $("#ticket-detail-subtitle").textContent=`${ticket.discord_username||ticket.roblox_username||"NexTech member"} · ${ticket.case_type||"Support"} · ${ticket.status||"open"}`;
        const messages=data.messages||[];
        $("#ticket-conversation").innerHTML=messages.length?messages.map(message=>`<article class="ticket-message"><div><strong>${esc(message.author||"NexTech")}</strong><time>${esc(message.created_at||"")}</time></div><p>${esc(message.content||"")}</p></article>`).join(""):'<div class="empty">This ticket has no readable messages.</div>';
        $("#ticket-conversation").scrollTop=$("#ticket-conversation").scrollHeight;
        if (!["closed","resolved","deleted"].includes(String(ticket.status||"").toLowerCase())) $("#ticket-reply-form").classList.remove("hidden");
        $("#ticket-detail-panel").scrollIntoView({behavior:"smooth",block:"start"});
      }
      async function loadAccounts() {
        const query=$("#account-query").value.trim();
        const data=await api(`/api/dashboard/accounts?q=${encodeURIComponent(query)}`);
        const rows=(data.accounts||[]).map(account=>row([
          `<strong>${esc(account.username||"—")}</strong><div style="color:#8d96a5;margin-top:3px">${esc(account.email||"")}</div><div style="color:#747d8b;margin-top:4px">Discord ${esc(account.discord_id||"—")} · Roblox ${esc(account.roblox_id||"—")}</div>`,
          statusBadge(account.email_verified?"Verified":"Unverified"),
          statusBadge(account.suspended?"Suspended":"Active"),
          statusBadge(account.discord_id&&account.roblox_id?"Hub IDs saved · rechecked at sign-in":"Needs Hub link"),
          stamp(account.created_at),
          `<div class="account-actions">${account.suspended?`<button class="btn secondary account-action" data-action="restore" data-email="${esc(account.email)}">Restore</button>`:`<button class="btn danger account-action" data-action="suspend" data-email="${esc(account.email)}">Suspend</button>`}<button class="btn secondary account-action" data-action="recover" data-email="${esc(account.email)}">Send recovery link</button></div><details class="account-link-details"><summary>Link verified Discord + Roblox</summary><form class="account-link-form" data-email="${esc(account.email)}"><label>Discord ID<input class="input" name="discord_id" inputmode="numeric" pattern="[0-9]{17,20}" maxlength="20" value="${esc(account.discord_id||"")}" required></label><label>Roblox user ID<input class="input" name="roblox_id" inputmode="numeric" pattern="[0-9]{1,20}" maxlength="20" value="${esc(account.roblox_id||"")}" required></label><button class="btn secondary" type="submit">Verify and link</button></form></details>`
        ]));
        $("#accounts-table").innerHTML=table(["Website account","Email status","Access","Hub link","Created","Admin actions / identity link"],rows,`No website accounts found. ${data.total||0} total match(es).`);
      }
      async function loadTeamProfiles() {
        const data=await api("/api/dashboard/team");
        const rows=(data.profiles||[]).map(profile=>row([
          `<strong>${esc(profile.display_name||profile.discord_id)}</strong><div style="color:#8d96a5;margin-top:3px">${esc(profile.discord_id)}</div>`,
          `<span class="verification-mark mark-${esc(profile.verification_mark)}">${esc(profile.verification_icon||"✔")}</span> ${esc(profile.verification_label||"Formal Staff")}`,
          esc(profile.title||""),
          `<div class="account-actions"><button class="btn secondary team-edit" type="button" data-discord-id="${esc(profile.discord_id)}" data-mark="${esc(profile.verification_mark||"formal_staff")}" data-title="${esc(profile.title||"")}" data-bio="${esc(profile.bio||"")}" data-joined="${esc(profile.team_join_date||"")}">Edit</button><button class="btn danger team-remove" type="button" data-discord-id="${esc(profile.discord_id)}">Remove</button></div>`
        ]));
        $("#team-profiles-table").innerHTML=table(["Verified member","Official mark","Public title","Actions"],rows,"No manually managed team profiles yet. Members with the configured NexTech Discord staff roles remain listed automatically.");
      }
      async function loadWarnings() {
        const data=await api("/api/dashboard/warnings");
        const rows=(data.warnings||[]).map(w=>row([
          esc(w.username||w.user_name||w.user_id||"Member"),
          esc(w.reason||w.details||"—"),
          `<span class="badge warn">${esc(w.points ?? 1)} point(s)</span>`,
          stamp(w.timestamp||w.created_at||w.date)
        ]));
        $("#warnings-table").innerHTML=table(["Member","Reason","Points","Date"],rows,"No warnings visible for this account.");
      }
      async function loadMembers() {
        const query=$("#member-query").value.trim();
        if (query.length<2) { $("#member-results").innerHTML='<div class="notice">Enter at least 2 characters to search.</div>'; return; }
        $("#member-results").innerHTML='<div class="empty">Searching…</div>';
        const data=await api(`/api/dashboard/members?q=${encodeURIComponent(query)}`);
        const rows=(data.members||[]).map(m=>row([
          `<strong>${esc(m.display_name||m.discord_username||"Member")}</strong><div style="color:#8d96a5;margin-top:3px">${esc(m.discord_username||"")} · ${esc(m.discord_id||"")}</div>`,
          esc(m.roblox_username||"Not linked"),
          `<span class="badge">${esc(m.role_name||"Member")}</span>`,
          esc((m.roles||[]).join(", ")||"—"),
          m.linked?`<span class="badge good">Linked</span>`:`<span class="badge">Not linked</span>`
        ]));
        $("#member-results").innerHTML=table(["Discord member","Roblox","Access","Server roles","Account"],rows,data.error||"No matching members.");
      }
      async function loadProducts() {
        const data=await api("/api/dashboard/products");
        const products=data.products||[];
        $("#products-grid").innerHTML=products.length?`<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(210px,1fr))">${products.map(p=>{const usd=p.currency==="USD"||(p.price_usd>0&&!p.robux);const amount=usd?Number(p.price_usd):Number(p.robux);const price=Number.isFinite(amount)&&amount>0?(usd?`$${amount.toFixed(2)} USD · Sold in USD`:`R$ ${amount.toLocaleString()} Robux`):"Price not listed";return `<article class="stat"><div class="stat-label">${esc(p.category||"General")}</div><div style="margin-top:9px;font-size:14px;font-weight:800">${esc(p.name||"Untitled")}</div><div class="stat-note" style="margin-top:8px;line-height:1.5">${esc(p.description||"")}</div><div class="stat-note" style="margin-top:10px">${esc(price)}</div></article>`}).join("")}</div>`:'<div class="empty">No products are listed.</div>';
      }
      async function loadFaq() {
        const data=await api("/api/dashboard/faq");
        const entries=data.faq||[];
        $("#faq-list").innerHTML=entries.length?`<div style="display:grid;gap:9px">${entries.map(item=>`<article class="stat" style="min-height:0"><div class="panel-head"><span class="badge">${esc(item.category||"General")}</span>${roleLevel()>=4?`<button class="btn danger faq-delete" data-id="${esc(item.id)}">Delete</button>`:""}</div><strong style="font-size:12px">${esc(item.question||"")}</strong><p style="margin:8px 0 0;color:#a2a9b7;font-size:11px;line-height:1.6;white-space:pre-wrap">${esc(item.answer||"")}</p></article>`).join("")}</div>`:'<div class="empty">No help articles are available.</div>';
      }
      async function loadActivity() {
        const data=await api("/api/dashboard/activity?limit=100");
        const rows=(data.actions||[]).map(a=>row([
          esc(a.timestamp_str||stamp(a.timestamp)),
          esc(a.actor||a.username||"—"),
          `<span class="badge">${esc(a.action_type||"action")}</span>`,
          esc(a.command_name?"/"+a.command_name:"—"),
          esc(a.details||"—")
        ]));
        $("#activity-table").innerHTML=table(["When","Actor","Action","Command","Details"],rows,"No activity records found.");
      }
      const loaders={overview:loadOverview,tickets:loadTickets,warnings:loadWarnings,products:loadProducts,faq:loadFaq,activity:loadActivity,accounts:()=>Promise.all([loadAccounts(),loadTeamProfiles()])};
      function setPage(page) {
        if ((["members","products","faq"].includes(page) && roleLevel()<2) || (page==="activity"&&roleLevel()<3) || (page==="accounts"&&roleLevel()<4)) page="overview";
        state.page=page;
        $$(".page-view").forEach(node=>node.classList.toggle("hidden",node.id!==`page-${page}`));
        $$(".nav button[data-page]").forEach(button=>button.classList.toggle("active",button.dataset.page===page));
        $("#sidebar").classList.remove("open");
        const loader=loaders[page];
        if (loader) loader().catch(error=>setNotice($("#recent-activity"),error.message));
      }
      const searchLabels={policies:"Policies",posts:"Posts",products:"Products",topics:"Topics",members:"Members"};
      const searchIcons={policies:"♧",posts:"▤",products:"▣",topics:"▱",members:"♙"};
      function showSearchEmpty(message="Start typing to search…",hint="⌘ K to focus") {
        $("#search-empty").innerHTML=`<div>${esc(message)}${hint?`<small>${esc(hint)}</small>`:""}</div>`;
        $("#search-empty").classList.remove("hidden");
        $("#search-results").classList.add("hidden");
        $("#search-results").innerHTML="";
      }
      function openSiteSearch() {
        if (!state.actor) return;
        $("#search-overlay").classList.remove("hidden");
        document.body.classList.add("search-open");
        window.setTimeout(()=>$("#site-search-input").focus(),0);
      }
      function closeSiteSearch() {
        $("#search-overlay").classList.add("hidden");
        document.body.classList.remove("search-open");
        $("#search-date-menu").classList.add("hidden");
        $("#search-date-trigger").setAttribute("aria-expanded","false");
        $("#open-search").focus();
      }
      function getSearchTimestamp(value) {
        if (!value) return null;
        const date=new Date(typeof value==="number"?(value<100000000000?value*1000:value):value);
        return Number.isNaN(date.getTime())?null:date;
      }
      function matchesSearchDate(item) {
        if (!searchState.days) return true;
        const date=getSearchTimestamp(item.date);
        return Boolean(date && date.getTime()>=Date.now()-searchState.days*86400000);
      }
      function searchTextMatches(text,tokens) {
        const normalized=String(text||"").toLocaleLowerCase();
        return tokens.every(token=>normalized.includes(token));
      }
      function searchSnippet(text,query) {
        const clean=String(text||"").replace(/\s+/g," ").trim();
        if (!clean) return "";
        const lower=clean.toLocaleLowerCase();
        const position=lower.indexOf(query.toLocaleLowerCase().split(/\s+/)[0]||"");
        const start=Math.max(0,position-48);
        const end=Math.min(clean.length,start+180);
        return `${start?"…":""}${clean.slice(start,end)}${end<clean.length?"…":""}`;
      }
      async function cachedSearchList(key,path,property) {
        if (!searchState.cache[key]) {
          searchState.cache[key]=api(path).then(data=>data[property]||[]).catch(error=>{
            searchState.cache[key]=null;
            throw error;
          });
        }
        return searchState.cache[key];
      }
      async function loadPolicySearchItems() {
        if (!searchState.cache.policies) {
          searchState.cache.policies=Promise.all([
            ["/privacy","Privacy Policy"],
            ["/terms","Terms of Service"]
          ].map(async ([href,title])=>{
            const response=await fetch(href,{credentials:"same-origin"});
            if (!response.ok) throw new Error(`Could not load ${title.toLowerCase()}.`);
            const html=await response.text();
            const documentPage=new DOMParser().parseFromString(html,"text/html");
            return {href,title,text:(documentPage.body?.innerText||documentPage.body?.textContent||"").replace(/\s+/g," ").trim()};
          })).catch(error=>{
            searchState.cache.policies=null;
            throw error;
          });
        }
        return searchState.cache.policies;
      }
      async function searchCategory(category,query,tokens) {
        if (category==="posts") return [];
        if (category==="policies") {
          const policies=await loadPolicySearchItems();
          return policies.filter(item=>searchTextMatches(`${item.title} ${item.text}`,tokens) && matchesSearchDate(item))
            .map(item=>({kind:category,title:item.title,summary:searchSnippet(item.text,query),target:item.href}));
        }
        if (category==="products") {
          const products=await cachedSearchList("products","/api/dashboard/products","products");
          return products.filter(item=>searchTextMatches(`${item.name||""} ${item.category||""} ${item.description||""}`,tokens) && matchesSearchDate({date:item.created_at||item.updated_at||item.timestamp}))
            .map(item=>({kind:category,title:item.name||"Untitled product",summary:[item.category,item.description].filter(Boolean).join(" · ")||"NexTech catalog",target:"products"}));
        }
        if (category==="topics") {
          const topics=await cachedSearchList("topics","/api/dashboard/faq","faq");
          return topics.filter(item=>searchTextMatches(`${item.category||""} ${item.question||""} ${item.answer||""}`,tokens) && matchesSearchDate({date:item.created_at||item.updated_at||item.timestamp}))
            .map(item=>({kind:category,title:item.question||item.category||"Help topic",summary:searchSnippet(item.answer||item.category||"",query),target:"faq"}));
        }
        if (category==="members") {
          if (roleLevel()<2 || query.length<2) return [];
          const data=await api(`/api/dashboard/members?q=${encodeURIComponent(query)}`);
          return (data.members||[]).filter(item=>searchTextMatches(`${item.display_name||""} ${item.discord_username||""} ${item.roblox_username||""} ${item.discord_id||""} ${(item.roles||[]).join(" ")}`,tokens) && matchesSearchDate({date:item.linked_at||item.created_at}))
            .map(item=>({kind:category,title:item.display_name||item.discord_username||item.roblox_username||"NexTech member",summary:[item.discord_username,item.roblox_username&&`Roblox: ${item.roblox_username}`,item.role_name].filter(Boolean).join(" · ")||item.discord_id||"Member profile",target:"members",query:item.discord_username||item.display_name||item.roblox_username||item.discord_id||""}));
        }
        return [];
      }
      async function runSiteSearch() {
        const query=$("#site-search-input").value.trim();
        const sequence=++searchState.sequence;
        if (!query) { showSearchEmpty(); return; }
        if (searchState.filter==="members" && roleLevel()<2) {
          showSearchEmpty("Member search is available to NexTech staff.","");
          return;
        }
        if (searchState.filter==="members" && query.length<2) {
          showSearchEmpty("Enter at least 2 characters to search members.","");
          return;
        }
        showSearchEmpty("Searching…","");
        const tokens=query.toLocaleLowerCase().split(/\s+/).filter(Boolean);
        const categories=searchState.filter==="all"?["policies","products","topics",...(roleLevel()>=2?["members"]:[])]:[searchState.filter];
        const responses=await Promise.allSettled(categories.map(category=>searchCategory(category,query,tokens)));
        if (sequence!==searchState.sequence) return;
        const items=responses.flatMap(result=>result.status==="fulfilled"?result.value:[]);
        const errors=responses.filter(result=>result.status==="rejected");
        const unique=items.filter((item,index,list)=>list.findIndex(other=>other.kind===item.kind&&other.title===item.title)===index);
        if (!unique.length) {
          let message=searchState.filter==="posts"?"Posts are not indexed in this dashboard yet.":"No matching results.";
          if (errors.length) message="Some search sources could not be loaded. Try again in a moment.";
          showSearchEmpty(message,searchState.filter==="all"?"Search policies, products, topics and staff-accessible member records.":"");
          return;
        }
        $("#search-empty").classList.add("hidden");
        $("#search-results").classList.remove("hidden");
        $("#search-results").innerHTML=unique.slice(0,40).map(item=>`
          <button class="search-result" type="button" role="option" data-search-kind="${esc(item.kind)}" data-search-target="${esc(item.target||"")}" data-search-query="${esc(item.query||"")}">
            <span class="search-result-icon">${searchIcons[item.kind]||"⌕"}</span>
            <span class="search-result-copy"><span class="search-result-title">${esc(item.title)}</span><span class="search-result-summary">${esc(item.summary||"")}</span><span class="search-result-type">${esc(searchLabels[item.kind]||item.kind)}</span></span>
          </button>`).join("");
      }
      function selectSearchFilter(filter) {
        searchState.filter=searchState.filter===filter?"all":filter;
        $$("[data-search-filter]").forEach(button=>button.classList.toggle("active",button.dataset.searchFilter===searchState.filter));
        runSiteSearch();
      }
      function handleSearchResult(event) {
        const result=event.target.closest(".search-result");
        if (!result) return;
        const {searchKind:kind,searchTarget:target,searchQuery:query}=result.dataset;
        closeSiteSearch();
        if (kind==="policies") { location.href=target; return; }
        if (kind==="members") {
          setPage("members");
          $("#member-query").value=query;
          loadMembers().catch(error=>setNotice($("#member-results"),error.message));
          return;
        }
        if (["products","topics"].includes(kind)) setPage(target);
      }
      function appendChat(role,text) {
        const node=document.createElement("div");
        node.className=`bubble ${role}`;
        node.textContent=text;
        $("#chat-messages").appendChild(node);
        $("#chat-messages").scrollTop=$("#chat-messages").scrollHeight;
      }
      async function sendChat() {
        const input=$("#chat-input");
        const text=input.value.trim();
        if (!text) return;
        input.value="";
        state.messages.push({role:"user",content:text});
        appendChat("user",text);
        $("#chat-send").disabled=true;
        setNotice($("#support-message"),"Thinking…");
        try {
          const data=await api("/api/dashboard/support/ai",{method:"POST",body:JSON.stringify({messages:state.messages})});
          const reply=data.reply||"Please tell me more about the issue.";
          state.messages.push({role:"assistant",content:reply});
          appendChat("assistant",reply);
          state.intake=data.intake||null;
          state.ready=Boolean(data.ready_to_create&&state.intake);
          const confirm=$("#ticket-confirm");
          confirm.classList.toggle("hidden",!state.ready);
          if (state.ready) confirm.innerHTML=`<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap"><span>The support details are ready. Open a private NexTech Discord ticket?</span><button class="btn" id="confirm-ticket">Open ticket</button></div>`;
          setNotice($("#support-message"),"");
        } catch (error) {
          appendChat("assistant",error.message);
          setNotice($("#support-message"),"");
        } finally { $("#chat-send").disabled=false; input.focus(); }
      }
      async function createTicket() {
        if (!state.ready||!state.intake||!window.confirm("Open this support ticket in the NexTech Discord server?")) return;
        const button=$("#confirm-ticket"); if (button) button.disabled=true;
        try {
          const data=await api("/api/dashboard/support/ticket",{method:"POST",body:JSON.stringify(state.intake)});
          $("#ticket-confirm").innerHTML=`<div class="notice success">Ticket created${data.case_id?` · Case ${esc(data.case_id)}`:""}.</div>`;
          state.ready=false;
          state.messages=[];
          await loadTickets();
        } catch (error) {
          setNotice($("#support-message"),error.message);
          if (button) button.disabled=false;
        }
      }
      async function submitReport(event) {
        event.preventDefault();
        const button=$("#report-form button[type=submit]"); button.disabled=true;
        setNotice($("#report-message"),"Submitting…");
        try {
          const data=await api("/api/dashboard/reports",{method:"POST",body:JSON.stringify({
            category:$("#report-category").value,
            target:$("#report-target").value.trim(),
            details:$("#report-details").value.trim()
          })});
          $("#report-form").reset();
          setNotice($("#report-message"),`Report submitted${data.report_id?` · ${data.report_id}`:""}.`,"success");
        } catch (error) { setNotice($("#report-message"),error.message); }
        finally { button.disabled=false; }
      }
      async function init() {
        const params=new URLSearchParams(location.search);
        const callbackToken=params.get("rt");
        const emailLoginToken=params.get("email_token");
        const authError=params.get("error");
        if (emailLoginToken) {
          history.replaceState({},document.title,"/dashboard");
          try {
            const response=await fetch("/api/dashboard/email-login/verify",{
              method:"POST",
              headers:{"Content-Type":"application/json","Accept":"application/json"},
              credentials:"same-origin",
              body:JSON.stringify({token:emailLoginToken})
            });
            const data=await response.json();
            if (!response.ok) throw new Error(data.error||"The sign-in link is invalid or expired.");
            state.token=data.token;
            localStorage.setItem("nx_cust_token",data.token);
          } catch(error) {
            localStorage.removeItem("nx_cust_token");
            showLogin(error.message);
            return;
          }
        } else if (callbackToken) {
          state.token=callbackToken;
          localStorage.setItem("nx_cust_token",callbackToken);
          history.replaceState({},document.title,"/dashboard");
        } else {
          state.token=localStorage.getItem("nx_cust_token")||"";
        }
        if (!state.token) { showLogin(loginErrors[authError]||""); return; }
        try {
          const data=await api("/api/dashboard/me");
          showApp(data.actor||{});
          setPage("overview");
        } catch (error) {
          localStorage.removeItem("nx_cust_token");
          showLogin(loginErrors[authError]||error.message);
        }
      }
      $$(".nav button[data-page]").forEach(button=>button.addEventListener("click",()=>setPage(button.dataset.page)));
      $$("[data-go]").forEach(button=>button.addEventListener("click",()=>setPage(button.dataset.go)));
      $("#menu-toggle").addEventListener("click",()=>$("#sidebar").classList.toggle("open"));
      $("#open-search").addEventListener("click",openSiteSearch);
      $("#close-search").addEventListener("click",closeSiteSearch);
      $("#search-results").addEventListener("click",handleSearchResult);
      $("#site-search-input").addEventListener("input",()=>{
        searchState.sequence++;
        window.clearTimeout(window.siteSearchTimer);
        window.siteSearchTimer=window.setTimeout(runSiteSearch,160);
      });
      $$("[data-search-filter]").forEach(button=>button.addEventListener("click",()=>selectSearchFilter(button.dataset.searchFilter)));
      $("#search-date-trigger").addEventListener("click",()=>{
        const menu=$("#search-date-menu");
        menu.classList.toggle("hidden");
        $("#search-date-trigger").setAttribute("aria-expanded",String(!menu.classList.contains("hidden")));
      });
      $("#search-date-menu").addEventListener("click",event=>{
        const option=event.target.closest("[data-search-days]");
        if (!option) return;
        searchState.days=Number(option.dataset.searchDays)||0;
        $$("#search-date-menu [data-search-days]").forEach(button=>button.classList.toggle("active",button===option));
        $("#search-date-label").textContent=searchState.days?option.textContent:"Date Filters";
        $("#search-date-menu").classList.add("hidden");
        $("#search-date-trigger").setAttribute("aria-expanded","false");
        runSiteSearch();
      });
      document.addEventListener("click",event=>{
        if (event.target instanceof Element && !event.target.closest(".search-date-wrap")) {
          $("#search-date-menu").classList.add("hidden");
          $("#search-date-trigger").setAttribute("aria-expanded","false");
        }
      });
      document.addEventListener("keydown",event=>{
        if ((event.metaKey||event.ctrlKey) && event.key.toLocaleLowerCase()==="k") {
          event.preventDefault();
          openSiteSearch();
        } else if (event.key==="Escape" && !$("#search-overlay").classList.contains("hidden")) {
          closeSiteSearch();
        }
      });
      $("#refresh-overview").addEventListener("click",()=>loadOverview().catch(error=>setNotice($("#recent-activity"),error.message)));
      $("#refresh-tickets").addEventListener("click",()=>loadTickets().catch(error=>setNotice($("#tickets-table"),error.message)));
      $("#refresh-warnings").addEventListener("click",()=>loadWarnings().catch(error=>setNotice($("#warnings-table"),error.message)));
      $("#refresh-products").addEventListener("click",()=>loadProducts().catch(error=>setNotice($("#products-grid"),error.message)));
      $("#refresh-faq").addEventListener("click",()=>loadFaq().catch(error=>setNotice($("#faq-list"),error.message)));
      $("#refresh-activity").addEventListener("click",()=>loadActivity().catch(error=>setNotice($("#activity-table"),error.message)));
      $("#refresh-accounts").addEventListener("click",()=>loadAccounts().catch(error=>setNotice($("#accounts-table"),error.message)));
      $("#refresh-team-admin").addEventListener("click",()=>loadTeamProfiles().catch(error=>setNotice($("#team-profiles-table"),error.message)));
      $("#member-search-form").addEventListener("submit",event=>{event.preventDefault();loadMembers().catch(error=>setNotice($("#member-results"),error.message));});
      $("#tickets-table").addEventListener("click",event=>{
        const button=event.target.closest(".ticket-open");
        if (!button) return;
        loadTicketConversation(button.dataset.caseId).catch(error=>{
          $("#ticket-detail-panel").classList.remove("hidden");
          $("#ticket-detail-subtitle").textContent="Could not load the ticket conversation.";
          $("#ticket-conversation").innerHTML=`<div class="notice error">${esc(error.message)}</div>`;
        });
      });
      $("#close-ticket-detail").addEventListener("click",()=>{
        $("#ticket-detail-panel").classList.add("hidden");
        state.currentTicket="";
      });
      $("#ticket-reply-form").addEventListener("submit",async event=>{
        event.preventDefault();
        const message=$("#ticket-reply-input").value.trim();
        const button=event.currentTarget.querySelector('button[type="submit"]');
        if (!state.currentTicket||!message) return;
        button.disabled=true;
        setNotice($("#ticket-reply-status"),"Sending reply…");
        try {
          await api(`/api/dashboard/tickets/${encodeURIComponent(state.currentTicket)}/reply`,{
            method:"POST",body:JSON.stringify({message})
          });
          $("#ticket-reply-input").value="";
          setNotice($("#ticket-reply-status"),"Reply sent to the Discord ticket.","success");
          await loadTicketConversation(state.currentTicket);
        } catch(error) {
          setNotice($("#ticket-reply-status"),error.message);
        } finally {
          button.disabled=false;
        }
      });
      $("#account-search-form").addEventListener("submit",event=>{event.preventDefault();loadAccounts().catch(error=>setNotice($("#accounts-table"),error.message));});
      $("#dashboard-email-login-form").addEventListener("submit",async event=>{
        event.preventDefault();
        const button=event.currentTarget.querySelector('button[type="submit"]');
        const email=$("#dashboard-login-email").value.trim();
        button.disabled=true;
        setNotice($("#email-login-status"),"Requesting your secure sign-in link…");
        try {
          const response=await fetch("/api/dashboard/email-login/request",{
            method:"POST",headers:{"Content-Type":"application/json","Accept":"application/json"},
            credentials:"same-origin",body:JSON.stringify({email})
          });
          const data=await response.json();
          if (!response.ok) throw new Error(data.error||"Could not send the sign-in link.");
          setNotice($("#email-login-status"),data.message||"If this verified email is linked to a current Discord ↔ Roblox Hub account, a single-use sign-in link has been sent.","success");
        } catch(error) {
          setNotice($("#email-login-status"),error.message);
        } finally {
          button.disabled=false;
        }
      });
      $("#dashboard-profile-login-form").addEventListener("submit",async event=>{
        event.preventDefault();
        const form=event.currentTarget;
        const button=form.querySelector("button[type=submit]");
        const status=$("#profile-login-status");
        button.disabled=true;
        status.textContent="";
        try {
          const response=await fetch("/api/dashboard/profile-login",{
            method:"POST",
            headers:{"Content-Type":"application/json","Accept":"application/json"},
            credentials:"same-origin",
            body:JSON.stringify({code:$("#dashboard-profile-code").value.trim()})
          });
          const data=await response.json();
          if (!response.ok) throw new Error(data.error||"The one-time code could not be verified.");
          if (typeof data.token!=="string"||!data.token) throw new Error("Sign-in succeeded without a session token. Contact support.");
          localStorage.setItem("nx_cust_token",data.token);
          if (typeof data.username==="string") localStorage.setItem("nx_cust_name",data.username);
          location.reload();
        } catch(error) {
          status.textContent=error.message||"The one-time code could not be verified.";
          button.disabled=false;
        }
      });
      $("#account-create-form").addEventListener("submit",async event=>{
        event.preventDefault();
        const status=$("#account-create-status"),button=event.currentTarget.querySelector('button[type="submit"]');
        button.disabled=true;setNotice(status,"Creating account and preparing setup email…");
        try {
          const result=await api("/api/dashboard/accounts",{
            method:"POST",
            body:JSON.stringify({username:$("#account-create-username").value.trim(),email:$("#account-create-email").value.trim()})
          });
          event.currentTarget.reset();
          setNotice(status,result.message||"Account created.","success");
          await loadAccounts();
        } catch(error) {
          setNotice(status,error.message);
          await loadAccounts().catch(()=>{});
        } finally {
          button.disabled=false;
        }
      });
      $("#accounts-table").addEventListener("click",async event=>{
        const button=event.target.closest(".account-action");
        if (!button) return;
        const email=button.dataset.email,action=button.dataset.action;
        let reason="";
        if (action==="suspend") {
          if (!window.confirm(`Suspend the NexTech website account for ${email}?`)) return;
          reason=window.prompt("Enter the suspension reason:");
          if (!reason||!reason.trim()) return;
        } else if (!window.confirm(`${action==="recover"?"Restore access and send a password reset link to":"Restore"} ${email}?`)) return;
        button.disabled=true;
        try {
          const result=await api("/api/dashboard/accounts/action",{
            method:"POST",body:JSON.stringify({email,action,reason})
          });
          setNotice($("#accounts-table"),result.message||"Account updated.","success");
          await loadAccounts();
        } catch(error) {
          setNotice($("#accounts-table"),error.message);
          await loadAccounts().catch(()=>{});
        }
      });
      $("#accounts-table").addEventListener("submit",async event=>{
        const form=event.target.closest(".account-link-form");
        if (!form) return;
        event.preventDefault();
        const button=form.querySelector('button[type="submit"]');
        button.disabled=true;
        try {
          const formData=new FormData(form);
          const result=await api("/api/dashboard/accounts/action",{
            method:"POST",
            body:JSON.stringify({
              email:form.dataset.email,
              action:"link",
              discord_id:String(formData.get("discord_id")||"").trim(),
              roblox_id:String(formData.get("roblox_id")||"").trim()
            })
          });
          setNotice($("#accounts-table"),result.message||"Verified Discord and Roblox identities linked.","success");
          await loadAccounts();
        } catch(error) {
          setNotice($("#accounts-table"),error.message);
        } finally {
          button.disabled=false;
        }
      });
      $("#team-profile-form").addEventListener("submit",async event=>{
        event.preventDefault();
        const button=event.currentTarget.querySelector('button[type="submit"]');
        button.disabled=true;
        setNotice($("#team-profile-status"),"Checking the Discord member and active Roblox Hub link…");
        try {
          const result=await api("/api/dashboard/team",{
            method:"POST",
            body:JSON.stringify({
              discord_id:$("#team-discord-id").value.trim(),
              verification_mark:$("#team-verification-mark").value,
              title:$("#team-title").value.trim(),
              team_join_date:$("#team-join-date").value,
              bio:$("#team-bio").value.trim()
            })
          });
          setNotice($("#team-profile-status"),result.message||"Team profile saved.","success");
          await loadTeamProfiles();
        } catch(error) {
          setNotice($("#team-profile-status"),error.message);
        } finally {
          button.disabled=false;
        }
      });
      $("#team-profiles-table").addEventListener("click",async event=>{
        const editButton=event.target.closest(".team-edit");
        if (editButton) {
          $("#team-discord-id").value=editButton.dataset.discordId;
          $("#team-verification-mark").value=editButton.dataset.mark;
          $("#team-title").value=editButton.dataset.title;
          $("#team-bio").value=editButton.dataset.bio;
          $("#team-join-date").value=editButton.dataset.joined;
          $("#team-profile-form").scrollIntoView({behavior:"smooth",block:"center"});
          return;
        }
        const button=event.target.closest(".team-remove");
        if (!button||!window.confirm(`Remove Discord user ${button.dataset.discordId} from the curated team directory?`)) return;
        button.disabled=true;
        try {
          await api(`/api/dashboard/team/${encodeURIComponent(button.dataset.discordId)}`,{method:"DELETE"});
          await loadTeamProfiles();
        } catch(error) {
          setNotice($("#team-profiles-table"),error.message);
        } finally {
          button.disabled=false;
        }
      });
      $("#chat-send").addEventListener("click",sendChat);
      $("#chat-input").addEventListener("keydown",event=>{if(event.key==="Enter")sendChat();});
      $("#ticket-confirm").addEventListener("click",event=>{if(event.target.id==="confirm-ticket")createTicket();});
      $("#clear-chat").addEventListener("click",()=>{state.messages=[];state.intake=null;state.ready=false;$("#chat-messages").innerHTML='<div class="bubble assistant">Hi — tell me what you need help with, and I’ll collect the information NexTech staff need.</div>';$("#ticket-confirm").classList.add("hidden");});
      $("#report-form").addEventListener("submit",submitReport);
      $("#faq-list").addEventListener("click",async event=>{
        const button=event.target.closest(".faq-delete");
        if (!button||!window.confirm("Delete this help article?")) return;
        button.disabled=true;
        try { await api(`/api/dashboard/faq/${encodeURIComponent(button.dataset.id)}`,{method:"DELETE"}); await loadFaq(); }
        catch(error) { button.disabled=false; window.alert(error.message); }
      });
      $("#product-form").addEventListener("submit",async event=>{
        event.preventDefault();
        const message=$("#product-message"),button=$("#product-form button[type=submit]");
        button.disabled=true;setNotice(message,"Creating product…");
        try {
          const form=new FormData();
          form.set("name",$("#product-name").value.trim());
          form.set("category",$("#product-category").value.trim());
          form.set("description",$("#product-description").value.trim());
          form.set("currency",$("#product-currency").value);
          form.set("price",$("#product-price").value);
          const image=$("#product-image").files[0],productFile=$("#product-file").files[0];
          if(image)form.set("image",image);
          if(productFile)form.set("product_file",productFile);
          await api("/api/dashboard/products",{method:"POST",body:form});
          $("#product-form").reset();$("#product-category").value="General";updateProductPriceInput();
          setNotice(message,"Product published.","success");await loadProducts();
        } catch(error) { setNotice(message,error.message); }
        finally { button.disabled=false; }
      });
      function updateProductPriceInput() {
        const usd=$("#product-currency").value==="USD",input=$("#product-price");
        $("#product-price-label").textContent=usd?"Price (USD) — sold in USD":"Price (Robux)";
        input.step=usd?"0.01":"1";input.min=usd?"0.01":"1";input.max=usd?"100000":"100000000";
        input.placeholder=usd?"e.g. 4.99":"e.g. 400";
      }
      $("#product-currency").addEventListener("change",updateProductPriceInput);
      $("#faq-form").addEventListener("submit",async event=>{
        event.preventDefault();
        const message=$("#faq-message"),button=$("#faq-form button[type=submit]");
        button.disabled=true;setNotice(message,"Saving article…");
        try {
          await api("/api/dashboard/faq",{method:"POST",body:JSON.stringify({
            category:$("#faq-category").value.trim(),question:$("#faq-question").value.trim(),answer:$("#faq-answer").value.trim()
          })});
          $("#faq-form").reset();setNotice(message,"Article added.","success");await loadFaq();
        } catch(error) { setNotice(message,error.message); }
        finally { button.disabled=false; }
      });
      $("#logout-button").addEventListener("click",async()=>{
        const token=state.token;
        try { await fetch("/api/customer/logout",{method:"POST",headers:{"Content-Type":"application/json","X-Customer-Token":token},body:JSON.stringify({token})}); } catch (_) {}
        localStorage.removeItem("nx_cust_token");
        localStorage.removeItem("nx_cust_name");
        location.href="/login";
      });
      init();
    })();
  </script>
</body>
</html>"""


PUBLIC_PAGE_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#080d1b">
  <title>__TITLE__ · NexTech</title>
  <style>
    :root{color-scheme:dark;--bg:#0b0b0c;--panel:#151516;--panel2:#101011;--line:#29292c;--gold:#9bc8ff;--blue:#7aaeff;--text:#f2f4f8;--muted:#9da6b4;--green:#4ade80;--red:#ff7788}
    *{box-sizing:border-box}body{margin:0;min-height:100vh;background:var(--bg);color:var(--text);font:14px Inter,Segoe UI,system-ui,sans-serif}
    a{color:inherit;text-decoration:none}.site-header{position:sticky;top:0;z-index:40;border-bottom:1px solid #252528;background:#0b0b0cf2;backdrop-filter:blur(15px)}
    .header-inner{width:min(1420px,calc(100% - 36px));min-height:58px;margin:auto;display:flex;align-items:center;gap:24px}
    .brand{display:flex;align-items:center;gap:10px;white-space:nowrap;font-size:15px;font-weight:900}.brand-mark{width:28px;height:28px;display:grid;place-items:center;border-radius:8px;background:linear-gradient(145deg,#83b6ff,#477dd5);color:#fff}.brand-name span{color:var(--gold)}
    .nav{display:flex;align-items:center;gap:10px;margin-left:auto;color:#d9dde5;font-size:13px;font-weight:650;white-space:nowrap}.nav>a,.nav-dropdown summary{min-height:38px;display:flex;align-items:center;gap:8px;padding:0 10px;border-radius:8px;transition:background .15s,color .15s}.nav>a:hover,.nav-dropdown summary:hover,.nav-dropdown[open] summary,.nav-action:hover{color:#fff;background:#19191c}.nav-icon{width:16px;flex:none;color:#9bbfff;font-size:15px;text-align:center}
    .nav-dropdown{position:relative}.nav-dropdown summary{list-style:none;cursor:pointer;white-space:nowrap}.nav-dropdown summary::-webkit-details-marker{display:none}
    .mobile-menu-toggle{display:none;width:36px;height:36px;place-items:center;border:1px solid #303034;border-radius:8px;background:#151516;color:#e6e9ef;font-size:17px;cursor:pointer}
    .nav-menu{position:absolute;top:calc(100% + 10px);left:0;z-index:45;width:280px;max-height:calc(100vh - 125px);overflow:auto;padding:7px;border:1px solid #343438;border-radius:11px;background:#171719;box-shadow:0 18px 45px #000b}
    .nav-menu a{display:flex;align-items:flex-start;gap:11px;padding:10px;border-radius:8px;color:#dbe1ea;font-size:12px}.nav-menu a:hover{background:#242428}.menu-icon{width:19px;flex:none;color:#9aaec7}.menu-copy{display:grid;gap:3px}.menu-copy strong{font-size:12px}.menu-copy small{color:#9da6b4;font-size:11px;line-height:1.4}.soon-tag{color:#8c9ab7;font-size:9px;font-weight:700}
    .nav-auth-actions{display:flex;align-items:center;gap:9px;margin-left:16px}.nav-auth-actions[hidden],.nav-mobile-auth[hidden],.nav-tools>[hidden]{display:none!important}.nav-auth-link{display:inline-flex;align-items:center;justify-content:center;min-height:36px;padding:0 13px;border-radius:7px;color:#e6e9ef;font-size:12px;font-weight:750}.nav-auth-link:hover{background:#19191c}.nav-auth-link.join{border:1px solid #e5e7eb;background:#f4f4f5;color:#111}.nav-auth-link.join:hover{background:#dfe3e8}.nav-mobile-auth{display:none}.nav-tools{display:flex;align-items:center;gap:4px;margin-left:14px}.nav-action{position:relative;min-width:36px;height:36px;display:grid;place-items:center;border:0;border-radius:8px;background:transparent;color:#c5cbd4;font-size:18px;cursor:pointer}.nav-action:hover{background:#19191c}.nav-action.search-action{width:auto;display:flex;gap:8px;padding:0 10px;border:1px solid #303034;color:#9da6b4;font-size:12px}.nav-action.search-action kbd{padding:2px 5px;border:1px solid #36363a;border-radius:4px;font-size:9px}.nav-action.account-summary{display:flex;gap:9px;padding:0 7px;font-size:11px;font-weight:700}.account-avatar{width:28px;height:28px;display:grid;place-items:center;border-radius:50%;background:#477dd5;color:#fff;font-weight:900;overflow:hidden}.account-avatar img{width:100%;height:100%;object-fit:cover}.cart-count{position:absolute;top:0;right:0;min-width:15px;height:15px;padding:0 4px;display:grid;place-items:center;border-radius:9px;background:#7aaeff;color:#07101c;font-size:9px;font-weight:900}.account-dropdown .nav-menu{left:auto;right:0;width:235px}.account-menu-heading{padding:10px;border-bottom:1px solid var(--line);margin-bottom:5px;font-weight:800}.account-menu-heading small{display:block;margin-top:3px;color:var(--muted);font-weight:400}.account-signout{width:100%;padding:10px;border:0;border-radius:7px;background:transparent;color:#e6e9ef;text-align:left;font-size:12px;cursor:pointer}.account-signout:hover{background:#242428}.nav-tools .nav-dropdown summary{padding:4px}
    .search-overlay{position:fixed;inset:0;z-index:80;display:none;align-items:flex-start;justify-content:center;padding:10vh 16px 24px;background:#000b}.search-overlay.open{display:flex}.search-dialog{width:min(820px,100%);max-height:80vh;overflow:auto;border:1px solid #343438;border-radius:10px;background:#141416;box-shadow:0 24px 80px #000b}.search-top{display:flex;align-items:center;gap:12px;padding:13px;border-bottom:1px solid var(--line)}.search-top input{flex:1;min-width:0;border:0;outline:0;background:transparent;color:var(--text);font-size:15px}.search-close{border:0;background:transparent;color:var(--muted);font-size:20px;cursor:pointer}.search-categories{display:flex;align-items:center;gap:7px;flex-wrap:wrap;padding:12px;border-bottom:1px solid var(--line);color:var(--muted);font-size:12px}.search-categories button{padding:7px 10px;border:1px solid transparent;border-radius:7px;background:#202023;color:#e6e9ef;font-size:11px;cursor:pointer}.search-categories button.active{border-color:#466ba2;background:#1b2940}.search-results{min-height:130px;padding:14px;color:var(--muted)}.search-result{display:flex;justify-content:space-between;padding:10px;border-radius:7px}.search-result:hover{background:#222225}.search-result small{color:var(--muted)}
    .notification-count{position:absolute;top:1px;right:0;min-width:15px;height:15px;padding:0 4px;display:grid;place-items:center;border-radius:9px;background:#e78966;color:#140b08;font-size:9px;font-weight:900}.notification-panel,.live-chat-panel{position:fixed;z-index:70;overflow:hidden;border:1px solid #36363b;border-radius:11px;background:#151516;box-shadow:0 18px 55px #000b}.notification-panel{top:68px;right:76px;width:min(370px,calc(100vw - 18px));max-height:min(560px,calc(100vh - 86px));display:flex;flex-direction:column}.notification-panel[hidden],.live-chat-panel[hidden]{display:none}.widget-header{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:13px 15px;border-bottom:1px solid var(--line)}.widget-header h2{margin:0;font-size:14px}.widget-header button{width:28px;height:28px;border:0;border-radius:6px;background:transparent;color:var(--muted);font-size:19px;cursor:pointer}.widget-header button:hover{background:#252528}.notification-list{overflow-y:auto;padding:7px}.notification-item{display:block;padding:11px;border-radius:8px}.notification-item:hover{background:#222225}.notification-item.unread{border-left:2px solid #83b6ff;background:#1b1d20}.notification-item strong{display:block;margin-bottom:4px;font-size:12px}.notification-item p{margin:0;color:#a8adb6;font-size:11px;line-height:1.5}.notification-item time{display:block;margin-top:5px;color:#7e858f;font-size:9px}.notification-empty{padding:20px 13px;color:var(--muted);font-size:12px;line-height:1.6;text-align:center}.notification-footer{padding:9px;border-top:1px solid var(--line)}.notification-footer button,.widget-login{width:100%;padding:9px;border:1px solid #3c526f;border-radius:7px;background:#202b39;color:#dce8f9;font-size:11px;font-weight:750;cursor:pointer}.notification-footer button:disabled{opacity:.45;cursor:default}.live-chat-launcher{position:fixed;right:20px;bottom:19px;z-index:65;display:flex;align-items:center;gap:9px;padding:12px 15px;border:1px solid #4774a4;border-radius:99px;background:#1b2c40;color:#eaf3ff;font-size:12px;font-weight:800;box-shadow:0 8px 28px #0008;cursor:pointer}.live-chat-launcher:hover{background:#243c57}.live-dot{width:8px;height:8px;border-radius:50%;background:#55dc96;box-shadow:0 0 0 4px #55dc9622}.live-chat-panel{right:20px;bottom:78px;width:min(390px,calc(100vw - 24px));height:min(540px,calc(100vh - 115px));display:flex;flex-direction:column}.live-chat-heading{min-width:0}.live-chat-heading span{display:block;margin-top:4px;color:var(--muted);font-size:10px}.live-chat-messages{flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:10px;padding:12px;background:#101011}.live-chat-message{display:flex;align-items:flex-start;gap:9px}.live-chat-avatar{width:29px;height:29px;flex:none;border-radius:50%;background:#25272a;object-fit:cover}.live-chat-copy{min-width:0;flex:1;padding:8px 10px;border:1px solid #303034;border-radius:9px;background:#19191b}.live-chat-meta{display:flex;align-items:center;gap:7px;margin-bottom:4px}.live-chat-meta strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:10px}.live-chat-meta span,.live-chat-meta time{color:#8e949e;font-size:9px}.live-chat-copy p{margin:0;color:#dfe3e9;font-size:11px;line-height:1.5;white-space:pre-wrap;overflow-wrap:anywhere}.live-chat-status{padding:9px 12px;border-top:1px solid var(--line);color:#898f98;font-size:9px;text-align:center}
    .member-directory-tools{display:flex;align-items:center;gap:10px;margin:0 0 14px}.member-directory-tools input{max-width:440px}.member-directory-tools span{color:var(--muted);font-size:11px}.member-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:11px}.member-card{display:flex;align-items:center;gap:11px;min-width:0;padding:13px;border:1px solid var(--line);border-radius:9px;background:#151516}.member-card:hover{border-color:#4b6584;background:#1a1a1d}.member-photo{position:relative;width:43px;height:43px;flex:none;border-radius:50%;background:#26282c;object-fit:cover}.member-online-dot{position:absolute;right:0;bottom:1px;width:11px;height:11px;border:2px solid #151516;border-radius:50%;background:#555b63}.member-online-dot.online{background:#4ade80}.member-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;font-weight:750}.member-status{margin-top:4px;color:var(--muted);font-size:10px}.member-profile-card{display:flex;align-items:center;gap:18px;padding:23px;border:1px solid var(--line);border-radius:10px;background:#151516}.member-profile-avatar{width:88px;height:88px;border-radius:18px;background:#26282c;object-fit:cover}.member-profile-card h1{margin:0 0 7px;font-size:28px}.member-profile-card p{margin:0;color:var(--muted);font-size:12px}.member-profile-status{display:inline-flex;align-items:center;gap:7px}.member-profile-status::before{content:"";width:8px;height:8px;border-radius:50%;background:#555b63}.member-profile-status.online::before{background:#4ade80}
    .main{width:min(1240px,calc(100% - 48px));margin:0 auto;padding:42px 0 80px}.hero{margin-bottom:27px}.eyebrow{color:var(--gold);font-size:10px;font-weight:850;letter-spacing:.13em;text-transform:uppercase}.hero h1{margin:9px 0 11px;font-size:clamp(31px,5vw,45px);letter-spacing:-.04em}.hero p{max-width:720px;margin:0;color:var(--muted);font-size:13px;line-height:1.7}
    .home-hero{position:relative;min-height:380px;display:flex;align-items:center;overflow:hidden;margin:0 0 34px;padding:clamp(26px,6vw,68px);border:1px solid #303034;border-radius:18px;background:radial-gradient(ellipse at 77% 32%,#203047 0,transparent 38%),linear-gradient(120deg,#151516,#101011 64%,#17191d)}.home-hero:after{position:absolute;right:9%;top:12%;width:250px;height:250px;border:1px solid #a9cfff36;border-radius:50%;box-shadow:0 0 0 24px #a9cfff09,0 0 0 55px #a9cfff05;content:"";pointer-events:none}.home-copy{position:relative;z-index:1;max-width:700px}.home-active{display:inline-flex;align-items:center;gap:8px;margin-bottom:18px;padding:6px 10px;border:1px solid #345d49;border-radius:99px;background:#15241b;color:#a6e5bd;font-size:10px;font-weight:800}.home-active .live-dot{width:7px;height:7px}.home-active.loading{border-color:#3b4149;background:#181a1d;color:#c1cad4}.home-active.loading .live-dot{background:#91a0b2;box-shadow:none}.home-active.unavailable{border-color:#5a4141;background:#241819;color:#ffc1c1}.home-active.unavailable .live-dot{background:#ff7788;box-shadow:0 0 0 4px #ff778822}.home-orbit{position:absolute;right:12%;top:50%;width:205px;height:205px;display:grid;place-items:center;transform:translateY(-50%);border:1px solid #90bdfb55;border-radius:50%;box-shadow:0 0 0 25px #90bdfb0b,0 0 0 58px #90bdfb08}.home-orbit:before,.home-orbit:after{position:absolute;inset:19px;border:1px solid #90bdfb30;border-radius:50%;content:""}.home-orbit:after{inset:42px}.home-orbit span{z-index:1;width:82px;height:82px;display:grid;place-items:center;border:1px solid #aacdfb80;border-radius:25px;background:linear-gradient(145deg,#28394d,#171b20);color:#e9f3ff;font-size:42px;font-weight:900;box-shadow:0 12px 35px #0008}.home-orbit i{position:absolute;width:9px;height:9px;border-radius:50%;background:#9bc8ff;box-shadow:0 0 16px #9bc8ff}.home-orbit i:nth-of-type(1){top:25px;left:55px}.home-orbit i:nth-of-type(2){right:14px;top:100px;background:#7ce0ac}.home-orbit i:nth-of-type(3){left:47px;bottom:14px;background:#f1bd6b}.home-latest{margin:32px 0}.home-latest-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}.home-latest-heading h2{margin:0;font-size:17px}.home-latest-heading>a,.home-community-stats>a{color:#a9cbff;font-size:10px;font-weight:750}.home-latest-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:11px}.home-latest-card{min-height:130px;padding:17px;border:1px solid #2d2d31;border-radius:10px;background:#141415}.home-latest-card:hover{border-color:#536174}.home-latest-card h3{margin:10px 0 5px;font-size:14px}.home-latest-card p{margin:0;color:var(--muted);font-size:10px;line-height:1.6}.home-community-stats{display:flex;align-items:center;gap:0;margin:30px 0;padding:18px 20px;border:1px solid #2d2d31;border-radius:10px;background:#141415}.home-community-stats article{min-width:170px;padding:0 20px;border-right:1px solid #303034}.home-community-stats article:first-child{padding-left:0}.home-community-stats article span{display:block;color:var(--muted);font-size:10px}.home-community-stats article strong{display:block;margin-top:5px;font-size:21px}.home-community-stats>a{margin-left:auto}    .home-sections{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:13px}.home-section{min-height:175px;padding:21px;border:1px solid #2d2d31;border-radius:11px;background:linear-gradient(145deg,#171719,#111112);transition:transform .15s,border-color .15s}.home-section:hover{transform:translateY(-2px);border-color:#536174}.home-section-label{color:#9aaec7;font-size:9px;font-weight:850;letter-spacing:.13em;text-transform:uppercase}.home-section h2{margin:12px 0 7px;font-size:18px;letter-spacing:-.02em}.home-section p{margin:0;color:var(--muted);font-size:11px;line-height:1.65}.home-section-arrow{display:block;margin-top:17px;color:#bad5fa;font-size:11px;font-weight:800}.auth-layout{max-width:480px;margin:26px auto 38px}.auth-brand{display:flex;align-items:center;gap:12px;margin:0 0 28px;font-size:14px;font-weight:800}.auth-brand .brand-mark{width:37px;height:37px}.auth-card{padding:clamp(22px,5vw,36px);border:1px solid #303034;border-radius:13px;background:#151516}.auth-card h1{margin:0 0 8px;font-size:30px;letter-spacing:-.04em}.auth-card>p{margin:0 0 23px;color:var(--muted);font-size:12px;line-height:1.6}.auth-option{display:flex;width:100%;justify-content:center;align-items:center;gap:10px;margin:9px 0;padding:13px;border:1px solid #3b3b40;border-radius:8px;background:#1b1b1e;font-size:12px;font-weight:800}.auth-option.primary{border-color:#5277a8;background:#29415f}.auth-provider-mark{width:25px;height:25px;display:grid;place-items:center;border-radius:6px;background:#111;color:#fff;font-size:12px;font-weight:900}.auth-option.primary .auth-provider-mark{background:#3a5b82}.auth-divider{display:flex;align-items:center;gap:12px;margin:18px 0;color:var(--muted);font-size:10px}.auth-divider:before,.auth-divider:after{height:1px;flex:1;background:var(--line);content:""}.auth-note{margin:16px 0 0;padding-top:15px;border-top:1px solid var(--line);color:var(--muted);font-size:10px;line-height:1.65}.report-form{display:grid;gap:12px;max-width:740px;padding:20px}.report-form label{display:grid;gap:7px;color:#dce1e9;font-size:11px;font-weight:750}.report-form input,.report-form textarea,.report-form select{width:100%;padding:11px;border:1px solid #35353a;border-radius:7px;background:#101011;color:var(--text);font:inherit}.report-form textarea{min-height:145px;resize:vertical}.report-form [role=status]{min-height:18px;color:var(--muted);font-size:11px;line-height:1.5}.report-form small{color:var(--muted);font-size:10px;line-height:1.6}.report-form button:disabled{opacity:.55;cursor:wait}.integration-card{display:flex;align-items:center;justify-content:space-between;gap:15px;padding:14px 0;border-top:1px solid var(--line)}.integration-card strong{display:block;font-size:12px}.integration-card span{display:block;margin-top:4px;color:var(--muted);font-size:10px}.integration-card a{flex:none}
    .profile-breadcrumb{display:flex;align-items:center;gap:8px;margin:0 0 16px;color:var(--muted);font-size:11px}.profile-breadcrumb a{color:#a9cbff}.public-profile-heading{display:flex;align-items:center;gap:17px;padding:22px}.public-profile-avatar{width:72px;height:72px;flex:none;display:grid;place-items:center;border:1px solid #46628a;border-radius:20px;background:linear-gradient(145deg,#2a3d58,#171719);color:#e8f2ff;font-size:30px;font-weight:850}.public-profile-title{min-width:0}.public-profile-title h1{margin:0;font-size:25px;overflow-wrap:anywhere}.public-profile-title p{margin:4px 0 8px;color:var(--muted);font-size:11px}.public-profile-title>a{color:#a9cbff;font-size:10px;font-weight:750}.profile-tabs{display:flex;gap:6px;overflow:auto;margin:14px 0;padding-bottom:3px;border-bottom:1px solid var(--line)}.profile-tab{flex:none;padding:10px 13px;border-bottom:2px solid transparent;color:var(--muted);font-size:11px;font-weight:750}.profile-tab:hover,.profile-tab.active{border-color:#82b6ff;color:#f2f6ff}.public-profile-panel{min-height:180px;padding:22px}.public-profile-panel h2{margin:0 0 8px;font-size:16px}.public-profile-panel p{margin:0;color:var(--muted);font-size:11px;line-height:1.65}
    .panel,.feature-card,.stat-card{border:1px solid var(--line);border-radius:10px;background:var(--panel);box-shadow:0 18px 48px #0002}
    .community-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:13px}.feature-card{min-height:150px;padding:19px;transition:transform .15s,border-color .15s}.feature-card:hover{transform:translateY(-2px);border-color:#526898}.feature-icon{font-size:21px}.feature-card h2{margin:14px 0 6px;font-size:15px}.feature-card p{margin:0;color:var(--muted);font-size:11px;line-height:1.6}.feature-card .soon-tag{display:inline-block;margin-top:10px;padding:4px 7px;border:1px solid #39415a;border-radius:99px}
    .panel{padding:17px}.record-tabs{display:flex;gap:7px;flex-wrap:wrap;padding-bottom:14px;border-bottom:1px solid var(--line)}
    .record-tab{display:flex;align-items:center;gap:8px;padding:9px 12px;border:1px solid transparent;border-radius:8px;background:#111827;color:#cbd5e1;font-size:11px;font-weight:800;cursor:pointer}
    .record-tab:hover,.record-tab.active{border-color:#405079;background:#1b2946;color:#fff}.record-count{color:#aab7d0;font-size:10px}
    .record-search{display:flex;gap:8px;margin:14px 0}.record-search input,.wiki-search{width:100%;min-height:40px;padding:10px 12px;border:1px solid #34415a;border-radius:8px;outline:0;background:#0a1020;color:#f3f6ff;font:inherit;font-size:12px}.record-search input:focus,.wiki-search:focus{border-color:#7188ff;box-shadow:0 0 0 3px #7188ff22}
    .record-search button,.refresh-button{flex:none;padding:0 14px;border:1px solid #4b61a5;border-radius:8px;background:#293e79;color:#fff;font-weight:800;cursor:pointer}.records-list{display:grid;gap:10px}
    .record-card{padding:15px;border:1px solid #293650;border-radius:11px;background:#0c1425}.record-top{display:flex;align-items:center;gap:11px}.record-avatar{width:39px;height:39px;flex:none;display:grid;place-items:center;border:1px solid #3a4b70;border-radius:11px;background:#1b2b4d;color:#d8e2ff;font-size:13px;font-weight:900}.record-person{min-width:0;flex:1}.record-name{font-size:13px;font-weight:850}.record-id{margin-top:3px;color:#8796b4;font-size:10px}.record-badge{padding:5px 8px;border:1px solid #70404a;border-radius:99px;background:#301c27;color:#ffbdc4;font-size:9px;font-weight:850;white-space:nowrap}.record-badge.warning{border-color:#715326;background:#302713;color:#ffdc92}.record-badge.community{border-color:#355884;background:#172a46;color:#b9d7ff}
    .record-case{margin:13px 0 7px;color:#b8c5dd;font-size:10px}.record-reason{color:#eef2fa;font-size:12px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere}.record-meta{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:13px;padding-top:10px;border-top:1px solid #26334c;color:#909db5;font-size:10px}.record-meta strong{color:#dce5f8}.record-profile{margin-left:auto;color:#c3d2ff;font-weight:750}.records-empty{padding:36px 18px;color:#a8b4ca;text-align:center;font-size:12px;line-height:1.6}
    .stats-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0}.stat-card{padding:16px}.stat-label{color:#91a0bb;font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.07em}.stat-value{margin-top:10px;font-size:25px;font-weight:900;letter-spacing:-.03em}.stat-note{margin-top:5px;color:#8997b0;font-size:10px}
    .status-pill{display:inline-flex;align-items:center;gap:7px;padding:6px 10px;border:1px solid #28543a;border-radius:99px;background:#102719;color:#87e5a7;font-size:10px;font-weight:850}.status-pill.offline{border-color:#663743;background:#2d1820;color:#ffabb5}.status-dot{width:7px;height:7px;border-radius:50%;background:var(--green)}.status-pill.offline .status-dot{background:var(--red)}
    .section-title{margin:25px 0 11px;font-size:15px}.command-search{margin:10px 0}.command-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.command-card,.wiki-item{padding:12px;border:1px solid #293650;border-radius:9px;background:#0c1425}.command-name{color:#d9e4ff;font:700 11px ui-monospace,SFMono-Regular,monospace}.command-desc,.wiki-answer{margin-top:5px;color:#96a3ba;font-size:10px;line-height:1.55}.wiki-list{display:grid;gap:9px;margin-top:12px}.wiki-question{font-size:12px;font-weight:800}.wiki-category{color:#f3c466;font-size:9px;font-weight:800;letter-spacing:.08em;text-transform:uppercase}
    .cta-row{display:flex;gap:10px;flex-wrap:wrap;margin-top:17px}.cta{display:inline-flex;align-items:center;justify-content:center;padding:10px 13px;border:1px solid #374565;border-radius:8px;background:#111a30;color:#e4ebfc;font-size:11px;font-weight:800}.cta.primary{border-color:#435fa6;background:#293e79}
    .sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}.support-chat{max-width:760px;margin:0 auto;padding:0;overflow:hidden}.support-chat-heading{display:flex;align-items:center;gap:12px;padding:16px 18px;border-bottom:1px solid var(--line)}.support-avatar{width:40px;height:40px;display:grid;place-items:center;border-radius:12px;background:linear-gradient(145deg,#7188ff,#375fe7);font-size:18px;font-weight:900}.support-chat-heading h2{margin:0;font-size:14px}.support-chat-heading p{margin:4px 0 0;color:var(--muted);font-size:10px}.support-online{display:flex;align-items:center;gap:7px;margin-left:auto;color:#87e5a7;font-size:10px;font-weight:800}.support-online .status-dot{width:7px;height:7px}.support-messages{height:340px;overflow-y:auto;display:flex;flex-direction:column;gap:10px;padding:17px;background:#0a1020}.chat-message{max-width:85%;padding:10px 12px;border:1px solid #293650;border-radius:12px;background:#111a30}.chat-message strong{font-size:10px}.chat-message p{margin:5px 0 0;color:#dce5f8;font-size:12px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere}.bot-message{align-self:flex-start}.user-message{align-self:flex-end;border-color:#3c5594;background:#1a2c52}.support-suggestions{display:flex;gap:7px;flex-wrap:wrap;padding:12px 15px 0}.support-suggestion{padding:7px 9px;border:1px solid #374565;border-radius:99px;background:#111a30;color:#dce5ff;font-size:10px;cursor:pointer}.support-suggestion:hover{border-color:#7188ff}.support-compose{display:flex;gap:8px;padding:12px 15px}.support-compose input{min-width:0;flex:1;padding:11px 12px;border:1px solid #34415a;border-radius:8px;background:#0a1020;color:var(--text);font:inherit;font-size:12px}.support-compose input:focus{outline:2px solid #7188ff}.support-compose button{padding:0 16px;border:1px solid #4b61a5;border-radius:8px;background:#293e79;color:#fff;font-weight:800;cursor:pointer}.support-compose button:disabled{opacity:.55;cursor:wait}.support-disclaimer{margin:0;padding:0 15px 15px;color:#96a3ba;font-size:10px;line-height:1.6}.support-disclaimer a{color:#c3d2ff;text-decoration:underline}
    .soon-panel{max-width:720px;padding:30px;text-align:center}.soon-icon{font-size:34px}.soon-panel h2{margin:12px 0 8px;font-size:23px}.soon-panel p{max-width:520px;margin:0 auto;color:var(--muted);font-size:12px;line-height:1.7}
    .record-tab{border-color:#303034;background:#171719;color:#cbd0d9}.record-tab:hover,.record-tab.active{border-color:#456289;background:#202a39;color:#fff}.record-search input,.wiki-search,.support-compose input{border-color:#35353a;background:#101011;color:var(--text)}.record-search input:focus,.wiki-search:focus{border-color:#7aaeff;box-shadow:0 0 0 3px #7aaeff22}.record-search button,.refresh-button,.support-compose button{border-color:#5277a8;background:#29415f}.record-card,.command-card,.wiki-item{border-color:#303034;background:#151516}.record-avatar{border-color:#35353a;background:#202023;color:#cdd6e5}.record-id,.record-meta,.command-desc,.wiki-answer{color:#a0a6af}.record-meta{border-color:#303034}.record-reason,.chat-message p{color:#e0e3e9}.record-badge.community{border-color:#3e5c7e;background:#1a2634;color:#bfdcff}.cta{border-color:#3b3b40;background:#1a1a1d;color:#e4e8ef}.cta.primary{border-color:#466b99;background:#29415f}.feature-card:hover{border-color:#526779}.support-avatar{background:linear-gradient(145deg,#83b6ff,#477dd5)}.support-messages{background:#101011}.chat-message,.support-suggestion{border-color:#35353a;background:#19191c}.user-message{border-color:#3e5c7e;background:#202a39}.support-suggestion{color:#dce1e9}.support-disclaimer{color:#a2a7b0}.support-disclaimer a{color:#a9cbff}.site-footer{border-color:#29292c}
    .site-footer{border-top:1px solid #29292c;padding:32px 20px 20px;color:#9da6b4;font-size:11px}.footer-inner{width:min(1180px,100%);margin:auto}.footer-catalogs{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:24px;padding-bottom:26px}.footer-catalog h2{margin:0 0 11px;color:#eef2f7;font-size:12px}.footer-catalog a{display:block;width:max-content;max-width:100%;padding:5px 0;color:#a6adb8;font-size:11px;line-height:1.4}.footer-catalog a:hover{color:#c5ddff;text-decoration:underline}.footer-bottom{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;padding-top:15px;border-top:1px solid #29292c;color:#858c96;font-size:10px}.footer-bottom-links{display:flex;gap:14px;flex-wrap:wrap}.footer-bottom a{color:#a6adb8}.footer-bottom a:hover{color:#c5ddff}
    .settings-layout{display:grid;grid-template-columns:250px minmax(0,1fr);gap:48px;align-items:start}.settings-sidebar{position:sticky;top:84px}.settings-sidebar-title{font-family:Impact,"Arial Narrow",sans-serif;font-size:25px;text-transform:uppercase}.settings-sidebar>p{margin:7px 0 18px;color:var(--muted);font-size:11px}.settings-group{margin:17px 0}.settings-group h3{margin:0 0 7px;padding:0 9px;color:#9fbde3;font-size:9px;font-weight:800;letter-spacing:.05em;text-transform:uppercase}.settings-link{display:block;margin:2px 0;padding:8px 10px;border-radius:6px;color:#c5d3e7;font-size:11px}.settings-link:hover,.settings-link.active{background:#1a2438;color:#a9cbff}.settings-heading{margin-bottom:18px}.settings-heading h1{margin:6px 0;font-size:27px}.settings-heading p,.settings-card p{color:var(--muted);font-size:12px;line-height:1.65}.settings-card{max-width:780px;min-height:230px;padding:23px;border:1px solid var(--line);border-radius:9px;background:#141415}.settings-card h2{margin:9px 0;font-size:20px}.settings-card-eyebrow{color:#9fbde3;font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.1em}.settings-card a,.profile-activity a,.settings-card p a{color:#a9cbff;text-decoration:underline}.settings-group+.settings-group{padding-top:4px}.settings-mobile-title{display:none}.account-summary{display:grid;gap:0;margin-top:17px}.account-summary-row{display:flex;justify-content:space-between;gap:12px;padding:12px 0;border-top:1px solid var(--line);font-size:12px}.account-summary-row span{color:var(--muted)}.account-summary-row strong{overflow-wrap:anywhere;text-align:right}.settings-pref{display:grid;gap:9px;max-width:300px;margin-top:20px;font-size:12px;font-weight:700}.settings-note{font-size:10px!important}
    .profile-banner{overflow:hidden;padding:0}.profile-cover{height:138px;background:linear-gradient(135deg,#433153,#17141e 68%,#141415)}.profile-identity{display:flex;align-items:center;gap:14px;padding:0 24px 15px;margin-top:-40px}.profile-avatar{width:94px;height:94px;display:grid;place-items:center;flex:none;border:4px solid #151516;border-radius:20px;background:#895f36;color:#ffe6a6;font-size:34px;font-weight:700;overflow:hidden}.profile-avatar img{width:100%;height:100%;object-fit:cover}.profile-identity>div:nth-child(2){flex:1;padding-top:42px}.profile-identity h2{margin:0;font-size:24px}.profile-identity p{margin:4px 0;color:var(--muted);font-size:11px}.profile-identity .cta{margin-top:40px}.profile-stats{display:flex;gap:20px;padding:14px 24px;border-top:1px solid var(--line);color:#a9cbff;font-size:11px}.profile-columns{display:grid;grid-template-columns:2fr 1fr;gap:20px;margin-top:20px}.profile-columns .panel{padding:20px}.profile-columns h2{margin:0 0 12px;font-size:14px}.profile-columns p{color:var(--muted);font-size:12px;line-height:1.6}
    .inbox-heading{display:flex;justify-content:space-between;align-items:center;gap:14px;margin-bottom:20px}.inbox-heading h1{margin:5px 0;font-size:26px}.inbox-heading p,.shop-filters>p{margin:0;color:var(--muted);font-size:12px}.inbox-layout{display:grid;grid-template-columns:220px minmax(0,1fr);gap:16px;min-height:440px}.inbox-folders{height:max-content;padding:9px}.inbox-folder{display:block;padding:10px;border-radius:6px;color:#cbd5e1;font-size:12px}.inbox-folder:hover,.inbox-folder.active{background:#1a2438;color:#a9cbff}.inbox-panel{padding:0;overflow:hidden}.inbox-title{display:flex;align-items:center;gap:9px;padding:15px;border-bottom:1px solid var(--line)}.inbox-title h2{margin:0;font-size:14px}.inbox-title span{color:var(--muted);font-size:11px}.inbox-tools{display:flex;gap:10px;padding:12px;border-bottom:1px solid var(--line)}.inbox-tools input{flex:1}.inbox-tools select{width:145px}.inbox-empty{display:grid;justify-items:center;align-content:center;gap:9px;min-height:280px;padding:24px;text-align:center;color:var(--muted);font-size:12px}.inbox-empty strong{color:var(--text);font-size:15px}.inbox-message{display:flex;align-items:center;gap:12px;padding:13px;border-bottom:1px solid var(--line)}.inbox-message-icon{width:30px;height:30px;display:grid;place-items:center;border:1px solid var(--line);border-radius:50%;color:#a9cbff}.inbox-message-body{display:grid;flex:1;gap:4px;min-width:0;font-size:12px}.inbox-message-body span{color:var(--muted);font-size:10px}.inbox-status{color:#a9cbff;font-size:10px}
    .shop-layout{display:grid;grid-template-columns:280px minmax(0,1fr);gap:25px;align-items:start}.shop-filters{padding:18px}.shop-filters h2,.shop-results-head h2{margin:0;font-size:16px}.shop-filters>p{margin:5px 0 18px}.shop-filters .field{display:grid;gap:8px;padding:16px 0;border-top:1px solid var(--line);color:#d6dce6;font-size:12px}.shop-filters .wiki-search{min-height:38px}.price-fields{display:grid;grid-template-columns:1fr 1fr;gap:9px}.shop-results-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:13px}.shop-results-head span,.product-count{color:var(--muted);font-size:11px}.product-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:13px}.product-card{overflow:hidden;border:1px solid var(--line);border-radius:9px;background:#141415}.product-image{height:165px;display:grid;place-items:center;background:#202023;overflow:hidden}.product-image img{width:100%;height:100%;object-fit:cover}.product-placeholder{color:#8199bb;font-size:40px;font-weight:900}.product-card-content{padding:14px}.product-category{color:#9fbde3;font-size:9px;font-weight:800;text-transform:uppercase}.product-card h3{margin:7px 0;font-size:14px}.product-card p{min-height:42px;margin:0;color:var(--muted);font-size:11px;line-height:1.55}.product-card-footer{display:flex;justify-content:space-between;align-items:center;gap:7px;margin-top:13px}.product-card-footer strong{font-size:11px}
    .auth-form{display:grid;gap:8px;margin:18px 0 13px}.auth-form>label,.auth-password-label label{color:#dce1e9;font-size:11px;font-weight:750}.auth-password-label{display:flex;align-items:center;justify-content:space-between;gap:10px}.auth-password-label a{color:#a9cbff;font-size:10px}.auth-form input{width:100%;min-height:42px;padding:10px 12px;border:1px solid #35353a;border-radius:7px;outline:0;background:#101011;color:var(--text);font:inherit;font-size:12px}.auth-form input:focus{border-color:#7188ff;box-shadow:0 0 0 3px #7188ff22}.auth-form-status{min-height:17px;color:#ffb3b8;font-size:11px;line-height:1.5}.auth-submit{width:100%;min-height:42px;border:1px solid #5277a8;border-radius:8px;background:#29415f;color:#fff;font-size:12px;font-weight:800;cursor:pointer}.auth-submit:hover{background:#345477}.auth-submit:disabled{opacity:.6;cursor:wait}
    .home-hero{display:grid;grid-template-columns:minmax(0,.82fr) minmax(0,1.18fr);align-items:center;gap:clamp(28px,5vw,72px);min-height:510px;padding:clamp(30px,4vw,58px);border:0;border-radius:0;background:#0b0b0c}.home-hero:after{display:none}.home-copy{max-width:560px}.home-active{margin-bottom:22px;border:0;background:transparent;padding:0;color:#aeb5c0;font-size:10px;letter-spacing:.13em}.home-active .live-dot{width:7px;height:7px;background:#62d991;box-shadow:0 0 12px #62d99199}.home-hero h1{max-width:550px;margin:0 0 17px;font-size:clamp(54px,7vw,88px);line-height:.97;letter-spacing:-.065em}.home-hero p{max-width:490px;margin:0;color:#a9aeb8;font-size:16px;line-height:1.75}.home-hero .cta-row{gap:12px;margin-top:25px}.home-join-cta{min-height:42px;padding:0 21px;background:#477cf5;border-color:#477cf5}.home-join-cta:hover{background:#3769dd;border-color:#3769dd}.home-feature-art{position:relative;min-height:390px;overflow:hidden;display:grid;place-items:center;border:1px solid #27272a;border-radius:7px;background:radial-gradient(ellipse at 50% 47%,#334766 0,transparent 36%),linear-gradient(135deg,#1a2230,#121316 52%,#20201f);isolation:isolate}.home-feature-art:before,.home-feature-art:after{position:absolute;inset:8%;border:1px solid #a8c9ff18;border-radius:50%;content:"";transform:rotate(-23deg) scaleY(.55)}.home-feature-art:after{inset:16%;transform:rotate(31deg) scaleY(.56);border-color:#a8c9ff20}.home-art-glow{position:absolute;width:45%;aspect-ratio:1;border-radius:50%;background:#6395ee26;filter:blur(50px)}.home-art-orbit{position:absolute;width:57%;aspect-ratio:1;border:1px solid #aecfff25;border-radius:50%;transform:rotateX(64deg) rotate(-28deg)}.home-art-orbit-one{width:74%;transform:rotateX(67deg) rotate(29deg)}.home-art-mark{position:relative;z-index:1;width:clamp(120px,15vw,190px);aspect-ratio:1;display:grid;place-items:center;border:1px solid #b5d2ff66;border-radius:34%;background:linear-gradient(145deg,#29416a,#141b29 74%);color:#ecf4ff;font-size:clamp(76px,10vw,130px);font-weight:950;letter-spacing:-.09em;text-shadow:0 5px 30px #8cbaff77;box-shadow:0 24px 90px #0009,0 0 80px #6395ee20;transform:rotate(-8deg)}.home-art-caption{position:absolute;right:20px;bottom:18px;left:20px;display:grid;gap:4px;padding:13px 15px;border:1px solid #ffffff18;border-radius:7px;background:#0c0d10c9;backdrop-filter:blur(10px)}.home-art-caption span,.home-art-caption small{color:#a9b4c5;font-size:9px;letter-spacing:.14em}.home-art-caption strong{font-size:16px;letter-spacing:.04em}.home-latest{margin:0;padding:42px 0 36px;border-top:1px solid #242427}.home-latest-heading h2{font-size:18px}.home-latest-grid{grid-template-columns:1fr;gap:0;border:1px solid #29292c;border-radius:7px}.home-latest-card{min-height:91px;display:grid;grid-template-columns:150px minmax(0,1fr);grid-template-rows:auto auto;align-content:center;column-gap:18px;padding:16px 20px;border:0;border-bottom:1px solid #29292c;border-radius:0;background:transparent}.home-latest-card:last-child{border-bottom:0}.home-latest-card:hover{background:#111113}.home-latest-card .home-section-label{grid-column:1;grid-row:1 / span 2;align-self:center}.home-latest-card h3{grid-column:2;grid-row:1;margin:0 0 5px;font-size:14px}.home-latest-card p{grid-column:2;grid-row:2;font-size:11px}.home-community-stats{margin-top:0;border-top:0;border-radius:0}.home-sections{padding-top:24px}
    @media(max-width:900px){.home-hero{grid-template-columns:minmax(0,1fr) minmax(0,1fr);min-height:440px;gap:28px;padding:28px 10px}.home-feature-art{min-height:330px}.home-hero h1{font-size:clamp(48px,7vw,70px)}.nav-auth-actions{margin-left:7px}.nav-auth-link{padding:0 10px}}
    @media(max-width:760px){.home-hero{grid-template-columns:1fr;gap:20px;min-height:0;padding:38px 10px 26px}.home-hero h1{font-size:clamp(46px,12vw,66px)}.home-hero p{font-size:14px}.home-feature-art{min-height:290px}.home-latest{margin:0 -15px;padding:30px 15px}.home-latest-card{grid-template-columns:90px minmax(0,1fr);column-gap:11px;padding:13px}.home-latest-card h3{font-size:12px}.home-latest-card p{font-size:10px}}
    @media(max-width:500px){.home-hero{padding-top:28px}.home-feature-art{min-height:240px}.home-art-caption{right:10px;bottom:10px;left:10px}.home-latest-card{grid-template-columns:1fr}.home-latest-card .home-section-label{grid-column:1;grid-row:1;margin-bottom:7px}.home-latest-card h3{grid-column:1;grid-row:2}.home-latest-card p{grid-column:1;grid-row:3}}
    .onboarding-layout{max-width:920px;display:grid;grid-template-columns:minmax(235px,.78fr) minmax(0,1fr);gap:20px;align-items:start}.onboarding-layout>.auth-brand,.onboarding-intro{grid-column:1/-1}.onboarding-layout>.auth-brand{margin-bottom:2px}.onboarding-intro{margin:-8px 0 4px}.onboarding-intro h1{margin:0 0 8px;font-size:30px;letter-spacing:-.04em}.onboarding-intro p{margin:0;color:var(--muted);font-size:12px;line-height:1.65}.onboarding-steps{grid-column:1;grid-row:3;display:grid;gap:9px;margin:0;padding:0;list-style:none}.onboarding-step{display:grid;grid-template-columns:35px minmax(0,1fr) auto;align-items:center;gap:10px;min-height:69px;padding:12px;border:1px solid #303034;border-radius:9px;background:#141415}.onboarding-step-number{width:31px;height:31px;display:grid;place-items:center;border:1px solid #424248;border-radius:50%;color:#c5ccd6;font-size:11px;font-weight:800}.onboarding-step strong,.onboarding-step span{display:block}.onboarding-step strong{font-size:12px}.onboarding-step span{margin-top:4px;color:var(--muted);font-size:10px}.onboarding-step-status{padding:5px 7px;border:1px solid #3b3b40;border-radius:99px;color:#bfc5ce;font-size:9px!important;white-space:nowrap}.onboarding-step.is-current{border-color:#45628b;background:linear-gradient(130deg,#1a2635,#151516)}.onboarding-step.is-current .onboarding-step-number{border-color:#739bd1;background:#29415f;color:#fff}.onboarding-step.is-current .onboarding-step-status{border-color:#45628b;color:#c6dcff}.onboarding-step.is-optional .onboarding-step-number{color:#9aa0aa}.onboarding-layout>.auth-card{grid-column:2;grid-row:3}.onboarding-layout .auth-card h1{font-size:24px}.onboarding-layout .auth-form{margin-top:14px}.onboarding-layout .auth-note{margin-top:14px}
    .onboarding-step .onboarding-step-number{margin:0;color:#c5ccd6;font-size:11px}.onboarding-step .onboarding-step-copy{margin:0;color:var(--text);font-size:inherit}.onboarding-step-copy strong{font-size:12px}.onboarding-step-copy small{display:block;margin-top:4px;color:var(--muted);font-size:10px}.onboarding-step.current{border-color:#45628b;background:linear-gradient(130deg,#1a2635,#151516)}.onboarding-step.current .onboarding-step-number{border-color:#739bd1;background:#29415f;color:#fff}.onboarding-step.current .onboarding-step-status{border-color:#45628b;color:#c6dcff}.onboarding-step .onboarding-step-status{margin:0;padding:5px 7px;color:#bfc5ce;font-size:9px;line-height:1.3}.onboarding-current-step{display:flex;justify-content:space-between;gap:10px;margin:0 0 14px;color:#b9c6d6;font-size:9px;font-weight:800;letter-spacing:.12em}.onboarding-current-step span:last-child{color:#a9cbff}
    .sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
    .verification-guide{margin-top:18px;padding:clamp(18px,4vw,30px);border:1px solid var(--line);border-radius:12px;background:linear-gradient(145deg,#15191f,#101114)}.verification-guide h2{margin:8px 0;font-size:22px}.verification-guide>p,.verification-guidance p{max-width:900px;color:var(--muted);font-size:12px;line-height:1.75}.verification-key{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:18px}.verification-key article{display:flex;align-items:flex-start;gap:11px;padding:13px;border:1px solid var(--line);border-radius:9px;background:#101114}.verification-key article p{margin:5px 0 0;color:var(--muted);font-size:11px;line-height:1.55}.verification-key strong{font-size:11px}.verification-mark{display:inline-grid;place-items:center;min-width:17px;font-size:14px;font-weight:900}.mark-green{color:#4ade80}.mark-blue{color:#60a5fa}.mark-yellow{color:#facc15}.mark-orange{color:#fb923c}.mark-silver{color:#cbd5e1}.mark-purple{color:#c084fc}.mark-deep_blue{color:#2563eb}.mark-black{color:#09090b;text-shadow:0 0 1px #fff}.mark-formal_staff{color:#fb7185;text-shadow:1px 1px #facc15}.team-bio{margin:6px 0 0;color:var(--muted);font-size:10px;line-height:1.5}.team-joined{display:block;margin-top:5px;color:var(--subtle);font-size:9px}.verification-guidance{margin-top:19px;padding-top:17px;border-top:1px solid var(--line)}.verification-guidance h3{margin:12px 0 5px;font-size:13px}
    @media(max-width:960px){.header-inner{gap:10px}.nav{gap:6px}.nav>a,.nav-dropdown summary{padding-left:7px;padding-right:7px}.nav-icon{width:14px}.nav-tools{margin-left:4px}.nav-action.search-action{font-size:0}.nav-action.search-action span{font-size:17px}.product-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.settings-layout{grid-template-columns:210px minmax(0,1fr);gap:25px}.member-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
    @media(max-width:960px){.home-section{min-height:160px}.home-orbit{right:6%;width:165px;height:165px}.home-orbit span{width:68px;height:68px;font-size:35px}}
    @media(max-width:760px){.onboarding-layout{grid-template-columns:minmax(0,1fr);gap:14px}.onboarding-layout>.auth-brand,.onboarding-intro,.onboarding-steps,.onboarding-layout>.auth-card{grid-column:1;grid-row:auto}.onboarding-intro{margin:0 0 2px}.onboarding-steps{grid-template-columns:repeat(2,minmax(0,1fr))}.onboarding-step{grid-template-columns:31px minmax(0,1fr);min-height:66px;gap:8px;padding:9px}.onboarding-step-number{width:28px;height:28px}.onboarding-step-status{grid-column:2;justify-self:start;margin-top:-4px}.onboarding-layout>.auth-card{width:100%}.header-inner{position:relative;width:calc(100% - 20px);min-height:58px;gap:7px;flex-wrap:nowrap;padding:7px 0}.brand{margin-right:auto}.mobile-menu-toggle{display:grid;order:1}.nav{display:none;order:3;position:absolute;top:100%;left:0;right:0;width:auto;max-height:calc(100vh - 68px);overflow:auto;flex-direction:column;align-items:stretch;justify-content:flex-start;gap:3px;padding:10px;margin:0;border-bottom:1px solid #303034;background:#101011;box-shadow:0 18px 36px #0009}.nav.mobile-open{display:flex}.nav>a{padding:10px}.nav-mobile-auth{display:block;margin-top:2px;border:1px solid #303034;border-radius:7px}.nav-mobile-auth:last-child{background:#f4f4f5;color:#111}.nav-auth-actions{display:none}.nav-dropdown summary{padding:10px}.nav-dropdown[open] .nav-menu{position:static;width:100%;max-height:none;margin:0 0 5px;box-shadow:none}.nav-tools{order:2;margin-left:0}.nav-tools>a[aria-label="Messages"],.nav-tools>a[aria-label="Marketplace cart"]{display:none}.nav-action.search-action{width:36px;padding:0}.nav-action.search-action .search-label{display:none}.main{width:calc(100% - 30px);padding-top:30px}.home-sections{grid-template-columns:repeat(2,minmax(0,1fr))}.home-hero{min-height:330px}.home-orbit{right:-48px;opacity:.4}.home-community-stats{flex-wrap:wrap;gap:14px}.home-community-stats article{min-width:0;flex:1;padding:0 12px}.home-community-stats>a{width:100%;margin:0}.home-latest-grid{grid-template-columns:1fr}.community-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.stats-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.settings-layout{display:block}.settings-sidebar{display:none}.settings-mobile-title{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:18px;font-weight:800}.settings-mobile-title select{max-width:65%}.profile-columns{grid-template-columns:1fr}.inbox-layout{grid-template-columns:1fr}.inbox-folders{display:flex;gap:5px}.shop-layout{grid-template-columns:1fr}.shop-filters{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.shop-filters h2,.shop-filters>p{grid-column:1/-1}.shop-filters .field{padding:9px 0}.product-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.member-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.verification-key{grid-template-columns:1fr}.notification-panel{top:70px;right:8px}.live-chat-launcher{right:12px;bottom:12px}.live-chat-panel{right:12px;bottom:68px}}
    @media(max-width:500px){.header-inner{width:calc(100% - 16px)}.brand{font-size:13px}.nav{font-size:11px;gap:0}.nav-dropdown summary{padding:8px 6px}.nav-menu{position:fixed;top:96px;left:8px;width:min(300px,calc(100vw - 16px))}.nav-tools{gap:0}.nav-action{min-width:31px}.nav-action.account-summary{padding:0 3px}.nav-account-name{display:none}.main{width:calc(100% - 24px);padding-top:25px}.home-hero{min-height:300px;padding:25px 20px}.home-orbit{display:none}.home-hero:after{right:-70px;top:20%;width:200px;height:200px}.home-hero h1{font-size:42px}.home-hero p{font-size:13px}.home-latest{margin:0 -12px;padding:30px 12px}.home-sections,.home-latest-grid,.community-grid,.command-list,.product-grid{grid-template-columns:1fr}.home-community-stats{align-items:flex-start}.home-community-stats article{flex-basis:calc(50% - 14px)}.home-section{min-height:140px}.record-search{flex-wrap:wrap}.record-search button{min-height:38px}.record-card{padding:12px}.record-meta{gap:6px}.record-profile{margin-left:0}.settings-card{padding:17px}.settings-heading h1{font-size:23px}.profile-identity{gap:10px;padding:0 13px 12px}.profile-avatar{width:70px;height:70px}.profile-identity h2{font-size:18px}.profile-identity .cta{margin-top:35px;padding:0 8px}.profile-stats{padding:12px;gap:10px;flex-wrap:wrap}.inbox-tools{flex-direction:column}.inbox-tools select{width:100%}.inbox-message{flex-wrap:wrap}.inbox-message .cta{margin-left:40px}.shop-filters{grid-template-columns:1fr}.search-categories{overflow-x:auto;flex-wrap:nowrap}.search-categories button{white-space:nowrap}.member-directory-tools{align-items:stretch;flex-direction:column}.member-grid{grid-template-columns:1fr 1fr;gap:7px}.member-card{padding:9px;gap:8px}.member-photo{width:35px;height:35px}.member-name{font-size:10px}.member-status{font-size:9px}.member-profile-card{align-items:flex-start;padding:15px;gap:12px}.member-profile-avatar{width:65px;height:65px;border-radius:14px}.member-profile-card h1{font-size:21px}.live-chat-launcher{padding:11px 13px}.live-chat-panel{width:calc(100vw - 20px);right:10px;height:min(520px,calc(100vh - 100px));bottom:65px}.notification-panel{right:5px;width:calc(100vw - 10px)}}
    .onboarding-layout{width:min(880px,100%);max-width:none;display:grid;grid-template-columns:minmax(0,1fr);gap:18px}.onboarding-layout>.auth-brand,.onboarding-intro,.onboarding-steps,.onboarding-layout>.auth-card{grid-column:1;grid-row:auto}.onboarding-layout>.auth-brand{margin:0}.onboarding-intro{margin:0}.onboarding-steps{grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}.onboarding-step{grid-template-columns:30px minmax(0,1fr);grid-template-rows:auto auto;gap:7px 9px;min-width:0;min-height:82px;padding:12px}.onboarding-step-copy{min-width:0}.onboarding-step-copy strong{font-size:11px}.onboarding-step-copy small{font-size:9px;line-height:1.4}.onboarding-step .onboarding-step-status{grid-column:2;justify-self:start;white-space:normal;padding:3px 7px}.onboarding-layout>.auth-card{width:min(620px,100%);margin:0 auto;padding:clamp(20px,4vw,32px)}.onboarding-layout .auth-form{margin:15px 0 10px}.onboarding-layout .auth-card>p{margin-bottom:18px}.onboarding-layout .auth-note{margin-top:12px}.verification-prompt{padding-top:4px}.verification-prompt h2{margin:0 0 8px;font-size:23px}.verification-prompt>p{color:var(--muted);font-size:12px;line-height:1.65}.verification-prompt>p strong{color:var(--text);overflow-wrap:anywhere}.verification-resend>p{margin:0;color:var(--muted);font-size:11px}.verification-message{padding:11px;border:1px solid #3e5c7e;border-radius:7px;background:#1a2634;color:#d4e5ff;font-size:11px;line-height:1.5}.verification-message.success,.auth-form-status.success{border-color:#315e45;background:#15251b;color:#a9e5bb}.auth-form-status.success{padding:8px;border:1px solid #315e45;border-radius:7px}.verification-prompt[hidden],.verification-resend[hidden],.verification-message[hidden]{display:none!important}
    @media(max-width:760px){.onboarding-layout{gap:14px}.onboarding-steps{grid-template-columns:repeat(2,minmax(0,1fr))}.onboarding-layout>.auth-card{width:100%}}
    @media(max-width:430px){.onboarding-steps{grid-template-columns:1fr}.onboarding-step{grid-template-columns:30px minmax(0,1fr) auto;grid-template-rows:auto;min-height:58px;padding:9px 11px}.onboarding-step .onboarding-step-status{grid-column:3;grid-row:1;justify-self:end;white-space:nowrap}.onboarding-step-copy small{font-size:9px}}
    @media(max-width:760px){.footer-catalogs{grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}.site-footer{padding:26px 16px 18px}}
    @media(max-width:430px){.footer-catalogs{gap:14px}.footer-catalog a{font-size:10px}}
    .settings-detail-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:22px}
    .settings-detail-card{min-width:0;padding:15px;border:1px solid #303034;border-radius:8px;background:#101011}
    .settings-detail-card h3{margin:0 0 7px;font-size:12px}
    .settings-detail-card p{margin:0;color:var(--muted);font-size:11px;line-height:1.65}
    .settings-detail-link{display:inline-block;margin-top:11px;color:#a9cbff;font-size:10px;font-weight:750}
    .settings-detail-link:hover{text-decoration:underline}
    @media(max-width:600px){.settings-detail-grid{grid-template-columns:1fr}.settings-card{padding:17px}}
    .nav-action.search-action{width:clamp(190px,19vw,250px);height:40px;justify-content:flex-start;gap:11px;padding:0 13px;border-color:#454b55;border-radius:9px;background:#17191d;color:#e0e6ef;font-size:14px}
    .nav-action.search-action:hover{border-color:#759bd0;background:#1c2026}
    .nav-action.search-action>span:first-child{color:#b8d2f5;font-size:20px}
    .nav-action.search-action .search-label{display:flex;flex:1;align-items:center;justify-content:space-between;gap:8px;text-align:left;font-size:13px}
    .nav-action.search-action kbd{margin-left:auto;padding:3px 7px;border:1px solid #444b56;border-radius:5px;background:#22252b;color:#c4cad4;font-size:10px}
    @media(max-width:960px){.nav-action.search-action{width:42px;height:42px;justify-content:center;padding:0}.nav-action.search-action .search-label{display:none}}
    @media(max-width:760px){.nav-action.search-action{width:42px;height:42px}}
    .settings-layout{max-width:1080px;margin:0 auto;grid-template-columns:196px minmax(0,1fr);gap:24px}
    .settings-detail-grid{grid-template-columns:minmax(0,1fr)}
    .public-profile-banner{overflow:hidden;padding:0!important}
    .public-profile-cover{height:128px;background:radial-gradient(ellipse at 20% 0%,#27466a 0,transparent 52%),linear-gradient(120deg,#141d2b,#20202a 58%,#111214)}
    .public-profile-identity{position:relative;display:flex;align-items:flex-end;gap:16px;margin-top:-42px;padding:0 21px 14px}
    .public-profile-identity .public-profile-avatar{width:76px;height:76px;border-radius:17px;background:linear-gradient(145deg,#2a3d58,#171719);box-shadow:0 2px 10px #0009}
    .public-profile-identity .public-profile-title{padding-bottom:3px}
    .public-profile-title h1{margin:0;font-size:25px;overflow-wrap:anywhere}
    .public-profile-title p{margin:4px 0 0;color:var(--muted);font-size:11px}
    .public-profile-title #discord-profile-status{display:inline-flex;align-items:center;gap:7px}
    .public-profile-title #discord-profile-status::before{content:"";width:8px;height:8px;border-radius:50%;background:#555b63}
    .public-profile-title #discord-profile-status.online::before{background:#4ade80}
    .public-profile-actions{display:flex;gap:7px;margin-left:auto;padding-bottom:4px}
    .public-profile-stats{display:flex;flex-wrap:wrap;gap:16px;padding:11px 21px;border-top:1px solid var(--line);color:var(--muted);font-size:10px}
    .public-profile-layout{display:grid;grid-template-columns:minmax(0,1fr) 245px;gap:14px;align-items:start}
    .public-profile-main{min-width:0}
    .public-profile-aside{display:grid;gap:10px}
    .public-profile-aside .panel{padding:15px}
    .public-profile-aside h2{margin:0 0 7px;font-size:12px}
    .public-profile-aside p{margin:0;color:var(--muted);font-size:10px;line-height:1.6}
    .public-profile-aside p+p{margin-top:7px}
    @media(max-width:960px){.settings-layout{grid-template-columns:190px minmax(0,1fr);gap:19px}.public-profile-layout{grid-template-columns:minmax(0,1fr) 220px}}
    @media(max-width:760px){.settings-layout{grid-template-columns:minmax(0,1fr);gap:16px}.public-profile-layout{grid-template-columns:minmax(0,1fr)}.public-profile-aside{grid-template-columns:repeat(2,minmax(0,1fr))}}
    @media(max-width:500px){.settings-layout{display:block}.public-profile-identity{align-items:flex-start;flex-wrap:wrap;gap:11px;padding:0 14px 12px;margin-top:-34px}.public-profile-identity .public-profile-avatar{width:62px;height:62px}.public-profile-title h1{font-size:20px}.public-profile-actions{width:100%;margin-left:73px;padding:0}.public-profile-actions .cta{padding:0 9px}.public-profile-stats{gap:10px;padding:10px 14px}.public-profile-aside{grid-template-columns:minmax(0,1fr)}}
    .report-tracker-content[hidden],.tracker-section[hidden]{display:none!important}
    .tracker-signin{max-width:680px;padding:22px}
    .tracker-signin h2{margin:0 0 8px;font-size:18px}
    .tracker-signin p,.tracker-section-heading p{color:var(--muted);font-size:11px;line-height:1.6}
    .tracker-section{margin:18px 0 28px}
    .tracker-section-heading{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:12px}
    .tracker-section-heading h2{margin:5px 0;font-size:18px}
    .tracker-section-heading p{margin:5px 0 0}
    .tracker-list{display:grid;gap:12px}
    .tracker-card{min-width:0;padding:18px;border:1px solid #30343c;border-radius:11px;background:linear-gradient(145deg,#15171b,#101113)}
    .tracker-card-header{display:flex;justify-content:space-between;align-items:flex-start;gap:14px}
    .tracker-reference{font-size:13px;font-weight:850;letter-spacing:.03em}
    .tracker-card-header p{margin:5px 0 0;color:var(--muted);font-size:11px;overflow-wrap:anywhere}
    .tracker-status-badge{flex:none;padding:6px 9px;border:1px solid #456080;border-radius:999px;background:#182432;color:#c7dcf9;font-size:10px;font-weight:800}
    .tracker-steps{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:5px;margin:20px 0 14px;padding:0;list-style:none}
    .tracker-steps li{position:relative;display:grid;justify-items:center;gap:7px;color:#747c89;text-align:center;font-size:9px;line-height:1.35}
    .tracker-steps li:not(:last-child):after{position:absolute;top:5px;left:calc(50% + 9px);width:calc(100% - 18px);height:2px;background:#30343c;content:""}
    .tracker-steps li.complete:after{background:#55bd81}
    .tracker-step-dot{z-index:1;width:11px;height:11px;border:2px solid #505864;border-radius:50%;background:#111318}
    .tracker-steps li.complete,.tracker-steps li.active{color:#dce6f3}
    .tracker-steps li.complete .tracker-step-dot{border-color:#55bd81;background:#55bd81}
    .tracker-steps li.active .tracker-step-dot{border-color:#9ac4ff;background:#28456a;box-shadow:0 0 0 4px #9ac4ff1b}
    .tracker-dates{display:flex;flex-wrap:wrap;gap:6px 16px;padding-top:10px;border-top:1px solid var(--line);color:var(--muted);font-size:9px}
    .tracker-timeline{display:grid;gap:10px;margin:14px 0 0;padding:0 0 0 15px;border-left:1px solid #38404b;list-style:none}
    .tracker-timeline li{position:relative;padding-left:4px}
    .tracker-timeline li:before{position:absolute;top:3px;left:-20px;width:8px;height:8px;border:2px solid #83b3f5;border-radius:50%;background:#111318;content:""}
    .tracker-update-label{display:inline-block;margin-right:8px;font-size:10px;font-weight:800}
    .tracker-timeline time{color:var(--muted);font-size:9px}
    .tracker-timeline p{margin:4px 0 0;color:#c0c7d2;font-size:10px;line-height:1.6;overflow-wrap:anywhere}
    .tracker-staff-form{display:grid;gap:10px;margin-top:16px;padding-top:14px;border-top:1px solid var(--line)}
    .tracker-staff-form label{display:grid;gap:6px;color:#dce1e9;font-size:10px;font-weight:750}
    .tracker-staff-form label span{color:var(--muted);font-size:9px;font-weight:500}
    .tracker-staff-form select,.tracker-staff-form textarea{width:100%;padding:10px;border:1px solid #353a44;border-radius:7px;background:#101216;color:var(--text);font:inherit}
    .tracker-staff-form textarea{min-height:75px;resize:vertical}
    .tracker-staff-form .cta{justify-self:start}
    .tracker-form-status,.tracker-notice{min-height:18px;color:var(--muted);font-size:10px}
    .tracker-empty{padding:20px;border:1px dashed #38404b;border-radius:9px;color:#dfe5ee}
    .tracker-empty p{margin:6px 0 0;color:var(--muted);font-size:10px}
    @media(max-width:560px){.tracker-card{padding:13px}.tracker-card-header{flex-direction:column}.tracker-steps{gap:3px}.tracker-steps li{font-size:8px}.tracker-dates{display:grid;gap:5px}}
  </style>
</head>
<body>
  <header class="site-header">
    <div class="header-inner">
      <a class="brand" href="/" aria-label="NexTech home"><span class="brand-mark">N</span><span class="brand-name">NexTech</span></a>
      <button class="mobile-menu-toggle" id="mobile-menu-toggle" type="button" aria-label="Open menu" aria-expanded="false">☰</button>
      <nav class="nav" aria-label="Main navigation">
        <a href="/"><span class="nav-icon" aria-hidden="true">⌂</span><span>Home</span></a>
        <a href="/hub"><span class="nav-icon" aria-hidden="true">▦</span><span>Hub</span></a>
        <a href="/forums"><span class="nav-icon" aria-hidden="true">▤</span><span>Forums</span></a>
        <a href="/shop"><span class="nav-icon" aria-hidden="true">◇</span><span>Shop</span></a>
        <a href="/support"><span class="nav-icon" aria-hidden="true">✉</span><span>Support</span></a>
        <a class="nav-mobile-auth" data-guest-action href="/login">Log in</a>
        <a class="nav-mobile-auth" data-guest-action href="/onboarding">Join NexTech</a>
      </nav>
      <div class="nav-tools" aria-label="Account and tools">
        <button class="nav-action search-action" id="open-search" type="button" aria-label="Search"><span>⌕</span><span class="search-label">Search <kbd>Ctrl K</kbd></span></button>
        <a class="nav-action" href="/messages" aria-label="Messages" title="Messages">▱</a>
        <a class="nav-action" href="/shop" aria-label="Marketplace cart" title="Marketplace cart">♧<span class="cart-count" id="nav-cart-count" hidden>0</span></a>
        <button class="nav-action notification-trigger" id="notification-trigger" type="button" aria-label="Notifications" aria-expanded="false" title="Notifications">🔔<span class="notification-count" id="notification-count" hidden>0</span></button>
        <details class="nav-dropdown account-dropdown"><summary class="account-summary">
          <span class="account-avatar" id="nav-account-avatar">N</span><span class="nav-account-name" id="nav-account-name">Account</span><span aria-hidden="true">⌄</span>
        </summary><div class="nav-menu">
          <div class="account-menu-heading" id="account-menu-heading">NexTech account<small>Sign in to access member tools</small></div>
          <a href="/profile"><span class="menu-icon">◉</span><span class="menu-copy"><strong>My profile</strong></span></a>
          <a href="/messages"><span class="menu-icon">▱</span><span class="menu-copy"><strong>Messages</strong></span></a>
          <a href="/support/tickets"><span class="menu-icon">▤</span><span class="menu-copy"><strong>Support tickets</strong><small>Live status from the ticket system</small></span></a>
          <a href="/settings"><span class="menu-icon">⚙</span><span class="menu-copy"><strong>Settings</strong></span></a>
          <a href="/login"><span class="menu-icon">↗</span><span class="menu-copy"><strong>Log in</strong></span></a>
          <a href="/dashboard"><span class="menu-icon">▦</span><span class="menu-copy"><strong>Dashboard</strong></span></a>
          <a href="/community/reports"><span class="menu-icon">⚑</span><span class="menu-copy"><strong>Report center</strong></span></a>
          <button class="account-signout" id="account-signout" type="button" hidden>Sign out</button>
        </div></details>
      </div>
      <div class="nav-auth-actions" id="nav-auth-actions" aria-label="Account access">
        <a class="nav-auth-link" data-guest-action href="/login">Log in</a>
        <a class="nav-auth-link join" data-guest-action href="/onboarding">Join</a>
      </div>
    </div>
  </header>
  <section class="notification-panel" id="notification-panel" aria-label="Your notifications" hidden>
    <div class="widget-header"><div><h2>Notifications</h2><span id="notification-account-label">Sign in with Discord for member alerts</span></div><button type="button" id="notification-close" aria-label="Close notifications">×</button></div>
    <div class="notification-list" id="notification-list"><div class="notification-empty">Sign in with Discord to get private alerts when you’re mentioned or staff record an action on your account.</div></div>
    <div class="notification-footer"><button type="button" id="notifications-mark-read" disabled>Mark all as read</button></div>
  </section>
  <button class="live-chat-launcher" id="live-chat-launcher" type="button" aria-expanded="false"><span class="live-dot"></span>Live from Discord</button>
  <section class="live-chat-panel" id="live-chat-panel" aria-label="Public live Discord chat" hidden>
    <div class="widget-header"><div class="live-chat-heading"><h2>Live from Discord</h2><span>Public channels from the NexTech server</span></div><button type="button" id="live-chat-close" aria-label="Close live Discord chat">×</button></div>
    <div class="live-chat-messages" id="live-chat-messages" role="log" aria-live="polite"><div class="notification-empty">Loading public Discord messages…</div></div>
    <div class="live-chat-status" id="live-chat-status" role="status">Only public messages are mirrored. Deleted messages are removed.</div>
  </section>
  <div class="search-overlay" id="search-overlay" aria-hidden="true">
    <section class="search-dialog" role="dialog" aria-modal="true" aria-label="Search NexTech">
      <div class="search-top"><span aria-hidden="true">⌕</span><input id="site-search" type="search" placeholder="Search NexTech…" autocomplete="off" aria-label="Search NexTech"><button class="search-close" id="close-search" type="button" aria-label="Close search">×</button></div>
      <div class="search-categories" role="tablist" aria-label="Search categories">
        <span>Search in</span><button class="active" type="button" data-search-type="all" role="tab" aria-selected="true">All</button><button type="button" data-search-type="wiki" role="tab" aria-selected="false">Wiki</button><button type="button" data-search-type="shop" role="tab" aria-selected="false">Shop</button><button type="button" data-search-type="settings" role="tab" aria-selected="false">Pages &amp; settings</button>
      </div>
      <div class="search-results" id="search-results" aria-live="polite">Search NexTech pages, help articles, products, and account settings.</div>
    </section>
  </div>
  <main class="main">__CONTENT__</main>
  <footer class="site-footer">
    <div class="footer-inner">
      <div class="footer-catalogs">
        <section class="footer-catalog" aria-labelledby="footer-community">
          <h2 id="footer-community">Community</h2>
          <a href="/community">Community hub</a><a href="/forums">Forums</a><a href="/groups">Groups</a><a href="/members">Discord members</a><a href="/feedback">Feedback</a><a href="/community/apply">Applications</a>
        </section>
        <section class="footer-catalog" aria-labelledby="footer-resources">
          <h2 id="footer-resources">Resources</h2>
          <a href="/team">Staff directory</a><a href="/community/records">Community records</a><a href="/community/osp">Online safety</a><a href="/knowledge-base">Knowledge base</a><a href="/wiki">Wiki</a><a href="/status">System status</a>
        </section>
        <section class="footer-catalog" aria-labelledby="footer-studio">
          <h2 id="footer-studio">Studio &amp; shop</h2>
          <a href="/studio">NexTech Studio</a><a href="/studio/titles">Titles</a><a href="/studio/news">Studio news</a><a href="/shop">Marketplace catalog</a><a href="/careers">Careers</a><a href="/club">NexTech Club</a>
        </section>
        <section class="footer-catalog" aria-labelledby="footer-help">
          <h2 id="footer-help">Support &amp; policies</h2>
          <a href="/support">Support Hub</a><a href="/support/tickets">Support tickets</a><a href="/community/reports">Report center</a><a href="/community/appeals">Appeals</a><a href="/community/staff-abuse">Staff conduct</a><a href="/policies">Policies</a>
        </section>
      </div>
      <div class="footer-bottom"><span>NexTech Interactive Development LLC</span><nav class="footer-bottom-links" aria-label="Footer links"><a href="/">Home</a><a href="/login">Log in</a><a href="/onboarding">Join NexTech</a></nav></div>
    </div>
  </footer>
  <script>
    document.addEventListener("click",event=>document.querySelectorAll(".nav-dropdown[open]").forEach(menu=>{if(!menu.contains(event.target))menu.open=false;}));
    (() => {
      const mobileMenuButton = document.getElementById("mobile-menu-toggle");
      const mainNavigation = document.querySelector(".nav");
      mobileMenuButton.addEventListener("click", () => {
        const isOpen = mainNavigation.classList.toggle("mobile-open");
        mobileMenuButton.setAttribute("aria-expanded", String(isOpen));
        mobileMenuButton.setAttribute("aria-label", isOpen ? "Close menu" : "Open menu");
      });
      mainNavigation.addEventListener("click", event => {
        if (event.target.closest("a")) {
          mainNavigation.classList.remove("mobile-open");
          mobileMenuButton.setAttribute("aria-expanded", "false");
          mobileMenuButton.setAttribute("aria-label", "Open menu");
        }
      });
      const overlay = document.getElementById("search-overlay");
      const input = document.getElementById("site-search");
      const results = document.getElementById("search-results");
      let activeType = "all";
      let searchTimer;
      const settingsSearchPages = [
        {title:"Account settings",detail:"Settings · Account",href:"/settings/account",keywords:"account email username sign in profile password security recovery"},
        {title:"Confirm password",detail:"Settings · Security",href:"/confirm-password",keywords:"password confirm re-authenticate reauthenticate verify security"},
        {title:"Customize profile",detail:"Settings · Profile",href:"/settings/customize",keywords:"customize profile display name avatar personalize"},
        {title:"Appearance",detail:"Settings · Profile",href:"/settings/appearance",keywords:"appearance theme dark color accessibility"},
        {title:"Verification",detail:"Settings · Profile",href:"/settings/verification",keywords:"verification verified email discord roblox identity staff marks"},
        {title:"Privacy & data",detail:"Settings · Privacy & notifications",href:"/settings/privacy",keywords:"privacy data visibility personal information removal"},
        {title:"Notifications",detail:"Settings · Privacy & notifications",href:"/settings/notifications",keywords:"notifications alerts email discord mentions bell"},
        {title:"Calls",detail:"Settings · Privacy & notifications",href:"/settings/calls",keywords:"calls voice video audio"},
        {title:"Ad preferences",detail:"Settings · Privacy & notifications",href:"/settings/ads",keywords:"ads advertising preferences targeting promotions"},
        {title:"Account standing",detail:"Settings · Privacy & notifications",href:"/settings/standing",keywords:"standing warnings moderation records appeal suspension"},
        {title:"Membership",detail:"Settings · Billing & purchases",href:"/settings/membership",keywords:"membership member premium access"},
        {title:"Subscription",detail:"Settings · Billing & purchases",href:"/settings/subscription",keywords:"subscription recurring plan membership"},
        {title:"Billing",detail:"Settings · Billing & purchases",href:"/settings/billing",keywords:"billing invoices statements charges payment"},
        {title:"Credit",detail:"Settings · Billing & purchases",href:"/settings/credit",keywords:"credit wallet balance account funds"},
        {title:"Points",detail:"Settings · Billing & purchases",href:"/settings/points",keywords:"points rewards balance redemption"},
        {title:"Orders",detail:"Settings · Billing & purchases",href:"/settings/orders",keywords:"orders purchase order history status"},
        {title:"Payment methods",detail:"Settings · Billing & purchases",href:"/settings/payment-methods",keywords:"payment methods card bank saved payment"},
        {title:"Discounts",detail:"Settings · Billing & purchases",href:"/settings/discounts",keywords:"discounts offers deals coupons codes"},
        {title:"Licenses",detail:"Settings · Billing & purchases",href:"/settings/licenses",keywords:"licenses keys ownership products"},
        {title:"Integrations",detail:"Settings · Connections",href:"/settings/integrations",keywords:"integrations connections connected accounts discord roblox hub link"},
        {title:"Rewards",detail:"Settings · Connections",href:"/rewards",keywords:"rewards points prizes redemption"},
        {title:"Advertising",detail:"Settings · Connections",href:"/advertising",keywords:"advertising promote campaigns business"},
      ];
      const closeSearch = () => { overlay.classList.remove("open"); overlay.setAttribute("aria-hidden", "true"); };
      document.getElementById("open-search").addEventListener("click", () => {
        overlay.classList.add("open"); overlay.setAttribute("aria-hidden", "false"); input.focus();
      });
      document.getElementById("close-search").addEventListener("click", closeSearch);
      overlay.addEventListener("click", event => { if (event.target === overlay) closeSearch(); });
      document.addEventListener("keydown", event => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); overlay.classList.add("open"); overlay.setAttribute("aria-hidden", "false"); input.focus(); }
        if (event.key === "Escape") closeSearch();
      });
      document.querySelectorAll("[data-search-type]").forEach(button => button.addEventListener("click", () => {
        activeType = button.dataset.searchType;
        document.querySelectorAll("[data-search-type]").forEach(tab => { const selected = tab === button; tab.classList.toggle("active", selected); tab.setAttribute("aria-selected", String(selected)); });
        runSearch();
      }));
      async function runSearch() {
        const query = input.value.trim().toLowerCase();
        window.clearTimeout(searchTimer);
        if (!query) { results.textContent = "Search NexTech pages, help articles, products, and account settings."; return; }
        results.textContent = "Searching…";
        searchTimer = window.setTimeout(async () => {
          const sources = [];
          const failures = [];
          if (activeType === "all" || activeType === "settings") {
            settingsSearchPages.forEach(item => {
              if ((item.title + " " + item.detail + " " + item.keywords).toLowerCase().includes(query))
                sources.push({title:item.title,detail:item.detail,href:item.href});
            });
          }
          if (activeType !== "shop" && activeType !== "settings") {
            try {
              const response = await fetch("/api/community/wiki", {headers: {"Accept": "application/json"}});
              if (!response.ok) throw new Error("Wiki search returned " + response.status);
              const data = await response.json();
              (data.articles || []).forEach(item => {
                if ((String(item.question || "") + " " + String(item.answer || "") + " " + String(item.category || "")).toLowerCase().includes(query))
                  sources.push({title: item.question || "Wiki article", detail: item.category || "Wiki", href: "/wiki"});
              });
            } catch (error) { console.error("Wiki search failed", error); failures.push("Wiki search is unavailable."); }
          }
          if (activeType !== "wiki" && activeType !== "settings") {
            try {
              const response = await fetch("/api/products", {headers: {"Accept": "application/json"}});
              if (!response.ok) throw new Error("Marketplace search returned " + response.status);
              const data = await response.json();
              const products = Array.isArray(data) ? data : (data.products || []);
              products.forEach(item => {
                if ((String(item.name || "") + " " + String(item.description || "") + " " + String(item.category || "")).toLowerCase().includes(query))
                  sources.push({title: item.name || "Marketplace product", detail: item.category || "Shop", href: "/shop"});
              });
            } catch (error) { console.error("Marketplace search failed", error); failures.push("Marketplace search is unavailable."); }
          }
          results.replaceChildren();
          if (sources.length) sources.slice(0, 20).forEach(item => {
            const link = document.createElement("a"); link.className = "search-result"; link.href = item.href;
            const title = document.createElement("strong"); title.textContent = item.title;
            const detail = document.createElement("small"); detail.textContent = item.detail;
            link.append(title, detail); results.append(link);
          });
          if (!sources.length) {
            const empty = document.createElement("div"); empty.textContent = failures.length ? failures.join(" ") : "No results found. Try a different search.";
            results.append(empty);
          } else if (failures.length) {
            const notice = document.createElement("small"); notice.textContent = failures.join(" "); results.append(notice);
          }
        }, 180);
      }
      input.addEventListener("input", runSearch);
      const hashParams = new URLSearchParams(location.hash.slice(1));
      const discordToken = hashParams.get("discord_token");
      if (discordToken) {
        localStorage.setItem("nx_discord_community_token", discordToken);
        history.replaceState({}, document.title, location.pathname + location.search);
      }
      const guestActions = document.getElementById("nav-auth-actions");
      const mobileGuestActions = document.querySelectorAll(".nav-mobile-auth");
      const memberOnlyTools = document.querySelectorAll(
        '.nav-tools > a[aria-label="Messages"], .nav-tools > a[aria-label="Marketplace cart"], .nav-tools > .notification-trigger, .nav-tools > .account-dropdown'
      );
      function showGuestActions(show) {
        guestActions.hidden = !show;
        mobileGuestActions.forEach(link => { link.hidden = !show; });
        memberOnlyTools.forEach(tool => { tool.hidden = show; });
      }
      showGuestActions(
        !localStorage.getItem("nx_cust_token")
        && !localStorage.getItem("nx_discord_community_token")
      );
      const cart = document.getElementById("nav-cart-count");
      const updateCart = () => {
        try {
          const items = JSON.parse(localStorage.getItem("nx_cart") || "[]");
          cart.textContent = String(Array.isArray(items) ? items.length : 0);
          cart.hidden = !Array.isArray(items) || !items.length;
        } catch (error) { console.error("Could not read local marketplace cart", error); }
      };
      updateCart();
      window.addEventListener("storage", event => { if (event.key === "nx_cart") updateCart(); });
      window.addEventListener("nx-cart-updated", updateCart);
      const token = localStorage.getItem("nx_cust_token");
      if (token) {
        fetch("/api/customer/me", {headers: {"X-Customer-Token": token, "Accept": "application/json"}})
          .then(async response => { if (!response.ok) throw new Error("Account lookup returned " + response.status); return response.json(); })
          .then(data => {
            showGuestActions(false);
            const user = data.customer || data;
            const name = user.display_name || user.username || user.name || "Account";
            document.getElementById("nav-account-name").textContent = name;
            document.getElementById("account-menu-heading").replaceChildren(document.createTextNode(name));
            const note = document.createElement("small"); note.textContent = "Signed in"; document.getElementById("account-menu-heading").append(note);
            const avatarUrl = user.avatar_url || user.avatar;
            if (avatarUrl) { const image = document.createElement("img"); image.src = avatarUrl; image.alt = ""; image.referrerPolicy = "no-referrer"; const avatar = document.getElementById("nav-account-avatar"); avatar.replaceChildren(image); }
            document.getElementById("account-signout").hidden = false;
          }).catch(error => {
            console.error("Could not load account menu", error);
            localStorage.removeItem("nx_cust_token");
            showGuestActions(!localStorage.getItem("nx_discord_community_token"));
          });
      }
      const discordCommunityToken = localStorage.getItem("nx_discord_community_token");
      const notificationTrigger = document.getElementById("notification-trigger");
      const notificationPanel = document.getElementById("notification-panel");
      const notificationList = document.getElementById("notification-list");
      const notificationCount = document.getElementById("notification-count");
      const notificationMarkRead = document.getElementById("notifications-mark-read");
      const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
      let notificationsLoading = false;
      function renderNotificationSignIn(message) {
        notificationList.replaceChildren();
        const copy = document.createElement("p");
        copy.className = "notification-empty";
        copy.textContent = message;
        const link = document.createElement("a");
        link.className = "widget-login";
        link.href = "/auth/discord/community?next=" + encodeURIComponent(location.pathname + location.search);
        link.textContent = "Sign in with Discord";
        notificationList.append(copy, link);
        notificationMarkRead.disabled = true;
      }
      function formatNoticeTime(value) {
        const timestamp = Number(value);
        return Number.isFinite(timestamp) && timestamp > 0
          ? new Date(timestamp * 1000).toLocaleString()
          : "";
      }
      async function loadNotifications() {
        if (!discordCommunityToken) {
          notificationCount.hidden = true;
          renderNotificationSignIn("Sign in with Discord to get private alerts when you’re mentioned or staff record an action on your account.");
          return;
        }
        if (notificationsLoading) return;
        notificationsLoading = true;
        try {
          const response = await fetch("/api/community/notifications", {
            headers: {"X-Discord-Community-Token": discordCommunityToken, "Accept": "application/json"}
          });
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || "Notifications could not be loaded.");
          document.getElementById("notification-account-label").textContent =
            "Private alerts for " + (data.member.display_name || "your account");
          notificationCount.textContent = String(data.unread_count || 0);
          notificationCount.hidden = !(data.unread_count > 0);
          notificationMarkRead.disabled = !(data.unread_count > 0);
          notificationList.replaceChildren();
          if (!data.notifications.length) {
            const empty = document.createElement("div");
            empty.className = "notification-empty";
            empty.textContent = "You’re all caught up. Discord mentions and staff account actions appear here.";
            notificationList.append(empty);
            return;
          }
          data.notifications.forEach(item => {
            const link = document.createElement("a");
            link.className = "notification-item" + (item.read ? "" : " unread");
            link.href = item.href;
            link.dataset.notificationId = item.id;
            const title = document.createElement("strong");
            title.textContent = item.title;
            const message = document.createElement("p");
            message.textContent = item.message;
            const date = document.createElement("time");
            date.textContent = formatNoticeTime(item.created_at);
            link.append(title, message, date);
            notificationList.append(link);
          });
        } catch (error) {
          console.error("Could not load Discord notifications", error);
          notificationList.replaceChildren();
          const message = document.createElement("div");
          message.className = "notification-empty";
          message.textContent = error.message || "Notifications are temporarily unavailable.";
          notificationList.append(message);
        } finally {
          notificationsLoading = false;
        }
      }
      notificationTrigger.addEventListener("click", () => {
        const isOpen = !notificationPanel.hidden;
        notificationPanel.hidden = isOpen;
        notificationTrigger.setAttribute("aria-expanded", String(!isOpen));
        if (!isOpen) loadNotifications();
      });
      document.getElementById("notification-close").addEventListener("click", () => {
        notificationPanel.hidden = true;
        notificationTrigger.setAttribute("aria-expanded", "false");
      });
      notificationMarkRead.addEventListener("click", async () => {
        if (!discordCommunityToken || notificationMarkRead.disabled) return;
        notificationMarkRead.disabled = true;
        try {
          const response = await fetch("/api/community/notifications/read", {
            method: "POST",
            headers: {"X-Discord-Community-Token": discordCommunityToken, "Content-Type": "application/json", "Accept": "application/json"},
            body: JSON.stringify({all: true})
          });
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || "Notifications could not be updated.");
          await loadNotifications();
        } catch (error) {
          console.error("Could not mark notifications read", error);
          notificationMarkRead.disabled = false;
        }
      });
      notificationList.addEventListener("click", async event => {
        const item = event.target.closest(".notification-item.unread");
        if (!item || !discordCommunityToken) return;
        try {
          const response = await fetch("/api/community/notifications/read", {
            method: "POST",
            headers: {"X-Discord-Community-Token": discordCommunityToken, "Content-Type": "application/json", "Accept": "application/json"},
            body: JSON.stringify({ids: [item.dataset.notificationId]})
          });
          if (!response.ok) throw new Error("Notification could not be marked read.");
          item.classList.remove("unread");
          await loadNotifications();
        } catch (error) { console.error("Could not mark notification read", error); }
      });
      if (!discordCommunityToken && !token) {
        document.getElementById("nav-account-name").textContent = "Sign in";
        document.getElementById("account-menu-heading").replaceChildren(document.createTextNode("Sign in to NexTech"));
        const signIn = document.createElement("a");
        signIn.href = "/auth/discord/community";
        const signInCopy = document.createElement("span");
        signInCopy.className = "menu-copy";
        const signInTitle = document.createElement("strong");
        signInTitle.textContent = "Continue with Discord";
        signInCopy.append(signInTitle);
        signIn.append(signInCopy);
        document.getElementById("account-menu-heading").after(signIn);
      }
      if (discordCommunityToken) {
        fetch("/api/community/notifications", {
          headers: {"X-Discord-Community-Token": discordCommunityToken, "Accept": "application/json"}
        }).then(async response => {
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || "Discord sign-in expired.");
          document.getElementById("nav-account-name").textContent = data.member.display_name;
          document.getElementById("notification-account-label").textContent = "Private alerts for " + data.member.display_name;
          if (data.member.avatar) {
            const image = document.createElement("img");
            image.src = data.member.avatar;
            image.alt = "";
            image.referrerPolicy = "no-referrer";
            document.getElementById("nav-account-avatar").replaceChildren(image);
          }
          notificationCount.textContent = String(data.unread_count || 0);
          notificationCount.hidden = !(data.unread_count > 0);
          document.getElementById("account-signout").hidden = false;
          showGuestActions(false);
        }).catch(error => {
          console.error("Discord community sign-in needs refresh", error);
          localStorage.removeItem("nx_discord_community_token");
          showGuestActions(!localStorage.getItem("nx_cust_token"));
          renderNotificationSignIn("Your Discord sign-in expired. Sign in again to view private notifications.");
        });
      } else {
        showGuestActions(!localStorage.getItem("nx_cust_token"));
        renderNotificationSignIn("Sign in with Discord to get private alerts when you’re mentioned or staff record an action on your account.");
      }
      window.setInterval(() => {
        if (discordCommunityToken && !notificationPanel.hidden) loadNotifications();
      }, 20000);
      document.getElementById("account-signout").addEventListener("click", () => {
        localStorage.removeItem("nx_cust_token");
        localStorage.removeItem("nx_discord_community_token");
        location.href = "/dashboard";
      });
      const liveChatPanel = document.getElementById("live-chat-panel");
      const liveChatLauncher = document.getElementById("live-chat-launcher");
      const liveChatMessages = document.getElementById("live-chat-messages");
      const liveChatStatus = document.getElementById("live-chat-status");
      const liveMessageIds = new Set();
      let liveChatLoading = false;
      function renderLiveMessages(messages) {
        const shouldScroll = liveChatMessages.scrollHeight - liveChatMessages.scrollTop <= liveChatMessages.clientHeight + 90;
        const currentIds = new Set(messages.map(message => String(message.id)));
        for (const child of [...liveChatMessages.children]) {
          const id = child.dataset.messageId;
          if (id && !currentIds.has(id)) { child.remove(); liveMessageIds.delete(id); }
        }
        const empty = liveChatMessages.querySelector(".notification-empty");
        if (empty) empty.remove();
        messages.forEach(message => {
          const id = String(message.id);
          if (liveMessageIds.has(id)) return;
          liveMessageIds.add(id);
          const article = document.createElement("article");
          article.className = "live-chat-message";
          article.dataset.messageId = id;
          const avatar = document.createElement("img");
          avatar.className = "live-chat-avatar";
          avatar.alt = "";
          avatar.loading = "lazy";
          avatar.referrerPolicy = "no-referrer";
          if (typeof message.avatar === "string" && message.avatar.startsWith("https://")) avatar.src = message.avatar;
          const copy = document.createElement("div");
          copy.className = "live-chat-copy";
          const meta = document.createElement("div");
          meta.className = "live-chat-meta";
          const author = document.createElement("strong");
          author.textContent = message.author || "Discord member";
          const channel = document.createElement("span");
          channel.textContent = "#" + String(message.channel || "public").slice(0, 80);
          const timestamp = document.createElement("time");
          const parsedDate = Date.parse(message.created_at || "");
          timestamp.textContent = Number.isFinite(parsedDate) ? new Date(parsedDate).toLocaleTimeString() : "";
          const content = document.createElement("p");
          content.textContent = String(message.content || "").slice(0, 1200);
          meta.append(author, channel, timestamp);
          copy.append(meta, content);
          article.append(avatar, copy);
          liveChatMessages.append(article);
        });
        if (!messages.length && !liveChatMessages.children.length) {
          const emptyState = document.createElement("div");
          emptyState.className = "notification-empty";
          emptyState.textContent = "No recent messages in public channels. New chat will appear here.";
          liveChatMessages.append(emptyState);
        }
        if (shouldScroll) liveChatMessages.scrollTop = liveChatMessages.scrollHeight;
      }
      async function refreshLiveMessages() {
        if (liveChatLoading || document.hidden) return;
        liveChatLoading = true;
        try {
          const response = await fetch("/api/community/live", {headers: {"Accept": "application/json"}});
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || "Discord chat could not be loaded.");
          renderLiveMessages(Array.isArray(data.messages) ? data.messages : []);
          liveChatStatus.textContent = "Live public channels · refreshed just now · deleted messages are removed";
        } catch (error) {
          console.error("Could not refresh public Discord chat", error);
          liveChatStatus.textContent = error.message || "Public Discord chat is temporarily unavailable.";
        } finally {
          liveChatLoading = false;
        }
      }
      liveChatLauncher.addEventListener("click", () => {
        const isOpen = !liveChatPanel.hidden;
        liveChatPanel.hidden = isOpen;
        liveChatLauncher.setAttribute("aria-expanded", String(!isOpen));
        if (!isOpen) refreshLiveMessages();
      });
      document.getElementById("live-chat-close").addEventListener("click", () => {
        liveChatPanel.hidden = true;
        liveChatLauncher.setAttribute("aria-expanded", "false");
      });
      window.setInterval(() => {
        if (!liveChatPanel.hidden) refreshLiveMessages();
      }, 4000);
    })();
    __SCRIPT__
  </script>
</body>
</html>"""


def _render_public_page(title: str, content: str, script: str = "") -> Response:
    html = (
        PUBLIC_PAGE_TEMPLATE
        .replace("__TITLE__", title)
        .replace("__CONTENT__", content)
        .replace("__SCRIPT__", script)
        .replace("__DISCORD_INVITE__", DISCORD_INVITE)
        .replace("__SUPPORT_EMAIL__", SUPPORT_EMAIL)
    )
    return Response(html, content_type="text/html; charset=utf-8")


def _coming_soon_content(title: str) -> str:
    return f"""
      <section class="hero"><div class="eyebrow">NexTech community</div><h1>{title}</h1><p>This community area is being prepared.</p></section>
      <section class="panel soon-panel"><div class="soon-icon">✦</div><h2>{title} is coming soon</h2><p>We’re still building this space. The community hub, public records, wiki, feedback, and system status are available from the Community menu.</p><div class="cta-row" style="justify-content:center"><a class="cta primary" href="/community">Back to community</a><a class="cta" href="/community/records">View public records</a></div></section>
    """


HOME_PAGE_CONTENT = """
  <section class="home-hero">
    <div class="home-copy">
      <div class="home-active"><span class="live-dot"></span><span>NEXTECH COMMUNITY</span></div>
      <h1>Build. Play. Connect.</h1>
      <p>A place for the NexTech community to meet, explore what we’re building, and get support from our team.</p>
      <div class="cta-row"><a class="cta primary home-join-cta" href="/onboarding">Join NexTech</a><a class="cta" href="/hub">Explore the Hub</a></div>
    </div>
    <div class="home-feature-art" role="img" aria-label="NexTech community artwork">
      <div class="home-art-orbit home-art-orbit-one"></div><div class="home-art-orbit home-art-orbit-two"></div>
      <div class="home-art-glow"></div>
      <div class="home-art-mark" aria-hidden="true">N</div>
      <div class="home-art-caption"><span>WELCOME TO</span><strong>NEXTECH</strong><small>COMMUNITY · CREATIVITY · CONNECTION</small></div>
    </div>
  </section>
  <section class="home-latest">
    <div class="home-latest-heading"><h2>Explore NexTech</h2><a href="/community">All community resources →</a></div>
    <div class="home-latest-grid">
      <a class="home-latest-card" href="/community/reports"><span class="home-section-label">Community safety</span><h3>Report a concern</h3><p>Send a private user or staff conduct report to the NexTech team.</p></a>
      <a class="home-latest-card" href="/knowledge-base"><span class="home-section-label">Help and resources</span><h3>Find an answer</h3><p>Search the NexTech Wiki and published support articles.</p></a>
      <a class="home-latest-card" href="/support/tickets"><span class="home-section-label">Support</span><h3>Track a ticket</h3><p>Check updates for tickets opened on the site or through Discord.</p></a>
    </div>
  </section>
  <section class="home-community-stats" aria-label="NexTech Discord community">
    <article><span>Community members</span><strong id="home-member-count">—</strong></article>
    <article><span>Online on Discord</span><strong id="home-online-count">—</strong></article>
    <a href="/members">Browse member profiles →</a>
  </section>
  <section class="home-sections" aria-label="More NexTech destinations">
    <a class="home-section" href="/team"><span class="home-section-label">The people behind NexTech</span><h2>Staff directory</h2><p>See the public NexTech operations team roster and Discord availability.</p><span class="home-section-arrow">Meet the team →</span></a>
    <a class="home-section" href="/careers#open-roles"><span class="home-section-label">Work with us</span><h2>Careers</h2><p>Open roles are to be announced. Check back for future opportunities.</p><span class="home-section-arrow">See careers →</span></a>
    <a class="home-section" href="/community/appeals"><span class="home-section-label">Account review</span><h2>Appeals</h2><p>Request a review of a NexTech moderation action.</p><span class="home-section-arrow">View appeal options →</span></a>
  </section>
"""


HOME_PAGE_SCRIPT = r"""
    (() => {
      const state = document.getElementById("home-community-state");
      const members = document.getElementById("home-member-count");
      const online = document.getElementById("home-online-count");
      let loading = false;
      async function refreshCommunitySummary() {
        if (loading || document.hidden) return;
        loading = true;
        try {
          const response = await fetch("/api/community/summary", {headers: {"Accept":"application/json"}});
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || "Discord community data unavailable.");
          members.textContent = Number(data.member_count || 0).toLocaleString();
          online.textContent = Number(data.online_count || 0).toLocaleString();
          if (state) {
            state.textContent = data.server_name || "Community active";
            state.parentElement.classList.remove("loading", "unavailable");
          }
        } catch (error) {
          console.error("Could not load NexTech community statistics", error);
          if (state) {
            state.parentElement.classList.remove("loading");
            state.parentElement.classList.add("unavailable");
            state.textContent = "Discord data unavailable";
          }
          members.textContent = "Unavailable";
          online.textContent = "Unavailable";
        } finally {
          loading = false;
        }
      }
      refreshCommunitySummary();
      window.setInterval(refreshCommunitySummary, 30000);
      document.addEventListener("visibilitychange", refreshCommunitySummary);
    })();
"""


LOGIN_PAGE_CONTENT = """
  <section class="auth-layout">
    <a class="auth-brand" href="/"><span class="brand-mark">N</span><span>NexTech · Welcome back</span></a>
    <div class="auth-card">
      <h1>Welcome back</h1>
      <p>Log in to pick up right where you left off.</p>
      <p class="verification-message" id="login-query-notice" role="status" aria-live="polite" hidden></p>
      <form class="auth-form" id="email-login-form" data-auth-endpoint="/api/customer/login">
        <label for="login-email">Email address</label>
        <input id="login-email" name="email" type="email" autocomplete="email" maxlength="200" placeholder="you@example.com" required>
        <div class="auth-password-label"><label for="login-password">Password</label><a href="/support">Need help?</a></div>
        <input id="login-password" name="password" type="password" autocomplete="current-password" maxlength="128" placeholder="Your password" required>
        <div class="auth-form-status" id="login-status" role="status" aria-live="polite"></div>
        <button class="auth-submit" type="submit">Log in</button>
      </form>
      <form class="verification-resend auth-form" id="login-resend-form" data-auth-endpoint="/api/customer/resend-verification" hidden>
        <p>Need another verification link?</p>
        <label for="login-resend-email">Email address</label>
        <input id="login-resend-email" name="email" type="email" autocomplete="email" maxlength="200" required>
        <div class="auth-form-status" id="login-resend-status" role="status" aria-live="polite"></div>
        <button class="auth-submit" type="submit">Resend verification email</button>
      </form>
      <div class="auth-divider" aria-hidden="true">or</div>
      <a class="auth-option" href="/auth/discord/community?next=/settings/integrations"><span class="auth-provider-mark" aria-hidden="true">D</span>Continue with Discord</a>
      <a class="auth-option primary" href="/auth/roblox?next=/dashboard"><span class="auth-provider-mark" aria-hidden="true">R</span>Continue with Roblox</a>
      <p class="auth-note">Roblox signs in to your NexTech member account. Discord connects your community identity and notifications. Need help? Visit <a href="/support">Support Hub</a>.</p>
      <p class="auth-note">NexTech team member? <a href="/staff/login">Staff sign in</a> with your linked Roblox account and the protected NexTech group role.</p>
      <p class="auth-note">New here? <a href="/onboarding">Create an account</a>.</p>
    </div>
  </section>
"""


ONBOARDING_PAGE_CONTENT = """
  <section class="auth-layout onboarding-layout">
    <a class="auth-brand" href="/"><span class="brand-mark">N</span><span>NexTech · Create account</span></a>
    <header class="onboarding-intro"><div class="eyebrow">Your NexTech journey</div><h1>Join the community</h1><p>Start with an account. The rest is up to you.</p></header>
    <ol class="onboarding-steps" aria-label="Onboarding steps">
      <li class="onboarding-step current" aria-current="step"><span class="onboarding-step-number">1</span><span class="onboarding-step-copy"><strong>Account</strong><small>The essentials</small></span><span class="onboarding-step-status">Start here</span></li>
      <li class="onboarding-step"><span class="onboarding-step-number">2</span><span class="onboarding-step-copy"><strong>Email</strong><small>Quick verification</small></span><span class="onboarding-step-status">Next up</span></li>
      <li class="onboarding-step"><span class="onboarding-step-number">3</span><span class="onboarding-step-copy"><strong>Connections</strong><small>Optional, link accounts</small></span><span class="onboarding-step-status">Optional</span></li>
      <li class="onboarding-step"><span class="onboarding-step-number">4</span><span class="onboarding-step-copy"><strong>Profile</strong><small>Make it yours</small></span><span class="onboarding-step-status">Optional</span></li>
      <li class="onboarding-step"><span class="onboarding-step-number">5</span><span class="onboarding-step-copy"><strong>Supporters</strong><small>Totally optional</small></span><span class="onboarding-step-status">Optional</span></li>
    </ol>
    <div class="auth-card">
      <div class="onboarding-current-step"><span>STEP 1 OF 5</span><span>ACCOUNT</span></div>
      <div id="onboarding-signup-content">
        <h2>Account</h2>
        <p>The essentials. Create an account to get started with the NexTech community.</p>
      <form class="auth-form" id="email-register-form" data-auth-endpoint="/api/customer/register">
        <label for="register-username">Username</label>
        <input id="register-username" name="username" type="text" autocomplete="username" maxlength="40" placeholder="Choose a username" required>
        <label for="register-email">Email address</label>
        <input id="register-email" name="email" type="email" autocomplete="email" maxlength="200" placeholder="you@example.com" required>
        <label for="register-password">Password</label>
        <input id="register-password" name="password" type="password" autocomplete="new-password" minlength="6" maxlength="128" placeholder="At least 6 characters" required>
        <label for="register-password-confirm">Confirm password</label>
        <input id="register-password-confirm" name="password_confirmation" type="password" autocomplete="new-password" minlength="6" maxlength="128" placeholder="Enter your password again" required>
        <div class="auth-form-status" id="register-status" role="status" aria-live="polite"></div>
        <button class="auth-submit" type="submit">Create an account</button>
      </form>
        <div class="auth-divider" aria-hidden="true">or sign up with</div>
        <a class="auth-option" href="/auth/discord/community?next=/settings/integrations"><span class="auth-provider-mark" aria-hidden="true">D</span>Continue with Discord</a>
        <a class="auth-option primary" href="/auth/roblox?next=/dashboard"><span class="auth-provider-mark" aria-hidden="true">R</span>Continue with Roblox</a>
      </div>
      <section class="verification-prompt" id="onboarding-verification-prompt" aria-live="polite" hidden>
        <h2>Check your inbox</h2>
        <p>We sent a verification link to <strong id="verification-email"></strong>. Follow that link to activate your account.</p>
        <form class="verification-resend auth-form" id="onboarding-resend-form" data-auth-endpoint="/api/customer/resend-verification">
          <label for="onboarding-resend-email">Email address</label>
          <input id="onboarding-resend-email" name="email" type="email" autocomplete="email" maxlength="200" required>
          <div class="auth-form-status" id="onboarding-resend-status" role="status" aria-live="polite"></div>
          <button class="auth-submit" type="submit">Resend verification email</button>
        </form>
        <p class="auth-note">Already verified? <a href="/login">Log in</a>.</p>
      </section>
      <p class="auth-note">Already have an account? <a href="/login">Log in</a>.</p>
      <p class="auth-note">Need help? Visit the <a href="/support">Support Hub</a>.</p>
    </div>
  </section>
"""


AUTH_PAGE_SCRIPT = r"""
    (() => {
      const showVerificationPrompt = email => {
        const signup = document.getElementById("onboarding-signup-content");
        const prompt = document.getElementById("onboarding-verification-prompt");
        if (!signup || !prompt) return;
        signup.hidden = true;
        prompt.hidden = false;
        document.getElementById("verification-email").textContent = email;
        document.getElementById("onboarding-resend-email").value = email;
        const steps = document.querySelectorAll(".onboarding-step");
        steps[0].classList.remove("current");
        steps[0].removeAttribute("aria-current");
        steps[0].querySelector(".onboarding-step-status").textContent = "Complete";
        steps[1].classList.add("current");
        steps[1].setAttribute("aria-current", "step");
        steps[1].querySelector(".onboarding-step-status").textContent = "Check email";
        document.querySelector(".onboarding-current-step").innerHTML = "<span>STEP 2 OF 5</span><span>EMAIL</span>";
        prompt.scrollIntoView({behavior: "smooth", block: "center"});
      };

      const emailVerificationNotice = document.getElementById("login-query-notice");
      if (emailVerificationNotice) {
        const verificationResult = new URLSearchParams(location.search).get("email_verification");
        if (verificationResult === "success") {
          emailVerificationNotice.textContent = "Email verified. You can now log in.";
          emailVerificationNotice.hidden = false;
          emailVerificationNotice.classList.add("success");
        } else if (verificationResult === "invalid") {
          emailVerificationNotice.textContent = "That verification link is invalid, expired, or already used. Request a new one below.";
          emailVerificationNotice.hidden = false;
        }
      }

      for (const form of document.querySelectorAll(".auth-form[data-auth-endpoint]:not(.verification-resend)")) {
        const status = document.getElementById(form.id === "email-login-form" ? "login-status" : "register-status");
        form.addEventListener("submit", async event => {
          event.preventDefault();
          status.textContent = "";
          const submit = form.querySelector('button[type="submit"]');
          const originalLabel = submit.textContent;
          submit.disabled = true;
          submit.textContent = form.id === "email-login-form" ? "Logging in…" : "Creating account…";
          const fields = new FormData(form);
          const password = String(fields.get("password") || "");
          if (form.id === "email-register-form" && password !== String(fields.get("password_confirmation") || "")) {
            status.textContent = "The passwords do not match.";
            submit.disabled = false;
            submit.textContent = originalLabel;
            return;
          }
          const payload = {email: String(fields.get("email") || ""), password};
          if (form.id === "email-register-form") payload.username = String(fields.get("username") || "");
          try {
            const response = await fetch(form.dataset.authEndpoint, {
              method: "POST",
              headers: {"Content-Type": "application/json", "Accept": "application/json"},
              body: JSON.stringify(payload)
            });
            const data = await response.json();
            if (form.id === "email-register-form" && data.account_created && data.verification_required) {
              showVerificationPrompt(payload.email);
              const resendStatus = document.getElementById("onboarding-resend-status");
              resendStatus.textContent = data.error || "Your account was created. Resend the verification email when delivery is available.";
              throw new Error(data.error || "Your account was created. Resend the verification email when delivery is available.");
            }
            if (form.id === "email-register-form" && data.verification_required) {
              showVerificationPrompt(payload.email);
              return;
            }
            if (form.id === "email-login-form" && data.verification_required) {
              const resendForm = document.getElementById("login-resend-form");
              resendForm.hidden = false;
              document.getElementById("login-resend-email").value = payload.email;
              throw new Error(data.error || "Please verify your email before logging in.");
            }
            if (!response.ok) throw new Error(data.error || "Account sign-in failed. Please try again.");
            if (typeof data.token !== "string" || !data.token) throw new Error("Sign-in succeeded without a session token. Contact support.");
            localStorage.setItem("nx_cust_token", data.token);
            if (typeof data.username === "string") localStorage.setItem("nx_cust_name", data.username);
            const requestedPath = new URLSearchParams(location.search).get("next") || "/profile";
            const target = new URL(requestedPath, location.origin);
            location.assign(target.origin === location.origin ? target.href : "/profile");
          } catch (error) {
            console.error("NexTech email sign-in failed", error);
            status.textContent = error.message || "Account sign-in failed. Please try again.";
            submit.disabled = false;
            submit.textContent = originalLabel;
          }
        });
      }

      for (const form of document.querySelectorAll(".verification-resend")) {
        form.addEventListener("submit", async event => {
          event.preventDefault();
          const status = document.getElementById(form.id === "login-resend-form" ? "login-resend-status" : "onboarding-resend-status");
          const submit = form.querySelector('button[type="submit"]');
          const originalLabel = submit.textContent;
          submit.disabled = true;
          submit.textContent = "Sending…";
          status.textContent = "";
          const email = String(new FormData(form).get("email") || "");
          try {
            const response = await fetch(form.dataset.authEndpoint, {
              method: "POST",
              headers: {"Content-Type": "application/json", "Accept": "application/json"},
              body: JSON.stringify({email})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "Could not resend the verification email.");
            status.textContent = data.message || "A new verification link has been sent.";
            status.classList.add("success");
          } catch (error) {
            console.error("NexTech verification email resend failed", error);
            status.textContent = error.message || "Could not resend the verification email.";
            status.classList.remove("success");
          } finally {
            submit.disabled = false;
            submit.textContent = originalLabel;
          }
        });
      }
    })();
"""


HUB_PAGE_CONTENT = """
  <section class="hero"><div class="eyebrow">Your NexTech starting point</div><h1>Hub</h1><p>Quick access to the community, member support, published resources, and the NexTech team.</p></section>
  <section class="community-grid" aria-label="Hub destinations">
    <a class="feature-card" href="/community"><div class="feature-icon">◉</div><h2>Community</h2><p>Explore member spaces and public community information.</p></a>
    <a class="feature-card" href="/support/tickets"><div class="feature-icon">▤</div><h2>My support tickets</h2><p>See your open and resolved tickets from NexTech’s shared support records.</p></a>
    <a class="feature-card" href="/community/reports"><div class="feature-icon">⚑</div><h2>Report center</h2><p>Submit a safety or conduct concern to the NexTech staff team.</p></a>
    <a class="feature-card" href="/knowledge-base"><div class="feature-icon">▧</div><h2>Knowledge base</h2><p>Search help articles and bot commands.</p></a>
    <a class="feature-card" href="/team"><div class="feature-icon">♙</div><h2>Staff directory</h2><p>Find the public operations staff roster.</p></a>
    <a class="feature-card" href="/settings/account"><div class="feature-icon">⚙</div><h2>Account settings</h2><p>Review account, appearance, privacy, integrations, and purchase settings.</p></a>
  </section>
"""


CAREERS_PAGE_CONTENT = """
  <section class="hero"><div class="eyebrow">Join NexTech</div><h1>Careers</h1><p>We’ll share opportunities here when roles are ready to be announced.</p></section>
  <section class="panel soon-panel" id="open-roles"><div class="soon-icon">＋</div><h2>Open roles</h2><p><strong>TBA — To be announced.</strong> There are no open roles posted right now. Check this page again later for updates.</p><div class="cta-row" style="justify-content:center"><a class="cta primary" href="/team">Meet the team</a><a class="cta" href="/community">Visit the community</a></div></section>
"""

POLICIES_PAGE_CONTENT = """
  <section class="hero"><div class="eyebrow">NexTech guidance</div><h1>Policies</h1><p>Find community guidance and manage your account privacy. Contact the NexTech team if you need help understanding a policy.</p></section>
  <section class="community-grid" aria-label="NexTech policy resources">
    <a class="feature-card" href="/community/osp"><div class="feature-icon">♡</div><h2>Online safety</h2><p>Safety guidance and ways to contact verified NexTech staff.</p></a>
    <a class="feature-card" href="/settings/privacy"><div class="feature-icon">⚙</div><h2>Account privacy</h2><p>Review the available privacy and account settings.</p></a>
    <a class="feature-card" href="/community/reports"><div class="feature-icon">⚑</div><h2>Report a concern</h2><p>Send a private report to the NexTech team for review.</p></a>
  </section>
  <section class="panel" style="margin-top:14px"><h2>Policy documents</h2><p>Additional formal policy documents will be published here when they are ready. For questions, contact the team through the <a href="/support">Support Hub</a>.</p></section>
"""


TEAM_PAGE_CONTENT = """
  <section class="hero"><div class="eyebrow">People behind NexTech</div><h1>Staff directory</h1><p>Public roster of NexTech operations, moderation, and support staff. Team titles, official verification marks, optional public biographies, and team join dates are managed by authorized NexTech administrators.</p></section>
  <section class="panel">
    <div class="member-directory-tools"><span id="team-count">Loading staff roster…</span></div>
    <div class="member-grid" id="team-grid" aria-live="polite"><div class="records-empty">Connecting to the NexTech Discord server…</div></div>
  </section>
  <section class="verification-guide" aria-labelledby="verification-title">
    <div class="eyebrow">Official identification system</div><h2 id="verification-title">NexTech verification marks</h2>
    <p>NexTech uses verification marks to help community members, clients, and team members identify verified personnel, departments, roles, and applicable levels of authorization within NexTech Interactive Development LLC.</p>
    <div class="verification-key">
      <article><span class="verification-mark mark-green">●</span><div><strong>Green check · Whitelisted Group Member</strong><p>Approved group membership; does not provide staff authority.</p></div></article>
      <article><span class="verification-mark mark-blue">●</span><div><strong>Blue check · Dedicated Member Supporter</strong><p>Approved to assist with designated community-support activities.</p></div></article>
      <article><span class="verification-mark mark-yellow">●</span><div><strong>Yellow mark · Staff Volunteer</strong><p>Volunteer assistance with assigned community duties. No automatic moderation, investigative, administrative, legal, or executive authority.</p></div></article>
      <article><span class="verification-mark mark-orange">✔</span><div><strong>Orange check · Community Moderation Staff</strong><p>Moderator, Senior Moderator, or Community Manager; authority depends on appointment and clearance.</p></div></article>
      <article><span class="verification-mark mark-silver">✔</span><div><strong>Silver check · Verified Hybrid &amp; Virtual Operations Staff</strong><p>Personnel assigned to NexTech Hybrid &amp; Virtual Operations.</p></div></article>
      <article><span class="verification-mark mark-purple">✔</span><div><strong>Purple check · Director or Chief-Level Staff</strong><p>Verified leadership with expanded authority based on appointment.</p></div></article>
      <article><span class="verification-mark mark-deep-blue">✔</span><div><strong>Deep blue check · Owner</strong><p>Highest organizational designation for NexTech Interactive Development LLC.</p></div></article>
      <article><span class="verification-mark mark-black">●</span><div><strong>Black mark · Suspended, Terminated, or Blacklisted</strong><p>The person or account is not currently recognized as an authorized NexTech representative.</p></div></article>
      <article><span class="verification-mark mark-formal-staff">✔</span><div><strong>Red/yellow mark · Formal Staff</strong><p>Identifies formal staff where a more specific designation is not published.</p></div></article>
    </div>
    <div class="verification-guidance">
      <h3>A mark identifies status; it does not grant system access</h3>
      <p>Actual authority is determined by the assigned role, clearance, permissions, and duties. Verification status can change after role or clearance changes, review, leave, inactivity, loss of authorization, suspension, termination, or formal restoration. Only authorized NexTech administrators can change official marks, roles, ranks, and clearance; editing a personal bio or profile does not change verification.</p>
      <h3>Account information and privacy</h3>
      <p>Where authorized, NexTech systems may maintain warnings, in-game moderation records, account and good-standing status, staff status, clearance (20, 30, 90, or 200+ where applicable), age, team join date, tier/rank, linked-account type, and biography. These details are not made public by a verification mark. Sensitive investigative, legal, security, personnel, age, and administrative records remain restricted to specifically authorized staff. Verification history may be recorded for authorized review.</p>
    </div>
  </section>
"""


TEAM_PAGE_SCRIPT = r"""
    (() => {
      const grid = document.getElementById("team-grid");
      const count = document.getElementById("team-count");
      const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
      let loading = false;
      async function load() {
        if (loading || document.hidden) return;
        loading = true;
        try {
          const response = await fetch("/api/community/team", {headers: {"Accept":"application/json"}});
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || "Staff roster could not be loaded.");
          const members = Array.isArray(data.members) ? data.members : [];
          count.textContent = `${members.length} public staff profile${members.length === 1 ? "" : "s"}`;
          grid.innerHTML = members.length ? members.map(member => `<a class="member-card" href="/members/${encodeURIComponent(member.id)}"><img class="member-photo" src="${esc(member.avatar)}" alt="" loading="lazy"><div><div class="member-name">${esc(member.display_name)} <span class="verification-mark mark-${esc(member.verification_mark||"formal_staff")}" title="${esc(member.verification_label||"Formal NexTech Staff")}" aria-label="${esc(member.verification_label||"Formal NexTech Staff")}">${esc(member.verification_icon||"✔")}</span></div><div class="member-status">${esc(member.role)} · ${member.online ? "Online" : "Offline"}</div>${member.bio?`<p class="team-bio">${esc(member.bio)}</p>`:""}${member.team_join_date?`<small class="team-joined">Team since ${esc(member.team_join_date)}</small>`:""}</div></a>`).join("") : '<div class="records-empty">No public staff profiles are available right now.</div>';
        } catch (error) {
          console.error("Could not load the public staff directory", error);
          count.textContent = "Staff roster unavailable";
          grid.innerHTML = `<div class="records-empty">${esc(error.message || "Staff roster unavailable.")} Please try again later.</div>`;
        } finally {
          loading = false;
        }
      }
      load();
      window.setInterval(load, 30000);
      document.addEventListener("visibilitychange", load);
    })();
"""


COMMUNITY_APPLY_PAGE_CONTENT = """
  <section class="hero"><div class="eyebrow">Community opportunities</div><h1>Applications</h1><p>Applications are not open yet. We’ll post details here when the next opportunity is announced.</p></section>
  <section class="panel soon-panel"><div class="soon-icon">＋</div><h2>Coming soon</h2><p>Community applications are being prepared. Until then, visit the community and keep an eye on official announcements.</p><div class="cta-row" style="justify-content:center"><a class="cta primary" href="/community">Community hub</a><a class="cta" href="/team">Staff directory</a></div></section>
"""


OSP_PAGE_CONTENT = """
  <section class="hero"><div class="eyebrow">Community safety</div><h1>Online safety resources</h1><p>Find the right channel for a concern, learn how reports are handled, and contact NexTech staff.</p></section>
  <section class="community-grid" aria-label="Online safety resources">
    <a class="feature-card" href="/community/reports"><div class="feature-icon">⚑</div><h2>Report a concern</h2><p>Send a safety or conduct concern privately to staff.</p></a>
    <a class="feature-card" href="/community/staff-abuse"><div class="feature-icon">⚠</div><h2>Staff misconduct</h2><p>Use a dedicated route for concerns about staff conduct or permissions.</p></a>
    <a class="feature-card" href="/community/appeals"><div class="feature-icon">↗</div><h2>Appeal a moderation action</h2><p>Ask staff to review a moderation decision.</p></a>
  </section>
"""


REPORT_TRACKING_PAGE_CONTENT = """
  <section class="hero">
    <div class="eyebrow">Account activity</div>
    <h1>Track reports &amp; appeals</h1>
    <p>Follow the staff review stages for submissions made while signed in to your NexTech account.</p>
    <div class="cta-row">
      <a class="cta primary" href="/community/reports">Submit a report</a>
      <a class="cta" href="/community/appeals">Submit an appeal</a>
      <button class="cta" id="report-tracker-refresh" type="button">Refresh status</button>
    </div>
  </section>
  <section class="panel tracker-signin" id="report-tracker-signin">
    <h2>Sign in to track your submissions</h2>
    <p>Only reports and appeals submitted from your linked account are shown here.</p>
    <a class="cta primary" href="/login?next=%2Fcommunity%2Fsubmissions">Sign in</a>
  </section>
  <section class="report-tracker-content" id="report-tracker-content" hidden>
    <div class="tracker-notice" id="report-tracker-notice" role="status" aria-live="polite"></div>
    <section class="tracker-section">
      <div class="tracker-section-heading"><div><span class="eyebrow">Your account</span><h2>My reports &amp; appeals</h2></div></div>
      <div class="tracker-list" id="report-tracker-list"></div>
    </section>
    <section class="tracker-section" id="report-tracker-staff" hidden>
      <div class="tracker-section-heading"><div><span class="eyebrow">Staff workspace</span><h2>Review queue</h2><p>Public updates entered here are visible to the person who submitted the case.</p></div></div>
      <div class="tracker-list" id="report-tracker-staff-list"></div>
    </section>
  </section>
"""


REPORT_TRACKING_PAGE_SCRIPT = r"""
    (() => {
      const token = localStorage.getItem("nx_cust_token");
      const signIn = document.getElementById("report-tracker-signin");
      const content = document.getElementById("report-tracker-content");
      const notice = document.getElementById("report-tracker-notice");
      const mine = document.getElementById("report-tracker-list");
      const staffSection = document.getElementById("report-tracker-staff");
      const staffList = document.getElementById("report-tracker-staff-list");
      const stages = [
        ["intake", "Completed intake"],
        ["under_review", "Under review by staff"],
        ["outcome", "Outcome shared"],
        ["closed", "Closed"]
      ];
      if (!token) return;
      signIn.hidden = true;
      content.hidden = false;

      const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({
        "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"
      }[char]));
      const dateLabel = value => {
        const date = new Date(Number(value || 0) * 1000);
        return Number.isNaN(date.getTime()) ? "Date unavailable" : date.toLocaleString();
      };
      const api = async (path, options = {}) => {
        const response = await fetch(path, {
          method: options.method || "GET",
          headers: {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Customer-Token": token
          },
          body: options.body ? JSON.stringify(options.body) : undefined
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || "Could not load report tracking.");
        return data;
      };
      const renderEmpty = (target, text) => {
        target.innerHTML = `<div class="tracker-empty"><strong>${escapeHtml(text)}</strong><p>New submissions will appear here after intake is complete.</p></div>`;
      };
      const renderCases = (target, reports, staffMode = false) => {
        if (!reports.length) {
          renderEmpty(target, staffMode ? "No reports or appeals are waiting for review." : "No reports or appeals yet.");
          return;
        }
        target.innerHTML = reports.map(report => {
          const active = Math.max(0, stages.findIndex(([key]) => key === report.status));
          const stepMarkup = stages.map(([key, label], index) =>
            `<li class="${index < active ? "complete" : index === active ? "active" : ""}"><span class="tracker-step-dot"></span><span>${label}</span></li>`
          ).join("");
          const updates = (report.timeline || []).map(item =>
            `<li><span class="tracker-update-label">${escapeHtml(item.label || "Status update")}</span><time>${escapeHtml(dateLabel(item.created_at))}</time><p>${escapeHtml(item.message || "")}</p></li>`
          ).join("");
          const canUpdate = staffMode && report.status !== "closed";
          const form = canUpdate ? `
            <form class="tracker-staff-form" data-report-id="${escapeHtml(report.report_id)}">
              <label>Set stage
                <select name="status">
                  <option value="under_review" ${report.status === "under_review" ? "selected" : ""}>Under review by staff</option>
                  <option value="outcome" ${report.status === "outcome" ? "selected" : ""}>Outcome shared</option>
                  <option value="closed">Closed</option>
                </select>
              </label>
              <label>Public update <span>Visible to the submitter</span>
                <textarea name="public_update" maxlength="1000" placeholder="Share the outcome or a short progress update"></textarea>
              </label>
              <button class="cta primary" type="submit">Save status</button>
              <div class="tracker-form-status" role="status" aria-live="polite"></div>
            </form>` : "";
          return `<article class="tracker-card">
            <header class="tracker-card-header">
              <div><span class="tracker-reference">${escapeHtml(report.report_id)}</span><p>${escapeHtml(report.category)}${report.target ? ` · ${escapeHtml(report.target)}` : ""}</p></div>
              <span class="tracker-status-badge">${escapeHtml(report.status_label || "Under review by staff")}</span>
            </header>
            <ol class="tracker-steps" aria-label="Submission progress">${stepMarkup}</ol>
            <div class="tracker-dates"><span>Submitted ${escapeHtml(dateLabel(report.created_at))}</span><span>Updated ${escapeHtml(dateLabel(report.updated_at))}</span></div>
            <ol class="tracker-timeline" aria-label="Status history">${updates}</ol>
            ${form}
          </article>`;
        }).join("");
        target.querySelectorAll(".tracker-staff-form").forEach(formEl => {
          formEl.addEventListener("submit", async event => {
            event.preventDefault();
            const button = formEl.querySelector('button[type="submit"]');
            const message = formEl.querySelector('[role="status"]');
            button.disabled = true;
            message.textContent = "Saving…";
            try {
              await api(`/api/dashboard/staff/report-submissions/${encodeURIComponent(formEl.dataset.reportId)}/status`, {
                method: "POST",
                body: {
                  status: formEl.elements.status.value,
                  public_update: formEl.elements.public_update.value.trim()
                }
              });
              message.textContent = "Status updated.";
              await loadAll();
            } catch (error) {
              message.textContent = error.message;
              button.disabled = false;
            }
          });
        });
      };
      const loadAll = async () => {
        notice.textContent = "Loading submissions…";
        try {
          const [me, mineData] = await Promise.all([
            api("/api/dashboard/me"),
            api("/api/dashboard/report-submissions")
          ]);
          renderCases(mine, mineData.reports || []);
          if (me.actor && me.actor.is_staff) {
            const staffData = await api("/api/dashboard/staff/report-submissions");
            staffSection.hidden = false;
            renderCases(staffList, staffData.reports || [], true);
          } else {
            staffSection.hidden = true;
          }
          notice.textContent = "";
        } catch (error) {
          notice.textContent = error.message;
          if (/sign in|expired|authenticate/i.test(error.message)) {
            signIn.hidden = false;
            content.hidden = true;
          }
        }
      };
      document.getElementById("report-tracker-refresh").addEventListener("click", loadAll);
      loadAll();
    })();
"""


def _report_page_content(category: str, title: str, description: str) -> str:
    return f"""
      <section class="hero"><div class="eyebrow">Staff review portal</div><h1>{title}</h1><p>{description}</p></section>
      <section class="panel">
        <form class="report-form" id="community-report-form" data-category="{category}">
          <label>Person, account, or case ID<input name="target" maxlength="160" autocomplete="off" required placeholder="Username, Discord ID, or case number"></label>
          <label>What happened?<textarea name="details" maxlength="3000" minlength="10" required placeholder="Include dates and relevant context. Do not include passwords or account recovery codes."></textarea></label>
          <small>Submissions are delivered to the configured Discord staff-review channel and include the signed-in account. This form is not anonymous. Do not include passwords or recovery codes. For appeals or staff conduct complaints, include any available case reference.</small>
          <div class="cta-row"><button class="cta primary" type="submit">Submit to NexTech staff</button><a class="cta" href="/login">Sign in</a><a class="cta" href="/community/submissions">Track a report or appeal</a></div>
          <div id="report-status" role="status" aria-live="polite"></div>
        </form>
      </section>
    """


REPORT_PAGE_SCRIPT = r"""
    (() => {
      const form = document.getElementById("community-report-form");
      const status = document.getElementById("report-status");
      const button = form.querySelector('button[type="submit"]');
      form.addEventListener("submit", async event => {
        event.preventDefault();
        const token = localStorage.getItem("nx_cust_token");
        if (!token) {
          status.innerHTML = 'Sign in with your linked NexTech account before submitting. <a href="/login">Log in</a>.';
          return;
        }
        button.disabled = true;
        status.textContent = "Sending your submission to the staff channel…";
        try {
          const response = await fetch("/api/dashboard/reports", {
            method: "POST",
            headers: {"Content-Type":"application/json","X-Customer-Token":token,"Accept":"application/json"},
            body: JSON.stringify({
              category: form.dataset.category,
              target: form.elements.target.value.trim(),
              details: form.elements.details.value.trim()
            })
          });
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || "Your report could not be submitted.");
          form.reset();
          status.textContent = `Submission ${data.report_id || ""} was sent to the NexTech staff channel.`;
        } catch (error) {
          console.error("Could not submit the community report", error);
          status.textContent = error.message || "Your report could not be submitted. Try again or contact staff through Discord.";
        } finally {
          button.disabled = false;
        }
      });
    })();
"""


SUPPORT_TICKETS_PAGE_CONTENT = """
  <section class="inbox-heading"><div><div class="eyebrow">NexTech Support</div><h1>Support tickets</h1><p>Tickets created on the site and in NexTech Discord are listed together. This page refreshes automatically.</p></div><a class="cta primary" href="/support">Contact support</a></section>
  <section class="panel">
    <div class="inbox-title"><h2>Your cases</h2><span id="support-ticket-count">—</span></div>
    <div class="inbox-tools"><input class="wiki-search" id="support-ticket-search" type="search" placeholder="Search cases…" aria-label="Search support tickets"><select class="wiki-search" id="support-ticket-filter" aria-label="Filter support tickets"><option value="all">All statuses</option><option value="open">Open</option><option value="closed">Closed</option></select></div>
    <div id="support-ticket-list"><div class="records-empty">Loading your support tickets…</div></div>
    <div id="support-ticket-status" role="status" class="settings-note">Waiting for account sign-in.</div>
  </section>
"""


SUPPORT_TICKETS_PAGE_SCRIPT = r"""
    (() => {
      const list = document.getElementById("support-ticket-list");
      const statusLine = document.getElementById("support-ticket-status");
      const count = document.getElementById("support-ticket-count");
      const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
      let tickets = [];
      let loading = false;
      function render() {
        const term = document.getElementById("support-ticket-search").value.trim().toLowerCase();
        const filter = document.getElementById("support-ticket-filter").value;
        const visible = tickets.filter(ticket => {
          const closed = ["closed", "resolved", "deleted"].includes(String(ticket.status || "").toLowerCase());
          return (filter === "all" || (filter === "closed" ? closed : !closed)) &&
            `${ticket.case_id || ""} ${ticket.case_type || ""} ${ticket.reason || ""}`.toLowerCase().includes(term);
        });
        count.textContent = `${visible.length} case${visible.length === 1 ? "" : "s"}`;
        list.innerHTML = visible.length ? visible.map(ticket => {
          const label = ticket.case_type || "Support case";
          const subject = ticket.reason || label;
          const caseId = ticket.case_id ? `Case ${ticket.case_id}` : "Support case";
          const opened = ticket.opened_date || "";
          return `<article class="inbox-message"><span class="inbox-message-icon">?</span><div class="inbox-message-body"><strong>${esc(subject)}</strong><span>${esc(caseId)} · ${esc(label)}${opened ? ` · ${esc(opened)}` : ""} · Synced case</span></div><span class="inbox-status">${esc(ticket.status || "open")}</span><a class="cta" href="/dashboard">Open dashboard</a></article>`;
        }).join("") : '<div class="inbox-empty"><strong>No matching tickets</strong><p>Cases connected to your account will appear here after you contact NexTech support.</p><a class="cta" href="/support">Visit Support Hub</a></div>';
      }
      async function refresh() {
        if (loading || document.hidden) return;
        const token = localStorage.getItem("nx_cust_token");
        if (!token) {
          list.innerHTML = '<div class="inbox-empty"><strong>Sign in to see your tickets</strong><p>Only tickets connected to your NexTech account are shown here.</p><a class="cta primary" href="/login">Log in</a></div>';
          count.textContent = "0 cases";
          statusLine.textContent = "Sign in to sync your website and Discord support tickets.";
          return;
        }
        loading = true;
        try {
          const response = await fetch("/api/dashboard/tickets", {headers: {"X-Customer-Token":token,"Accept":"application/json"}});
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || "Tickets could not be loaded.");
          tickets = Array.isArray(data.tickets) ? data.tickets : [];
          render();
          statusLine.textContent = `Synced from NexTech support records · refreshed ${new Date().toLocaleTimeString()}`;
        } catch (error) {
          console.error("Could not refresh the support ticket list", error);
          statusLine.textContent = error.message || "Support ticket updates are temporarily unavailable.";
        } finally {
          loading = false;
        }
      }
      document.getElementById("support-ticket-search").addEventListener("input", render);
      document.getElementById("support-ticket-filter").addEventListener("change", render);
      refresh();
      window.setInterval(refresh, 20000);
      document.addEventListener("visibilitychange", refresh);
    })();
"""


SUPPORT_PAGE_CONTENT = """
  <section class="hero"><div class="eyebrow">NexTech help</div><h1>Support Hub</h1><p>Chat with NexVisions, the NexTech support bot. Ask about sign-in, community resources, reports, or support hours.</p></section>
  <section class="support-chat panel" aria-label="Chat with NexVisions">
    <div class="support-chat-heading"><div class="support-avatar" aria-hidden="true">N</div><div><h2>NexVisions</h2><p>NexTech Support Bot</p></div><span class="support-online"><span class="status-dot"></span>Online</span></div>
    <div class="support-messages" id="support-messages" role="log" aria-live="polite" aria-relevant="additions text"><div class="chat-message bot-message"><strong>NexVisions</strong><p>Hi! How can I help you today? Choose a suggested question or type a message below.</p></div></div>
    <div class="support-suggestions" aria-label="Suggested questions">
      <button type="button" class="support-suggestion">How do I sign in?</button>
      <button type="button" class="support-suggestion">How do I report a user?</button>
      <button type="button" class="support-suggestion">What are your support hours?</button>
      <button type="button" class="support-suggestion">How do I join Discord?</button>
    </div>
    <form class="support-compose" id="support-form"><label class="sr-only" for="support-input">Message NexVisions</label><input id="support-input" name="message" type="text" maxlength="1000" autocomplete="off" placeholder="Message NexVisions..." required><button type="submit" id="support-send">Send</button></form>
    <p class="support-disclaimer" id="support-status" role="status">NexVisions uses answers configured by the NexTech team. For more help, <a href="/report-user">contact staff or report a user</a>.</p>
  </section>
"""


SUPPORT_PAGE_SCRIPT = r"""
    (() => {
      const form = document.getElementById("support-form");
      const input = document.getElementById("support-input");
      const messages = document.getElementById("support-messages");
      const send = document.getElementById("support-send");
      const status = document.getElementById("support-status");
      function addMessage(name, text, kind) {
        const article = document.createElement("div");
        article.className = `chat-message ${kind}-message`;
        const label = document.createElement("strong");
        label.textContent = name;
        const paragraph = document.createElement("p");
        paragraph.textContent = text;
        article.append(label, paragraph);
        messages.append(article);
        messages.scrollTop = messages.scrollHeight;
      }
      async function ask(message) {
        const question = message.trim();
        if (!question || send.disabled) return;
        addMessage("You", question, "user");
        input.value = "";
        send.disabled = true;
        status.textContent = "NexVisions is replying...";
        try {
          const response = await fetch("/api/support/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json", "Accept": "application/json" },
            body: JSON.stringify({ message: question })
          });
          const result = await response.json();
          if (!response.ok) throw new Error(result.error || "Support is temporarily unavailable.");
          addMessage("NexVisions", result.reply, "bot");
          status.textContent = "Need help from a person? Visit Report user to contact the NexTech staff team.";
        } catch (error) {
          addMessage("NexVisions", error.message || "I couldn't send your message. Please try again.", "bot");
          status.textContent = "Your message could not be sent. Please try again or contact staff on the Report user page.";
        } finally {
          send.disabled = false;
          input.focus();
        }
      }
      form.addEventListener("submit", event => { event.preventDefault(); ask(input.value); });
      document.querySelectorAll(".support-suggestion").forEach(button => button.addEventListener("click", () => ask(button.textContent)));
    })();
"""


REPORT_USER_PAGE_CONTENT = """
  <section class="hero"><div class="eyebrow">Community safety</div><h1>Report a user</h1><p>If you have a safety concern or need to report a member, contact our verified operations staff directly. Please share relevant details, but never send passwords or private account credentials.</p></section>
  <section class="community-grid" aria-label="Report a user contact options">
    <article class="feature-card"><div class="feature-icon">Discord</div><h2>Contact us on Discord</h2><p>Join our official Discord and contact the <strong>Verified Hybrid &amp; Virtual Operations Staff</strong>. You can ping this role to request help with a report.</p><div class="cta-row"><a class="cta primary" href="__DISCORD_INVITE__" target="_blank" rel="noopener">Open NexTech Discord</a></div></article>
    <article class="feature-card"><div class="feature-icon">Email</div><h2>Email our staff</h2><p>Send your report to <a href="mailto:__SUPPORT_EMAIL__">__SUPPORT_EMAIL__</a>. Include the username, what happened, and any relevant context.</p><div class="cta-row"><a class="cta" href="mailto:__SUPPORT_EMAIL__?subject=NexTech%20user%20report">Email NexTech staff</a></div></article>
    <article class="feature-card"><div class="feature-icon">Hours</div><h2>Staff availability</h2><p>Our team is available Monday-Friday:</p><p><strong>7:30 AM-6:30 PM Pacific Time (PT)</strong><br><strong>6:30 PM-7:30 PM Australian Eastern Time (AET)</strong></p><p>You can still email us outside these hours; our team will respond when available.</p></article>
  </section>
  <section class="cta-row"><a class="cta" href="/support">Back to Support Hub</a><a class="cta" href="/community">Back to Community</a></section>
"""


SETTINGS_GROUPS = (
    ("Profile", (
        ("account", "Account"),
        ("customize", "Customize profile"),
        ("appearance", "Appearance"),
        ("verification", "Verification"),
        ("trusted-member", "Trusted member"),
    )),
    ("Security", (
        ("confirm-password", "Confirm password"),
    )),
    ("Privacy & notifications", (
        ("privacy", "Privacy & data"),
        ("notifications", "Notifications"),
        ("calls", "Calls"),
        ("ads", "Ad preferences"),
        ("standing", "Account standing"),
    )),
    ("Billing & purchases", (
        ("membership", "Membership"),
        ("subscription", "Subscription"),
        ("billing", "Billing"),
        ("credit", "Credit"),
        ("points", "Points"),
        ("orders", "Orders"),
        ("payment-methods", "Payment methods"),
        ("discounts", "Discounts"),
        ("licenses", "Licenses"),
    )),
    ("Connections", (
        ("integrations", "Integrations"),
        ("rewards", "Rewards"),
        ("advertising", "Advertising"),
    )),
)

SETTINGS_PAGE_INFO = {
    slug: (label, "")
    for group, items in SETTINGS_GROUPS
    for slug, label in items
    if slug != "confirm-password"
}
SETTINGS_PAGE_INFO.update({
    "account": ("Account", "Manage the profile and account information connected to NexTech."),
    "customize": ("Customize profile", "Review your NexTech profile and connected account."),
    "appearance": ("Appearance", "Review appearance preferences for this browser."),
    "verification": ("Verification", "Review the account verification available to your sign-in provider."),
    "trusted-member": ("Trusted member", "Review NexTech's trusted-member information and account verification."),
    "privacy": ("Privacy & data", "Review privacy and account data information."),
    "notifications": ("Notifications", "Review notification options for your account."),
    "calls": ("Calls", "NexTech calling preferences are not configured for this account."),
    "ads": ("Ad preferences", "NexTech advertising preferences are not configured for this account."),
    "standing": ("Account standing", "Review moderation records associated with the community."),
    "membership": ("Membership", "Review NexTech membership and product information."),
    "subscription": ("Subscription", "Review subscription information connected to NexTech."),
    "billing": ("Billing", "Billing records are available through the original purchase provider."),
    "credit": ("Credit", "NexTech account credit is not available in this site."),
    "points": ("Points", "NexTech account points are not available in this site."),
    "orders": ("Orders", "Review products currently available in the NexTech marketplace."),
    "payment-methods": ("Payment methods", "Payment methods are managed by the original purchase provider."),
    "discounts": ("Discounts", "Check NexTech marketplace products for current offers."),
    "licenses": ("Licenses", "NexTech license records are not configured for this account."),
    "integrations": ("Integrations", "Review connected NexTech account providers."),
    "rewards": ("Rewards", "NexTech rewards are not configured for this account."),
    "advertising": ("Advertising", "NexTech advertising settings are not configured for this account."),
})

SETTINGS_DETAIL_CARDS = {
    "account": (
        ("Sign-in and recovery", "Your sign-in method is shown above. Email accounts can request a password reset link; Roblox sign-in is managed by Roblox.", "/password/forgot", "Request a password reset"),
        ("Confirm your password", "Sign in again to re-authenticate your account before returning to account settings.", "/confirm-password", "Confirm account access"),
        ("Connected identities", "Roblox and Discord connections are shown separately. A linked identity is not the same as a NexTech staff role or permission.", "/settings/integrations", "Review integrations"),
        ("Account help", "For account access, data corrections, or account-removal requests, contact NexTech Support. Never send your password or verification link to staff.", "/support", "Contact Support"),
    ),
    "customize": (
        ("Profile details", "Your display name and avatar are supplied by your linked sign-in provider. Profile customization controls are not available in this portal yet.", "/profile", "View your profile"),
        ("Official status", "Profile edits do not change verification marks, staff roles, or permissions. Those are assigned by authorized NexTech administrators.", "/team", "Read the verification guide"),
    ),
    "appearance": (
        ("Current site appearance", "The NexTech portal currently uses its dark appearance across pages. Per-account theme selection and synchronized appearance preferences are not available yet.", None, None),
        ("Accessibility", "You can use your browser or operating-system zoom and contrast settings. The portal does not store those device settings in your account.", None, None),
    ),
    "verification": (
        ("Email verification", "Email and password accounts must verify their email before signing in. If you have not received a verification message, return to sign-in and request another link.", "/login", "Go to sign-in"),
        ("Roblox and Discord links", "Dashboard access checks the linked Discord and Roblox identities against the active NexTech Hub link. Verification marks identify status; they do not grant access by themselves.", "/settings/integrations", "Review integrations"),
        ("Staff verification", "Official marks, staff titles, and clearances are managed by authorized NexTech administrators. Contact Support if you believe your displayed status is incorrect.", "/support", "Contact Support"),
    ),
    "trusted-member": (
        ("Trusted-member status", "Trusted-member status is not currently managed through a self-service setting. Official NexTech roles, marks, and permissions are assigned by authorized administrators.", "/settings/verification", "Review verification"),
        ("Account safety", "Keep your linked Roblox and Discord accounts secure. Never share passwords, one-time codes, or recovery links with another member.", "/settings/integrations", "Review integrations"),
    ),
    "privacy": (
        ("Profile visibility", "Your account email and authentication details are private. Community pages may display public Discord profile information and staff-directory details that administrators have approved.", "/team", "View the staff directory"),
        ("Account data", "NexTech uses account information to provide sign-in, connected community features, and support. Account data is not exposed by the public profile pages.", "/policies", "Read policies"),
        ("Privacy requests", "Contact NexTech Support for help with account-data questions or removal requests. Some records may need to be retained for security, moderation, or legal obligations.", "/support", "Contact Support"),
    ),
    "notifications": (
        ("Portal notifications", "The site notification bell can show account alerts when you sign in with Discord and grant the required community access.", "/settings/integrations", "Review Discord connection"),
        ("Email messages", "Account verification and password-recovery messages are sent only when you request them. NexTech does not currently provide per-category email notification controls.", "/support", "Get notification help"),
    ),
    "calls": (
        ("Voice and video calls", "NexTech calling features are not available in this portal. No call history, microphone access, or calling preferences are configured here.", None, None),
        ("Need help?", "For community voice-channel or account questions, contact the team through the Support Hub.", "/support", "Open Support Hub"),
    ),
    "ads": (
        ("Advertising preferences", "The current NexTech portal does not serve personalized ads or provide ad-targeting preferences. No advertising profile can be edited here.", None, None),
        ("Promotions", "Marketplace announcements and offers, when available, are shown as NexTech site content rather than personalized advertising.", "/shop", "Browse the marketplace"),
    ),
    "standing": (
        ("Private account standing", "Account-specific moderation details are not shown on public profiles. Sign in to the member dashboard to view records available to your account.", "/dashboard", "Open the member dashboard"),
        ("Public records", "Only records approved for public viewing appear in the community records area. Private cases and staff-only notes remain restricted.", "/community/records", "View public records"),
        ("Appeal a decision", "Use the appeals page to request a review of an eligible community action.", "/community/appeals", "Review appeal options"),
    ),
    "membership": (
        ("NexTech membership", "A paid or premium membership program is not configured for this portal. Community access does not require a subscription.", None, None),
        ("Community participation", "Browse community spaces and available NexTech services without entering payment details.", "/community", "Explore the community"),
    ),
    "subscription": (
        ("Subscriptions", "Recurring subscriptions and self-service plan changes are not configured in the NexTech portal.", None, None),
        ("Purchase support", "If you have a question about a purchase made through another provider, contact that provider or NexTech Support with a receipt reference. Do not send payment-card details.", "/support", "Contact Support"),
    ),
    "billing": (
        ("Billing history", "NexTech does not currently expose billing statements or payment history in account settings.", None, None),
        ("Payment safety", "Do not email or message full card numbers, security codes, or banking credentials. Purchases are handled by their original provider.", "/support", "Get billing help"),
    ),
    "credit": (
        ("Account credit", "NexTech account credit and wallet balances are not available in this portal. No stored-value balance is maintained here.", None, None),
        ("Purchase questions", "For questions about a payment made through another provider, contact that provider or NexTech Support.", "/support", "Contact Support"),
    ),
    "points": (
        ("Points balance", "A NexTech points balance or points-redemption system is not configured for this account.", None, None),
        ("Available rewards", "If a reward program becomes available, its eligibility and redemption rules will be published on the Rewards page.", "/rewards", "View Rewards"),
    ),
    "orders": (
        ("Order history", "Order history and delivery status are not connected to this NexTech account yet.", "/shop", "Browse the marketplace"),
        ("Order support", "For an existing purchase, contact its original provider. You can contact NexTech Support for help identifying the right channel.", "/support", "Contact Support"),
    ),
    "payment-methods": (
        ("Saved payment methods", "NexTech does not store payment-card or bank details, and there are no saved payment methods to manage here.", None, None),
        ("Secure payments", "Manage payment details only with the provider that processed your purchase. Never share complete payment credentials with support.", "/support", "Get purchase help"),
    ),
    "discounts": (
        ("Discounts and offers", "There are no account-specific discount codes or saved offers configured in the NexTech portal.", "/shop", "Check marketplace listings"),
        ("Offer questions", "Any offer terms will be shown with the relevant NexTech product. Contact Support if you need clarification.", "/support", "Contact Support"),
    ),
    "licenses": (
        ("Product licenses", "Digital-license ownership and license keys are not currently connected to this account settings page.", None, None),
        ("License assistance", "For a product-specific license question, include the product name and a safe order reference when contacting Support. Never post license keys publicly.", "/support", "Contact Support"),
    ),
    "integrations": (
        ("Connection and access", "Connected providers help identify your account. Connection alone does not grant staff privileges; permissions come from authorized server roles and linked-account checks.", None, None),
        ("Disconnecting an account", "To correct or remove a connection, contact NexTech Support. Changes may affect sign-in, Hub verification, or notifications.", "/support", "Request connection help"),
    ),
    "rewards": (
        ("Rewards program", "Points earning, reward inventory, and redemption are not configured yet. No points or prizes are being tracked by this page.", None, None),
        ("Announcements", "NexTech will publish program details here if a rewards feature becomes available.", "/community", "Visit the community hub"),
    ),
    "advertising": (
        ("Promote a project", "Paid placements or self-service advertising campaigns are not available from this portal.", None, None),
        ("Business inquiries", "For a partnership or promotion inquiry, contact NexTech Support with a short description. Do not include passwords or payment details.", "/support", "Contact NexTech"),
    ),
}


def _settings_page_content(section: str, title: str, description: str) -> str:
    navigation = []
    for group_name, items in SETTINGS_GROUPS:
        links = ""
        for slug, label in items:
            href = "/confirm-password" if slug == "confirm-password" else f"/settings/{slug}"
            active = " active" if slug == section else ""
            current = ' aria-current="page"' if slug == section else ""
            links += f'<a class="settings-link{active}" href="{href}"{current}>{label}</a>'
        navigation.append(
            f'<section class="settings-group"><h3>{group_name}</h3>{links}</section>'
        )
    content = (
        '<section class="settings-card">'
        f'<div class="settings-card-eyebrow">{title}</div>'
        f'<h2>{title}</h2><p>{description}</p>'
    )
    if section in {"account", "customize"}:
        content += (
            '<div class="account-summary" id="settings-account">'
            '<div class="records-empty">Loading connected account information…</div>'
            '</div>'
        )
    elif section == "verification":
        content += (
            '<div class="account-summary" id="settings-account">'
            '<div class="records-empty">Loading connected account information…</div>'
            '</div>'
        )
    elif section == "integrations":
        content += (
            '<div id="settings-integrations">'
            '<article class="integration-card" data-provider="roblox"><div><strong>Roblox</strong><span>Member account sign-in</span></div><a class="cta" href="/auth/roblox?next=/settings/integrations">Connect with Roblox</a></article>'
            '<article class="integration-card" data-provider="discord"><div><strong>Discord</strong><span>Community alerts and server identity</span></div><a class="cta" href="/auth/discord/community?next=/settings/integrations">Connect with Discord</a></article>'
            '</div>'
            '<p class="settings-note">Roblox provides member-account access. Discord sign-in is used for community alerts and does not replace your NexTech account.</p>'
        )
    cards = SETTINGS_DETAIL_CARDS.get(section, ())
    if cards:
        content += '<div class="settings-detail-grid">'
        for heading, copy, href, label in cards:
            content += (
                '<article class="settings-detail-card">'
                f'<h3>{html.escape(heading)}</h3><p>{html.escape(copy)}</p>'
            )
            if href and label:
                content += f'<a class="settings-detail-link" href="{html.escape(href, quote=True)}">{html.escape(label)} →</a>'
            content += '</article>'
        content += '</div>'
    content += '</section>'
    return (
        '<section class="settings-layout" aria-label="Account settings">'
        '<aside class="settings-sidebar"><div class="settings-sidebar-title">Settings</div>'
        '<p>Manage your profile and account settings</p>'
        + "".join(navigation)
        + '</aside><div class="settings-main">'
        f'<div class="settings-mobile-title"><span>Settings</span><select aria-label="Settings section" id="settings-select">'
        + "".join(
            f'<option value="{"/confirm-password" if slug == "confirm-password" else f"/settings/{slug}"}"'
            f'{" selected" if slug == section else ""}>{label}</option>'
            for group, items in SETTINGS_GROUPS
            for slug, label in items
        )
        + '</select></div><header class="settings-heading"><div class="eyebrow">Account settings</div>'
        f'<h1>{title}</h1><p>{description}</p></header>{content}</div></section>'
    )


PROFILE_PAGE_CONTENT = """
  <section class="hero"><div class="eyebrow">NexTech member</div><h1>Your profile</h1><p>Account information below is loaded from your current NexTech sign-in. Profile activity and follower data are not available in the current project.</p></section>
  <section class="profile-banner panel">
    <div class="profile-cover"></div>
    <div class="profile-identity"><div class="profile-avatar" id="profile-avatar">N</div><div><h2 id="profile-name">Loading profile…</h2><p id="profile-handle"></p></div><a class="cta" href="/settings/customize">Edit profile</a></div>
    <div class="profile-stats"><span id="profile-provider">Account provider</span><span id="profile-email"></span></div>
  </section>
  <section class="profile-columns"><div class="panel profile-activity"><h2>Overview</h2><div class="records-empty">NexTech does not currently expose public profile activity.</div><p><a class="cta" href="/community/records">View community records</a></p></div><aside class="panel"><h2>About</h2><p>Your account details are visible only to you in this browser session.</p><p><a href="/settings/account">Account settings</a></p></aside></section>
"""

MEMBERS_PAGE_CONTENT = """
  <section class="hero"><div class="eyebrow">NexTech Discord</div><h1>Community members</h1><p>Browse Discord display names, avatars, and current online status for members of the NexTech server. Status is supplied by Discord and may not always be available.</p></section>
  <section class="member-directory-tools"><label class="sr-only" for="member-search">Search members</label><input id="member-search" class="wiki-search" type="search" maxlength="80" placeholder="Search Discord members…"><span id="member-count">Loading member profiles…</span></section>
  <section class="member-grid" id="member-grid" aria-live="polite"><div class="records-empty">Loading member profiles…</div></section>
"""

MEMBERS_PAGE_SCRIPT = r"""
    (() => {
      const list = document.getElementById("member-grid");
      const count = document.getElementById("member-count");
      const search = document.getElementById("member-search");
      let timer;
      const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
      async function loadMembers() {
        count.textContent = "Loading member profiles…";
        try {
          const params = new URLSearchParams();
          if (search.value.trim()) params.set("q", search.value.trim());
          const response = await fetch("/api/community/members?" + params.toString(), {headers: {"Accept": "application/json"}});
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || "Discord member profiles could not be loaded.");
          count.textContent = `${data.total} member${data.total === 1 ? "" : "s"}${data.truncated ? " · showing first 250" : ""}`;
          list.replaceChildren();
          if (!data.members.length) {
            const empty = document.createElement("div"); empty.className = "records-empty";
            empty.textContent = "No Discord members match that search."; list.append(empty); return;
          }
          data.members.forEach(member => {
            const link = document.createElement("a");
            link.className = "member-card"; link.href = "/members/" + encodeURIComponent(member.id);
            const avatar = document.createElement("img"); avatar.className = "member-photo"; avatar.alt = ""; avatar.loading = "lazy"; avatar.referrerPolicy = "no-referrer";
            if (typeof member.avatar === "string" && member.avatar.startsWith("https://")) avatar.src = member.avatar;
            const status = document.createElement("span"); status.className = "member-online-dot" + (member.online ? " online" : "");
            const avatarWrap = document.createElement("span"); avatarWrap.style.position = "relative"; avatarWrap.append(avatar, status);
            const details = document.createElement("span"); details.style.minWidth = "0";
            const name = document.createElement("span"); name.className = "member-name"; name.textContent = member.display_name;
            const presence = document.createElement("span"); presence.className = "member-status"; presence.textContent = member.online ? "Online on Discord" : "Offline on Discord";
            details.append(name, presence); link.append(avatarWrap, details); list.append(link);
          });
        } catch (error) {
          console.error("Could not load Discord member profiles", error);
          count.textContent = "Member profiles unavailable";
          list.replaceChildren();
          const empty = document.createElement("div"); empty.className = "records-empty"; empty.textContent = error.message || "Member profiles are temporarily unavailable."; list.append(empty);
        }
      }
      search.addEventListener("input", () => { window.clearTimeout(timer); timer = window.setTimeout(loadMembers, 250); });
      loadMembers();
      window.setInterval(() => { if (!document.hidden && !search.value.trim()) loadMembers(); }, 30000);
    })();
"""

DISCORD_MEMBER_PAGE_CONTENT = """
  <section class="hero"><div class="eyebrow">NexTech Discord member</div><a class="cta" href="/members">← All members</a></section>
  <section class="member-profile-card" id="discord-profile" data-discord-id="__DISCORD_ID__" aria-live="polite"><img class="member-profile-avatar" id="discord-profile-avatar" alt=""><div><h1 id="discord-profile-name">Loading profile…</h1><p class="member-profile-status" id="discord-profile-status">Checking Discord presence…</p><p style="margin-top:8px">NexTech community member</p></div></section>
"""

DISCORD_MEMBER_PAGE_SCRIPT = r"""
    (() => {
      const card = document.getElementById("discord-profile");
      const id = card.dataset.discordId;
      fetch("/api/community/members/" + encodeURIComponent(id), {headers: {"Accept": "application/json"}})
        .then(async response => {
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || "Discord member was not found.");
          const member = data.member;
          document.getElementById("discord-profile-name").textContent = member.display_name;
          const status = document.getElementById("discord-profile-status");
          status.textContent = member.online ? "Online on Discord" : "Offline on Discord";
          if (member.online) status.classList.add("online");
          document.getElementById("discord-profile-highlight").textContent =
            member.online ? "Online on Discord" : "Offline on Discord";
          const avatar = document.getElementById("discord-profile-avatar");
          if (typeof member.avatar === "string" && member.avatar.startsWith("https://")) avatar.src = member.avatar;
          avatar.alt = member.display_name + " Discord avatar";
        })
        .catch(error => {
          console.error("Could not load Discord member profile", error);
          document.getElementById("discord-profile-name").textContent = error.message || "Member profile unavailable.";
          document.getElementById("discord-profile-status").textContent = "Discord profile could not be loaded.";
        });
    })();
"""

PROFILE_PAGE_SCRIPT = r"""
    (() => {
      const token = localStorage.getItem("nx_cust_token");
      const name = document.getElementById("profile-name");
      if (!token) {
        name.textContent = "Sign in to view your profile";
        document.getElementById("profile-handle").innerHTML = '<a href="/dashboard">Continue to sign in</a>';
        return;
      }
      fetch("/api/customer/me", {headers: {"X-Customer-Token": token, "Accept": "application/json"}})
        .then(async response => {
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || "Profile unavailable.");
          name.textContent = data.display_name || data.username || "NexTech member";
          document.getElementById("profile-handle").textContent = data.username ? `@${data.username}` : "";
          document.getElementById("profile-provider").textContent = data.provider === "roblox" ? "Connected with Roblox" : data.provider === "email" ? "Email account" : "NexTech account";
          document.getElementById("profile-email").textContent = data.email || "";
          const avatar = document.getElementById("profile-avatar");
          if (data.avatar && String(data.avatar).startsWith("https://")) {
            const image = document.createElement("img");
            image.src = data.avatar;
            image.alt = "";
            avatar.replaceChildren(image);
          } else avatar.textContent = (data.display_name || data.username || "N").slice(0, 1).toUpperCase();
        })
        .catch(error => {
          name.textContent = error.message || "Profile unavailable.";
          document.getElementById("profile-handle").innerHTML = '<a href="/dashboard">Open Dashboard</a>';
        });
    })();
"""

MESSAGES_PAGE_CONTENT = """
  <section class="inbox-heading"><div><div class="eyebrow">Your NexTech account</div><h1>Messages</h1><p>Support tickets connected to your signed-in account.</p></div><a class="cta primary" href="/support">Contact NexVisions</a></section>
  <section class="inbox-layout"><aside class="inbox-folders panel"><a class="inbox-folder active" href="/messages">Inbox</a><a class="inbox-folder" href="/messages?filter=archived">Archived</a><a class="inbox-folder" href="/support">New support request</a></aside><section class="inbox-panel panel"><div class="inbox-title"><h2>Support conversations</h2><span id="inbox-count">—</span></div><div class="inbox-tools"><input class="wiki-search" id="inbox-search" type="search" placeholder="Search by ticket or subject…" aria-label="Search support conversations"><select class="wiki-search" id="inbox-filter" aria-label="Filter conversations"><option value="all">All</option><option value="open">Open</option><option value="closed">Resolved</option></select></div><div id="inbox-list"><div class="records-empty">Loading account conversations…</div></div></section></section>
"""

MESSAGES_PAGE_SCRIPT = r"""
    (() => {
      const list = document.getElementById("inbox-list");
      const token = localStorage.getItem("nx_cust_token");
      const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
      let tickets = [];
      function render() {
        const term = document.getElementById("inbox-search").value.trim().toLowerCase();
        const filter = document.getElementById("inbox-filter").value;
        const visible = tickets.filter(ticket => {
          const status = String(ticket.status || "open").toLowerCase();
          const isClosed = ["closed", "resolved"].includes(status);
          const matchesStatus = filter === "all" || (filter === "closed" ? isClosed : !isClosed);
          return matchesStatus && `${ticket.case_id || ""} ${ticket.case_type || ""} ${ticket.reason || ""}`.toLowerCase().includes(term);
        });
        document.getElementById("inbox-count").textContent = `${visible.length}`;
        if (!visible.length) {
          list.innerHTML = '<div class="inbox-empty"><strong>Your inbox is empty</strong><p>Support conversations attached to this account will appear here.</p><a class="cta" href="/support">Visit Support Hub</a></div>';
          return;
        }
        list.innerHTML = visible.map(ticket => {
          const label = ticket.case_type || "Support request";
          const status = ticket.status || "open";
          const subject = ticket.reason || label;
          const id = ticket.case_id ? `Case ${ticket.case_id}` : "Support case";
          return `<article class="inbox-message"><span class="inbox-message-icon">?</span><div class="inbox-message-body"><strong>${escape(subject)}</strong><span>${escape(id)} · ${escape(label)}</span></div><span class="inbox-status">${escape(status)}</span><a class="cta" href="/dashboard">Open support</a></article>`;
        }).join("");
      }
      document.getElementById("inbox-search").addEventListener("input", render);
      document.getElementById("inbox-filter").addEventListener("change", render);
      if (!token) {
        list.innerHTML = '<div class="inbox-empty"><strong>Sign in to see your conversations</strong><p>We only show support tickets belonging to your account.</p><a class="cta primary" href="/login?next=%2Fsupport%2Ftickets">Sign in</a></div>';
        document.getElementById("inbox-count").textContent = "0";
        return;
      }
      fetch("/api/dashboard/tickets", {headers: {"X-Customer-Token": token, "Accept": "application/json"}})
        .then(async response => {
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || "Could not load support conversations.");
          tickets = Array.isArray(data.tickets) ? data.tickets : [];
          render();
        })
        .catch(error => { list.innerHTML = `<div class="inbox-empty">${escape(error.message || "Could not load support conversations.")}<p><a href="/dashboard">Open the member dashboard</a></p></div>`; });
    })();
"""

SHOP_PAGE_CONTENT = """
  <section class="hero"><div class="eyebrow">NexTech Marketplace</div><h1>Products</h1><p>Browse products published by the NexTech team. Product details are loaded from the project’s existing product catalog.</p><div class="product-count" id="product-count">Loading products…</div></section>
  <section class="shop-layout"><aside class="shop-filters panel"><h2>Filters</h2><p>Refine these products</p><label class="field">Sorting<select id="product-sort" class="wiki-search"><option value="newest">Newest</option><option value="name">Name</option><option value="price-low">Price: low to high</option><option value="price-high">Price: high to low</option></select></label><label class="field">Search<input id="product-search" class="wiki-search" type="search" placeholder="Search by name…"></label><div class="price-fields"><label class="field">Minimum price<input class="wiki-search" id="product-min" type="number" min="0" placeholder="Min"></label><label class="field">Maximum price<input class="wiki-search" id="product-max" type="number" min="0" placeholder="Max"></label></div><label class="field">Category<select id="product-category" class="wiki-search"><option value="">All products</option></select></label></aside><div class="shop-results"><div class="shop-results-head"><h2>Marketplace</h2><span id="cart-count">Cart: 0</span></div><div class="product-grid" id="product-grid"><div class="records-empty">Loading products…</div></div></div></section>
"""

SHOP_PAGE_SCRIPT = r"""
    (() => {
      const list = document.getElementById("product-grid");
      let products = [];
      const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
      function price(product) {
        const isUsd = product.currency === "USD" || (Number(product.price_usd) > 0 && !Number(product.robux));
        const amount = Number(isUsd ? product.price_usd : product.robux);
        if (!Number.isFinite(amount) || amount <= 0) return "Price not listed";
        return isUsd ? `$${amount.toFixed(2)} USD · Sold in USD` : `R$ ${amount.toLocaleString()} Robux`;
      }
      function render() {
        const term = document.getElementById("product-search").value.trim().toLowerCase();
        const category = document.getElementById("product-category").value;
        const min = Number(document.getElementById("product-min").value);
        const max = Number(document.getElementById("product-max").value);
        const mode = document.getElementById("product-sort").value;
        let filtered = products.filter(item => {
          const amount = Number(item.currency === "USD" || (Number(item.price_usd) > 0 && !Number(item.robux)) ? item.price_usd : item.robux);
          const text = `${item.name || ""} ${item.description || ""} ${item.category || ""}`.toLowerCase();
          return text.includes(term) && (!category || item.category === category) &&
            (!document.getElementById("product-min").value || (Number.isFinite(amount) && amount >= min)) &&
            (!document.getElementById("product-max").value || (Number.isFinite(amount) && amount <= max));
        });
        if (mode === "name") filtered.sort((a,b) => String(a.name || "").localeCompare(String(b.name || "")));
        const numericPrice = item => { const usd=item.currency==="USD"||(Number(item.price_usd)>0&&!Number(item.robux));return Number(usd?item.price_usd:item.robux); };
        if (mode === "price-low") filtered.sort((a,b) => numericPrice(a) - numericPrice(b));
        if (mode === "price-high") filtered.sort((a,b) => numericPrice(b) - numericPrice(a));
        document.getElementById("product-count").textContent = `${filtered.length} product${filtered.length === 1 ? "" : "s"}${category ? ` in ${category}` : ""}`;
        if (!filtered.length) { list.innerHTML = '<div class="records-empty">No products match these filters.</div>'; return; }
        list.innerHTML = filtered.map(product => {
          const imageValue = String(product.image || "");
          let image = "";
          try { const url = new URL(imageValue, location.origin); if (["http:", "https:"].includes(url.protocol)) image = `<img src="${escape(url.href)}" alt="" loading="lazy">`; } catch (_) {}
          return `<article class="product-card">${image ? `<div class="product-image">${image}</div>` : '<div class="product-image product-placeholder">N</div>'}<div class="product-card-content"><span class="product-category">${escape(product.category || "NexTech")}</span><h3>${escape(product.name || "Untitled product")}</h3><p>${escape(product.description || "No description provided.")}</p><div class="product-card-footer"><strong>${escape(price(product))}</strong><button type="button" class="cta" data-product-id="${escape(product.id || "")}">Add to cart</button></div></div></article>`;
        }).join("");
      }
      for (const id of ["product-search","product-min","product-max","product-category","product-sort"]) document.getElementById(id).addEventListener("input",render);
      document.getElementById("product-grid").addEventListener("click",event => {
        const button = event.target.closest("[data-product-id]");
        if (!button) return;
        const cart = JSON.parse(localStorage.getItem("nx_cart") || "[]");
        cart.push(button.dataset.productId);
        localStorage.setItem("nx_cart", JSON.stringify(cart));
        document.getElementById("cart-count").textContent = `Cart: ${cart.length}`;
        window.dispatchEvent(new Event("nx-cart-updated"));
        button.textContent = "Added";
        window.setTimeout(() => { if (button.isConnected) button.textContent = "Add to cart"; }, 1200);
      });
      try { document.getElementById("cart-count").textContent = `Cart: ${JSON.parse(localStorage.getItem("nx_cart") || "[]").length}`; } catch (_) {}
      fetch("/api/products", {headers: {"Accept":"application/json"}})
        .then(async response => { if (!response.ok) throw new Error("Products are temporarily unavailable."); return response.json(); })
        .then(data => {
          products = Array.isArray(data) ? data : [];
          const categories = [...new Set(products.map(item => item.category).filter(Boolean))].sort();
          document.getElementById("product-category").insertAdjacentHTML("beforeend", categories.map(value => `<option value="${escape(value)}">${escape(value)}</option>`).join(""));
          render();
        })
        .catch(error => { document.getElementById("product-count").textContent = error.message; list.innerHTML = `<div class="records-empty">${escape(error.message)}</div>`; });
    })();
"""

SETTINGS_PAGE_SCRIPT = r"""
    (() => {
      const token = localStorage.getItem("nx_cust_token");
      const container = document.getElementById("settings-account");
      if (container) {
        if (!token) {
          const signInLink = "/login?next=" + encodeURIComponent(location.pathname + location.search);
          container.innerHTML = `<div class="inbox-empty"><strong>Sign in to view your account details.</strong><p>Account settings come from your NexTech sign-in.</p><a class="cta primary" href="${signInLink}">Sign in</a></div>`;
        }
        else fetch("/api/customer/me", {headers: {"X-Customer-Token":token,"Accept":"application/json"}})
          .then(async response => { const data=await response.json(); if(!response.ok) throw new Error(data.error || "Account details unavailable."); return data; })
          .then(data => {
            const row=(label,value)=>{ const wrap=document.createElement("div");wrap.className="account-summary-row";const key=document.createElement("span");key.textContent=label;const val=document.createElement("strong");val.textContent=value||"Not provided";wrap.append(key,val);container.append(wrap); };
            row("Display name",data.display_name||data.username);row("Username",data.username);row("Email",data.email);row("Sign-in provider",data.provider==="roblox"?"Roblox":"Email");
            if (typeof data.email_verified==="boolean") row("Email verification",data.email_verified?"Verified":"Needs verification");
            if (data.roblox_id) row("Roblox account","Connected");
          })
          .catch(error => { container.textContent=error.message || "Account details unavailable."; });
      }
      const integrations = document.getElementById("settings-integrations");
      if (integrations) {
        const robloxCard = integrations.querySelector('[data-provider="roblox"]');
        const discordCard = integrations.querySelector('[data-provider="discord"]');
        if (token) {
          fetch("/api/customer/me", {headers: {"X-Customer-Token":token,"Accept":"application/json"}})
            .then(async response => { const data=await response.json(); if(!response.ok) throw new Error(data.error || "Roblox account status unavailable."); return data; })
            .then(data => { robloxCard.querySelector("span").textContent = `Connected as ${data.username || data.display_name || "NexTech member"}`; })
            .catch(error => { console.error("Could not load Roblox integration status", error); robloxCard.querySelector("span").textContent = error.message; });
        }
        const discordToken = localStorage.getItem("nx_discord_community_token");
        if (discordToken) {
          fetch("/api/community/notifications", {headers: {"X-Discord-Community-Token":discordToken,"Accept":"application/json"}})
            .then(async response => { const data=await response.json(); if(!response.ok) throw new Error(data.error || "Discord connection expired."); return data; })
            .then(data => { discordCard.querySelector("span").textContent = `Connected as ${data.member.display_name}`; })
            .catch(error => { console.error("Could not load Discord integration status", error); discordCard.querySelector("span").textContent = error.message; });
        }
      }
      const selector=document.getElementById("settings-select");
      if(selector) selector.addEventListener("change",()=>{location.href=selector.value;});
      document.querySelectorAll("[data-settings-pref]").forEach(select=>{
        const key=`nx_pref_${select.dataset.settingsPref}`;
        select.value=localStorage.getItem(key)||"dark";
        select.addEventListener("change",()=>localStorage.setItem(key,select.value));
      });
    })();
"""

STUDIO_PAGE_CONTENT = """
  <section class="hero"><div class="eyebrow">NexTech Studio</div><h1>Studio</h1><p>Explore NexTech projects and team information.</p></section>
  <section class="community-grid" aria-label="Studio sections">
    <a class="feature-card" href="/studio/titles"><div class="feature-icon">01</div><h2>Titles</h2><p>Games and experiences we make and ship.</p></a>
    <a class="feature-card" href="/studio/news"><div class="feature-icon">02</div><h2>News</h2><p>News and updates from the studio.</p></a>
    <a class="feature-card" href="/studio/team"><div class="feature-icon">03</div><h2>Team</h2><p>Meet the people behind NexTech.</p></a>
    <a class="feature-card" href="/studio/careers"><div class="feature-icon">04</div><h2>Careers</h2><p>Open roles and how to join the team.</p></a>
    <a class="feature-card" href="/studio/press"><div class="feature-icon">05</div><h2>Press</h2><p>Press information and media contact.</p></a>
  </section>
"""


COMMUNITY_PAGE_CONTENT = """
  <section class="hero"><div class="eyebrow">NexTech community</div><h1>Community</h1><p>Find member spaces, community safety resources, public records, discussions, and ways to connect with the NexTech team.</p><div class="cta-row"><a class="cta primary" href="/hub">Open the Hub</a><a class="cta" href="/community/reports">Go to report center</a><a class="cta" href="/community/submissions">Track reports &amp; appeals</a></div></section>
  <section class="community-grid" aria-label="Community sections">
    <a class="feature-card" href="/members"><div class="feature-icon">👥</div><h2>Discord members</h2><p>Browse community profiles and current Discord online status.</p></a>
    <a class="feature-card" href="/community"><div class="feature-icon">🔴</div><h2>Live from Discord</h2><p>Open the live chat bubble to read messages from public server channels.</p></a>
    <a class="feature-card" href="/community/records"><div class="feature-icon">🛡️</div><h2>Public records</h2><p>Browse recorded bans, warnings, and community moderation actions, including issuer details when available.</p></a>
    <a class="feature-card" href="/community/reports"><div class="feature-icon">⚑</div><h2>Report center</h2><p>Send a private user, safety, or conduct report to the staff review channel.</p></a>
    <a class="feature-card" href="/community/appeals"><div class="feature-icon">↗</div><h2>Appeals</h2><p>Request a review of a NexTech moderation action.</p></a>
    <a class="feature-card" href="/community/submissions"><div class="feature-icon">◷</div><h2>Track submissions</h2><p>Check the intake, review, outcome, and closure stages for your own reports and appeals.</p></a>
    <a class="feature-card" href="/community/staff-abuse"><div class="feature-icon">⚠</div><h2>Staff misconduct</h2><p>Report concerns about staff conduct or permissions through a dedicated route.</p></a>
    <a class="feature-card" href="/community/osp"><div class="feature-icon">♡</div><h2>Online safety</h2><p>Find safety guidance and the correct report channels.</p></a>
    <a class="feature-card" href="/knowledge-base"><div class="feature-icon">📚</div><h2>Wiki &amp; knowledge base</h2><p>Find NexTech help articles and the bot’s available slash commands.</p></a>
    <a class="feature-card" href="/feedback"><div class="feature-icon">💬</div><h2>Feedback</h2><p>Find the current routes for support and sharing feedback with NexTech staff.</p></a>
    <a class="feature-card" href="/groups"><div class="feature-icon">👥</div><h2>Groups</h2><p>Community group pages are planned for a future update.</p><span class="soon-tag">Coming soon</span></a>
    <a class="feature-card" href="/forums"><div class="feature-icon">🗨️</div><h2>Forums</h2><p>Community discussions are planned for a future update.</p><span class="soon-tag">Coming soon</span></a>
    <a class="feature-card" href="/community/apply"><div class="feature-icon">＋</div><h2>Apply</h2><p>Community application information will be posted here.</p><span class="soon-tag">Coming soon</span></a>
    <a class="feature-card" href="/team"><div class="feature-icon">♙</div><h2>Staff directory</h2><p>Find public NexTech operations staff profiles.</p></a>
    <a class="feature-card" href="/status"><div class="feature-icon">📡</div><h2>System status</h2><p>Check bot uptime, connected servers, member counts, latency, and commands.</p></a>
  </section>
"""


RECORDS_PAGE_CONTENT = """
  <section class="hero"><div class="eyebrow">Community safety</div><h1>Public moderation records</h1><p>Browse NexTech Discord warnings and community moderation events, plus Roblox moderation actions issued by staff. An issuer is shown when the source log records one.</p></section>
  <section class="panel">
    <div class="record-tabs" role="tablist" aria-label="Record type">
      <button class="record-tab" type="button" data-record-type="ban" role="tab">Bans <span class="record-count" id="count-ban">—</span></button>
      <button class="record-tab" type="button" data-record-type="warning" role="tab">Warnings <span class="record-count" id="count-warning">—</span></button>
      <button class="record-tab" type="button" data-record-type="community" role="tab">Community <span class="record-count" id="count-community">—</span></button>
    </div>
    <form class="record-search" id="records-search"><input id="records-query" type="search" placeholder="Search records by member, staff, ID, or reason…" aria-label="Search public records"><button type="submit">Search</button></form>
    <div class="records-list" id="records-list" aria-live="polite"><div class="records-empty">Loading public records…</div></div>
  </section>
"""


RECORDS_PAGE_SCRIPT = r"""
    (() => {
      const esc=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
      const params=new URLSearchParams(location.search);
      let type=["ban","warning","community"].includes(params.get("type"))?params.get("type"):"ban";
      let query=params.get("q")||"";
      const input=document.getElementById("records-query");
      const list=document.getElementById("records-list");
      input.value=query;
      function dateLabel(record){
        if(record.timestamp_str)return record.timestamp_str;
        if(!record.timestamp)return "Date not recorded";
        const date=new Date(Number(record.timestamp)*1000);
        return Number.isNaN(date.getTime())?"Date not recorded":date.toLocaleString();
      }
      function setTab(){
        document.querySelectorAll("[data-record-type]").forEach(button=>{
          const selected=button.dataset.recordType===type;
          button.classList.toggle("active",selected);
          button.setAttribute("aria-selected",String(selected));
        });
      }
      function render(records){
        if(!records.length){
          const message=type==="community"?"No community ban or unban records have been logged yet.":"No records match these filters.";
          list.innerHTML=`<div class="records-empty">${esc(message)}</div>`;
          return;
        }
        list.innerHTML=records.map(record=>{
          const name=record.target||"NexTech member";
          const initials=name.split(/\s+/).slice(0,2).map(part=>part[0]||"").join("").toUpperCase()||"NX";
          const badgeClass=record.type==="warning"?"warning":record.type==="community"?"community":"";
          const badge=record.type==="warning"?`${record.points||1} point warning`:record.label||"Ban record";
          const profile=record.profile_url&&/^https:\/\/www\.roblox\.com\//i.test(record.profile_url)?`<a class="record-profile" href="${esc(record.profile_url)}" target="_blank" rel="noopener">Roblox profile ↗</a>`:"";
          const id=record.target_id?`ID ${esc(record.target_id)}`:"ID not recorded";
          const caseLabel=record.case_id?`Case #${esc(record.case_id)}`:`${esc(record.source||"NexTech")} record`;
          return `<article class="record-card"><div class="record-top"><div class="record-avatar">${esc(initials)}</div><div class="record-person"><div class="record-name">${esc(name)}</div><div class="record-id">${id}</div></div><span class="record-badge ${badgeClass}">${esc(badge)}</span></div><div class="record-case">${caseLabel}</div><div class="record-reason">${esc(record.reason||"No reason recorded")}</div><div class="record-meta"><span>Issued <strong>${esc(dateLabel(record))}</strong></span><span>·</span><span>by <strong>${esc(record.moderator||"Not recorded")}</strong></span><span>·</span><span>${esc(record.source||"NexTech")}</span>${profile}</div></article>`;
        }).join("");
      }
      async function load(){
        query=input.value.trim().slice(0,120);
        setTab();
        const searchParams=new URLSearchParams();
        searchParams.set("q",query);
        searchParams.set("type",type);
        history.replaceState({},document.title,`${location.pathname}?${searchParams.toString()}`);
        list.innerHTML='<div class="records-empty">Loading public records…</div>';
        try{
          const response=await fetch(`/api/community/records?${searchParams.toString()}`,{headers:{"Accept":"application/json"}});
          const data=await response.json();
          if(!response.ok)throw new Error(data.error||"Could not load records.");
          for(const key of ["ban","warning","community"]){
            const badge=document.getElementById(`count-${key}`);
            if(badge)badge.textContent=Number(data.counts?.[key]||0).toLocaleString();
          }
          render(data.records||[]);
        }catch(error){list.innerHTML=`<div class="records-empty">${esc(error.message||"Public records are unavailable.")}</div>`;}
      }
      document.querySelectorAll("[data-record-type]").forEach(button=>button.addEventListener("click",()=>{type=button.dataset.recordType;load();}));
      document.getElementById("records-search").addEventListener("submit",event=>{event.preventDefault();load();});
      let timer=0;
      input.addEventListener("input",()=>{window.clearTimeout(timer);timer=window.setTimeout(load,180);});
      load();
    })();
"""


WIKI_PAGE_CONTENT = """
  <section class="hero"><div class="eyebrow">Help and reference</div><h1>NexTech Wiki</h1><p>Search the published help articles and public slash-command reference.</p></section>
  <section class="panel"><input class="wiki-search" id="wiki-search" type="search" placeholder="Search help articles and commands…" aria-label="Search wiki"><h2 class="section-title">Help articles</h2><div class="wiki-list" id="wiki-articles"><div class="records-empty">Loading articles…</div></div><h2 class="section-title">Bot commands</h2><div class="command-list" id="wiki-commands"><div class="records-empty">Loading commands…</div></div></section>
"""


KNOWLEDGE_BASE_PAGE_CONTENT = """
  <section class="hero"><div class="eyebrow">Help center</div><h1>Knowledge base</h1><p>Search NexTech’s published help articles and command reference. Answers are loaded from the same maintained knowledge source as the Wiki.</p><div class="cta-row"><a class="cta" href="/wiki">Open the Wiki</a><a class="cta" href="/support">Ask NexVisions</a></div></section>
  <section class="panel"><input class="wiki-search" id="wiki-search" type="search" placeholder="Search help articles and commands…" aria-label="Search knowledge base"><h2 class="section-title">Help articles</h2><div class="wiki-list" id="wiki-articles"><div class="records-empty">Loading articles…</div></div><h2 class="section-title">Bot commands</h2><div class="command-list" id="wiki-commands"><div class="records-empty">Loading commands…</div></div></section>
"""


WIKI_PAGE_SCRIPT = r"""
    (() => {
      const esc=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
      let articles=[],commands=[];
      const query=document.getElementById("wiki-search");
      function render(){
        const term=query.value.trim().toLowerCase();
        const visibleArticles=articles.filter(item=>`${item.category||""} ${item.question||""} ${item.answer||""}`.toLowerCase().includes(term));
        const visibleCommands=commands.filter(item=>`${item.name||""} ${item.description||""}`.toLowerCase().includes(term));
        document.getElementById("wiki-articles").innerHTML=visibleArticles.length?visibleArticles.map(item=>`<article class="wiki-item"><div class="wiki-category">${esc(item.category||"General")}</div><div class="wiki-question">${esc(item.question||"Help article")}</div><div class="wiki-answer">${esc(item.answer||"")}</div></article>`).join(""):'<div class="records-empty">No matching help articles.</div>';
        document.getElementById("wiki-commands").innerHTML=visibleCommands.length?visibleCommands.map(item=>`<article class="command-card"><div class="command-name">/${esc(item.name||"command")}</div><div class="command-desc">${esc(item.description||"No description provided.")}</div></article>`).join(""):'<div class="records-empty">No matching commands.</div>';
      }
      query.addEventListener("input",render);
      Promise.allSettled([
        fetch("/api/community/wiki").then(response=>response.ok?response.json():Promise.reject(new Error("Wiki unavailable"))),
        fetch("/api/commands").then(response=>response.ok?response.json():Promise.reject(new Error("Commands unavailable")))
      ]).then(results=>{
        if(results[0].status==="fulfilled")articles=results[0].value.articles||[];
        if(results[1].status==="fulfilled")commands=Array.isArray(results[1].value)?results[1].value:[];
        render();
      });
    })();
"""


FEEDBACK_PAGE_CONTENT = """
  <section class="hero"><div class="eyebrow">Help shape NexTech</div><h1>Feedback</h1><p>Reach the NexTech team with an issue, idea, or community concern. Use the secure report portal for account-linked reports, or join Discord to share general feedback.</p></section>
  <section class="community-grid">
    <article class="feature-card"><div class="feature-icon">⚑</div><h2>Report a concern</h2><p>Sign in with your linked Roblox account to submit a report that is delivered to the configured staff channel.</p><div class="cta-row"><a class="cta primary" href="/dashboard">Open secure report portal</a></div></article>
    <article class="feature-card"><div class="feature-icon">💡</div><h2>Share an idea</h2><p>Join the NexTech Discord and share feedback with the community and staff team.</p><div class="cta-row"><a class="cta" href="https://discord.gg/hWVJ5jgAwh" target="_blank" rel="noopener">Join NexTech Discord ↗</a></div></article>
    <article class="feature-card"><div class="feature-icon">🎫</div><h2>Get support</h2><p>Ask NexVisions a question, or contact NexTech staff directly through the Support Hub.</p><div class="cta-row"><a class="cta" href="/support">Open Support Hub</a></div></article>
  </section>
"""


STATUS_PAGE_CONTENT = """
  <section class="hero"><div class="eyebrow">Live service information</div><h1>System status</h1><p>Current NexTech bot availability, uptime, server membership, connection latency, and command reference.</p><div style="margin-top:14px"><span class="status-pill" id="status-pill"><span class="status-dot"></span>Checking status…</span></div></section>
  <div class="stats-grid" id="status-cards"><article class="stat-card"><div class="stat-label">Uptime</div><div class="stat-value">—</div></article><article class="stat-card"><div class="stat-label">NexTech members</div><div class="stat-value">—</div></article><article class="stat-card"><div class="stat-label">Servers</div><div class="stat-value">—</div></article><article class="stat-card"><div class="stat-label">Latency</div><div class="stat-value">—</div></article></div>
  <section class="panel"><div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap"><h2 class="section-title" style="margin:0">Bot commands</h2><button class="refresh-button" id="status-refresh" type="button">Refresh status</button></div><input class="wiki-search command-search" id="status-command-search" type="search" placeholder="Filter slash commands…" aria-label="Filter slash commands"><div class="command-list" id="status-commands"><div class="records-empty">Loading commands…</div></div></section>
"""


STATUS_PAGE_SCRIPT = r"""
    (() => {
      const esc=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
      let commands=[];
      const uptime=value=>{
        let seconds=Math.max(0,Number(value)||0);
        const days=Math.floor(seconds/86400);seconds%=86400;
        const hours=Math.floor(seconds/3600);seconds%=3600;
        const minutes=Math.floor(seconds/60);seconds=Math.floor(seconds%60);
        return [days?`${days}d`:"",hours?`${hours}h`:"",minutes?`${minutes}m`:"",`${seconds}s`].filter(Boolean).join(" ");
      };
      function renderCommands(){
        const term=document.getElementById("status-command-search").value.trim().toLowerCase();
        const visible=commands.filter(command=>`${command.name||""} ${command.description||""}`.toLowerCase().includes(term));
        document.getElementById("status-commands").innerHTML=visible.length?visible.map(command=>`<article class="command-card"><div class="command-name">/${esc(command.name||"command")}</div><div class="command-desc">${esc(command.description||"No description provided.")}</div></article>`).join(""):'<div class="records-empty">No matching commands.</div>';
      }
      async function refresh(){
        const badge=document.getElementById("status-pill");
        let data=null;
        try{
          const response=await fetch("/api/community/status",{headers:{"Accept":"application/json"}});
          data=await response.json();
          if(!response.ok)throw new Error(data.error||"Status unavailable");
          const online=Boolean(data.online);
          badge.classList.toggle("offline",!online);
          badge.innerHTML=`<span class="status-dot"></span>${online?"Operational":"Bot offline"}`;
          const cards=[
            ["Uptime",uptime(data.uptime_seconds),data.bot_name||"NexTech Bot"],
            ["NexTech members",data.member_count==null?"—":Number(data.member_count).toLocaleString(),data.server_name||"Primary Discord server"],
            ["Servers",Number(data.server_count||0).toLocaleString(),data.network_member_count==null?"Connected Discord servers":`${Number(data.network_member_count).toLocaleString()} combined member count; may include people in multiple servers`],
            ["Latency",data.latency_ms==null?"—":`${Number(data.latency_ms)} ms`,online?"Discord gateway":"Unavailable"]
          ];
          document.getElementById("status-cards").innerHTML=cards.map(([label,value,note])=>`<article class="stat-card"><div class="stat-label">${esc(label)}</div><div class="stat-value">${esc(value)}</div><div class="stat-note">${esc(note)}</div></article>`).join("");
        }catch(error){
          badge.classList.add("offline");
          badge.innerHTML=`<span class="status-dot"></span>${esc(error.message||"Status unavailable")}`;
        }
        try{
          const commandsResponse=await fetch("/api/commands");
          if(!commandsResponse.ok)throw new Error("Commands are temporarily unavailable.");
          const result=await commandsResponse.json();
          commands=Array.isArray(result)?result:[];
          renderCommands();
        }catch(error){
          commands=[];
          document.getElementById("status-commands").innerHTML=`<div class="records-empty">${esc(error.message||"Commands are temporarily unavailable.")}</div>`;
        }
      }
      document.getElementById("status-command-search").addEventListener("input",renderCommands);
      document.getElementById("status-refresh").addEventListener("click",refresh);
      refresh();
      window.setInterval(refresh,30000);
    })();
"""