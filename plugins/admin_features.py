import asyncio
import datetime
import html
import pytz
import logging

from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database.users_chats_db import db
from database.config_db import mdb
from database.ia_filterdb import Media, Media2
from info import ADMINS, IS_VERIFY, LOG_CHANNEL, MULTIPLE_DB, STAR_PREMIUM_PLANS, TIMEZONE
from utils import temp, save_group_settings, get_settings

logger = logging.getLogger(__name__)

# TIMEZONE defined locally — not exported by info.py

# Per-user request cooldown: user_id -> last timestamp
_req_cooldown: dict = {}
_REQ_COOLDOWN_SEC = 60          # 1 request per user per minute


# ─────────────────────────────────────────────────────────────────
#  /ban  &  /unban  (admin only)
# ─────────────────────────────────────────────────────────────────

@Client.on_message(filters.command("ban") & filters.user(ADMINS))
async def ban_user_cmd(client, message):
    """Usage: /ban <user_id> [reason]"""
    args = message.command[1:]
    if not args:
        return await message.reply_text(
            "<b>Usage:</b> <code>/ban &lt;user_id&gt; [reason]</code>\n"
            "<b>Example:</b> <code>/ban 123456789 Spam</code>"
        )
    try:
        user_id = int(args[0])
    except ValueError:
        return await message.reply_text("❌ Invalid user ID — must be a number.")

    reason = " ".join(args[1:]) or "No reason provided"
    ban_status = await db.get_ban_status(user_id)
    if ban_status.get("is_banned"):
        return await message.reply_text(
            f"⚠️ User <code>{user_id}</code> is already banned.\n"
            f"📝 Reason: {ban_status.get('ban_reason', 'N/A')}"
        )

    await db.ban_user(user_id, reason)
    # Bug fix: sync in-memory banned list so the filter takes effect immediately
    if user_id not in temp.BANNED_USERS:
        temp.BANNED_USERS.append(user_id)
    await message.reply_text(
        f"🚫 <b>User Banned</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"📝 Reason: {html.escape(reason)}"
    )
    try:
        await client.send_message(
            chat_id=LOG_CHANNEL,
            text=(
                f"<b>🚫 #BANNED\n\n"
                f"🆔 User ID: <code>{user_id}</code>\n"
                f"📝 Reason: {html.escape(reason)}\n"
                f"👮 Admin: {message.from_user.mention}</b>"
            )
        )
    except Exception:
        pass


@Client.on_message(filters.command("unban") & filters.user(ADMINS))
async def unban_user_cmd(client, message):
    """Usage: /unban <user_id>"""
    args = message.command[1:]
    if not args:
        return await message.reply_text("<b>Usage:</b> <code>/unban &lt;user_id&gt;</code>")
    try:
        user_id = int(args[0])
    except ValueError:
        return await message.reply_text("❌ Invalid user ID — must be a number.")

    ban_status = await db.get_ban_status(user_id)
    if not ban_status.get("is_banned"):
        return await message.reply_text(
            f"⚠️ User <code>{user_id}</code> is not currently banned."
        )

    await db.remove_ban(user_id)
    # Bug fix: remove from in-memory list immediately so the filter stops blocking them
    try:
        temp.BANNED_USERS.remove(user_id)
    except ValueError:
        pass
    await message.reply_text(f"✅ <b>User Unbanned</b>\n\n🆔 ID: <code>{user_id}</code>")
    try:
        await client.send_message(
            chat_id=LOG_CHANNEL,
            text=(
                f"<b>✅ #UNBANNED\n\n"
                f"🆔 User ID: <code>{user_id}</code>\n"
                f"👮 Admin: {message.from_user.mention}</b>"
            )
        )
    except Exception:
        pass


@Client.on_message(filters.command("banned") & filters.user(ADMINS))
async def banned_list_cmd(client, message):
    """List all banned users. get_banned() returns (user_ids, chat_ids)."""
    try:
        b_users, b_chats = await db.get_banned()
    except Exception:
        return await message.reply_text("❌ Could not fetch banned users.")

    if not b_users:
        return await message.reply_text("✅ No users are currently banned.")

    lines = "\n".join(f"• <code>{uid}</code>" for uid in b_users[:50])
    total = len(b_users)
    suffix = f"\n<i>…and {total - 50} more</i>" if total > 50 else ""
    await message.reply_text(
        f"<b>🚫 Banned Users ({total})</b>\n\n{lines}{suffix}"
    )


