import sys
import importlib
from pathlib import Path
from pyrogram import Client, idle, __version__
from pyrogram.raw.all import layer
import time
import traceback
from pyrogram.errors import FloodWait
import asyncio
from collections import OrderedDict
from datetime import date, datetime
import pytz
from aiohttp import web
from database.ia_filterdb import Media, Media2
from database.users_chats_db import db
from info import *
from utils import temp
from Script import script
from plugins import web_server, check_expired_premium, keep_alive
from dreamxbotz.Bot import dreamxbotz
from dreamxbotz.util.keepalive import ping_server
from dreamxbotz.Bot.clients import initialize_clients
from PIL import Image
Image.MAX_IMAGE_PIXELS = 500_000_000

import logging
import logging.config

logging.config.fileConfig('logging.conf')
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("imdbpy").setLevel(logging.ERROR)
logging.getLogger("aiohttp").setLevel(logging.ERROR)
logging.getLogger("aiohttp.web").setLevel(logging.ERROR)
logging.getLogger("pymongo").setLevel(logging.WARNING)

botStartTime = time.time()

def _tg_alert(token, chat_id, text):
    try:
        import urllib.request as _req, urllib.parse as _parse
        _data = _parse.urlencode({
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'HTML',
            'disable_web_page_preview': 'true'
        }).encode()
        _req.urlopen(
            f'https://api.telegram.org/bot{token}/sendMessage',
            data=_data, timeout=8
        )
    except Exception:
        pass


def _register_handler_direct(dispatcher, handler, group):
    """
    Bypass Client.add_handler() which calls Dispatcher.add_handler() which
    uses self.loop.create_task(fn()) -- but self.loop was captured at __init__
    time (before asyncio.run() creates the real event loop), so it points to
    a DEAD loop.  create_task schedules tasks that NEVER execute => handlers
    are silently dropped => bot ignores every message.
    Fix: write synchronously to dispatcher.groups.
    Returns True on success; caller falls back to client.add_handler().
    """
    try:
        if group not in dispatcher.groups:
            dispatcher.groups[group] = []
            dispatcher.groups = OrderedDict(sorted(dispatcher.groups.items()))
        if handler not in dispatcher.groups[group]:
            dispatcher.groups[group].append(handler)
        return True
    except Exception:
        return False


async def _auto_reconnect_watchdog():
    while True:
        await asyncio.sleep(300)
        try:
            await asyncio.wait_for(dreamxbotz.get_me(), timeout=30)
        except asyncio.TimeoutError:
            logging.warning("Watchdog: get_me() timed out -- reconnecting...")
            try:
                await dreamxbotz.stop()
                await asyncio.sleep(5)
                await dreamxbotz.start()
                logging.info("Watchdog: reconnected after timeout.")
            except Exception as _re:
                logging.error(f"Watchdog: reconnect failed: {_re}")
        except (OSError, ConnectionError) as _e:
            logging.warning(f"Watchdog: network error ({_e}) -- reconnecting...")
            try:
                await dreamxbotz.stop()
                await asyncio.sleep(5)
                await dreamxbotz.start()
                logging.info("Watchdog: reconnected after network error.")
            except Exception as _re:
                logging.error(f"Watchdog: reconnect failed: {_re}")
        except Exception:
            pass


