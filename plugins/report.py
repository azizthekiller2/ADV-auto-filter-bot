import asyncio
from datetime import datetime
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from info import ADMINS, LOG_CHANNEL, REQST_CHANNEL
import pytz

# Per-user cooldown store: {user_id: last_report_datetime_utc}
_report_cooldowns = {}
REPORT_COOLDOWN_SECS = 300  # 5 minutes

def _forward_channel():
    """Use REQST_CHANNEL if configured, else fall back to LOG_CHANNEL."""
    return REQST_CHANNEL if REQST_CHANNEL is not None else LOG_CHANNEL


@Client.on_message(
    filters.command(["report", "Report"]) &
    (filters.group | filters.private)
)
async def report_handler(bot, message):
    user = message.from_user
    if not user:
        return  # anonymous admin, skip

    user_id = user.id
    mention = user.mention
    now = datetime.now(pytz.utc)

    # ── Rate limiting ──────────────────────────────────────────────────────────
    last = _report_cooldowns.get(user_id)
    if last:
        diff = (now - last).total_seconds()
        if diff < REPORT_COOLDOWN_SECS:
            remaining = int(REPORT_COOLDOWN_SECS - diff)
            mins, secs = divmod(remaining, 60)
            await message.reply_text(
                f"<b>⏳ ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ <code>{mins}ᴍ {secs}ꜱ</code> ʙᴇꜰᴏʀᴇ ꜱᴇɴᴅɪɴɢ ᴀɴᴏᴛʜᴇʀ ʀᴇᴘᴏʀᴛ.</b>",
                parse_mode=enums.ParseMode.HTML
            )
            return

    # ── Extract optional reason from command text ──────────────────────────────
    raw = message.text or ""
    for prefix in ["/report", "/Report"]:
        if raw.lower().startswith(prefix.lower()):
            raw = raw[len(prefix):].strip()
            break
    reason = raw[:200]  # cap at 200 chars

    # ── Extract file names from replied-to bot message ─────────────────────────
    file_lines = []
    reply = message.reply_to_message
    if reply and reply.reply_markup:
        for row in reply.reply_markup.inline_keyboard:
            for btn in row:
                if btn.callback_data and btn.callback_data.startswith("file#"):
                    # Button text looks like: "🔗 260MB ≽ BoJack.S04E03.mkv"
                    clean = btn.text.strip()
                    file_lines.append(f"• <code>{clean[:80]}</code>")
        file_lines = file_lines[:5]  # max 5 files per report

    # ── Require at least a reason or a file ───────────────────────────────────
    if not reason and not file_lines:
        await message.reply_text(
            "<b>📝 ᴜꜱᴀɢᴇ:</b>\n\n"
            "1️⃣ ʀᴇᴘʟʏ ᴛᴏ ᴀ ꜰɪʟᴇ ʀᴇꜱᴜʟᴛ ᴍᴇꜱꜱᴀɢᴇ ᴀɴᴅ ᴛʏᴘᴇ:\n"
            "   <code>/report [optional reason]</code>\n\n"
            "2️⃣ ᴏʀ ꜱᴇɴᴅ ᴀ ꜱᴛᴀɴᴅᴀʟᴏɴᴇ ʀᴇᴘᴏʀᴛ:\n"
            "   <code>/report wrong quality file</code>\n\n"
            "<b>ᴇxᴀᴍᴘʟᴇꜱ:</b>\n"
            "<code>/report audio out of sync</code>\n"
            "<code>/report wrong movie uploaded</code>",
            parse_mode=enums.ParseMode.HTML
        )
        return

    # ── Build forwarded report card ────────────────────────────────────────────
    chat = message.chat
    if chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        chat_info = f"{chat.title} (<code>{chat.id}</code>)"
    else:
        chat_info = "Private Chat / Bot PM"

    date_str = now.astimezone(
        pytz.timezone("Asia/Kolkata")
    ).strftime("%d %b %Y, %I:%M %p IST")

    reason_section = (
        f"⚠️ ʀᴇᴀꜱᴏɴ : <u>{reason}</u>\n\n" if reason else ""
    )
    files_section = (
        "📂 ʀᴇᴘᴏʀᴛᴇᴅ ꜰɪʟᴇꜱ :\n" + "\n".join(file_lines) + "\n\n"
        if file_lines else ""
    )

    forward_text = (
        f"<b>🚨 #ʙᴜɢʀᴇᴘᴏʀᴛ\n\n"
        f"{reason_section}"
        f"{files_section}"
        f"👤 ʀᴇᴘᴏʀᴛᴇᴅ ʙʏ : {mention}\n"
        f"🆔 ᴜꜱᴇʀ ɪᴅ : <code>{user_id}</code>\n"
        f"💬 ᴄʜᴀᴛ : {chat_info}\n"
        f"📅 ᴅᴀᴛᴇ : <code>{date_str}</code></b>"
    )

    fwd_channel = _forward_channel()
    try:
        await bot.send_message(
            chat_id=fwd_channel,
            text=forward_text,
            parse_mode=enums.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ ꜰɪxᴇᴅ",         callback_data=f"rp_fixed#{user_id}"),
                    InlineKeyboardButton("🗑️ ꜰɪʟᴇ ᴅᴇʟᴇᴛᴇᴅ", callback_data=f"rp_del#{user_id}"),
                ],
                [
                    InlineKeyboardButton("🚫 ɪɢɴᴏʀᴇ",         callback_data=f"rp_skip#{user_id}"),
                ]
            ])
        )
    except Exception as e:
        await message.reply_text(
            f"<b>⚠️ ᴄᴏᴜʟᴅ ɴᴏᴛ ꜱᴜʙᴍɪᴛ ʀᴇᴘᴏʀᴛ. ᴘʟᴇᴀꜱᴇ ᴛʀʏ ʟᴀᴛᴇʀ.</b>",
            parse_mode=enums.ParseMode.HTML
        )
        return

    _report_cooldowns[user_id] = now

    await message.reply_text(
        "<b>✅ ʀᴇᴘᴏʀᴛ ꜱᴜʙᴍɪᴛᴛᴇᴅ!\n\n"
        "⏳ ᴏᴜʀ ᴛᴇᴀᴍ ᴡɪʟʟ ʀᴇᴠɪᴇᴡ ɪᴛ ᴀɴᴅ ꜰɪx ᴛʜᴇ ɪꜱꜱᴜᴇ ꜱᴏᴏɴ.\n"
        "ʏᴏᴜ ᴡɪʟʟ ʙᴇ ɴᴏᴛɪꜰɪᴇᴅ ʜᴇʀᴇ!</b>",
        parse_mode=enums.ParseMode.HTML
    )