# ─────────────────────────────────────────────────────────────────
#  /trending
# ─────────────────────────────────────────────────────────────────

@Client.on_message(filters.command("trending") & (filters.group | filters.private))
async def trending_searches(client, message):
    try:
        top = await mdb.get_top_messages(limit=15)
    except Exception:
        return await message.reply_text("❌ Could not fetch trending searches.")

    if not top:
        return await message.reply_text("📊 No trending searches recorded yet.")

    medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 12
    lines = "\n".join(
        f"{medals[i]} <code>{html.escape(str(term))}</code>"
        for i, term in enumerate(top)
    )
    await message.reply_text(
        f"<b>🔥 Trending Searches</b>\n\n{lines}\n\n"
        f"<i>Top {len(top)} most searched terms</i>"
    )


# ─────────────────────────────────────────────────────────────────
#  /buy_premium — Telegram Stars payment
# ─────────────────────────────────────────────────────────────────

_PLAN_LABELS = {
    "7day":   "7 Days",
    "15day":  "15 Days",
    "1month": "1 Month",
    "45day":  "45 Days",
    "60day":  "2 Months",
}


def _parse_duration(plan_str: str) -> datetime.timedelta:
    """Convert '7day', '1month', '45day' → timedelta. Returns 30 days on error."""
    try:
        if "month" in plan_str:
            months = int(plan_str.replace("month", "").strip() or "1")
            return datetime.timedelta(days=months * 30)
        if "day" in plan_str:
            days = int(plan_str.replace("day", "").strip())
            return datetime.timedelta(days=days)
    except (ValueError, AttributeError):
        pass
    return datetime.timedelta(days=30)   # safe fallback


