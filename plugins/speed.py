import time
import os
import io
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from info import ADMINS, BIN_CHANNEL

DC_INFO = {
    1: ("Miami, USA 🇺🇸", "US East"),
    2: ("Amsterdam, EU 🇳🇱", "EU West (Frankfurt)"),
    3: ("Miami, USA 🇺🇸", "US East"),
    4: ("Amsterdam, EU 🇳🇱", "EU West (Frankfurt)"),
    5: ("Singapore 🇸🇬", "Asia Pacific (Singapore)"),
}


@Client.on_message(filters.command("ping"))
async def ping_cmd(client: Client, message: Message):
    start = time.time()
    reply = await message.reply_text("🏓 Pong!")
    elapsed = (time.time() - start) * 1000
    await reply.edit_text(
        f"🏓 <b>Pong!</b>\n"
        f"⚡ <b>Speed:</b> <code>{elapsed:.0f} ms</code>",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.command("speedtest") & filters.user(ADMINS))
async def speedtest_cmd(client: Client, message: Message):
    msg = await message.reply_text(
        "<b>🚀 Speed Test Starting...</b>\n<i>⏳ Please wait ~20 seconds</i>",
        parse_mode=enums.ParseMode.HTML
    )

    try:
        # ── DC Detection ─────────────────────────────────────────────────
        try:
            dc_id = await client.storage.dc_id()
        except Exception:
            dc_id = None

        dc_location, dc_best_region = DC_INFO.get(dc_id, ("Unknown", "Unknown"))

        # ── Ping ─────────────────────────────────────────────────────────
        ping_start = time.time()
        await client.get_me()
        ping_ms = (time.time() - ping_start) * 1000

        # ── Upload ───────────────────────────────────────────────────────
        await msg.edit_text(
            f"<b>📡 Telegram DC:</b> <code>DC{dc_id} — {dc_location}</code>\n"
            f"<b>🏓 Ping:</b> <code>{ping_ms:.0f} ms</code>\n\n"
            "<b>📤 Testing upload speed...</b>",
            parse_mode=enums.ParseMode.HTML
        )
        test_size = 5 * 1024 * 1024
        test_data = io.BytesIO(os.urandom(test_size))
        test_data.name = "speedtest.bin"

        upload_start = time.time()
        sent = await client.send_document(
            chat_id=BIN_CHANNEL,
            document=test_data,
            file_name="speedtest.bin",
            caption="⚡ Speed Test — auto-delete"
        )
        upload_time = time.time() - upload_start
        upload_speed = test_size / upload_time / (1024 * 1024)

        # ── Download ─────────────────────────────────────────────────────
        await msg.edit_text(
            f"<b>📡 DC:</b> <code>DC{dc_id} — {dc_location}</code>\n"
            f"<b>🏓 Ping:</b> <code>{ping_ms:.0f} ms</code>\n"
            f"<b>⬆️ Upload:</b> <code>{upload_speed:.2f} MB/s</code>\n\n"
            "<b>📥 Testing download speed...</b>",
            parse_mode=enums.ParseMode.HTML
        )
        dl_start = time.time()
        await client.download_media(sent.document.file_id, in_memory=True)
        dl_time = time.time() - dl_start
        dl_speed = test_size / dl_time / (1024 * 1024)

        await sent.delete()

        # ── Rating ───────────────────────────────────────────────────────
        if dl_speed >= 10:
            rating = "🟢 Excellent"
        elif dl_speed >= 5:
            rating = "🟡 Good"
        elif dl_speed >= 2:
            rating = "🟠 Average"
        else:
            rating = "🔴 Poor"

        result = (
            "<b>📊 Speed Test Results</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📡 <b>Telegram DC:</b> <code>DC{dc_id} — {dc_location}</code>\n"
            f"🏓 <b>Ping:</b>        <code>{ping_ms:.0f} ms</code>\n"
            f"⬆️ <b>Upload:</b>      <code>{upload_speed:.2f} MB/s</code>\n"
            f"⬇️ <b>Download:</b>    <code>{dl_speed:.2f} MB/s</code>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📶 <b>Rating:</b> {rating}\n"
            f"💡 <b>Best server region:</b> <code>{dc_best_region}</code>\n"
            f"<i>Test size: 5 MB | Host: Railway</i>"
        )
        await msg.edit_text(result, parse_mode=enums.ParseMode.HTML)

    except Exception as e:
        await msg.edit_text(
            f"<b>❌ Speed Test Failed:</b>\n<code>{str(e)}</code>",
            parse_mode=enums.ParseMode.HTML
        )
