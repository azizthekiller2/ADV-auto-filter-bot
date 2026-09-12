import os
import re, sys
import json
import base64
import logging
import random
import asyncio
import string
import pytz
from urllib.parse import quote_plus
from dreamxbotz.util.file_properties import get_name, get_hash
from Script import script
from datetime import datetime, timedelta
from database.refer import referdb
from database.config_db import mdb
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardMarkup
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, ChatAdminRequired, UserNotParticipant
from database.ia_filterdb import Media, Media2, get_file_details, unpack_new_file_id, get_bad_files
from database.users_chats_db import db
from info import *
from utils import get_settings, save_group_settings, is_subscribed, is_req_subscribed, get_size, get_shortlink, raw_shorten, is_check_admin, temp, get_readable_time, get_time, generate_settings_text, log_error, clean_filename
import time



logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

TIMEZONE = "Asia/Kolkata"
BATCH_FILES = {}

async def _pregenerate_stream_url(client, file_id, user_id, username):
    """Pre-generate stream download URL and log to BIN_CHANNEL. Returns URL or None."""
    try:
        log_msg = await client.send_cached_media(chat_id=BIN_CHANNEL, file_id=file_id)
        fileName = quote_plus(get_name(log_msg))
        url = f"{URL}{str(log_msg.id)}/{fileName}?hash={get_hash(log_msg)}"
        await log_msg.reply_text(
            text=f"•• ʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇᴅ ꜰᴏʀ ɪᴅ #{user_id} \n•• ᴜꜱᴇʀɴᴀᴍᴇ : {username} \n\n•• ᖴᎥᒪᗴ Nᗩᗰᗴ : {fileName}",
            quote=True,
            disable_web_page_preview=True,
        )
        return url
    except Exception as e:
        logger.error(f"_pregenerate_stream_url error: {e}")
        return None

@Client.on_message(filters.command("start") & filters.incoming)
async def start(client, message):
    try:
        await _start_handler(client, message)
    except Exception as _e:
        import traceback as _tb
        _trace = _tb.format_exc()
        try:
            await client.send_message(LOG_CHANNEL, f"⚠️ <b>/start crashed</b>\nUser: <code>{message.from_user.id if message.from_user else '?'}</code>\nError: <code>{str(_e)[:300]}</code>\n<pre>{_trace[-600:]}</pre>")
        except Exception:
            pass
        try:
            await message.reply_text(f"❌ Internal error — reported to admin.")
        except Exception:
            pass