@Client.on_message(filters.command("buy_premium") & filters.private)
async def buy_premium(client, message):
    user_id = message.from_user.id
    has_prem = await db.has_premium_access(user_id)
    status_line = (
        "✅ You already have Premium! Buying again extends your plan."
        if has_prem
        else "⭐ Pay with Telegram Stars — instant activation, no card needed."
    )

    buttons = [
        [InlineKeyboardButton(
            f"⭐ {stars} Stars  ➜  {_PLAN_LABELS.get(plan, plan)}",
            callback_data=f"stars_buy#{stars}#{plan}"
        )]
        for stars, plan in STAR_PREMIUM_PLANS.items()
    ]
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="close_data")])

    await message.reply_text(
        f"<b>💎 Premium Plans\n\n{status_line}\n\nChoose a plan to continue:</b>",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@Client.on_callback_query(filters.regex(r"^stars_buy#"))
async def stars_buy_callback(client, query):
    await query.answer()
    try:
        parts = query.data.split("#")
        if len(parts) < 3:
            return
        stars = int(parts[1])
        plan = parts[2]
        label = _PLAN_LABELS.get(plan, plan)
    except (ValueError, IndexError):
        return

    try:
        from pyrogram.types import LabeledPrice
        await client.send_invoice(
            chat_id=query.from_user.id,
            title=f"💎 Premium — {label}",
            description=(
                f"Get Premium access for {label}.\n"
                f"✅ No ads  •  ✅ Priority results  •  ✅ Instant activation"
            ),
            payload=f"premium_{plan}",
            currency="XTR",
            prices=[LabeledPrice(label=f"Premium {label}", amount=stars)]
        )
    except Exception:
        # LabeledPrice not available in this pyrofork build — show manual info
        await query.message.reply_text(
            f"<b>⭐ Premium — {label}\n\n"
            f"Cost: {stars} Telegram Stars\n\n"
            f"Send {stars} Stars to this bot, then contact admin with your user ID to activate.\n"
            f"🆔 Your ID: <code>{query.from_user.id}</code></b>"
        )


@Client.on_message(filters.successful_payment & filters.private)
async def successful_payment_handler(client, message):
    try:
        payload = message.successful_payment.invoice_payload
        stars_paid = message.successful_payment.total_amount
        user_id = message.from_user.id

        plan_str = payload.replace("premium_", "")
        delta = _parse_duration(plan_str)
        label = _PLAN_LABELS.get(plan_str, plan_str)

        user_data = await db.get_user(user_id)
        current_expiry = user_data.get("expiry_time") if user_data else None
        now_utc = datetime.datetime.utcnow().replace(tzinfo=pytz.utc)
        base = now_utc
        if current_expiry:
            try:
                exp_aware = current_expiry.replace(tzinfo=pytz.utc)
                if exp_aware > now_utc:
                    base = exp_aware
            except Exception:
                pass

        expiry_utc = base + delta
        await db.update_one({"id": user_id}, {"$set": {"expiry_time": expiry_utc}})
        ist = pytz.timezone(TIMEZONE)
        expiry_ist = expiry_utc.astimezone(ist)

        await message.reply_text(
            f"🎉 <b>Payment Successful!</b>\n\n"
            f"💎 Plan: <b>{label}</b>\n"
            f"⭐ Stars Paid: <b>{stars_paid}</b>\n"
            f"📅 Expires: <b>{expiry_ist.strftime('%d %b %Y, %I:%M %p IST')}</b>\n\n"
            f"✅ Premium is now active. Enjoy!"
        )
        try:
            await client.send_message(
                chat_id=LOG_CHANNEL,
                text=(
                    f"<b>💎 #PREMIUM_PURCHASE\n\n"
                    f"👤 User: {message.from_user.mention}\n"
                    f"🆔 ID: <code>{user_id}</code>\n"
                    f"⭐ Stars: {stars_paid}\n"
                    f"📦 Plan: {label}\n"
                    f"📅 Expires: {expiry_ist.strftime('%d %b %Y, %I:%M %p IST')}</b>"
                )
            )
        except Exception:
            pass
    except Exception as e:
        logger.error(f"successful_payment error: {e}")


# ─────────────────────────────────────────────────────────────────
#  Auto Daily Stats  (sends to LOG_CHANNEL at midnight IST)
# ─────────────────────────────────────────────────────────────────

_scheduler_started = False


async def _build_stats_message() -> str:
    ist = pytz.timezone(TIMEZONE)
    now = datetime.datetime.now(ist)

    try:
        total_files = await Media.count_documents({})
        if MULTIPLE_DB:
            try:
                f2 = await Media2.count_documents({})
                total_files_str = f"{total_files} + {f2} = {total_files + f2}"
            except Exception:
                total_files_str = str(total_files)
        else:
            total_files_str = str(total_files)
    except Exception:
        total_files_str = "N/A"

    try:
        total_users = await db.total_users_count()
    except Exception:
        total_users = "N/A"

    try:
        total_chats = await db.total_chat_count()
    except Exception:
        total_chats = "N/A"

    try:
        premium_count = await db.all_premium_users()
    except Exception:
        premium_count = "N/A"

    try:
        top = await mdb.get_top_messages(limit=5)
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        trending_lines = "\n".join(
            f"  {medals[i]} {html.escape(str(term))}"
            for i, term in enumerate(top)
        ) if top else "  No data yet"
    except Exception:
        trending_lines = "  Unavailable"

    return (
        f"<b>📊 Daily Bot Report — {now.strftime('%d %b %Y')}</b>\n\n"
        f"👥 <b>Total Users:</b> {total_users}\n"
        f"💬 <b>Active Groups:</b> {total_chats}\n"
        f"💎 <b>Premium Users:</b> {premium_count}\n"
        f"📁 <b>Total Files:</b> {total_files_str}\n\n"
        f"🔥 <b>Top Searches Today:</b>\n{trending_lines}\n\n"
        f"<i>🤖 Auto-report • {now.strftime('%I:%M %p IST')}</i>"
    )


async def _daily_stats_loop(client):
    ist = pytz.timezone(TIMEZONE)
    while True:
        try:
            now = datetime.datetime.now(ist)
            tomorrow = (now + datetime.timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            wait_sec = (tomorrow - now).total_seconds()
            await asyncio.sleep(wait_sec)
            msg = await _build_stats_message()
            await client.send_message(chat_id=LOG_CHANNEL, text=msg)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[daily_stats] error: {e}")
            await asyncio.sleep(300)


@Client.on_message(filters.incoming, group=-999)
async def _maybe_start_scheduler(client, message):
    global _scheduler_started
    if not _scheduler_started:
        _scheduler_started = True
        asyncio.create_task(_daily_stats_loop(client))


@Client.on_message(filters.command("dailystats") & filters.user(ADMINS))
async def manual_daily_stats(client, message):
    m = await message.reply_text("📊 Generating stats...")
    try:
        text = await _build_stats_message()
        await client.send_message(chat_id=LOG_CHANNEL, text=text)
        await m.edit("✅ Daily stats sent to log channel!")
    except Exception as e:
        await m.edit(f"❌ Error: {html.escape(str(e))}")

# ─────────────────────────────────────────────────────────────────
#  /delete_lang — bulk-delete South Indian language files
#  Keeps any file that contains "Hindi", "Dual", or "Multi"
# ─────────────────────────────────────────────────────────────────
import re as _re

_LANG_PATTERNS = {
    "malayalam": r"(?i)malayalam",
    "tamil":     r"(?i)tamil",
    "telugu":    r"(?i)telugu",
    "all":       r"(?i)(malayalam|tamil|telugu)",
}
_LANG_LABELS = {
    "malayalam": "Malayalam",
    "tamil":     "Tamil",
    "telugu":    "Telugu",
    "all":       "Malayalam + Tamil + Telugu",
}
# Files containing these words are kept (they have Hindi audio too)
_KEEP_RE = _re.compile(r"(?i)(hindi|dual|multi)", _re.IGNORECASE)


def _lang_filter(lang_key: str) -> dict:
    """Build a MongoDB filter that matches language files but excludes Hindi/Dual/Multi."""
    lang_re = _re.compile(_LANG_PATTERNS[lang_key], _re.IGNORECASE)
    return {
        "$and": [
            {"file_name": {"$regex": _LANG_PATTERNS[lang_key], "$options": "i"}},
            {"file_name": {"$not": _re.compile(r"(?i)(hindi|dual|multi)")}},
        ]
    }


async def _count_lang(lang_key: str) -> tuple:
    """Return (db1_count, db2_count) for a language filter."""
    flt = _lang_filter(lang_key)
    try:
        c1 = await Media.count_documents(flt)
    except Exception:
        c1 = 0
    c2 = 0
    if MULTIPLE_DB:
        try:
            c2 = await Media2.count_documents(flt)
        except Exception:
            c2 = 0
    return c1, c2


@Client.on_message(filters.command("delete_lang") & filters.user(ADMINS))
async def delete_lang_cmd(client, message):
    """Show language selection buttons for bulk-delete."""
    buttons = [
        [
            InlineKeyboardButton("🎬 Malayalam", callback_data="dellang_pick#malayalam"),
            InlineKeyboardButton("🎬 Tamil",     callback_data="dellang_pick#tamil"),
        ],
        [
            InlineKeyboardButton("🎬 Telugu",    callback_data="dellang_pick#telugu"),
            InlineKeyboardButton("🗑 All 3",     callback_data="dellang_pick#all"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="dellang_cancel")],
    ]
    await message.reply_text(
        "<b>🗑 Delete Language Files\n\n"
        "Choose which language to bulk-delete.\n\n"
        "✅ Files with <u>Hindi / Dual / Multi</u> audio are automatically kept safe — "
        "only pure South Indian files will be removed.</b>",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@Client.on_callback_query(filters.regex(r"^dellang_pick#") & filters.user(ADMINS), group=-1)
async def dellang_pick(client, query):
    await query.answer()
    lang = query.data.split("#", 1)[1]
    if lang not in _LANG_PATTERNS:
        return
    label = _LANG_LABELS[lang]

    m = await query.message.edit_text("⏳ Counting files, please wait...")
    c1, c2 = await _count_lang(lang)
    total = c1 + c2

    if total == 0:
        return await m.edit_text(
            f"✅ No pure <b>{label}</b> files found in the database.\n"
            f"(Hindi/Dual/Multi files are already excluded.)"
        )

    db2_line = f"\n• DB2: <b>{c2}</b>" if MULTIPLE_DB else ""
    await m.edit_text(
        f"<b>🗑 Confirm Delete\n\n"
        f"Language: {label}\n"
        f"• DB1: <b>{c1}</b>{db2_line}\n"
        f"• Total: <b>{total}</b> files\n\n"
        f"⚠️ This cannot be undone!\n"
        f"Hindi/Dual/Multi files are safe.</b>",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Yes, Delete", callback_data=f"dellang_confirm#{lang}"),
                InlineKeyboardButton("❌ Cancel",      callback_data="dellang_cancel"),
            ]
        ])
    )