async def dreamxbotz_start():
    print('\n\nStarting Moviebot123...')

    # Start web server FIRST so Railway health-check passes
    try:
        app = web.AppRunner(await web_server())
        await app.setup()
        await web.TCPSite(app, "0.0.0.0", PORT).start()
        logging.info(f"Web server started on port {PORT}")
    except Exception as _web_err:
        logging.error(f"Web server failed to start: {_web_err}")

    # ── pyrofork dead-loop fix ────────────────────────────────────────────────
    # Dispatcher.__init__ does:
    #     self.loop = asyncio.get_event_loop()   ← captured BEFORE asyncio.run()
    #     self.updates_queue = asyncio.Queue()   ← bound to that dead loop
    # Then Dispatcher.start() does:
    #     self.loop.create_task(self.handler_worker(...))  ← 60 workers on dead loop
    # Workers are scheduled on a loop that is NEVER driven → they never run →
    # every incoming update stays in the queue forever → bot never responds.
    #
    # Fixing only client.loop (the old approach) is NOT enough; the Dispatcher
    # has its own self.loop field.  The correct fix: recreate the Dispatcher
    # AFTER asyncio.run() has started so self.loop captures the live running loop.
    # ─────────────────────────────────────────────────────────────────────────
    dreamxbotz.loop = asyncio.get_event_loop()  # fix client-level loop too
    from pyrogram.dispatcher import Dispatcher as _Dispatcher
    dreamxbotz.dispatcher = _Dispatcher(dreamxbotz)  # fresh Dispatcher with live loop

    # Connect to Telegram -- retry on FloodWait
    while True:
        try:
            await dreamxbotz.start()
            break
        except FloodWait as e:
            logging.warning(f"FloodWait! Sleeping {e.value}s (web server stays up).")
            await asyncio.sleep(e.value)

    # =========================================================================
    # Plugin loader
    #
    # WHY importlib.import_module instead of exec_module:
    #   Uses full Python package machinery -- relative imports, sys.modules
    #   caching and proper package context all work correctly.
    #
    # WHY _register_handler_direct instead of client.add_handler:
    #   In pyrogram / pyrofork, Dispatcher.add_handler() does:
    #       self.loop.create_task(fn())
    #   self.loop is captured at Client.__init__ time, BEFORE asyncio.run()
    #   creates the actual running loop.  The stored loop is dead, so
    #   create_task schedules tasks that NEVER run => handlers are silently
    #   dropped => bot ignores every message.
    #   Fix: write directly and synchronously to dispatcher.groups.
    # =========================================================================
    plugin_dir = Path("plugins")
    plugin_paths = sorted([p for p in plugin_dir.glob("*.py") if p.stem not in ("__init__",)])
    total_registered = 0
    load_errors = []
    error_details = []

    for path in plugin_paths:
        plugin_name = path.stem
        import_path = f"plugins.{plugin_name}"
        try:
            if import_path in sys.modules:
                load = sys.modules[import_path]
            else:
                load = importlib.import_module(import_path)

            for name in vars(load).keys():
                try:
                    obj = getattr(load, name)
                    if not callable(obj) or not hasattr(obj, "handlers"):
                        continue
                    for handler, group in obj.handlers:
                        if _register_handler_direct(dreamxbotz.dispatcher, handler, group):
                            total_registered += 1
                        else:
                            dreamxbotz.add_handler(handler, group)
                            total_registered += 1
                except Exception:
                    pass
            logging.info(f"\u2705 Plugin loaded: {plugin_name}")
        except Exception as _plug_err:
            _full_trace = traceback.format_exc()
            short_err = str(_plug_err)
            load_errors.append(f"{plugin_name}: {short_err}")
            error_details.append(
                f"<b>{plugin_name}</b>: <code>{short_err[:200]}</code>"
                f"\n<pre>{_full_trace[-500:]}</pre>"
            )
            logging.error(f"\u274c Plugin load failed: {plugin_name}\n{_full_trace}")

    logging.info(f"Plugins: {len(plugin_paths)} files, {total_registered} handlers, {len(load_errors)} errors")

    # Diagnostic
    try:
        total_handlers = sum(len(v) for v in dreamxbotz.dispatcher.groups.values())
    except Exception:
        total_handlers = -1

    _diag_lines = [
        "\U0001f50d <b>Plugin Diagnostics</b>",
        "",
        f"\U0001f4e6 Dispatcher handlers: <code>{total_handlers}</code>",
        f"\U0001f9e9 Plugins: <code>{len(plugin_paths)}</code> files, <code>{total_registered}</code> handlers registered",
    ]
    if load_errors:
        _diag_lines.append(f"\u26a0\ufe0f Load errors ({len(load_errors)}):")
        for ed in error_details[:3]:
            _diag_lines.append(ed)
    _diag_text = "\n".join(_diag_lines)

    try:
        await dreamxbotz.send_message(LOG_CHANNEL, _diag_text)
    except Exception as _de:
        logging.warning(f"Could not send diagnostics: {_de}")

    bot_info = await dreamxbotz.get_me()
    dreamxbotz.username = bot_info.username
    try:
        await initialize_clients()
    except Exception as _ic_err:
        logging.error(f"initialize_clients() failed (skipping multi-client): {_ic_err}")

    if ON_HEROKU:
        asyncio.create_task(ping_server())

    try:
        b_users, b_chats = await db.get_banned()
        temp.BANNED_USERS = b_users
        temp.BANNED_CHATS = b_chats
    except Exception as _db_err:
        logging.warning(f"Could not load banned list: {_db_err}")

    try:
        await Media.ensure_indexes()
        if MULTIPLE_DB:
            await Media2.ensure_indexes()
            print("Multiple Database Mode On.")
        else:
            print("Single DB Mode On!")
    except Exception as _idx_err:
        logging.warning(f"Could not ensure DB indexes: {_idx_err}")

    me = await dreamxbotz.get_me()
    temp.ME = me.id
    temp.U_NAME = me.username
    temp.B_NAME = me.first_name
    temp.B_LINK = me.mention
    dreamxbotz.username = '@' + me.username

    asyncio.create_task(check_expired_premium(dreamxbotz))
    logging.info(f"{me.first_name} with Pyrogram v{__version__} (Layer {layer}) started on {me.username}.")
    logging.info(LOG_STR)
    logging.info(script.LOGO)

    tz = pytz.timezone('Asia/Kolkata')
    today = date.today()
    now = datetime.now(tz)
    time_str = now.strftime("%H:%M:%S %p")
    try:
        await dreamxbotz.send_message(
            chat_id=LOG_CHANNEL,
            text=script.RESTART_TXT.format(temp.B_LINK, today, time_str)
        )
    except Exception as _e:
        logging.warning(f"Could not send restart message: {_e}")

    from pyrogram.types import (BotCommand, BotCommandScopeDefault,
                                BotCommandScopeAllPrivateChats)
    user_commands = [
        BotCommand("start",        "Check if bot is alive"),
        BotCommand("help",         "Help & support menu"),
        BotCommand("movies",       "Browse latest movies"),
        BotCommand("series",       "Browse latest series"),
        BotCommand("top_search",   "Top searched movies"),
        BotCommand("imdb",         "Search IMDB info"),
        BotCommand("settings",     "Your personal settings"),
        BotCommand("set_caption",  "Set custom file caption"),
        BotCommand("set_template", "Set custom IMDB template"),
        BotCommand("verify",       "Verify your account"),
        BotCommand("plan",         "View premium plans & pricing"),
        BotCommand("myplan",       "Check your active premium plan"),
    ]
    admin_commands = user_commands + [
        BotCommand("status",          "Bot uptime & stats"),
        BotCommand("restart",         "Restart the bot"),
        BotCommand("logs",            "View bot logs"),
        BotCommand("broadcast",       "Broadcast to all users"),
        BotCommand("grp_broadcast",   "Broadcast to all groups"),
        BotCommand("info",            "Bot information"),
        BotCommand("id",              "Get user or chat ID"),
        BotCommand("reload",          "Reload bot settings"),
        BotCommand("send",            "Send message to a user"),
        BotCommand("deletefiles",     "Delete files from DB"),
        BotCommand("deleteall",       "Delete all files from DB"),
        BotCommand("set_fsub",        "Set force subscribe channel"),
        BotCommand("set_log_channel", "Set log channel"),
        BotCommand("set_shortner",    "Set URL shortener"),
        BotCommand("set_time",        "Set file auto-delete time"),
        BotCommand("set_tutorial",    "Set tutorial link"),
        BotCommand("pm_search",       "Toggle PM search"),
        BotCommand("movie_update",    "Toggle movie update alerts"),
        BotCommand("trendlist",       "View trending list"),
        BotCommand("trial_reset",     "Reset user free trial"),
        BotCommand("clear_junk",      "Clear junk files"),
        BotCommand("details",         "Get file details"),
    ]
    try:
        await dreamxbotz.set_bot_commands(user_commands, scope=BotCommandScopeDefault())
        await dreamxbotz.set_bot_commands(
            admin_commands, scope=BotCommandScopeAllPrivateChats()
        )
        logging.info("Bot commands menu set successfully.")
    except Exception as _e:
        logging.warning(f"Could not set bot commands: {_e}")

    asyncio.create_task(keep_alive())
    asyncio.create_task(_auto_reconnect_watchdog())
    logging.info("Auto-reconnect watchdog started.")
    try:
        await idle()
    finally:
        # Stop cleanly so the next asyncio.run() restart does not
        # inherit pyrogram state bound to this now-closed event loop.
        try:
            await dreamxbotz.stop()
        except Exception:
            pass


if __name__ == '__main__':
    while True:
        try:
            asyncio.run(dreamxbotz_start())
            break
        except KeyboardInterrupt:
            logging.info('Service Stopped Bye \U0001f44b')
            break
        except Exception as e:
            tb = traceback.format_exc()
            logging.error(f'Bot crashed: {e}\n{tb}')
            _tg_alert(
                BOT_TOKEN, LOG_CHANNEL,
                f"\U0001f534 <b>Bot Crashed</b>\n\n"
                f"<b>Error:</b> <code>{str(e)[:400]}</code>\n\n"
                f"<b>Traceback:</b>\n<pre>{tb[-1200:]}</pre>"
            )
            logging.info('Restarting in 10 seconds...')
            time.sleep(10)