async def _start_handler(client, message, skip_verify_check=False):
    if EMOJI_MODE:
        try:
            await message.react(emoji=random.choice(REACTIONS), big=True)
        except Exception:
            try:
                await message.react(emoji="⚡️", big=True)
            except Exception:
                pass
    m = message
    # Ban check — banned users cannot use bot or receive files via deep-link
    if message.from_user:
        _ban_check = await db.get_ban_status(message.from_user.id)
        if _ban_check.get("is_banned"):
            return await message.reply_text("🚫 You are banned from using this bot.")
    if len(m.command) == 2 and m.command[1].startswith(('notcopy', 'sendall')):
        _, userid, verify_id, file_id = m.command[1].split("_", 3)
        user_id = int(userid)
        grp_id = temp.VERIFICATIONS.get(user_id, 0)
        settings = await get_settings(grp_id)         
        verify_id_info = await db.get_verify_id_info(user_id, verify_id)
        if not verify_id_info or verify_id_info["verified"]:
            return await message.reply("<b>ʟɪɴᴋ ᴇxᴘɪʀᴇᴅ ᴛʀʏ ᴀɢᴀɪɴ...</b>")  
        
        ist_timezone = pytz.timezone('Asia/Kolkata')
        if await db.user_verified(user_id):
            key = "third_time_verified"
        else:
            key = "second_time_verified" if await db.is_user_verified(user_id) else "last_verified"
        current_time = datetime.now(tz=ist_timezone)
        _has_third_shortener = bool(settings.get('shortner_three') and settings.get('api_three'))
        _is_final_step = (key == "third_time_verified") or (key == "second_time_verified" and not _has_third_shortener)
        # Record only THIS step's completion. We deliberately do NOT advance the
        # next step's timestamp here — that field is shared per-user state, and a
        # concurrent /start request for a different file (e.g. the user tapping
        # another search result seconds later) would read that temporarily-advanced
        # value and skip its own verification requirement (race condition).
        # Instead, this specific delivery is unblocked via skip_verify_check below,
        # which is a local call argument — not shared state — so it cannot race.
        await db.update_notcopy_user(user_id, {key: current_time})
        await db.update_verify_id_info(user_id, verify_id, {"verified":True})
        if key == "third_time_verified": 
            num = 3 
        else: 
            num =  2 if key == "second_time_verified" else 1 
        if key == "third_time_verified": 
            msg = script.THIRDT_VERIFY_COMPLETE_TEXT
        else:
            msg = script.SECOND_VERIFY_COMPLETE_TEXT if key == "second_time_verified" else script.VERIFY_COMPLETE_TEXT
        if message.command[1].startswith('sendall'):
            verifiedfiles = f"https://telegram.me/{temp.U_NAME}?start=allfiles_{grp_id}_{file_id}"
        else:
            verifiedfiles = f"https://telegram.me/{temp.U_NAME}?start=file_{grp_id}_{file_id}"
        try:
            log_ch = settings.get('log', LOG_VR_CHANNEL)
            if log_ch and int(log_ch) != -100:
                await client.send_message(log_ch, script.VERIFIED_LOG_TEXT.format(m.from_user.mention, user_id, datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%d %B %Y'), num))
        except Exception as _log_err:
            logging.warning(f"Verification log failed for user {user_id}: {_log_err}")
        prefix = "allfiles" if message.command[1].startswith('sendall') else "file"
        m.command = ["start", f"{prefix}_{grp_id}_{file_id}"]
        # skip_verify_check=True unblocks THIS delivery only (a local call argument),
        # without touching shared per-user verification state — see note above.
        # Use try/finally so the final-step reset always runs even if delivery errors.
        try:
            await _start_handler(client, m, skip_verify_check=True)
        finally:
            if _is_final_step:
                # Final configured step done — reset the full chain so the next
                # request starts fresh from step 1.
                await db.update_notcopy_user(user_id, {
                    "last_verified":        ist_timezone.localize(datetime(2020, 5, 17, 0, 0, 0)),
                    "second_time_verified": ist_timezone.localize(datetime(2019, 5, 17, 0, 0, 0)),
                    "third_time_verified":  ist_timezone.localize(datetime(2018, 5, 17, 0, 0, 0)),
                })
        return         
    if message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        buttons = [[
                    InlineKeyboardButton('❤️ ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ ❤️', url=f'http://t.me/{temp.U_NAME}?startgroup=true')
                ],[
                    InlineKeyboardButton('🍁 Update Channel 🍁', url=UPDATE_CHNL_LNK)
                  ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await message.reply(script.GSTART_TXT.format(message.from_user.mention if message.from_user else message.chat.title, temp.U_NAME, temp.B_NAME), reply_markup=reply_markup, disable_web_page_preview=True)
        await asyncio.sleep(2) 
        if not await db.get_chat(message.chat.id):
            total=await client.get_chat_members_count(message.chat.id)
            try:
                await client.send_message(LOG_CHANNEL, script.LOG_TEXT_G.format(message.chat.title, message.chat.id, total, "Unknown"))
            except Exception:
                pass
            await db.add_chat(message.chat.id, message.chat.title)
        return 
    if not await db.is_user_exist(message.from_user.id):
        await db.add_user(message.from_user.id, message.from_user.first_name)
        try:
            await client.send_message(LOG_CHANNEL, script.LOG_TEXT_P.format(message.from_user.id, message.from_user.mention))
        except Exception:
            pass
    if len(message.command) != 2:
        buttons = [[
                    InlineKeyboardButton('🔰 ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ 🔰', url=f'http://t.me/{temp.U_NAME}?startgroup=true')
                ],[
                    InlineKeyboardButton(' ʜᴇʟᴘ 📢', callback_data='help'),
                    InlineKeyboardButton(' ᴀʙᴏᴜᴛ 📖', callback_data='about')
                ],[
                    InlineKeyboardButton('ᴛᴏᴘ sᴇᴀʀᴄʜɪɴɢ ⭐', callback_data="topsearch"),
                    InlineKeyboardButton('ᴜᴘɢʀᴀᴅᴇ 🎟', callback_data="premium_info"),
                ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        current_time = datetime.now(pytz.timezone(TIMEZONE))
        curr_time = current_time.hour        
        if curr_time < 12:
            gtxt = "ɢᴏᴏᴅ ᴍᴏʀɴɪɴɢ 🌞" 
        elif curr_time < 17:
            gtxt = "ɢᴏᴏᴅ ᴀғᴛᴇʀɴᴏᴏɴ 🌓" 
        elif curr_time < 21:
            gtxt = "ɢᴏᴏᴅ ᴇᴠᴇɴɪɴɢ 🌘"
        else:
            gtxt = "ɢᴏᴏᴅ ɴɪɢʜᴛ 🌑"
        m=await message.reply_text("⏳")
        await asyncio.sleep(0.4)
        await m.delete()        
        await message.reply_photo(
            photo=random.choice(PICS),
            caption=script.START_TXT.format(message.from_user.mention, gtxt, temp.U_NAME, temp.B_NAME),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        return

    if len(message.command) == 2 and message.command[1] in ["subscribe", "error", "okay", "help"]:
        buttons = [[
                    InlineKeyboardButton('🔰 ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ 🔰', url=f'http://t.me/{temp.U_NAME}?startgroup=true')
                ],[
                    InlineKeyboardButton(' ʜᴇʟᴘ 📢', callback_data='help'),
                    InlineKeyboardButton(' ᴀʙᴏᴜᴛ 📖', callback_data='about')
                ],[
                    InlineKeyboardButton('ᴛᴏᴘ sᴇᴀʀᴄʜɪɴɢ ⭐', callback_data="topsearch"),
                    InlineKeyboardButton('ᴜᴘɢʀᴀᴅᴇ 🎟', callback_data="premium_info"),
                ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        current_time = datetime.now(pytz.timezone(TIMEZONE))
        curr_time = current_time.hour        
        if curr_time < 12:
            gtxt = "ɢᴏᴏᴅ ᴍᴏʀɴɪɴɢ 🌞" 
        elif curr_time < 17:
            gtxt = "ɢᴏᴏᴅ ᴀғᴛᴇʀɴᴏᴏɴ 🌓" 
        elif curr_time < 21:
            gtxt = "ɢᴏᴏᴅ ᴇᴠᴇɴɪɴɢ 🌘"
        else:
            gtxt = "ɢᴏᴏᴅ ɴɪɢʜᴛ 🌑"
        m=await message.reply_text("⏳")
        await asyncio.sleep(0.4)
        await m.delete()        
        await message.reply_photo(
            photo=random.choice(PICS),
            caption=script.START_TXT.format(message.from_user.mention, gtxt, temp.U_NAME, temp.B_NAME),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        return
    if message.command[1].startswith("reff_"):
        try:
            user_id = int(message.command[1].split("_")[1])
        except ValueError:
            await message.reply_text("Invalid refer!")
            return
        if user_id == message.from_user.id:
            await message.reply_text("Hᴇʏ Dᴜᴅᴇ, Yᴏᴜ Cᴀɴ'ᴛ Rᴇғᴇʀ Yᴏᴜʀsᴇʟғ 🤣!\n\nsʜᴀʀᴇ ʟɪɴᴋ ʏᴏᴜʀ ғʀɪᴇɴᴅ ᴀɴᴅ ɢᴇᴛ 10 ʀᴇғᴇʀʀᴀʟ ᴘᴏɪɴᴛ ɪғ ʏᴏᴜ ᴀʀᴇ ᴄᴏʟʟᴇᴄᴛɪɴɢ 100 ʀᴇғᴇʀʀᴀʟ ᴘᴏɪɴᴛs ᴛʜᴇɴ ʏᴏᴜ ᴄᴀɴ ɢᴇᴛ 1 ᴍᴏɴᴛʜ ғʀᴇᴇ ᴘʀᴇᴍɪᴜᴍ ᴍᴇᴍʙᴇʀsʜɪᴘ.")
            return
        if await referdb.is_user_in_list(message.from_user.id):
            await message.reply_text("Yᴏᴜ ʜᴀᴠᴇ ʙᴇᴇɴ ᴀʟʀᴇᴀᴅʏ ɪɴᴠɪᴛᴇᴅ ❗")
            return
        if await db.is_user_exist(message.from_user.id): 
            await message.reply_text("‼️ Yᴏᴜ Hᴀᴠᴇ Bᴇᴇɴ Aʟʀᴇᴀᴅʏ Iɴᴠɪᴛᴇᴅ ᴏʀ Jᴏɪɴᴇᴅ")
            return 
        try:
            uss = await client.get_users(user_id)
        except Exception:
            return          
        await referdb.add_user(message.from_user.id)
        fromuse = await referdb.get_refer_points(user_id) + 10
        if fromuse == 100:
            await referdb.add_refer_points(user_id, 0) 
            await message.reply_text(f"🎉 𝗖𝗼𝗻𝗴𝗿𝗮𝘁𝘂𝗹𝗮𝘁𝗶𝗼𝗻𝘀! 𝗬𝗼𝘂 𝘄𝗼𝗻 𝟭𝟬 𝗥𝗲𝗳𝗲𝗿𝗿𝗮𝗹 𝗽𝗼𝗶𝗻𝘁 𝗯𝗲𝗰𝗮𝘂𝘀𝗲 𝗬𝗼𝘂 𝗵𝗮𝘃𝗲 𝗯𝗲𝗲𝗻 𝗦𝘂𝗰𝗰𝗲𝘀𝘀𝗳𝘂𝗹𝗹𝘆 𝗜𝗻𝘃𝗶𝘁𝗲𝗱 ☞ {uss.mention}!")                   
            await client.send_message(user_id, f"You have been successfully invited by {message.from_user.mention}!")   
            seconds = 2592000
            if seconds > 0:
                expiry_time = datetime.utcnow() + timedelta(seconds=seconds)
                user_data = {"id": user_id, "expiry_time": expiry_time}  
                await db.update_user(user_data)  # Use the update_user method to update or insert user data                 
                await client.send_message(
                chat_id=user_id,
                text=f"<b>Hᴇʏ {uss.mention}\n\nYᴏᴜ ɢᴏᴛ 1 ᴍᴏɴᴛʜ ᴘʀᴇᴍɪᴜᴍ sᴜʙsᴄʀɪᴘᴛɪᴏɴ ʙʏ ɪɴᴠɪᴛɪɴɢ 10 ᴜsᴇʀs ❗</b>", disable_web_page_preview=True              
                )
            for admin in ADMINS:
                await client.send_message(chat_id=admin, text=f"Sᴜᴄᴄᴇss ғᴜʟʟʏ ᴛᴀsᴋ ᴄᴏᴍᴘʟᴇᴛᴇᴅ ʙʏ ᴛʜɪs ᴜsᴇʀ:\n\nuser Nᴀᴍᴇ: {uss.mention}\n\nUsᴇʀ ɪᴅ: {uss.id}!")  
        else:
            await referdb.add_refer_points(user_id, fromuse)
            await message.reply_text(f"You have been successfully invited by {uss.mention}!")
            await client.send_message(user_id, f"𝗖𝗼𝗻𝗴𝗿𝗮𝘁𝘂𝗹𝗮𝘁𝗶𝗼𝗻𝘀! 𝗬𝗼𝘂 𝘄𝗼𝗻 𝟭𝟬 𝗥𝗲𝗳𝗲𝗿𝗿𝗮𝗹 𝗽𝗼𝗶𝗻𝘁 𝗯𝗲𝗰𝗮𝘂𝘀𝗲 𝗬𝗼𝘂 𝗵𝗮𝘃𝗲 𝗯𝗲𝗲𝗻 𝗦𝘂𝗰𝗰𝗲𝘀𝘀𝗳𝘂𝗹𝗹𝘆 𝗜𝗻𝘃𝗶𝘁𝗲𝗱 ☞{message.from_user.mention}!")
        return
        
    if len(message.command) == 2 and message.command[1] in ["premium"]:
        buttons = [[
                    InlineKeyboardButton('📲 ꜱᴇɴᴅ ᴘᴀʏᴍᴇɴᴛ ꜱᴄʀᴇᴇɴꜱʜᴏᴛ', url=OWNER_LNK)
                  ],[
                    InlineKeyboardButton('❌ ᴄʟᴏꜱᴇ ❌', callback_data='close_data')
                  ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await message.reply_photo(
            photo=(SUBSCRIPTION),
            caption=script.PREPLANS_TXT.format(message.from_user.mention, OWNER_UPI_ID, QR_CODE),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        return  
    
    if len(message.command) == 2 and message.command[1].startswith('getfile'):
        movies = message.command[1].split("-", 1)[1] 
        movie = movies.replace('-',' ')
        message.text = movie 
        from plugins.pmfilter import auto_filter  # lazy import to avoid startup crash
        await auto_filter(client, message)
        return
    
    data = message.command[1]
    try:
        _, grp_id, file_id = data.split("_", 2)
        grp_id = int(grp_id)
    except:
        _, grp_id, file_id = "", 0, data

    # Fetch file details concurrently with user checks
    file_details_task = asyncio.create_task(get_file_details(file_id))

    if not await db.has_premium_access(message.from_user.id): 
        try:
            btn = []
            chat = int(data.split("_", 2)[1])
            settings      = await get_settings(chat)
            fsub_channels = list(dict.fromkeys((settings.get('fsub', []) if settings else [])+ AUTH_CHANNELS)) 

            if fsub_channels:
                btn += await is_subscribed(client, message.from_user.id, fsub_channels)
            if AUTH_REQ_CHANNELS:
                btn += await is_req_subscribed(client, message.from_user.id, AUTH_REQ_CHANNELS)
            if btn:
                if len(message.command) > 1 and "_" in message.command[1]:
                    kk, file_id = message.command[1].split("_", 1)
                    btn.append([
                        InlineKeyboardButton("♻️ ᴛʀʏ ᴀɢᴀɪɴ ♻️", callback_data=f"checksub#{kk}#{file_id}")
                    ])
                    reply_markup = InlineKeyboardMarkup(btn)
                photo = random.choice(FSUB_PICS) if FSUB_PICS else "https://graph.org/file/7478ff3eac37f4329c3d8.jpg"
                caption = (
                    f"👋 ʜᴇʟʟᴏ {message.from_user.mention}\n\n"
                    "🛑 ʏᴏᴜ ᴍᴜsᴛ ᴊᴏɪɴ ᴛʜᴇ ʀᴇǫᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟs ᴛᴏ ᴄᴏɴᴛɪɴᴜᴇ.\n"
                    "👉 ᴊᴏɪɴ ᴀʟʟ ᴛʜᴇ ʙᴇʟᴏᴡ ᴄʜᴀɴɴᴇʟs ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ."
                )
                await message.reply_photo(
                    photo=photo,
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode=enums.ParseMode.HTML
                )
                return

        except Exception as e:
            await log_error(client, f"❗️ Force Sub Error:\n\n{repr(e)}")
            logger.error(f"❗️ Force Sub Error:\n\n{repr(e)}")


    user_id = m.from_user.id
    if not skip_verify_check and not await db.has_premium_access(user_id):
        # ── Part 1: check verification status (DB reads) ──────────────────────
        _needs_verify = False
        _is_second = False
        _is_third = False
        _verify_settings = None
        try:
            grp_id = int(grp_id)
            _user_verified = await db.is_user_verified(user_id)
            _verify_settings = await get_settings(grp_id)
            _is_second = await db.use_second_shortener(user_id, _verify_settings.get('verify_time', TWO_VERIFY_GAP))
            _is_third = await db.use_third_shortener(user_id, _verify_settings.get('third_verify_time', THREE_VERIFY_GAP))
            if _verify_settings.get("is_verify", IS_VERIFY) and (not _user_verified or _is_second or _is_third):
                _needs_verify = True
        except Exception as e:
            # Fail CLOSED, not open: if the status check itself breaks (DB hiccup,
            # bad settings, etc.) we must not silently deliver the file unverified —
            # that would defeat the whole point of this gate. Fall back to requiring
            # a plain (1st-shortener) verification instead.
            logger.error(f"Verification status check failed: {e}")
            _needs_verify = True
            _is_second = False
            _is_third = False
            _verify_settings = _verify_settings or {}

        # ── Part 2: enforce verification (shortener call) ─────────────────────
        # If the shortener call fails we must NOT deliver the file — return with an error.
        if _needs_verify:
            try:
                verify_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=7))
                await db.create_verify_id(user_id, verify_id)
                temp.VERIFICATIONS[user_id] = grp_id
                if message.command[1].startswith('allfiles'):
                    verify = await get_shortlink(f"https://telegram.me/{temp.U_NAME}?start=sendall_{user_id}_{verify_id}_{file_id}", grp_id, _is_second, _is_third)
                else:
                    verify = await get_shortlink(f"https://telegram.me/{temp.U_NAME}?start=notcopy_{user_id}_{verify_id}_{file_id}", grp_id, _is_second, _is_third)
                if not verify or not str(verify).startswith(("http://", "https://")):
                    raise ValueError(f"Shortener returned an invalid URL: {verify!r}")
                buttons = [[
                    InlineKeyboardButton(text="♻️ ᴄʟɪᴄᴋ ʜᴇʀᴇ ᴛᴏ ᴠᴇʀɪꜰʏ ♻️", url=verify)
                ]]
                if _is_third:
                    _tutorial_url = _verify_settings.get('tutorial_3', TUTORIAL_3)
                elif _is_second:
                    _tutorial_url = _verify_settings.get('tutorial_2', TUTORIAL_2)
                else:
                    _tutorial_url = _verify_settings.get('tutorial', TUTORIAL)
                if _tutorial_url and str(_tutorial_url).startswith(("http://", "https://")):
                    buttons.append([InlineKeyboardButton(text="⁉️ ʜᴏᴡ ᴛᴏ ᴠᴇʀɪꜰʏ ⁉️", url=_tutorial_url)])
                reply_markup=InlineKeyboardMarkup(buttons)
                if await db.user_verified(user_id):
                    msg = script.THIRDT_VERIFICATION_TEXT
                else:
                    msg = script.SECOND_VERIFICATION_TEXT if _is_second else script.VERIFICATION_TEXT
                n=await m.reply_text(
                    text=msg.format(message.from_user.mention),
                    protect_content = PROTECT_CONTENT,
                    reply_markup=reply_markup,
                    parse_mode=enums.ParseMode.HTML
                )
                await asyncio.sleep(300)
                await n.delete()
                await m.delete()
                return
            except Exception as e:
                logger.error(f"Shortener call failed for user {user_id}: {e}")
                await m.reply_text(
                    f"<b>⚠️ ꜱʜᴏʀᴛᴇɴᴇʀ ᴇʀʀᴏʀ!\n\n<code>{str(e)[:300]}</code>\n\nᴜꜱᴇ /set_shortner ᴛᴏ ᴜᴘᴅᴀᴛᴇ ᴏʀ /remove_shortner ᴛᴏ ʀᴇꜱᴇᴛ.</b>",
                    parse_mode=enums.ParseMode.HTML
                )
                return

    # Now, await the file details task
    files_ = await file_details_task

    if data.startswith("allfiles"):
        try:
            files = temp.GETALL.get(file_id)
            if not files:
                return await message.reply('<b><i>ɴᴏ ꜱᴜᴄʜ ꜰɪʟᴇ ᴇxɪꜱᴛꜱ !</b></i>')
            filesarr = []
            for file in files:
                file_id = file.file_id
                files_ = await get_file_details(file_id)
                files1 = files_[0]
                title = clean_filename(files1.file_name)
                size = get_size(files1.file_size)
                f_caption = files1.caption
                settings = await get_settings(int(grp_id))
                DREAMX_CAPTION = settings.get('caption', CUSTOM_FILE_CAPTION)
                if DREAMX_CAPTION:
                    try:
                        f_caption=DREAMX_CAPTION.format(file_name= '' if title is None else title, file_size='' if size is None else size, file_caption='' if f_caption is None else f_caption)
                    except Exception as e:
                        logger.exception(e)
                        f_caption = f_caption
                if f_caption is None:
                    f_caption = f"{clean_filename(files1.file_name)}"
                
                if STREAM_MODE and not PREMIUM_STREAM_MODE:
                    _stream_url = await _pregenerate_stream_url(client, file_id, message.from_user.id, message.from_user.mention)
                    if _stream_url:
                        btn = [
                            [InlineKeyboardButton('🚀 ꜰᴀꜱᴛ ᴅᴏᴡɴʟᴏᴀᴅ 🚀', url=_stream_url)],
                            [InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)]
                        ]
                    else:
                        btn = [
                            [InlineKeyboardButton('🚀 ꜰᴀꜱᴛ ᴅᴏᴡɴʟᴏᴀᴅ 🚀', callback_data=f'generate_stream_link:{file_id}')],
                            [InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)]
                        ]
                elif STREAM_MODE and PREMIUM_STREAM_MODE:
                    if not await db.has_premium_access(message.from_user.id):
                        btn = [
                            [InlineKeyboardButton('🚀 ꜰᴀꜱᴛ ᴅᴏᴡɴʟᴏᴀᴅ 🚀', callback_data=f'prestream')],
                            [InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)]
                        ]
                    else:
                        _stream_url = await _pregenerate_stream_url(client, file_id, message.from_user.id, message.from_user.mention)
                        if _stream_url:
                            btn = [
                                [InlineKeyboardButton('🚀 ꜰᴀꜱᴛ ᴅᴏᴡɴʟᴏᴀᴅ 🚀', url=_stream_url)],
                                [InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)]
                            ]
                        else:
                            btn = [
                                [InlineKeyboardButton('🚀 ꜰᴀꜱᴛ ᴅᴏᴡɴʟᴏᴀᴅ 🚀', callback_data=f'generate_stream_link:{file_id}')],
                                [InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)]
                            ]
                else:
                    btn = [[InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)]]
                msg = await client.send_cached_media(
                    chat_id=message.from_user.id,
                    file_id=file_id,
                    caption=f_caption,
                    protect_content=settings.get('file_secure', PROTECT_CONTENT),
                    reply_markup=InlineKeyboardMarkup(btn)
                )
                filesarr.append(msg)
            k = await client.send_message(chat_id=message.from_user.id, text=script.DEL_MSG.format(get_time(DELETE_TIME)), parse_mode=enums.ParseMode.HTML)
            await asyncio.sleep(DELETE_TIME)
            for x in filesarr:
                try:
                    await x.delete()
                except Exception:
                    pass
            try:
                await k.edit_text("<b>ʏᴏᴜʀ ᴀʟʟ ᴠɪᴅᴇᴏꜱ/ꜰɪʟᴇꜱ ᴀʀᴇ ᴅᴇʟᴇᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ !\nᴋɪɴᴅʟʏ ꜱᴇᴀʀᴄʜ ᴀɢᴀɪɴ</b>")
            except Exception:
                pass
            return
        except Exception as e:
            logger.exception(e)
            return

    user = message.from_user.id
    settings = await get_settings(int(grp_id))
    if not files_:
        pre, file_id = ((base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))).decode("ascii")).split("_", 1)
        try:
            if STREAM_MODE and not PREMIUM_STREAM_MODE:
                _stream_url = await _pregenerate_stream_url(client, file_id, message.from_user.id, message.from_user.mention)
                if _stream_url:
                    btn = [
                        [InlineKeyboardButton('🚀 ꜰᴀꜱᴛ ᴅᴏᴡɴʟᴏᴀᴅ 🚀', url=_stream_url)],
                        [InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)]
                    ]
                else:
                    btn = [
                        [InlineKeyboardButton('🚀 ꜰᴀꜱᴛ ᴅᴏᴡɴʟᴏᴀᴅ 🚀', callback_data=f'generate_stream_link:{file_id}')],
                        [InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)]
                    ]
            elif STREAM_MODE and PREMIUM_STREAM_MODE:
                if not await db.has_premium_access(message.from_user.id):
                    btn = [
                        [InlineKeyboardButton('🚀 ꜰᴀꜱᴛ ᴅᴏᴡɴʟᴏᴀᴅ 🚀', callback_data=f'prestream')],
                        [InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)]
                    ]
                else:
                    _stream_url = await _pregenerate_stream_url(client, file_id, message.from_user.id, message.from_user.mention)
                    if _stream_url:
                        btn = [
                            [InlineKeyboardButton('🚀 ꜰᴀꜱᴛ ᴅᴏᴡɴʟᴏᴀᴅ 🚀', url=_stream_url)],
                            [InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)]
                        ]
                    else:
                        btn = [
                            [InlineKeyboardButton('🚀 ꜰᴀꜱᴛ ᴅᴏᴡɴʟᴏᴀᴅ 🚀', callback_data=f'generate_stream_link:{file_id}')],
                            [InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)]
                        ]
            else:
                btn = [[InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)]]
            msg = await client.send_cached_media(
                chat_id=message.from_user.id,
                file_id=file_id,
                protect_content=settings.get('file_secure', PROTECT_CONTENT),
                reply_markup=InlineKeyboardMarkup(btn))

            filetype = msg.media
            file = getattr(msg, filetype.value)
            title = clean_filename(file.file_name)
            size=get_size(file.file_size)
            f_caption = f"<code>{title}</code>"
            settings = await get_settings(int(grp_id))
            DREAMX_CAPTION = settings.get('caption', CUSTOM_FILE_CAPTION)
            if DREAMX_CAPTION:
                try:
                    f_caption=DREAMX_CAPTION.format(file_name= '' if title is None else title, file_size='' if size is None else size, file_caption='')
                except:
                    return
            await msg.edit_caption(
                f_caption,
                reply_markup=InlineKeyboardMarkup(btn)
            )
            k = await msg.reply(script.DEL_MSG.format(get_time(DELETE_TIME)),
                quote=True, parse_mode=enums.ParseMode.HTML
            )
            await asyncio.sleep(DELETE_TIME)
            try:
                await msg.delete()
            except Exception:
                pass
            try:
                await k.edit_text("<b>ʏᴏᴜʀ ᴠɪᴅᴇᴏ / ꜰɪʟᴇ ɪꜱ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ !!</b>")
            except Exception:
                pass
            return
        except Exception as e:
            logger.exception(e)
            pass
        return await message.reply('ɴᴏ ꜱᴜᴄʜ ꜰɪʟᴇ ᴇxɪꜱᴛꜱ !')
    
    files = files_[0]
    title = clean_filename(files.file_name)
    size = get_size(files.file_size)
    f_caption = files.caption
    settings = await get_settings(int(grp_id))            
    DREAMX_CAPTION = settings.get('caption', CUSTOM_FILE_CAPTION)
    if DREAMX_CAPTION:
        try:
            f_caption=DREAMX_CAPTION.format(file_name= '' if title is None else title, file_size='' if size is None else size, file_caption='' if f_caption is None else f_caption)
        except Exception as e:
            logger.exception(e)
            f_caption = f_caption

    if f_caption is None:
        f_caption = clean_filename(files.file_name)
    
    if STREAM_MODE and not PREMIUM_STREAM_MODE:
        _stream_url = await _pregenerate_stream_url(client, file_id, message.from_user.id, message.from_user.mention)
        if _stream_url:
            btn = [
                [InlineKeyboardButton('🚀 ꜰᴀꜱᴛ ᴅᴏᴡɴʟᴏᴀᴅ 🚀', url=_stream_url)],
                [InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)]
            ]
        else:
            btn = [
                [InlineKeyboardButton('🚀 ꜰᴀꜱᴛ ᴅᴏᴡɴʟᴏᴀᴅ 🚀', callback_data=f'generate_stream_link:{file_id}')],
                [InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)]
            ]
    elif STREAM_MODE and PREMIUM_STREAM_MODE:
        if not await db.has_premium_access(message.from_user.id):
            btn = [
                [InlineKeyboardButton('🚀 ꜰᴀꜱᴛ ᴅᴏᴡɴʟᴏᴀᴅ 🚀', callback_data=f'prestream')],
                [InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)]
            ]
        else:
            _stream_url = await _pregenerate_stream_url(client, file_id, message.from_user.id, message.from_user.mention)
            if _stream_url:
                btn = [
                    [InlineKeyboardButton('🚀 ꜰᴀꜱᴛ ᴅᴏᴡɴʟᴏᴀᴅ 🚀', url=_stream_url)],
                    [InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)]
                ]
            else:
                btn = [
                    [InlineKeyboardButton('🚀 ꜰᴀꜱᴛ ᴅᴏᴡɴʟᴏᴀᴅ 🚀', callback_data=f'generate_stream_link:{file_id}')],
                    [InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)]
                ]
    else:
        btn = [[InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)]]
    msg = await client.send_cached_media(
        chat_id=message.from_user.id,
        file_id=file_id,
        caption=f_caption,
        protect_content=settings.get('file_secure', PROTECT_CONTENT),
        reply_markup=InlineKeyboardMarkup(btn)
    )
    k = await msg.reply(script.DEL_MSG.format(get_time(DELETE_TIME)),
        quote=True, parse_mode=enums.ParseMode.HTML
    )     
    await asyncio.sleep(DELETE_TIME)
    try:
        await msg.delete()
    except Exception:
        pass
    try:
        await k.edit_text("<b>ʏᴏᴜʀ ᴠɪᴅᴇᴏ / ꜰɪʟᴇ ɪꜱ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ !!</b>")
    except Exception:
        pass
    return