@Client.on_callback_query(filters.regex(r"^dellang_confirm#") & filters.user(ADMINS), group=-1)
async def dellang_confirm(client, query):
    await query.answer()
    lang = query.data.split("#", 1)[1]
    if lang not in _LANG_PATTERNS:
        return
    label = _LANG_LABELS[lang]

    m = await query.message.edit_text(f"🗑 Deleting <b>{label}</b> files...")

    flt = _lang_filter(lang)
    deleted1 = deleted2 = 0

    try:
        r1 = await Media.collection.delete_many(flt)
        deleted1 = r1.deleted_count
    except Exception as e:
        logger.error(f"[delete_lang] DB1 error: {e}")

    if MULTIPLE_DB:
        try:
            r2 = await Media2.collection.delete_many(flt)
            deleted2 = r2.deleted_count
        except Exception as e:
            logger.error(f"[delete_lang] DB2 error: {e}")

    total = deleted1 + deleted2
    db2_line = f"\n• DB2: <b>{deleted2}</b> deleted" if MULTIPLE_DB else ""

    await m.edit_text(
        f"<b>✅ Done!\n\n"
        f"🗑 {label} files removed\n"
        f"• DB1: <b>{deleted1}</b> deleted{db2_line}\n"
        f"• Total: <b>{total}</b> files removed</b>"
    )
    try:
        await client.send_message(
            chat_id=LOG_CHANNEL,
            text=(
                f"<b>#LANG_DELETE\n\n"
                f"🌐 Language: {label}\n"
                f"🗑 Deleted: {total} files (DB1:{deleted1} DB2:{deleted2})</b>"
            )
        )
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^dellang_cancel$") & filters.user(ADMINS), group=-1)
async def dellang_cancel(client, query):
    await query.answer("Cancelled.")
    await query.message.edit_text("❌ Cancelled — no files were deleted.")


