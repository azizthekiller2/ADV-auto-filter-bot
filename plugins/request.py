import asyncio
from datetime import datetime
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from info import ADMINS, LOG_CHANNEL, REQST_CHANNEL
import pytz

# Per-user cooldown store: {user_id: last_request_datetime_utc}
_request_cooldowns = {}
REQUEST_COOLDOWN_SECS = 300  # 5 minutes between requests per user

def _forward_channel():
    """Use REQST_CHANNEL if configured, otherwise fall back to LOG_CHANNEL."""
    return REQST_CHANNEL if REQST_CHANNEL is not None else LOG_CHANNEL


@Client.on_message(
    (filters.command(["request", "Request"]) | filters.regex(r"^#[Rr]equest")) &
    (filters.group | filters.private)
)
async def movie_request_handler(bot, message):
    user = message.from_user
    if not user:
        return  # anonymous admin, skip

    user_id = user.id
    mention = user.mention
    now = datetime.now(pytz.utc)

    # ── Rate limiting ─────────────────────────────────────────────────────────
    last = _request_cooldowns.get(user_id)
    if last:
        diff = (now - last).total_seconds()
        if diff < REQUEST_COOLDOWN_SECS:
            remaining = int(REQUEST_COOLDOWN_SECS - diff)
            mins, secs = divmod(remaining, 60)
            await message.reply_text(
                f"<b>⏳ ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ <code>{mins}ᴍ {secs}ꜱ</code> ʙᴇꜰᴏʀᴇ ꜱᴇɴᴅɪɴɢ ᴀɴᴏᴛʜᴇʀ ʀᴇǫᴜᴇꜱᴛ.</b>",
                parse_mode=enums.ParseMode.HTML
            )
            return

    # ── Extract movie name ────────────────────────────────────────────────────
    text = message.text or ""
    for prefix in ["/request", "/Request", "#request", "#Request"]:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break

    if not text:
        await message.reply_text(
            "<b>📝 ᴜꜱᴀɢᴇ:</b> <code>/request Movie Name</code>\n\n"
            "<b>ᴇxᴀᴍᴘʟᴇ:</b> <code>/request Jawan 2023</code>\n"
            "<b>ꜰᴏʀ ꜱᴇʀɪᴇꜱ:</b> <code>/request Loki S01E03</code>",
            parse_mode=enums.ParseMode.HTML
        )
        return

    if len(text) < 3:
        await message.reply_text(
            "<b>⚠️ ᴍᴏᴠɪᴇ ɴᴀᴍᴇ ᴛᴏᴏ ꜱʜᴏʀᴛ — ᴍɪɴɪᴍᴜᴍ 3 ᴄʜᴀʀᴀᴄᴛᴇʀꜱ.</b>",
            parse_mode=enums.ParseMode.HTML
        )
        return

    # ── Build the forwarded message ───────────────────────────────────────────
    chat = message.chat
    if chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        chat_info = f"{chat.title} (<code>{chat.id}</code>)"
    else:
        chat_info = "Private Chat / Bot PM"

    date_str = now.astimezone(pytz.timezone("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST")
    movie_short = text[:50]  # keep callback_data under Telegram 64-byte limit

    forward_text = (
        f"<b>🎬 #ᴍᴏᴠɪᴇʀᴇǫᴜᴇꜱᴛ\n\n"
        f"📝 ᴍᴏᴠɪᴇ : <u>{text}</u>\n\n"
        f"👤 ʀᴇǫᴜᴇꜱᴛᴇᴅ ʙʏ : {mention}\n"
        f"🆔 ᴜꜱᴇʀ ɪᴅ : <code>{user_id}</code>\n"
        f"💬 ᴄʜᴀᴛ : {chat_info}\n"
        f"📅 ᴅᴀᴛᴇ : <code>{date_str}</code></b>"
    )

    # callback_data max 64 bytes — keep movie_short at 50 chars
    fwd_channel = _forward_channel()
    try:
        await bot.send_message(
            chat_id=fwd_channel,
            text=forward_text,
            parse_mode=enums.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ ᴅᴏɴᴇ",            callback_data=f"rq_done#{user_id}"),
                    InlineKeyboardButton("❌ ɴᴏᴛ ᴀᴠᴀɪʟᴀʙʟᴇ",   callback_data=f"rq_na#{user_id}"),
                ],
                [
                    InlineKeyboardButton("📁 ᴀʟʀᴇᴀᴅʏ ɪɴ ᴅʙ",   callback_data=f"rq_indb#{user_id}"),
                    InlineKeyboardButton("🚫 ɪɢɴᴏʀᴇ",           callback_data=f"rq_skip#{user_id}"),
                ]
            ])
        )
    except Exception as e:
        await message.reply_text(
            f"<b>⚠️ ᴄᴏᴜʟᴅ ɴᴏᴛ ꜱᴜʙᴍɪᴛ ʀᴇǫᴜᴇꜱᴛ: {e}</b>",
            parse_mode=enums.ParseMode.HTML
        )
        return

    # ── Record cooldown & confirm to user ─────────────────────────────────────
    _request_cooldowns[user_id] = now

    await message.reply_text(
        f"<b>✅ ʀᴇǫᴜᴇꜱᴛ ꜱᴜʙᴍɪᴛᴛᴇᴅ!\n\n"
        f"🎬 ᴍᴏᴠɪᴇ : <code>{text}</code>\n\n"
        f"⏳ ᴏᴜʀ ᴛᴇᴀᴍ ᴡɪʟʟ ᴜᴘʟᴏᴀᴅ ɪᴛ ꜱᴏᴏɴ. ʏᴏᴜ ᴡɪʟʟ ʙᴇ ɴᴏᴛɪꜰɪᴇᴅ ʜᴇʀᴇ!</b>",
        parse_mode=enums.ParseMode.HTML
    )