@Client.on_message(filters.command("help") & filters.incoming)
async def help_command(client, message):
    buttons = [[
        InlineKeyboardButton('⇋ ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ ⇋', callback_data='start')
    ]]
    reply_markup = InlineKeyboardMarkup(buttons)
    await message.reply_text(
        text=script.HELP_TXT,
        reply_markup=reply_markup,
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_message(filters.command('logs') & filters.user(ADMINS))
async def log_file(bot, message):
    """Send log file"""
    try:
        await message.reply_document('BotLogs.txt', caption="📑 **ʟᴏɢꜱ**")
    except Exception as e:
        await message.reply(str(e))

@Client.on_message(filters.command('delete') & filters.user(ADMINS))
async def delete(bot, message):
    """Delete file from database"""
    reply = message.reply_to_message
    if reply and reply.media:
        msg = await message.reply("Pʀᴏᴄᴇssɪɴɢ...⏳", quote=True)
    else:
        await message.reply('Rᴇᴘʟʏ ᴛᴏ ғɪʟᴇ ᴡɪᴛʜ /delete ᴡʜɪᴄʜ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴅᴇʟᴇᴛᴇ', quote=True)
        return

    for file_type in ("document", "video", "audio"):
        media = getattr(reply, file_type, None)
        if media is not None:
            break
    else:
        await msg.edit('Tʜɪs ɪs ɴᴏᴛ sᴜᴘᴘᴏʀᴛᴇᴅ ғɪʟᴇ ғᴏʀᴍᴀᴛ')
        return
    
    file_id, file_ref = unpack_new_file_id(media.file_id)
    if await Media.count_documents({'file_id': file_id}):
        result = await Media.collection.delete_one({
            '_id': file_id,
        })
    else:
        result = await Media2.collection.delete_one({
            '_id': file_id,
        })
    if result.deleted_count:
        await msg.edit('Fɪʟᴇ ɪs sᴜᴄᴄᴇssғᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ ғʀᴏᴍ ᴅᴀᴛᴀʙᴀsᴇ ✅')
    else:
        file_name = re.sub(r"(_|\-|\.|\+)", " ", str(media.file_name))
        result = await Media.collection.delete_many({
            'file_name': file_name,
            'file_size': media.file_size,
            'mime_type': media.mime_type
            })
        if result.deleted_count:
            await msg.edit('Fɪʟᴇ ɪs sᴜᴄᴄᴇssғᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ ғʀᴏᴍ ᴅᴀᴛᴀʙᴀsᴇ ✅')
        else:
            result = await Media2.collection.delete_many({
                'file_name': file_name,
                'file_size': media.file_size,
                'mime_type': media.mime_type
            })
            if result.deleted_count:
                await msg.edit('Fɪʟᴇ ɪs sᴜᴄᴄᴇssғᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ ғʀᴏᴍ ᴅᴀᴛᴀʙᴀsᴇ')
            else:
                result = await Media.collection.delete_many({
                    'file_name': media.file_name,
                    'file_size': media.file_size,
                    'mime_type': media.mime_type
                })
                if result.deleted_count:
                    await msg.edit('Fɪʟᴇ ɪs sᴜᴄᴄᴇssғᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ ғʀᴏᴍ ᴅᴀᴛᴀʙᴀsᴇ ✅')
                else:
                    result = await Media2.collection.delete_many({
                        'file_name': media.file_name,
                        'file_size': media.file_size,
                        'mime_type': media.mime_type
                    })
                    if result.deleted_count:
                        await msg.edit('Fɪʟᴇ ɪs sᴜᴄᴄᴇssғᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ ғʀᴏᴍ ᴅᴀᴛᴀʙᴀsᴇ ✅')
                    else:
                        await msg.edit('Fɪʟᴇ ɴᴏᴛ ғᴏᴜɴᴅ ɪɴ ᴅᴀᴛᴀʙᴀsᴇ ❌')


@Client.on_message(filters.command('disabled_deleteall') & filters.user(ADMINS))
async def delete_all_index(bot, message):
    await message.reply_text(
        'ᴛʜɪꜱ ᴡɪʟʟ ᴅᴇʟᴇᴛᴇ ᴀʟʟ ʏᴏᴜʀ ɪɴᴅᴇxᴇᴅ ꜰɪʟᴇꜱ !\nᴅᴏ ʏᴏᴜ ꜱᴛɪʟʟ ᴡᴀɴᴛ ᴛᴏ ᴄᴏɴᴛɪɴᴜᴇ ?',
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="⚠️ ʏᴇꜱ ⚠️", callback_data="autofilter_delete"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ ɴᴏ ❌", callback_data="close_data"
                    )
                ],
            ]
        ),
        quote=True,
    )