# ─────────────────────────────────────────────────────────────────
#  /enableverify, /disableverify, /enableshortner, /disableshortner (admin only)
# ─────────────────────────────────────────────────────────────────
@Client.on_message(filters.command(["enableverify", "disableverify", "enableshortner", "disableshortner"]) & filters.user(ADMINS))
async def toggle_verify_cmd(client, message):
    """
    In a group:  /enableverify, /disableverify, /enableshortner, /disableshortner
    In PM:       /disableverify [all|pm|global]  -> Toggles globally & for PM
                 /disableverify <group_id>       -> Toggles for specific group
    """
    cmd = message.command[0].lower()
    enable = cmd in ["enableverify", "enableshortner"]

    # Determine target chat
    if message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        grp_id = message.chat.id
        grp_label = message.chat.title or str(grp_id)
        settings = await get_settings(grp_id)
        current = settings.get("is_verify", IS_VERIFY)
        if enable and current:
            return await message.reply_text(
                f"ℹ️ Verification is already <b>enabled</b> for <code>{grp_id}</code>."
            )
        if not enable and not current:
            return await message.reply_text(
                f"ℹ️ Verification is already <b>disabled</b> for <code>{grp_id}</code>."
            )
        await save_group_settings(grp_id, "is_verify", enable)
        status_text = "✅ <b>Enabled</b>" if enable else "❌ <b>Disabled</b>"
        await message.reply_text(
            f"{status_text} verification/shortener for group <code>{grp_id}</code>\n"
        )
    else:
        # PM
        args = message.command[1:]
        if not args or args[0].lower() in ["all", "global", "pm", "bot"]:
            import info
            info.IS_VERIFY = enable
            bot_id = getattr(temp, 'ME', 0)
            if bot_id:
                await db.update_bot_setting(bot_id, "IS_VERIFY", enable)
            temp.SETTINGS.clear()
            status_text = "✅ <b>Enabled</b>" if enable else "❌ <b>Disabled</b>"
            action_desc = (
                "Users will now be asked to verify via shortener before receiving files."
                if enable else
                "Users will now receive files <b>directly with zero shorteners, zero ads, and zero website redirects</b>!"
            )
            grp_label = "Global & PM"
            reply_msg = f"{status_text} <b>Shortener Verification globally & for PM!</b>\n\n{action_desc}"
            await message.reply_text(reply_msg)
        else:
            try:
                grp_id = int(args[0])
            except ValueError:
                return await message.reply_text(
                    "❌ Invalid group ID — must be a number (e.g. <code>-100123456789</code>) or <code>global</code>."
                )
            grp_label = str(grp_id)
            settings = await get_settings(grp_id)
            current = settings.get("is_verify", IS_VERIFY)
            if enable and current:
                return await message.reply_text(
                    f"ℹ️ Verification is already <b>enabled</b> for <code>{grp_id}</code>."
                )
            if not enable and not current:
                return await message.reply_text(
                    f"ℹ️ Verification is already <b>disabled</b> for <code>{grp_id}</code>."
                )
            await save_group_settings(grp_id, "is_verify", enable)
            status_text = "✅ <b>Enabled</b>" if enable else "❌ <b>Disabled</b>"
            await save_group_settings(grp_id, "is_verify", enable)
            status_text = "✅ <b>Enabled</b>" if enable else "❌ <b>Disabled</b>"
            await message.reply_text(f"{status_text} verification for group <code>{grp_id}</code>\n<b>Group:</b> {html.escape(grp_label)}")

    try:
        toggle_state = "ON" if enable else "OFF"
        await client.send_message(
            chat_id=LOG_CHANNEL,
            text=f"<b>#VERIFY_TOGGLE\n\n👤 Admin: {message.from_user.mention}\n🏠 Target: {html.escape(grp_label)}\n🔘 Verification / Shortener: {toggle_state}</b>"
        )
    except Exception:
        pass
        pass