# ── Admin callback: act on a report ──────────────────────────────────────────

@Client.on_callback_query(filters.regex(r"^rp_(fixed|del|skip)#(\d+)$"))
async def report_admin_callback(bot, callback_query):
    if callback_query.from_user.id not in ADMINS:
        await callback_query.answer("⛔ ᴀᴅᴍɪɴꜱ ᴏɴʟʏ!", show_alert=True)
        return

    data  = callback_query.data          # e.g. "rp_fixed#123456789"
    parts = data.split("#")
    action  = parts[0]                   # rp_fixed / rp_del / rp_skip
    user_id = int(parts[1])
    admin_name = callback_query.from_user.mention

    if action == "rp_fixed":
        user_msg = (
            "<b>✅ ɢʀᴇᴀᴛ ɴᴇᴡꜱ! ᴛʜᴀɴᴋ ʏᴏᴜ ꜰᴏʀ ʀᴇᴘᴏʀᴛɪɴɢ!\n\n"
            "🔧 ᴛʜᴇ ɪꜱꜱᴜᴇ ʜᴀꜱ ʙᴇᴇɴ ꜰɪxᴇᴅ ʙʏ ᴏᴜʀ ᴛᴇᴀᴍ.\n\n"
            "🔍 ᴘʟᴇᴀꜱᴇ ꜱᴇᴀʀᴄʜ ᴀɢᴀɪɴ ɪɴ ᴛʜᴇ ɢʀᴏᴜᴘ!</b>"
        )
        admin_ack = f"✅ ꜰɪxᴇᴅ — ᴍᴀʀᴋᴇᴅ ʙʏ {admin_name}"

    elif action == "rp_del":
        user_msg = (
            "<b>🗑️ ᴛʜᴀɴᴋ ʏᴏᴜ ꜰᴏʀ ʀᴇᴘᴏʀᴛɪɴɢ!\n\n"
            "📁 ᴛʜᴇ ᴡʀᴏɴɢ ꜰɪʟᴇ ʜᴀꜱ ʙᴇᴇɴ ʀᴇᴍᴏᴠᴇᴅ ꜰʀᴏᴍ ᴏᴜʀ ᴅᴀᴛᴀʙᴀꜱᴇ.\n\n"
            "⬆️ ᴀ ᴄᴏʀʀᴇᴄᴛ ᴠᴇʀꜱɪᴏɴ ᴡɪʟʟ ʙᴇ ᴜᴘʟᴏᴀᴅᴇᴅ ꜱᴏᴏɴ!</b>"
        )
        admin_ack = f"🗑️ ꜰɪʟᴇ ᴅᴇʟᴇᴛᴇᴅ — ᴍᴀʀᴋᴇᴅ ʙʏ {admin_name}"

    else:  # rp_skip
        await callback_query.answer("🚫 ʀᴇᴘᴏʀᴛ ɪɢɴᴏʀᴇᴅ.", show_alert=True)
        try:
            await callback_query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    # Notify the user (silently skip if they blocked the bot)
    try:
        await bot.send_message(
            chat_id=user_id,
            text=user_msg,
            parse_mode=enums.ParseMode.HTML
        )
    except Exception:
        pass

    # Acknowledge admin and remove buttons
    await callback_query.answer(admin_ack, show_alert=True)
    try:
        await callback_query.message.edit_reply_markup(reply_markup=None)
        await callback_query.message.reply_text(
            f"<b>{admin_ack}</b>",
            parse_mode=enums.ParseMode.HTML
        )
    except Exception:
        pass