@Client.on_message(filters.command('settings'))
async def settings(client, message):
    user_id = message.from_user.id if message.from_user else None
    if not user_id:
        return await message.reply(f"ʏᴏᴜ'ʀᴇ ᴀɴᴏɴʏᴍᴏᴜꜱ ᴀᴅᴍɪɴ.")
    chat_type = message.chat.type
    if chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        grp_id = message.chat.id
        if not await is_check_admin(client, grp_id, message.from_user.id):
            return await message.reply_text(script.NT_ADMIN_ALRT_TXT)
        await db.connect_group(grp_id, user_id)
        btn = [[
                InlineKeyboardButton("👤 ᴏᴘᴇɴ ɪɴ ᴘʀɪᴠᴀᴛᴇ ᴄʜᴀᴛ 👤", callback_data=f"opnsetpm#{grp_id}")
              ],[
                InlineKeyboardButton("👥 ᴏᴘᴇɴ ʜᴇʀᴇ 👥", callback_data=f"opnsetgrp#{grp_id}")
              ]]
        await message.reply_text(
                text="<b>ᴡʜᴇʀᴇ ᴅᴏ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴏᴘᴇɴ ꜱᴇᴛᴛɪɴɢꜱ ᴍᴇɴᴜ ? ⚙️</b>",
                reply_markup=InlineKeyboardMarkup(btn),
                disable_web_page_preview=True,
                parse_mode=enums.ParseMode.HTML,
                reply_to_message_id=message.id
        )
    elif chat_type == enums.ChatType.PRIVATE:
        connected_groups = await db.get_connected_grps(user_id)
        if not connected_groups:
            return await message.reply_text("Nᴏ Cᴏɴɴᴇᴄᴛᴇᴅ Gʀᴏᴜᴘs Fᴏᴜɴᴅ .")
        group_list = []
        for group in connected_groups:
            try:
                Chat = await client.get_chat(group)
                group_list.append([ InlineKeyboardButton(text=Chat.title, callback_data=f"grp_pm#{Chat.id}") ])
            except Exception as e:
                print(f"Error In PM Settings Button - {e}")
                pass
        await message.reply_text(
                    "⚠️ ꜱᴇʟᴇᴄᴛ ᴛʜᴇ ɢʀᴏᴜᴘ ᴡʜᴏꜱᴇ ꜱᴇᴛᴛɪɴɢꜱ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴄʜᴀɴɢᴇ.\n\n"
                    "ɪꜰ ʏᴏᴜʀ ɢʀᴏᴜᴘ ɪꜱ ɴᴏᴛ ꜱʜᴏᴡɪɴɢ ʜᴇʀᴇ,\n"
                    "ᴜꜱᴇ /reload ɪɴ ᴛʜᴀᴛ ɢʀᴏᴜᴘ ᴀɴᴅ ɪᴛ ᴡɪʟʟ ᴀᴘᴘᴇᴀʀ ʜᴇʀᴇ.",
                    reply_markup=InlineKeyboardMarkup(group_list)
                )

@Client.on_message(filters.command('reload'))
async def connect_group(client, message):
    user_id = message.from_user.id
    if message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        await db.connect_group(message.chat.id, user_id)
        await message.reply_text("Gʀᴏᴜᴘ Rᴇʟᴏᴀᴅᴇᴅ ✅ Nᴏᴡ Yᴏᴜ Cᴀɴ Mᴀɴᴀɢᴇ Tʜɪs Gʀᴏᴜᴘ Fʀᴏᴍ PM.")
    elif message.chat.type == enums.ChatType.PRIVATE:
        if len(message.command) < 2:
            await message.reply_text("Example: /reload 123456789")
            return
        try:
            group_id = int(message.command[1])
            if not await is_check_admin(client, group_id, user_id):
                await message.reply_text(script.NT_ADMIN_ALRT_TXT)
                return
            chat = await client.get_chat(group_id)
            await db.connect_group(group_id, user_id)
            await message.reply_text(f"Lɪɴᴋᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ ✅ {chat.title} ᴛᴏ PM.")
        except:
            await message.reply_text("Invalid group ID or error occurred.")

@Client.on_message(filters.command('set_template'))
async def save_template(client, message):
    sts = await message.reply("ᴄʜᴇᴄᴋɪɴɢ ᴛᴇᴍᴘʟᴀᴛᴇ...")
    user_id = message.from_user.id if message.from_user else None
    if not user_id:
        return await message.reply("ʏᴏᴜ'ʀᴇ ᴀɴᴏɴʏᴍᴏᴜꜱ ᴀᴅᴍɪɴ.")

    if message.chat.type not in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        return await sts.edit("⚠️ ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ ɪɴ ᴀ ɢʀᴏᴜᴘ ᴄʜᴀᴛ.")

    group_id = message.chat.id
    title = message.chat.title
    if not await is_check_admin(client, group_id, user_id):
        await message.reply_text(script.NT_ADMIN_ALRT_TXT)
        return
    if len(message.command) < 2:
        return await sts.edit("⚠️ ɴᴏ ᴛᴇᴍᴘʟᴀᴛᴇ ᴘʀᴏᴠɪᴅᴇᴅ!")

    template = message.text.split(" ", 1)[1]
    await save_group_settings(group_id, 'template', template)
    await sts.edit(
        f"✅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴜᴘᴅᴀᴛᴇᴅ ᴛᴇᴍᴘʟᴀᴛᴇ ꜰᴏʀ <code>{title}</code> ᴛᴏ:\n\n{template}"
    )



@Client.on_message(filters.command("send") & filters.user(ADMINS))
async def send_msg(bot, message):
    if message.reply_to_message:
        target_id = message.text.split(" ", 1)[1]
        out = "Users Saved In DB Are:\n\n"
        success = False
        try:
            user = await bot.get_users(target_id)
            user_ids = set()
            users = await db.get_all_users()
            async for usr in users:
                user_ids.add(int(usr['id']))
            if user.id in user_ids:
                await message.reply_to_message.copy(int(user.id))
                success = True
            else:
                success = False
            if success:
                await message.reply_text(f"<b>ʏᴏᴜʀ ᴍᴇꜱꜱᴀɢᴇ ʜᴀꜱ ʙᴇᴇɴ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ꜱᴇɴᴛ ᴛᴏ {user.mention}.</b>")
            else:
                await message.reply_text("<b>ᴛʜɪꜱ ᴜꜱᴇʀ ᴅɪᴅɴ'ᴛ ꜱᴛᴀʀᴛᴇᴅ ᴛʜɪꜱ ʙᴏᴛ ʏᴇᴛ !</b>")
        except Exception as e:
            await message.reply_text(f"<b>Error: {e}</b>")
    else:
        await message.reply_text("<b>ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ ᴀꜱ ᴀ ʀᴇᴘʟʏ ᴛᴏ ᴀɴʏ ᴍᴇꜱꜱᴀɢᴇ ᴜꜱɪɴɢ ᴛʜᴇ ᴛᴀʀɢᴇᴛ ᴄʜᴀᴛ ɪᴅ. ꜰᴏʀ ᴇɢ:  /send ᴜꜱᴇʀɪᴅ</b>")

@Client.on_message(filters.command("disabled_deletefiles") & filters.user(ADMINS))
async def deletemultiplefiles(bot, message):
    chat_type = message.chat.type
    if chat_type != enums.ChatType.PRIVATE:
        return await message.reply_text(f"<b>Hey {message.from_user.mention}, This command won't work in groups. It only works on my PM !</b>")
    else:
        pass
    try:
        keyword = message.text.split(" ", 1)[1]
    except:
        return await message.reply_text(f"<b>Hey {message.from_user.mention}, Give me a keyword along with the command to delete files.</b>")
    k = await bot.send_message(chat_id=message.chat.id, text=f"<b>Fetching Files for your query {keyword} on DB... Please wait...</b>")
    files, total = await get_bad_files(keyword)
    total = len(files)
    if total == 0:
        await k.edit_text(f"<b>No files found for your query {keyword} !</b>")
        await asyncio.sleep(DELETE_TIME)
        await k.delete()
        return
    await k.delete()
    btn = [[
       InlineKeyboardButton("⚠️ Yes, Continue ! ⚠️", callback_data=f"killfilesdq#{keyword}")
       ],[
       InlineKeyboardButton("❌ No, Abort operation ! ❌", callback_data="close_data")
    ]]
    await message.reply_text(
        text=f"<b>Found {total} files for your query {keyword} !\n\nDo you want to delete?</b>",
        reply_markup=InlineKeyboardMarkup(btn),
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_callback_query(filters.regex("topsearch"))
async def topsearch_callback(client, callback_query):
    def is_alphanumeric(string):
        return bool(re.match('^[a-zA-Z0-9 ]*$', string))

    limit = 20
    top_messages = await mdb.get_top_messages(limit)
    seen_messages = set()
    truncated_messages = []
    for msg in top_messages:
        msg_lower = msg.lower()
        if msg_lower not in seen_messages and is_alphanumeric(msg):
            seen_messages.add(msg_lower)
            if len(msg) > 35:
                truncated_messages.append(msg[:32] + "...")
            else:
                truncated_messages.append(msg)
    keyboard = [truncated_messages[i:i+2] for i in range(0, len(truncated_messages), 2)]
    reply_markup = ReplyKeyboardMarkup(
        keyboard,
        one_time_keyboard=True,
        resize_keyboard=True,
        placeholder="Most searches of the day"
    )
    await callback_query.message.reply_text(
        "<b>Tᴏᴘ Sᴇᴀʀᴄʜᴇs Oғ Tʜᴇ Dᴀʏ 👇</b>",
        reply_markup=reply_markup
    )
    await callback_query.answer()

@Client.on_message(filters.command('top_search'))
async def top(_, message):
    def is_alphanumeric(string):
        return bool(re.match('^[a-zA-Z0-9 ]*$', string))
    try:
        limit = int(message.command[1])
    except (IndexError, ValueError):
        limit = 20
    top_messages = await mdb.get_top_messages(limit)
    seen_messages = set()
    truncated_messages = []
    for msg in top_messages:
        msg_lower = msg.lower()
        if msg_lower not in seen_messages and is_alphanumeric(msg):
            seen_messages.add(msg_lower)
            if len(msg) > 35:
                truncated_messages.append(msg[:32] + "...")
            else:
                truncated_messages.append(msg)
    keyboard = [truncated_messages[i:i+2] for i in range(0, len(truncated_messages), 2)]
    reply_markup = ReplyKeyboardMarkup(
        keyboard,
        one_time_keyboard=True,
        resize_keyboard=True,
        placeholder="Most searches of the day"
    )
    await message.reply_text(
        "<b>Tᴏᴘ Sᴇᴀʀᴄʜᴇs Oғ Tʜᴇ Dᴀʏ 👇</b>",
        reply_markup=reply_markup
    )

@Client.on_message(filters.command('trendlist'))
async def trendlist(client, message):
    def is_alphanumeric(string):
        return bool(re.match('^[a-zA-Z0-9 ]*$', string))
    limit = 31
    if len(message.command) > 1:
        try:
            limit = int(message.command[1])
        except ValueError:
            await message.reply_text(
                "Invalid number format.\nPlease provide a valid number after the /trendlist command."
            )
            return
    try:
        top_messages = await mdb.get_top_messages(limit)
    except Exception as e:
        await message.reply_text(f"Error retrieving messages: {str(e)}")
        return

    if not top_messages:
        await message.reply_text("No top messages found.")
        return
    seen_messages = set()
    truncated_messages = []

    for msg in top_messages:
        msg_lower = msg.lower()
        if msg_lower not in seen_messages and is_alphanumeric(msg):
            seen_messages.add(msg_lower)
            truncated_messages.append(msg[:32] + '...' if len(msg) > 35 else msg)

    if not truncated_messages:
        await message.reply_text("No valid top messages found.")
        return
    formatted_list = "\n".join([f"{i+1}. <b>{msg}</b>" for i, msg in enumerate(truncated_messages)])
    additional_message = (
        "⚡️ 𝑨𝒍𝒍 𝒕𝒉𝒆 𝒓𝒆𝒔𝒖𝒍𝒕𝒔 𝒂𝒃𝒐𝒗𝒆 𝒄𝒐𝒎𝒆 𝒇𝒓𝒐𝒎 𝒘𝒉𝒂𝒕 𝒖𝒔𝒆𝒓𝒔 𝒉𝒂𝒗𝒆 𝒔𝒆𝒂𝒓𝒄𝒉𝒆𝒅 𝒇𝒐𝒓. "
        "𝑻𝒉𝒆𝒚'𝒓𝒆 𝒔𝒉𝒐𝒘𝒏 𝒕𝒐 𝒚𝒐𝒖 𝒆𝒙𝒂𝒄𝒕𝒍𝒚 𝒂𝒔 𝒕𝒉𝒆𝒚 𝒘𝒆𝒓𝒆 𝒔𝒆𝒂𝒓𝒄𝒉𝒆𝒅, "
        "𝒘𝒊𝒕𝒉𝒐𝒖𝒕 𝒂𝒏𝒚 𝒄𝒉𝒂𝒏𝒈𝒆𝒔 𝒃𝒚 𝒕𝒉𝒆 𝒐𝒘𝒏𝒆𝒓."
    )
    formatted_list += f"\n\n{additional_message}"
    reply_text = f"<b>Top {len(truncated_messages)} Tʀᴀɴᴅɪɴɢ ᴏғ ᴛʜᴇ ᴅᴀʏ 👇:</b>\n\n{formatted_list}"
    await message.reply_text(reply_text)

@Client.on_message(filters.private & filters.command("pm_search") & filters.user(ADMINS))
async def set_pm_search(client, message):
    bot_id = client.me.id
    try:
        option = message.text.split(" ", 1)[1].strip().lower()
        enable_status = option in ['on', 'true']
    except (IndexError, ValueError):
        await message.reply_text("<b>💔 Invalid option. Please send 'on' or 'off' after the command..</b>")
        return
    try:
        await db.update_pm_search_status(bot_id, enable_status)
        response_text = (
            "<b> ᴘᴍ ꜱᴇᴀʀᴄʜ ᴇɴᴀʙʟᴇᴅ ✅</b>" if enable_status
            else "<b> ᴘᴍ ꜱᴇᴀʀᴄʜ ᴅɪꜱᴀʙʟᴇᴅ ❌</b>"
        )
        await message.reply_text(response_text)
    except Exception as e:
        logger.error(f"Error in set_pm_search: {e}")
        await message.reply_text(f"<b>❗ An error occurred: {e}</b>")

@Client.on_message(filters.private & filters.command("movie_update") & filters.user(ADMINS))
async def set_movie_update_notification(client, message):
    bot_id = client.me.id
    try:
        option = message.text.split(" ", 1)[1].strip().lower()
        enable_status = option in ['on', 'true']
    except (IndexError, ValueError):
        await message.reply_text("<b>💔 Invalid option. Please send 'on' or 'off' after the command.</b>")
        return
    try:
        await db.update_movie_update_status(bot_id, enable_status)
        response_text = (
            "<b>ᴍᴏᴠɪᴇ ᴜᴘᴅᴀᴛᴇ ɴᴏᴛɪꜰɪᴄᴀᴛɪᴏɴ ᴇɴᴀʙʟᴇᴅ ✅</b>" if enable_status
            else "<b>ᴍᴏᴠɪᴇ ᴜᴘᴅᴀᴛᴇ ɴᴏᴛɪꜰɪᴄᴀᴛɪᴏɴ ᴅɪꜱᴀʙʟᴇᴅ ❌</b>"
        )
        await message.reply_text(response_text)
    except Exception as e:
        logger.error(f"Error in set_movie_update_notification: {e}")
        await message.reply_text(f"<b>❗ An error occurred: {e}</b>")

@Client.on_message(filters.command("restart") & filters.user(ADMINS))
async def restart_bot(bot, message):
    try:
        await message.reply_text("<b>🔄 ʀᴇꜱᴛᴀʀᴛɪɴɢ ʙᴏᴛ...\n\n<i>ʙᴏᴛ ᴡɪʟʟ ʙᴇ ʙᴀᴄᴋ ɪɴ ᴀ ꜰᴇᴡ ꜱᴇᴄᴏɴᴅꜱ ✅</i></b>")
    except Exception:
        pass
    os.execl(sys.executable, sys.executable, *sys.argv)

@Client.on_message(filters.command("del_msg") & filters.user(ADMINS))
async def del_msg(client, message):
    confirm_markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("Yes", callback_data="confirm_del_yes"),
        InlineKeyboardButton("No", callback_data="confirm_del_no")
    ]])
    sent_message = await message.reply_text(
        "⚠️ Aʀᴇ ʏᴏᴜ sᴜʀᴇ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴄʟᴇᴀʀ ᴛʜᴇ ᴜᴘᴅᴀᴛᴇs ᴄʜᴀɴɴᴇʟ ʟɪsᴛ ?\n\n ᴅᴏ ʏᴏᴜ ꜱᴛɪʟʟ ᴡᴀɴᴛ ᴛᴏ ᴄᴏɴᴛɪɴᴜᴇ ?",
        reply_markup=confirm_markup
    )
    await asyncio.sleep(60)
    try:
        await sent_message.delete()
    except Exception as e:
        print(f"Error deleting the message: {e}")