# ── Admin callback: respond to movie requests ─────────────────────────────────

@Client.on_callback_query(filters.regex(r"^rq_(done|na|indb|skip)#(\d+)$"))
async def request_admin_callback(bot, callback_query):
    if callback_query.from_user.id not in ADMINS:
        await callback_query.answer("⛔ ᴀᴅᴍɪɴꜱ ᴏɴʟʏ!", show_alert=True)
        return

    data = callback_query.data          # e.g. "rq_done#123456789"
    parts = data.split("#")
    action = parts[0]                   # rq_done / rq_na / rq_indb / rq_skip
    user_id = int(parts[1])
    admin_name = callback_query.from_user.mention

    # Pull movie name from the forwarded message text (line 2)
    try:
        msg_lines = callback_query.message.text.split("\n")
        movie_line = next((l for l in msg_lines if "ᴍᴏᴠɪᴇ :" in l), "")
        movie_name = movie_line.split(":", 1)[-1].strip().strip("\u200b")
    except Exception:
        movie_name = "ʏᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ"

    if action == "rq_done":
        user_msg = (
            f"<b>🎉 ɢᴏᴏᴅ ɴᴇᴡꜱ! ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʜᴀꜱ ʙᴇᴇɴ ᴜᴘʟᴏᴀᴅᴇᴅ!\n\n"
            f"🎬 ᴍᴏᴠɪᴇ : <code>{movie_name}</code>\n\n"
            f"🔍 ꜱᴇᴀʀᴄʜ ɪᴛ ɪɴ ᴛʜᴇ ɢʀᴏᴜᴘ ɴᴏᴡ!</b>"
        )
        admin_ack = f"✅ ᴅᴏɴᴇ — ᴍᴀʀᴋᴇᴅ ʙʏ {admin_name}"

    elif action == "rq_na":
        user_msg = (
            f"<b>😔 ꜱᴏʀʀʏ, ᴛʜɪꜱ ᴍᴏᴠɪᴇ ɪꜱ ɴᴏᴛ ᴀᴠᴀɪʟᴀʙʟᴇ ʏᴇᴛ.\n\n"
            f"🎬 ᴍᴏᴠɪᴇ : <code>{movie_name}</code>\n\n"
            f"💡 ᴏᴛᴛ/ᴅᴠᴅ ʀᴇʟᴇᴀꜱᴇ ᴍᴀʏ ɴᴏᴛ ʜᴀᴠᴇ ʜᴀᴘᴘᴇɴᴇᴅ ʏᴇᴛ. ᴛʀʏ ʟᴀᴛᴇʀ!</b>"
        )
        admin_ack = f"❌ ɴᴏᴛ ᴀᴠᴀɪʟᴀʙʟᴇ — ᴍᴀʀᴋᴇᴅ ʙʏ {admin_name}"

    elif action == "rq_indb":
        user_msg = (
            f"<b>📁 ᴛʜɪꜱ ᴍᴏᴠɪᴇ ɪꜱ ᴀʟʀᴇᴀᴅʏ ɪɴ ᴏᴜʀ ᴅᴀᴛᴀʙᴀꜱᴇ!\n\n"
            f"🎬 ᴍᴏᴠɪᴇ : <code>{movie_name}</code>\n\n"
            f"🔍 ᴘʟᴇᴀꜱᴇ ꜱᴇᴀʀᴄʜ ᴡɪᴛʜ ᴄᴏʀʀᴇᴄᴛ ꜱᴘᴇʟʟɪɴɢ ɪɴ ᴛʜᴇ ɢʀᴏᴜᴘ.</b>"
        )
        admin_ack = f"📁 ᴀʟʀᴇᴀᴅʏ ɪɴ ᴅʙ — ᴍᴀʀᴋᴇᴅ ʙʏ {admin_name}"

    else:  # rq_skip
        await callback_query.answer("🚫 ʀᴇǫᴜᴇꜱᴛ ɪɢɴᴏʀᴇᴅ.", show_alert=True)
        try:
            await callback_query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    # Notify the requesting user (silently skip if they blocked the bot)
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