# ─────────────────────────────────────────────────────────────────
#  /admincommandlist (admin only)
# ─────────────────────────────────────────────────────────────────
@Client.on_message(filters.command("admincommandlist") & filters.user(ADMINS))
async def admin_command_list(client, message):
    text = """<b>🛠️ Admin Commands List:</b>

<b>Bot Management:</b>
• <code>/status</code> - Bot uptime & stats
• <code>/restart</code> - Restart the bot
• <code>/logs</code> - View bot logs
• <code>/info</code> - Bot information
• <code>/id</code> - Get user or chat ID
• <code>/reload</code> - Reload bot settings
• <code>/maintenance</code> - Toggle maintenance mode

<b>Database & Files:</b>
• <code>/clear_junk</code> - Clear junk files
• <code>/details</code> - Get file details

<b>Broadcast & Users:</b>
• <code>/broadcast</code> - Broadcast to all users
• <code>/grp_broadcast</code> - Broadcast to all groups
• <code>/send</code> - Send message to a user
• <code>/ban</code> - Ban a user
• <code>/unban</code> - Unban a user
• <code>/trial_reset</code> - Reset user free trial

<b>Settings Configuration:</b>
• <code>/set_fsub</code> - Set force subscribe channel
• <code>/set_log_channel</code> - Set log channel
• <code>/set_shortner</code> - Set URL shortener
• <code>/set_time</code> - Set file auto-delete time
• <code>/disableverify</code> or <code>/disableshortner</code> - Disable shorteners/ads (PM or Group)
• <code>/enableverify</code> or <code>/enableshortner</code> - Enable shorteners/ads
• <code>/set_tutorial</code> - Set tutorial link
• <code>/pm_search</code> - Toggle PM search
• <code>/movie_update</code> - Toggle movie update alerts
• <code>/trendlist</code> - View trending list
"""
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