@Client.on_callback_query(filters.regex('^confirm_del_'))
async def confirmation_handler(client, callback_query):
    action = callback_query.data.split("_")[-1]
    if action == "yes":
        await db.delete_all_msg()
        await callback_query.message.edit_text('🧹 ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ ʟɪsᴛ ʜᴀs ʙᴇᴇɴ ᴄʟᴇᴀʀᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ ✅')
    elif action == "no":
        await callback_query.message.delete()
    await callback_query.answer()

@Client.on_message(filters.command('set_caption'))
async def save_caption(client, message):
    grp_id = message.chat.id
    title = message.chat.title
    invite_link = await client.export_chat_invite_link(grp_id)
    if not await is_check_admin(client, grp_id, message.from_user.id):
        return await message.reply_text(script.NT_ADMIN_ALRT_TXT)
    chat_type = message.chat.type
    if chat_type not in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        return await message.reply_text("<b>ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ ɪɴ ɢʀᴏᴜᴘ...</b>")
    try:
        caption = message.text.split(" ", 1)[1]
    except:
        return await message.reply_text("<code>ɢɪᴠᴇ ᴍᴇ ᴀ ᴄᴀᴘᴛɪᴏɴ ᴀʟᴏɴɢ ᴡɪᴛʜ ɪᴛ.\n\nᴇxᴀᴍᴘʟᴇ -\n\nꜰᴏʀ ꜰɪʟᴇ ɴᴀᴍᴇ ꜱᴇɴᴅ <code>{file_name}</code>\nꜰᴏʀ ꜰɪʟᴇ ꜱɪᴢᴇ ꜱᴇɴᴅ <code>{file_size}</code>\n\n<code>/set_caption {file_name}</code></code>")
    await save_group_settings(grp_id, 'caption', caption)
    await message.reply_text(f"ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴄʜᴀɴɢᴇᴅ ᴄᴀᴘᴛɪᴏɴ ꜰᴏʀ {title}\n\nᴄᴀᴘᴛɪᴏɴ - {caption}", disable_web_page_preview=True)
    try:
        await client.send_message(LOG_API_CHANNEL, f"#Set_Caption\n\nɢʀᴏᴜᴘ ɴᴀᴍᴇ : {title}\n\nɢʀᴏᴜᴘ ɪᴅ: {grp_id}\nɪɴᴠɪᴛᴇ ʟɪɴᴋ : {invite_link}\n\nᴜᴘᴅᴀᴛᴇᴅ ʙʏ : {message.from_user.username}")
    except Exception as _log_err:
        logging.warning(f"set_caption: log send failed: {_log_err}")


@Client.on_message(filters.command(["set_tutorial", "set_tutorial_2", "set_tutorial_3"]))
async def set_tutorial(client, message: Message):
    grp_id = message.chat.id
    title = message.chat.title
    chat_type = message.chat.type
    if chat_type not in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        return await message.reply_text(
            f"<b>ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ ɪɴ ɢʀᴏᴜᴘ...\n\nGroup Name: {title}\nGroup ID: {grp_id}</b>"
        )
    if not await is_check_admin(client, grp_id, message.from_user.id):
        return await message.reply_text(script.NT_ADMIN_ALRT_TXT)

    try:
        tutorial_link = message.text.split(" ", 1)[1]
    except IndexError:
        return await message.reply_text(
            f"<b>ᴄᴏᴍᴍᴀɴᴅ ɪɴᴄᴏᴍᴘʟᴇᴛᴇ !!\n\nᴜꜱᴇ ʟɪᴋᴇ ᴛʜɪꜱ -</b>\n\n"
            f"<code>/{message.command[0]} https://t.me/dreamxbotz</code>"
        )
    if message.command[0] == "set_tutorial":
        tutorial_key = "tutorial"
    else:
        tutorial_key = f"tutorial_{message.command[0].split('_', 2)[2]}"

    await save_group_settings(grp_id, tutorial_key, tutorial_link)
    invite_link = await client.export_chat_invite_link(grp_id)
    await message.reply_text(
        f"<b>ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴄʜᴀɴɢᴇᴅ {tutorial_key.replace('_', ' ').title()} ꜰᴏʀ {title}</b>\n\n"
        f"ʟɪɴᴋ - {tutorial_link}",
        disable_web_page_preview=True
    )
    await client.send_message(
        LOG_API_CHANNEL,
        f"#Set_{tutorial_key.title()}_Video\n\n"
        f"ɢʀᴏᴜᴘ ɴᴀᴍᴇ : {title}\n"
        f"ɢʀᴏᴜᴘ ɪᴅ : {grp_id}\n"
        f"ɪɴᴠɪᴛᴇ ʟɪɴᴋ : {invite_link}\n"
        f"ᴜᴘᴅᴀᴛᴇᴅ ʙʏ : {message.from_user.mention}"
    )



async def _validate_shortner_api(url: str, api: str):
    """
    Test a shortener API key with a live dummy request. Returns (ok, message).
    Tries Shortzy first (standard format with format=json).
    Falls back to a raw GET request without format=json for sites like teraboxlinks.com.
    """
    from shortzy import Shortzy
    _test_url = "https://telegram.me/telegram"

    # Attempt 1: Shortzy (adds &format=json — works for most shorteners)
    try:
        shortzy = Shortzy(api, url)
        result = await asyncio.wait_for(shortzy.convert(_test_url), timeout=10)
        if (isinstance(result, str) and result.startswith("http")
                and result.rstrip("/") != _test_url.rstrip("/")
                and "?api=" not in result):
            return True, result
        # If we get here Shortzy "succeeded" but returned a bad value — fall through
    except asyncio.TimeoutError:
        return False, "Request timed out (10s) — check that the site URL is correct"
    except Exception:
        pass  # Shortzy failed — try raw fallback below

    # Attempt 2: raw GET without format=json (teraboxlinks.com style)
    try:
        result = await asyncio.wait_for(raw_shorten(api, url, _test_url), timeout=10)
        if result and result.startswith("http") and "?api=" not in result:
            return True, result
        return False, f"API returned an unexpected URL: {result!r}"
    except asyncio.TimeoutError:
        return False, "Request timed out (10s) — check that the site URL is correct"
    except Exception as e:
        return False, str(e)

async def handle_shortner_command(c, m, shortner_key, api_key, log_prefix, fallback_url, fallback_api):
    # Check chat type FIRST - so PM users get the right error, not "not admin"
    if m.chat.type not in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        return await m.reply_text("<b>ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ ɪɴ ɢʀᴏᴜᴘ...</b>")
    grp_id = m.chat.id
    if not await is_check_admin(c, grp_id, m.from_user.id):
        return await m.reply_text(script.NT_ADMIN_ALRT_TXT)
    if len(m.command) != 3:
        return await m.reply(
            "<b>ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ ʟɪᴋᴇ -\n\n"
            f"`/{m.command[0]} omegalinks.in your_api_key_here`</b>"
        )
    sts = await m.reply("<b>♻️ ᴄʜᴇᴄᴋɪɴɢ...</b>")
    await asyncio.sleep(1.2)
    await sts.delete()
    URL = m.command[1]
    API = m.command[2]
    # ── Validate API key with a live test before saving ───────────────────
    sts2 = await m.reply("<b>🔍 ᴠᴀʟɪᴅᴀᴛɪɴɢ ᴀᴘɪ ᴋᴇʏ...</b>")
    ok, result = await _validate_shortner_api(URL, API)
    if not ok:
        await sts2.edit(
            f"<b>❌ API Key Test Failed!</b>\n\n"
            f"<b>Site:</b> <code>{URL}</code>\n"
            f"<b>Error:</b> <code>{result}</code>\n\n"
            "<b>Please double-check your site URL and API key, then try again.\n"
            "Shortner was NOT saved.</b>"
        )
        return
    await sts2.delete()
    # ─────────────────────────────────────────────────────────────────────
    # Save settings BEFORE anything else - must never be lost due to logging failures
    await save_group_settings(grp_id, shortner_key, URL)
    await save_group_settings(grp_id, api_key, API)
    await m.reply_text(
        f"<b><u>✅ sʜᴏʀᴛɴᴇʀ ᴀᴅᴅᴇᴅ</u>\n\nꜱɪᴛᴇ - `{URL}`\nᴀᴘɪ - `{API}`</b>"
    )
    # Log to admin channel - fully isolated so any failure here NEVER corrupts saved shortner
    try:
        user_id = m.from_user.id
        user_info = f"@{m.from_user.username}" if m.from_user.username else f"{m.from_user.mention}"
        try:
            chat_obj = await c.get_chat(m.chat.id)
            link = chat_obj.invite_link or "N/A"
        except Exception:
            link = "N/A"
        grp_link = f"[{m.chat.title}]({link})" if link != "N/A" else str(m.chat.title)
        log_message = (
            f"#{log_prefix}\n\nɴᴀᴍᴇ - {user_info}\n\nɪᴅ - `{user_id}`"
            f"\n\nꜱɪᴛᴇ - {URL}\n\nᴀᴘɪ - `{API}`"
            f"\n\nɢʀᴏᴜᴘ - {grp_link}\nɢʀᴏᴜᴘ ɪᴅ - `{grp_id}`"
        )
        await c.send_message(LOG_API_CHANNEL, log_message, disable_web_page_preview=True)
    except Exception as _log_err:
        logging.warning(f"handle_shortner_command: log send failed: {_log_err}")

