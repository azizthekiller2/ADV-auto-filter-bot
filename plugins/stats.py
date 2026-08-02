import time
import asyncio
import shutil
import os
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from info import ADMINS
from database.users_chats_db import db
from database.ia_filterdb import Media, Media2, db as primary_db, db2 as secondary_db
from utils import get_readable_time
from dreamxbotz.Bot import multi_clients, work_loads

START_TIME = time.time()

DB_MAX_MB = 512.0


def _bar(used_pct: float, length: int = 10) -> str:
    filled = round(used_pct / 100 * length)
    return "█" * filled + "░" * (length - filled)


async def _db_size_mb(motor_db) -> float:
    try:
        stats = await motor_db.command("dbstats")
        logical = stats.get("dataSize", 0)
        index   = stats.get("indexSize", 0)
        return round((logical + index) / (1024 * 1024), 2)
    except Exception:
        return 0.0


async def _cpu_percent(interval: float = 0.5) -> float:
    def _read_stat():
        with open("/proc/stat") as f:
            line = f.readline()
        vals = list(map(int, line.split()[1:]))
        idle = vals[3]
        total = sum(vals)
        return idle, total

    idle1, total1 = _read_stat()
    await asyncio.sleep(interval)
    idle2, total2 = _read_stat()
    diff_idle  = idle2  - idle1
    diff_total = total2 - total1
    if diff_total == 0:
        return 0.0
    return round((1 - diff_idle / diff_total) * 100, 1)


def _mem_info():
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k.strip()] = int(v.strip().split()[0])
        total_mb = info["MemTotal"] / 1024
        avail_mb = info.get("MemAvailable", info.get("MemFree", 0)) / 1024
        used_mb  = total_mb - avail_mb
        pct      = round(used_mb / total_mb * 100, 1) if total_mb else 0
        return round(used_mb, 2), round(total_mb, 2), pct
    except Exception:
        return 0.0, 0.0, 0.0


def _disk_info():
    try:
        usage = shutil.disk_usage("/")
        total_gb = usage.total / (1024 ** 3)
        used_gb  = usage.used  / (1024 ** 3)
        pct      = round(used_gb / total_gb * 100, 1) if total_gb else 0
        return round(used_gb, 2), round(total_gb, 2), pct
    except Exception:
        return 0.0, 0.0, 0.0


@Client.on_message(filters.command("stats") & filters.user(ADMINS))
async def stats_cmd(client: Client, message: Message):
    msg = await message.reply_text(
        "<b>📊 Fetching stats...</b>",
        parse_mode=enums.ParseMode.HTML
    )

    try:
        (
            users, groups, files1, files2, premium,
            db1_mb, db2_mb, cpu_pct
        ) = await asyncio.gather(
            db.total_users_count(),
            db.total_chat_count(),
            Media.count_documents({}),
            Media2.count_documents({}),
            db.all_premium_users(),
            _db_size_mb(primary_db),
            _db_size_mb(secondary_db),
            _cpu_percent(0.5),
        )

        total_files = files1 + files2
        uptime_str  = get_readable_time(int(time.time() - START_TIME))

        db1_free = round(max(DB_MAX_MB - db1_mb, 0), 2)
        db2_free = round(max(DB_MAX_MB - db2_mb, 0), 2)
        db1_pct  = round(db1_mb / DB_MAX_MB * 100, 1)
        db2_pct  = round(db2_mb / DB_MAX_MB * 100, 1)

        mem_used, mem_total, mem_pct = _mem_info()
        disk_used, disk_total, disk_pct = _disk_info()

        ping_start = time.time()
        await client.get_me()
        latency_ms = round((time.time() - ping_start) * 1000)
        if latency_ms < 100:
            lat_emoji, lat_label = "🟢", "Excellent"
        elif latency_ms < 250:
            lat_emoji, lat_label = "🟡", "Good"
        elif latency_ms < 500:
            lat_emoji, lat_label = "🟠", "Average"
        else:
            lat_emoji, lat_label = "🔴", "Poor"

        num_clients = len(multi_clients)
        client_lines = ""
        for cid in sorted(multi_clients.keys()):
            c = multi_clients[cid]
            try:
                me   = await c.get_me()
                load = work_loads.get(cid, 0)
                label = "Main Bot" if cid == 0 else f"Client {cid}"
                client_lines += f"  ✅ {label}: @{me.username} [{load} req]\n"
            except Exception:
                label = "Main Bot" if cid == 0 else f"Client {cid}"
                client_lines += f"  ⚠️ {label}: Offline\n"

        text = (
            "<b>📊 Bot Statistics</b>\n\n"

            "<b>👥 Users &amp; Groups</b>\n"
            f"├ Total Users    : <code>{users:,}</code>\n"
            f"├ Active Groups  : <code>{groups:,}</code>\n"
            f"└ Premium Users  : <code>{premium:,}</code>\n\n"

            "<b>🗄 Database 1 (Primary)</b>\n"
            f" ★ Total Files   : <code>{files1:,}</code>\n"
            f" ★ Used Storage  : <code>{db1_mb} MB</code>\n"
            f" <code>[{_bar(db1_pct)}] {db1_pct}%</code>\n"
            f" ★ Free Storage  : <code>{db1_free} MB</code>\n\n"

            "<b>🗄 Database 2 (Secondary)</b>\n"
            f" ★ Total Files   : <code>{files2:,}</code>\n"
            f" ★ Used Storage  : <code>{db2_mb} MB</code>\n"
            f" <code>[{_bar(db2_pct)}] {db2_pct}%</code>\n"
            f" ★ Free Storage  : <code>{db2_free} MB</code>\n\n"

            f"<b>📦 Total Files (All DBs) : <code>{total_files:,}</code></b>\n\n"

            "<b>💻 Bot Resource Usage</b>\n"
            f" 🔵 CPU    : <code>{cpu_pct}% [{_bar(cpu_pct)}]</code>\n"
            f" 🟣 Memory : <code>{mem_used} MB / {mem_total} MB ({mem_pct}%)</code>\n"
            f" <code>  [{_bar(mem_pct)}]</code>\n"
            f" ↳ 🟠 Disk : <code>{disk_used} GB / {disk_total} GB ({disk_pct}%)</code>\n"
            f" <code>  [{_bar(disk_pct)}]</code>\n\n"

            f"<b>⏱ Uptime  : <code>{uptime_str}</code></b>\n"
            f"<b>🚀 Latency : <code>{latency_ms} ms</code> — {lat_emoji} {lat_label}</b>\n\n"

            f"<b>🤖 Active Clients ({num_clients}):</b>\n"
            f"{client_lines}\n"
            "❤️ <b>Powered by AZIZ SER</b>"
        )

        await msg.edit_text(text, parse_mode=enums.ParseMode.HTML)

    except Exception as e:
        await msg.edit_text(
            f"<b>❌ Failed to fetch stats:</b>\n<code>{str(e)}</code>",
            parse_mode=enums.ParseMode.HTML
        )