@Client.on_message(filters.command('set_shortner'))
async def set_shortner(c, m):
    await handle_shortner_command(c, m, 'shortner', 'api', 'New_Shortner_Set_For_1st_Verify', SHORTENER_WEBSITE, SHORTENER_API)

@Client.on_message(filters.command('set_shortner_2'))
async def set_shortner_2(c, m):
    await handle_shortner_command(c, m, 'shortner_two', 'api_two', 'New_Shortner_Set_For_2nd_Verify', SHORTENER_WEBSITE2, SHORTENER_API2)

@Client.on_message(filters.command('set_shortner_3'))
async def set_shortner_3(c, m):
    await handle_shortner_command(c, m, 'shortner_three', 'api_three', 'New_Shortner_Set_For_3rd_Verify', SHORTENER_WEBSITE3, SHORTENER_API3)


@Client.on_message(filters.command('remove_shortner'))
async def remove_shortner_cmd(c, m):
    if m.chat.type not in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        return await m.reply_text("<b>ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ ɪɴ ɢʀᴏᴜᴘ...</b>")
    grp_id = m.chat.id
    if not await is_check_admin(c, grp_id, m.from_user.id):
        return await m.reply_text(script.NT_ADMIN_ALRT_TXT)
    await save_group_settings(grp_id, 'shortner', SHORTENER_WEBSITE)
    await save_group_settings(grp_id, 'api', SHORTENER_API)
    await m.reply_text(
        "<b>✅ 1ꜱᴛ ꜱʜᴏʀᴛɴᴇʀ ʀᴇᴍᴏᴠᴇᴅ!</b>\n\n"
        "<b>ɴᴏᴡ ᴜꜱɪɴɢ ɢʟᴏʙᴀʟ ᴅᴇꜰᴀᴜʟᴛ.</b>\n"
        "<b>ᴜꜱᴇ /set_shortner ᴛᴏ ꜱᴇᴛ ᴀ ɴᴇᴡ ᴏɴᴇ.</b>"
    )

@Client.on_message(filters.command('remove_shortner_2'))
async def remove_shortner_2_cmd(c, m):
    if m.chat.type not in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        return await m.reply_text("<b>ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ ɪɴ ɢʀᴏᴜᴘ...</b>")
    grp_id = m.chat.id
    if not await is_check_admin(c, grp_id, m.from_user.id):
        return await m.reply_text(script.NT_ADMIN_ALRT_TXT)
    await save_group_settings(grp_id, 'shortner_two', SHORTENER_WEBSITE2)
    await save_group_settings(grp_id, 'api_two', SHORTENER_API2)
    await m.reply_text(
        "<b>✅ 2ɴᴅ ꜱʜᴏʀᴛɴᴇʀ ʀᴇᴍᴏᴠᴇᴅ!</b>\n\n"
        "<b>ɴᴏᴡ ᴜꜱɪɴɢ ɢʟᴏʙᴀʟ ᴅᴇꜰᴀᴜʟᴛ.</b>\n"
        "<b>ᴜꜱᴇ /set_shortner_2 ᴛᴏ ꜱᴇᴛ ᴀ ɴᴇᴡ ᴏɴᴇ.</b>"
    )

@Client.on_message(filters.command('remove_shortner_3'))
async def remove_shortner_3_cmd(c, m):
    if m.chat.type not in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        return await m.reply_text("<b>ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ ɪɴ ɢʀᴏᴜᴘ...</b>")
    grp_id = m.chat.id
    if not await is_check_admin(c, grp_id, m.from_user.id):
        return await m.reply_text(script.NT_ADMIN_ALRT_TXT)
    await save_group_settings(grp_id, 'shortner_three', SHORTENER_WEBSITE3)
    await save_group_settings(grp_id, 'api_three', SHORTENER_API3)
    await m.reply_text(
        "<b>✅ 3ʀᴅ ꜱʜᴏʀᴛɴᴇʀ ʀᴇᴍᴏᴠᴇᴅ!</b>\n\n"
        "<b>ɴᴏᴡ ᴜꜱɪɴɢ ɢʟᴏʙᴀʟ ᴅᴇꜰᴀᴜʟᴛ.</b>\n"
        "<b>ᴜꜱᴇ /set_shortner_3 ᴛᴏ ꜱᴇᴛ ᴀ ɴᴇᴡ ᴏɴᴇ.</b>"
    )


async def _handle_tutorial_command(c, m, tutorial_key, default_val, label):
    if m.chat.type not in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        return await m.reply_text("<b>ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ ɪɴ ɢʀᴏᴜᴘ...</b>")
    grp_id = m.chat.id
    if not await is_check_admin(c, grp_id, m.from_user.id):
        return await m.reply_text(script.NT_ADMIN_ALRT_TXT)
    if len(m.command) < 2:
        settings = await get_settings(grp_id)
        current = settings.get(tutorial_key, default_val) or "ɴᴏᴛ ꜱᴇᴛ"
        return await m.reply_text(
            f"<b>📎 {label} ᴛᴜᴛᴏʀɪᴀʟ ʟɪɴᴋ</b>\n\n"
            f"<b>ᴄᴜʀʀᴇɴᴛ:</b> <code>{current}</code>\n\n"
            f"<b>ᴜꜱᴀɢᴇ:</b> <code>/{m.command[0]} https://your-link-here</code>\n"
            f"<b>ᴛᴏ ʀᴇᴍᴏᴠᴇ:</b> <code>/remove_tutorial</code>",
            parse_mode=enums.ParseMode.HTML,
            disable_web_page_preview=True
        )
    url = m.command[1].strip()
    if not url.startswith(("http://", "https://")):
        return await m.reply_text(
            "<b>❌ ɪɴᴠᴀʟɪᴅ ᴜʀʟ!</b>\n\n"
            "<b>URL ᴍᴜꜱᴛ ꜱᴛᴀʀᴛ ᴡɪᴛʜ <code>https://</code> ᴏʀ <code>http://</code></b>",
            parse_mode=enums.ParseMode.HTML
        )
    await save_group_settings(grp_id, tutorial_key, url)
    await m.reply_text(
        f"<b>✅ {label} ᴛᴜᴛᴏʀɪᴀʟ ʟɪɴᴋ ꜱᴀᴠᴇᴅ!\n\n"
        f"🔗 <code>{url}</code>\n\n"
        f"ᴛʜɪꜱ ᴡɪʟʟ ᴀᴘᴘᴇᴀʀ ᴀꜱ ᴛʜᴇ \"⁉️ ʜᴏᴡ ᴛᴏ ᴠᴇʀɪꜰʏ\" ʙᴜᴛᴛᴏɴ ᴏɴᴄᴇ ʏᴏᴜ ʀᴇ-ᴀᴅᴅ ɪᴛ.</b>",
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True
    )

@Client.on_message(filters.command('set_tutorial'))
async def set_tutorial_cmd(c, m):
    await _handle_tutorial_command(c, m, 'tutorial', TUTORIAL, '1ꜱᴛ')

@Client.on_message(filters.command('set_tutorial_2'))
async def set_tutorial_2_cmd(c, m):
    await _handle_tutorial_command(c, m, 'tutorial_2', TUTORIAL_2, '2ɴᴅ')

@Client.on_message(filters.command('set_tutorial_3'))
async def set_tutorial_3_cmd(c, m):
    await _handle_tutorial_command(c, m, 'tutorial_3', TUTORIAL_3, '3ʀᴅ')

@Client.on_message(filters.command('remove_tutorial'))
async def remove_tutorial_cmd(c, m):
    if m.chat.type not in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        return await m.reply_text("<b>ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ ɪɴ ɢʀᴏᴜᴘ...</b>")
    grp_id = m.chat.id
    if not await is_check_admin(c, grp_id, m.from_user.id):
        return await m.reply_text(script.NT_ADMIN_ALRT_TXT)
    await save_group_settings(grp_id, 'tutorial', TUTORIAL)
    await save_group_settings(grp_id, 'tutorial_2', TUTORIAL_2)
    await save_group_settings(grp_id, 'tutorial_3', TUTORIAL_3)
    await m.reply_text(
        "<b>✅ ᴀʟʟ ᴛᴜᴛᴏʀɪᴀʟ ʟɪɴᴋꜱ ʀᴇꜱᴇᴛ ᴛᴏ ɢʟᴏʙᴀʟ ᴅᴇꜰᴀᴜʟᴛ.\n\n"
        "ᴜꜱᴇ /set_tutorial <url> ᴛᴏ ꜱᴇᴛ ᴀ ɴᴇᴡ ᴏɴᴇ.</b>",
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_message(filters.command('test_shortner'))
async def test_shortner_cmd(c, m):
    if m.chat.type not in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        return await m.reply_text("<b>ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ ɪɴ ɢʀᴏᴜᴘ...</b>")
    grp_id = m.chat.id
    if not await is_check_admin(c, grp_id, m.from_user.id):
        return await m.reply_text(script.NT_ADMIN_ALRT_TXT)
    settings = await get_settings(grp_id)
    msg = await m.reply_text("<b>🔍 ᴛᴇꜱᴛɪɴɢ ꜱʜᴏʀᴛɴᴇʀꜱ, ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ...</b>")
    checks = [
        ("1ꜱᴛ", settings.get('shortner', SHORTENER_WEBSITE), settings.get('api', SHORTENER_API)),
        ("2ɴᴅ", settings.get('shortner_two', SHORTENER_WEBSITE2), settings.get('api_two', SHORTENER_API2)),
        ("3ʀᴅ", settings.get('shortner_three', SHORTENER_WEBSITE3), settings.get('api_three', SHORTENER_API3)),
    ]
    results = []
    for label, site, api in checks:
        if not api:
            results.append(f"<b>{label}:</b> ⚪ ɴᴏᴛ ᴄᴏɴꜰɪɢᴜʀᴇᴅ")
            continue
        masked = f"{api[:4]}...{api[-4:]}" if len(api) > 8 else "***"
        ok, detail = await _validate_shortner_api(site, api)
        if ok:
            results.append(
                f"<b>{label} ({site}) <code>[{masked}]</code>:</b> ✅ ᴡᴏʀᴋɪɴɢ\n"
                f"<code>{detail[:80]}</code>"
            )
        else:
            results.append(
                f"<b>{label} ({site}) <code>[{masked}]</code>:</b> ❌ ꜰᴀɪʟᴇᴅ\n"
                f"<code>{detail[:250]}</code>"
            )
    text = "<b>🔍 ꜱʜᴏʀᴛɴᴇʀ ᴛᴇꜱᴛ ʀᴇꜱᴜʟᴛꜱ</b>\n\n" + "\n\n".join(results)
    await msg.edit(text, parse_mode=enums.ParseMode.HTML)

@Client.on_message(filters.command('set_log_channel'))
async def set_log(client, message):
    grp_id = message.chat.id
    title = message.chat.title
    if not await is_check_admin(client, grp_id, message.from_user.id):
        return await message.reply_text(script.NT_ADMIN_ALRT_TXT)
    if len(message.text.split()) == 1:
        await message.reply("<b>ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ ʟɪᴋᴇ ᴛʜɪꜱ - \n\n`/set_log_channel -100******`</b>")
        return
    sts = await message.reply("<b>♻️ ᴄʜᴇᴄᴋɪɴɢ...</b>")
    await asyncio.sleep(1.2)
    await sts.delete()
    chat_type = message.chat.type
    if chat_type not in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        return await message.reply_text("<b>ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ ɪɴ ɢʀᴏᴜᴘ...</b>")
    try:
        log = int(message.text.split(" ", 1)[1])
    except IndexError:
        return await message.reply_text("<b><u>ɪɴᴠᴀɪʟᴅ ꜰᴏʀᴍᴀᴛ!!</u>\n\nᴜsᴇ ʟɪᴋᴇ ᴛʜɪs - `/set_log_channel -100xxxxxxxx`</b>")
    except ValueError:
        return await message.reply_text('<b>ᴍᴀᴋᴇ sᴜʀᴇ ɪᴅ ɪs ɪɴᴛᴇɢᴇʀ...</b>')
    try:
        t = await client.send_message(chat_id=log, text="<b>ʜᴇʏ ᴡʜᴀᴛ's ᴜᴘ!!</b>")
        await asyncio.sleep(3)
        await t.delete()
    except Exception as e:
        return await message.reply_text(f'<b><u>😐 ᴍᴀᴋᴇ sᴜʀᴇ ᴛʜɪs ʙᴏᴛ ᴀᴅᴍɪɴ ɪɴ ᴛʜᴀᴛ ᴄʜᴀɴɴᴇʟ...</u>\n\n💔 ᴇʀʀᴏʀ - <code>{e}</code></b>')
    await save_group_settings(grp_id, 'log', log)
    await message.reply_text(f"<b>✅ sᴜᴄᴄᴇssꜰᴜʟʟʏ sᴇᴛ ʏᴏᴜʀ ʟᴏɢ ᴄʜᴀɴɴᴇʟ ꜰᴏʀ {title}\n\nɪᴅ - `{log}`</b>", disable_web_page_preview=True)
    try:
        user_id = message.from_user.id
        user_info = f"@{message.from_user.username}" if message.from_user.username else f"{message.from_user.mention}"
        link = (await client.get_chat(message.chat.id)).invite_link
        grp_link = f"[{message.chat.title}]({link})"
        log_message = f"#New_Log_Channel_Set\n\nɴᴀᴍᴇ - {user_info}\n\nɪᴅ - `{user_id}`\n\nʟᴏɢ ᴄʜᴀɴɴᴇʟ ɪᴅ - `{log}`\nɢʀᴏᴜᴘ ʟɪɴᴋ - `{grp_link}`\n\nɢʀᴏᴜᴘ ɪᴅ : `{grp_id}`"
        await client.send_message(LOG_API_CHANNEL, log_message, disable_web_page_preview=True) 
    except Exception as _log_err:
        logging.warning(f"set_log_channel: log send failed: {_log_err}")


@Client.on_message(filters.command('set_time'))
async def set_time(client, message):
    chat_type = message.chat.type
    if chat_type not in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        return await message.reply_text("<b>ᴜsᴇ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ɪɴ ɢʀᴏᴜᴘ...</b>")       
    grp_id = message.chat.id
    title = message.chat.title
    invite_link = await client.export_chat_invite_link(grp_id)
    if not await is_check_admin(client, grp_id, message.from_user.id):
        return await message.reply_text(script.NT_ADMIN_ALRT_TXT)
    try:
        time = int(message.text.split(" ", 1)[1])
    except:
        return await message.reply_text("<b>ᴄᴏᴍᴍᴀɴᴅ ɪɴᴄᴏᴍᴘʟᴇᴛᴇ\n\nᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ ʟɪᴋᴇ ᴛʜɪꜱ - <code>/set_time 600</code> [ ᴛɪᴍᴇ ᴍᴜꜱᴛ ʙᴇ ɪɴ ꜱᴇᴄᴏɴᴅꜱ ]</b>")   
    await save_group_settings(grp_id, 'verify_time', time)
    await message.reply_text(f"<b>✅️ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ꜱᴇᴛ 2ɴᴅ ᴠᴇʀɪꜰʏ ᴛɪᴍᴇ ꜰᴏʀ {title}\n\nᴛɪᴍᴇ - <code>{time}</code></b>")
    try:
        await client.send_message(LOG_API_CHANNEL, f"#Set_2nd_Verify_Time\n\nɢʀᴏᴜᴘ ɴᴀᴍᴇ : {title}\n\nɢʀᴏᴜᴘ ɪᴅ : {grp_id}\n\nɪɴᴠɪᴛᴇ ʟɪɴᴋ : {invite_link}\n\nᴜᴘᴅᴀᴛᴇᴅ ʙʏ : {message.from_user.username}")
    except Exception as _log_err:
        logging.warning(f"set_time: log send failed: {_log_err}")

@Client.on_message(filters.command('set_time_2'))
async def set_time_2(client, message):
    chat_type = message.chat.type
    if chat_type not in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        return await message.reply_text("<b>ᴜsᴇ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ɪɴ ɢʀᴏᴜᴘ...</b>")       
    grp_id = message.chat.id
    title = message.chat.title
    invite_link = await client.export_chat_invite_link(grp_id)
    if not await is_check_admin(client, grp_id, message.from_user.id):
        return await message.reply_text(script.NT_ADMIN_ALRT_TXT)
    try:
        time = int(message.text.split(" ", 1)[1])
    except:
        return await message.reply_text("<b>ᴄᴏᴍᴍᴀɴᴅ ɪɴᴄᴏᴍᴘʟᴇᴛᴇ\n\nᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ ʟɪᴋᴇ ᴛʜɪꜱ - <code>/set_time 3600</code> [ ᴛɪᴍᴇ ᴍᴜꜱᴛ ʙᴇ ɪɴ ꜱᴇᴄᴏɴᴅꜱ ]</b>")   
    await save_group_settings(grp_id, 'third_verify_time', time)
    await message.reply_text(f"<b>✅️ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ꜱᴇᴛ 3ʀᴅ ᴠᴇʀɪꜰʏ ᴛɪᴍᴇ ꜰᴏʀ {title}\n\nᴛɪᴍᴇ - <code>{time}</code></b>")
    try:
        await client.send_message(LOG_API_CHANNEL, f"#Set_3rd_Verify_Time\n\nɢʀᴏᴜᴘ ɴᴀᴍᴇ : {title}\n\nɢʀᴏᴜᴘ ɪᴅ : {grp_id}\n\nɪɴᴠɪᴛᴇ ʟɪɴᴋ : {invite_link}\n\nᴜᴘᴅᴀᴛᴇᴅ ʙʏ : {message.from_user.username}")
    except Exception as _log_err:
        logging.warning(f"set_time_2: log send failed: {_log_err}")


@Client.on_message(filters.command('details'))
async def all_settings(client, message):
    if message.chat.type not in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        return await message.reply_text("<b>ᴜsᴇ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ɪɴ ɢʀᴏᴜᴘ...</b>")
    grp_id = message.chat.id
    title = message.chat.title
    if not await is_check_admin(client, grp_id, message.from_user.id):
        return await message.reply_text(script.NT_ADMIN_ALRT_TXT)
    try:
        settings = await get_settings(grp_id)
    except Exception as e:
        return await message.reply_text(f"<b>⚠️ ᴇʀʀᴏʀ ꜰᴇᴛᴄʜɪɴɢ ꜱᴇᴛᴛɪɴɢꜱ:</b>\n<code>{e}</code>")
    text = generate_settings_text(settings, title)
    btn = [
        [InlineKeyboardButton("♻️ ʀᴇꜱᴇᴛ ꜱᴇᴛᴛɪɴɢꜱ", callback_data=f"reset_group_{grp_id}")],
        [InlineKeyboardButton("🚫 ᴄʟᴏꜱᴇ", callback_data="close_data")]
    ]
    dlt = await message.reply_text(text, reply_markup=InlineKeyboardMarkup(btn), disable_web_page_preview=True)
    await asyncio.sleep(300)
    await dlt.delete()

@Client.on_callback_query(filters.regex(r"^reset_group_(\-\d+)$"))
async def reset_group_callback(client, callback_query):
    grp_id = int(callback_query.matches[0].group(1))
    user_id = callback_query.from_user.id
    if not await is_check_admin(client, grp_id, user_id):
        return await callback_query.answer(script.NT_ADMIN_ALRT_TXT, show_alert=True)
    await callback_query.answer("♻️ ʀᴇꜱᴇᴛᴛɪɴɢ ꜱᴇᴛᴛɪɴɢꜱ...")
    defaults = {
        'shortner': SHORTENER_WEBSITE,
        'api': SHORTENER_API,
        'shortner_two': SHORTENER_WEBSITE2,
        'api_two': SHORTENER_API2,
        'shortner_three': SHORTENER_WEBSITE3,
        'api_three': SHORTENER_API3,
        'verify_time': TWO_VERIFY_GAP,
        'third_verify_time': THREE_VERIFY_GAP,
        'template': IMDB_TEMPLATE,
        'tutorial': TUTORIAL,
        'tutorial_2': TUTORIAL_2,
        'tutorial_3': TUTORIAL_3,
        'caption': CUSTOM_FILE_CAPTION,
        'log': LOG_CHANNEL,
        'is_verify': IS_VERIFY,
        'fsub': AUTH_CHANNELS
    }
    current = await get_settings(grp_id)
    if current == defaults:
        return await callback_query.answer("✅ ꜱᴇᴛᴛɪɴɢꜱ ᴀʟʀᴇᴀᴅʏ ᴅᴇꜰᴀᴜʟᴛ.", show_alert=True)
    for key, value in defaults.items():
        await save_group_settings(grp_id, key, value)
    updated = await get_settings(grp_id)
    title = callback_query.message.chat.title
    text = generate_settings_text(updated, title, reset_done=True)
    buttons = [
        [InlineKeyboardButton("♻️ ʀᴇꜱᴇᴛ ꜱᴇᴛᴛɪɴɢꜱ", callback_data=f"reset_group_{grp_id}")],
        [InlineKeyboardButton("🚫 ᴄʟᴏꜱᴇ", callback_data="close_data")]
    ]
    await callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)

@Client.on_message(filters.command("verify") & filters.user(ADMINS))
async def verify(bot, message):
    try:
        chat_type = message.chat.type
        command_text = message.text.split(' ')[1].lower() if len(message.text.split(' ')) > 1 else None
        if chat_type == enums.ChatType.PRIVATE:
            if command_text == "off":
                import info
                info.IS_VERIFY = False
                bot_id = getattr(temp, 'ME', 0)
                if bot_id:
                    await db.botcol.update_one({'setting': 'IS_VERIFY'}, {'$set': {'IS_VERIFY': False}}, upsert=True)
                temp.SETTINGS.clear()
                return await message.reply_text("✗ <b>ᴠᴇʀɪꜰʏ sᴜᴄᴄᴇssꜰᴜʟʟʏ ᴅɪsᴀʙʟᴇᴅ ɢʟᴏʙᴀʟʟʏ & ꜰᴏʀ ᴘᴍ.</b>\nUsers will now receive files directly without shorteners or ads.")
            elif command_text == "on":
                import info
                info.IS_VERIFY = True
                bot_id = getattr(temp, 'ME', 0)
                if bot_id:
                    await db.botcol.update_one({'setting': 'IS_VERIFY'}, {'$set': {'IS_VERIFY': True}}, upsert=True)
                temp.SETTINGS.clear()
                return await message.reply_text("✓ <b>ᴠᴇʀɪꜰʏ sᴜᴄᴄᴇssꜰᴜʟʟʏ ᴇɴᴀʙʟᴇᴅ ɢʟᴏʙᴀʟʟʏ & ꜰᴏʀ ᴘᴍ.</b>")
            else:
                return await message.reply_text("ʜɪ, ᴛᴏ ᴇɴᴀʙʟᴇ ᴠᴇʀɪꜰʏ ɪɴ ᴘᴍ/ɢʟᴏʙᴀʟ, ᴜsᴇ <code>/verify on</code> ᴀɴᴅ ᴛᴏ ᴅɪsᴀʙʟᴇ, ᴜsᴇ <code>/verify off</code>.")
        if chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
            grpid = message.chat.id
            title = message.chat.title
            if command_text == "off":
                await save_group_settings(grpid, 'is_verify', False)
                return await message.reply_text("✗ ᴠᴇʀɪꜰʏ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴅɪsᴀʙʟᴇᴅ.")
            elif command_text == "on":
                await save_group_settings(grpid, 'is_verify', True)
                return await message.reply_text("✓ ᴠᴇʀɪꜰʏ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴇɴᴀʙʟᴇᴅ.")
            else:
                return await message.reply_text("ʜɪ, ᴛᴏ ᴇɴᴀʙʟᴇ ᴠᴇʀɪꜰʏ, ᴜsᴇ <code>/verify on</code> ᴀɴᴅ ᴛᴏ ᴅɪsᴀʙʟᴇ ᴠᴇʀɪꜰʏ, ᴜsᴇ <code>/verify off</code>.")
    except Exception as e:
        print(f"Error: {e}")
        await message.reply_text(f"Error: {e}")

@Client.on_message(filters.command("verify_status") & filters.user(ADMINS))
async def verify_status(bot, message):
    try:
        if message.chat.type == enums.ChatType.PRIVATE:
            import info
            bot_id = getattr(temp, 'ME', 0)
            bot_doc = await db.botcol.find_one({'setting': 'IS_VERIFY'})
is_verify = bot_doc['IS_VERIFY'] if bot_doc and 'IS_VERIFY' in bot_doc else info.IS_VERIFY
            status_icon = "✅ ᴇɴᴀʙʟᴇᴅ" if is_verify else "❌ ᴅɪsᴀʙʟᴇᴅ"
            text = (
                f"<b>📊 ɢʟᴏʙᴀʟ & ᴘᴍ ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ sᴛᴀᴛᴜs</b>\n\n"
                f"<b>🔐 ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ / sʜᴏʀᴛɴᴇʀ:</b> {status_icon}\n\n"
                f"💡 <i>Tip: Use <code>/disableverify</code> or <code>/verify off</code> to disable ads/shorteners in PM.</i>"
            )
            return await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
        grp_id = message.chat.id
        title = message.chat.title or str(grp_id)
        settings = await get_settings(grp_id)

        is_verify   = settings.get('is_verify', IS_VERIFY)
        verify_time = settings.get('verify_time', TWO_VERIFY_GAP)
        third_time  = settings.get('third_verify_time', THREE_VERIFY_GAP)
        shortner1   = settings.get('shortner', SHORTENER_WEBSITE) or "ɴᴏᴛ sᴇᴛ"
        shortner2   = settings.get('shortner_two', SHORTENER_WEBSITE2) or "ɴᴏᴛ sᴇᴛ"
        shortner3   = settings.get('shortner_three', SHORTENER_WEBSITE3) or "ɴᴏᴛ sᴇᴛ"
        api1_ok     = "✓ sᴇᴛ" if settings.get('api', SHORTENER_API) else "✗ ᴍɪssɪɴɢ"
        api2_ok     = "✓ sᴇᴛ" if settings.get('api_two', SHORTENER_API2) else "✗ ᴍɪssɪɴɢ"
        api3_ok     = "✓ sᴇᴛ" if settings.get('api_three', SHORTENER_API3) else "✗ ᴍɪssɪɴɢ"
        status_icon = "✅ ᴇɴᴀʙʟᴇᴅ" if is_verify else "❌ ᴅɪsᴀʙʟᴇᴅ"

        text = (
            f"<b>📊 ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ sᴛᴀᴛᴜs</b>\n"
            f"<b>👥 ɢʀᴏᴜᴘ:</b> {title}\n\n"
            f"<b>🔐 ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ:</b> {status_icon}\n\n"
            f"<b>⏱ ᴛɪᴍᴇ ɢᴀᴘs:</b>\n"
            f"  • sᴛᴇᴘ 1 → sᴛᴇᴘ 2: <code>{get_readable_time(verify_time)}</code>\n"
            f"  • sᴛᴇᴘ 2 → sᴛᴇᴘ 3: <code>{get_readable_time(third_time)}</code>\n\n"
            f"<b>🔗 sʜᴏʀᴛᴇɴᴇʀs:</b>\n"
            f"  1⃣ <code>{shortner1}</code>  │  ᴀᴘɪ: {api1_ok}\n"
            f"  2⃣ <code>{shortner2}</code>  │  ᴀᴘɪ: {api2_ok}\n"
            f"  3⃣ <code>{shortner3}</code>  │  ᴀᴘɪ: {api3_ok}\n\n"
            f"<i>ᴛᴏɢɢʟᴇ: /verify on  ·  /verify off</i>"
        )
        await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await message.reply_text(f"Error: {e}")

@Client.on_message(filters.command('set_fsub'))
async def set_fsub(client, message):
    try:
        userid = message.from_user.id if message.from_user else None
        if not userid:
            return await message.reply("<b>You are Anonymous admin you can't use this command !</b>")
        if message.chat.type not in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
            return await message.reply_text("ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ ᴄᴀɴ ᴏɴʟʏ ʙᴇ ᴜsᴇᴅ ɪɴ ɢʀᴏᴜᴘs")
        grp_id = message.chat.id
        title = message.chat.title
        if not await is_check_admin(client, grp_id, userid):
            return await message.reply_text(script.NT_ADMIN_ALRT_TXT)
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            return await message.reply_text(
                "ᴄᴏᴍᴍᴀɴᴅ ɪɴᴄᴏᴍᴘʟᴇᴛᴇ!\n\n"
                "ᴄᴀɴ ᴀᴅᴅ ᴍᴜʟᴛɪᴘʟᴇ ᴄʜᴀɴɴᴇʟs sᴇᴘᴀʀᴀᴛᴇᴅ ʙʏ sᴘᴀᴄᴇs. ʟɪᴋᴇ: /sᴇᴛ_ғsᴜʙ ɪᴅ1 ɪᴅ2 ɪᴅ3\n"
            )
        option = args[1].strip()
        try:
            fsub_ids = [int(x) for x in option.split()]
        except ValueError:
            return await message.reply_text('ᴍᴀᴋᴇ sᴜʀᴇ ᴀʟʟ ɪᴅs ᴀʀᴇ ɪɴᴛᴇɢᴇʀs.')
        if len(fsub_ids) > 5:
            return await message.reply_text("ᴍᴀxɪᴍᴜᴍ 5 ᴄʜᴀɴɴᴇʟs ᴀʟʟᴏᴡᴇᴅ.")
        channels = "ᴄʜᴀɴɴᴇʟs:\n"
        channel_titles = []
        for id in fsub_ids:
            try:
                chat = await client.get_chat(id)
            except Exception as e:
                return await message.reply_text(
                    f"{id} ɪs ɪɴᴠᴀʟɪᴅ!\nᴍᴀᴋᴇ sᴜʀᴇ ᴛʜɪs ʙᴏᴛ ɪs ᴀᴅᴍɪɴ ɪɴ ᴛʜᴀᴛ ᴄʜᴀɴɴᴇʟ.\n\nError - {e}"
                )
            if chat.type != enums.ChatType.CHANNEL:
                return await message.reply_text(f"{id} ɪs ɴᴏᴛ ᴀ ᴄʜᴀɴɴᴇʟ.")
            channel_titles.append(f"{chat.title} (`{id}`)")
            channels += f'{chat.title}\n'
        await save_group_settings(grp_id, 'fsub', fsub_ids)
        await message.reply_text(f"sᴜᴄᴄᴇssғᴜʟʟʏ sᴇᴛ ꜰꜱᴜʙ ᴄʜᴀɴɴᴇʟ(ꜱ) ғᴏʀ {title} ᴛᴏ\n\n{channels}")
        mention = message.from_user.mention if message.from_user else "Unknown"
        await client.send_message(
            LOG_API_CHANNEL,
            f"#Fsub_Channel_set\n\n"
            f"ᴜꜱᴇʀ - {mention} ꜱᴇᴛ ᴛʜᴇ ꜰᴏʀᴄᴇ ᴄʜᴀɴɴᴇʟ(ꜱ) ꜰᴏʀ {title}:\n\n"
            f"ꜰꜱᴜʙ ᴄʜᴀɴɴᴇʟ(ꜱ):\n" + '\n'.join(channel_titles)
        )
    except Exception as e:
        err_text = f"⚠️ Error in set_fSub :\n{e}"
        logger.error(err_text)
        try:
            await client.send_message(LOG_API_CHANNEL, err_text)
        except Exception as _log_err:
            logging.warning(f"set_fsub: error log send failed: {_log_err}")

@Client.on_message(filters.private & filters.command("resetallgroup") & filters.user(ADMINS))
async def reset_all_settings(client, message):
    try:
        reset_count = await db.dreamx_reset_settings()
        await message.reply_text(
            f"<b>ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ ꜱᴇᴛᴛɪɴɢꜱ ꜰᴏʀ  <code>{reset_count}</code> ɢʀᴏᴜᴘꜱ. ᴅᴇꜰᴀᴜʟᴛ ᴠᴀʟᴜᴇꜱ ᴡɪʟʟ ʙᴇ ᴜꜱᴇᴅ ✅</b>",
            quote=True
        )
    except Exception as e:
        print(f"[ERROR] reset_all_settings: {e}")
        await message.reply_text(
            "<b>🚫 An error occurred while resetting group settings.\nPlease try again later.</b>",
            quote=True
        )

@Client.on_message(filters.command("trial_reset"))
async def reset_trial(client, message):
    user_id = message.from_user.id
    if user_id not in ADMINS:
        await message.reply("ʏᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ ᴀɴʏ ᴘᴇʀᴍɪꜱꜱɪᴏɴ ᴛᴏ ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ.")
        return
    try:
        if len(message.command) > 1:
            target_user_id = int(message.command[1])
            updated_count = await db.reset_free_trial(target_user_id)
            message_text = f"ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ʀᴇꜱᴇᴛ ꜰʀᴇᴇ ᴛʀᴀɪʟ ꜰᴏʀ ᴜꜱᴇʀꜱ {target_user_id}." if updated_count else f"ᴜꜱᴇʀ {target_user_id} ɴᴏᴛ ꜰᴏᴜɴᴅ ᴏʀ ᴅᴏɴ'ᴛ ᴄʟᴀɪᴍ ꜰʀᴇᴇ ᴛʀᴀɪʟ ʏᴇᴛ."
        else:
            updated_count = await db.reset_free_trial()
            message_text = f"ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ʀᴇꜱᴇᴛ ꜰʀᴇᴇ ᴛʀᴀɪʟ ꜰᴏʀ {updated_count} ᴜꜱᴇʀꜱ."
        await message.reply_text(message_text)
    except Exception as e:
        await message.reply_text(f"An error occurred: {e}")


@Client.on_message(filters.private & filters.command("status"))
async def bot_status(bot, message):
    # Admin guard: if ADMINS list is configured, restrict to those users only.
    # If ADMINS is empty (not set in env), allow anyone — useful when first setting up.
    if ADMINS and message.from_user.id not in ADMINS:
        return await message.reply_text("⛔ This command is for admins only.")

    try:
        from dreamxbotz.zzint import StartTime
        now = time.time()
        uptime_secs = int(now - StartTime)
        days    = uptime_secs // 86400
        hours   = (uptime_secs % 86400) // 3600
        mins    = (uptime_secs % 3600) // 60
        secs    = uptime_secs % 60
        uptime_str = f"{days}d {hours}h {mins}m {secs}s"
    except Exception:
        uptime_str = "N/A"

    # Files in DB (supports MULTIPLE_DB mode)
    try:
        total_files = await Media.count_documents({})
        if MULTIPLE_DB:
            try:
                f2 = await Media2.count_documents({})
                total_files = f"{total_files} + {f2} = {total_files + f2}"
            except Exception:
                pass
    except Exception:
        total_files = "N/A"

    # Users and chats
    try:
        total_users = await db.total_users_count()
    except Exception:
        total_users = "N/A"

    try:
        total_chats = await db.total_chat_count()
    except Exception:
        total_chats = "N/A"

    # MongoDB health — try a lightweight ping
    try:
        await db.get_db_size()
        mongo_status = "✅ Connected"
    except Exception:
        mongo_status = "❌ Error"

    # RAM usage via /proc (Linux only — works on Railway)
    try:
        with open(f"/proc/{os.getpid()}/status") as _f:
            for _line in _f:
                if _line.startswith("VmRSS:"):
                    ram_mb = round(int(_line.split()[1]) / 1024, 1)
                    break
            else:
                ram_mb = "N/A"
    except Exception:
        ram_mb = "N/A"

    # Railway host — check multiple env vars in priority order
    host = (
        os.environ.get("RAILWAY_PUBLIC_DOMAIN") or
        os.environ.get("RAILWAY_SERVICE_NAME") or
        os.environ.get("RAILWAY_PROJECT_NAME") or
        "Railway"
    )

    status_msg = (
        "<b>ℹ️ Bot Status</b>\n\n"
        f"⏱ <b>Uptime:</b> <code>{uptime_str}</code>\n"
        f"📁 <b>Files in DB:</b> <code>{total_files}</code>\n"
        f"👥 <b>Total Users:</b> <code>{total_users}</code>\n"
        f"💬 <b>Total Groups:</b> <code>{total_chats}</code>\n"
        f"🗄 <b>MongoDB:</b> {mongo_status}\n"
        f"💾 <b>RAM:</b> <code>{ram_mb} MB</code>\n"
        f"🌐 <b>Host:</b> <code>{host}</code>\n"
        f"🤖 <b>Bot:</b> @{temp.U_NAME}"
    )
    await message.reply_text(status_msg)
