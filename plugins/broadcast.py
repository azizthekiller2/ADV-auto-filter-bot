import datetime
import time
import os
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.errors.exceptions.bad_request_400 import MessageTooLong
from pyrogram.errors import FloodWait
from database.users_chats_db import db
from info import ADMINS
from utils import users_broadcast, groups_broadcast, temp, get_readable_time, clear_junk, junk_group
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

lock = asyncio.Lock()

@Client.on_callback_query(filters.regex(r'^broadcast_cancel'))
async def broadcast_cancel(bot, query):
    _, target = query.data.split("#", 1)
    if target == 'users':
        temp.B_USERS_CANCEL = True
        await query.message.edit("🛑 ᴛʀʏɪɴɢ ᴛᴏ ᴄᴀɴᴄᴇʟ ᴜꜱᴇʀꜱ ʙʀᴏᴀᴅᴄᴀꜱᴛɪɴɢ...")
    elif target == 'groups':
        temp.B_GROUPS_CANCEL = True
        await query.message.edit("🛑 ᴛʀʏɪɴɢ ᴛᴏ ᴄᴀɴᴄᴇʟ ɢʀᴏᴜᴘꜱ ʙʀᴏᴀᴅᴄᴀꜱᴛɪɴɢ...")

@Client.on_message(filters.command("broadcast") & filters.user(ADMINS))
async def broadcast_users(bot, message):
    # Support two modes:
    # 1. Reply to a message + /broadcast  → broadcasts that message (supports video/photo/any media)
    # 2. /broadcast <text>               → broadcasts the inline text directly
    # Use message.text (not message.command) to preserve newlines and formatting
    raw_text = message.text or message.caption or ""
    parts = raw_text.split(None, 1)
    inline_text = parts[1].strip() if len(parts) > 1 else ""
    if not message.reply_to_message and not inline_text:
        return await message.reply(
            "❌ <b>How to use /broadcast:</b>\n\n"
            "<b>For text:</b> /broadcast Your announcement here\n"
            "<b>For video/photo:</b>\n"
            "1. Send your video/photo to this chat\n"
            "2. Long-press it → Reply\n"
            "3. Type /broadcast and send"
        )
    # Bug fix #2: show busy warning
    if lock.locked():
        return await message.reply("⚠️ Another broadcast is in progress. Please wait...")
    ask = await message.reply(
        "<b>Do you want to pin this message in users?</b>",
        reply_markup=ReplyKeyboardMarkup([["Yes", "No"]], one_time_keyboard=True, resize_keyboard=True)
    )
    try:
        dreamxbotz_user_response = await bot.listen(chat_id=message.chat.id, user_id=message.from_user.id, timeout=60)
    except asyncio.TimeoutError:
        await ask.delete()
        return await message.reply("❌ Timed out. Broadcast cancelled.")
    await ask.delete()
    try:
        await dreamxbotz_user_response.delete()
    except Exception:
        pass
    if dreamxbotz_user_response.text not in ("Yes", "No"):
        return await message.reply("❌ Invalid input. Broadcast cancelled.")

    is_pin = dreamxbotz_user_response.text == "Yes"
    # For inline text mode: send a temporary message and use it as the broadcast source,
    # then delete it after broadcast so the admin's chat stays clean.
    temp_b_msg = None
    if message.reply_to_message:
        b_msg = message.reply_to_message
    else:
        temp_b_msg = await bot.send_message(message.chat.id, inline_text)
        b_msg = temp_b_msg

    # Bug fix #3: get count separately, then stream from cursor — avoids loading all users into RAM
    total_users = await db.total_users_count()
    user_cursor = await db.get_all_users()

    dreamxbotz_status_msg = await message.reply_text("📤 <b>Broadcasting your message...</b>")
    success = blocked = deleted = failed = 0
    start_time = time.time()
    cancelled = False

    async def send(user):
        try:
            _, result = await users_broadcast(int(user["id"]), b_msg, is_pin)
            return result
        except Exception as e:
            logging.exception(f"Error sending broadcast to {user['id']}")
            return "Error"

    async with lock:
        # Bug fix #3+4: stream cursor in batches of 25 with 1s sleep (was: full list, 100/batch, 0.1s sleep)
        batch = []
        done = 0
        async for user in user_cursor:
            if temp.B_USERS_CANCEL:
                temp.B_USERS_CANCEL = False
                cancelled = True
                break
            batch.append(user)
            if len(batch) >= 25:
                results = await asyncio.gather(*[send(u) for u in batch])
                for res in results:
                    if res == "Success":
                        success += 1
                    elif res == "Blocked":
                        blocked += 1
                    elif res == "Deleted":
                        deleted += 1
                    elif res == "Error":
                        failed += 1
                done += len(batch)
                batch = []
                elapsed = get_readable_time(time.time() - start_time)
                try:
                    await dreamxbotz_status_msg.edit(
                        f"📣 <b>Broadcast Progress....:</b>\n\n"
                        f"👥 Total: <code>{total_users}</code>\n"
                        f"✅ Done: <code>{done}</code>\n"
                        f"📬 Success: <code>{success}</code>\n"
                        f"⛔ Blocked: <code>{blocked}</code>\n"
                        f"🗑️ Deleted: <code>{deleted}</code>\n"
                        f"⏱️ Time: {elapsed}",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("❌ CANCEL", callback_data="broadcast_cancel#users")]
                        ])
                    )
                except Exception:
                    pass
                # Bug fix #4: 1s sleep between batches to stay under Telegram rate limit (was 0.1s)
                await asyncio.sleep(1)

        # flush the last partial batch
        if batch and not cancelled:
            results = await asyncio.gather(*[send(u) for u in batch])
            for res in results:
                if res == "Success":
                    success += 1
                elif res == "Blocked":
                    blocked += 1
                elif res == "Deleted":
                    deleted += 1
                elif res == "Error":
                    failed += 1
            done += len(batch)

    elapsed = get_readable_time(time.time() - start_time)
    final_status = (
        f"{'❌ <b>Broadcast Cancelled.</b>' if cancelled else '✅ <b>Broadcast Completed.</b>'}\n\n"
        f"🕒 Time: {elapsed}\n"
        f"👥 Total: <code>{total_users}</code>\n"
        f"📬 Success: <code>{success}</code>\n"
        f"⛔ Blocked: <code>{blocked}</code>\n"
        f"🗑️ Deleted: <code>{deleted}</code>\n"
        f"❌ Failed: <code>{failed}</code>"
    )
    await dreamxbotz_status_msg.edit(final_status)
    # Clean up the temporary source message from admin's chat (inline text mode only)
    if temp_b_msg:
        try:
            await temp_b_msg.delete()
        except Exception:
            pass


@Client.on_message(filters.command("grp_broadcast") & filters.user(ADMINS))
async def broadcast_group(bot, message):
    raw_text = message.text or message.caption or ""
    parts = raw_text.split(None, 1)
    inline_text = parts[1].strip() if len(parts) > 1 else ""
    if not message.reply_to_message and not inline_text:
        return await message.reply(
            "❌ <b>How to use /grp_broadcast:</b>\n\n"
            "<b>For text:</b> /grp_broadcast Your announcement here\n"
            "<b>For video/photo:</b>\n"
            "1. Send your video/photo to this chat\n"
            "2. Long-press it → Reply\n"
            "3. Type /grp_broadcast and send"
        )
    if lock.locked():
        return await message.reply("⚠️ Another broadcast is in progress. Please wait...")
    ask = await message.reply(
        "<b>Do you want to pin this message in groups?</b>",
        reply_markup=ReplyKeyboardMarkup([["Yes", "No"]], one_time_keyboard=True, resize_keyboard=True)
    )
    try:
        dreamxbotz_user_response = await bot.listen(chat_id=message.chat.id, user_id=message.from_user.id, timeout=60)
    except asyncio.TimeoutError:
        await ask.delete()
        return await message.reply("❌ Timed out. Broadcast cancelled.")
    await ask.delete()
    try:
        await dreamxbotz_user_response.delete()
    except Exception:
        pass
    if dreamxbotz_user_response.text not in ("Yes", "No"):
        return await message.reply("❌ Invalid input. Broadcast cancelled.")

    is_pin = dreamxbotz_user_response.text == "Yes"
    temp_b_msg = None
    if message.reply_to_message:
        b_msg = message.reply_to_message
    else:
        temp_b_msg = await bot.send_message(message.chat.id, inline_text)
        b_msg = temp_b_msg
    chats = await db.get_all_chats()
    total_chats = await db.total_chat_count()
    dreamxbotz_status_msg = await message.reply_text("📤 <b>Broadcasting your message to groups...</b>")
    start_time = time.time()
    done = success = failed = 0
    cancelled = False

    async with lock:
        async for chat in chats:
            time_taken = get_readable_time(time.time() - start_time)
            if temp.B_GROUPS_CANCEL:
                temp.B_GROUPS_CANCEL = False
                cancelled = True
                break
            try:
                sts = await groups_broadcast(int(chat['id']), b_msg, is_pin)
            except Exception as e:
                logging.exception(f"Error broadcasting to group {chat['id']}")
                sts = 'Error'
            if sts == "Success":
                success += 1
            else:
                failed += 1
            done += 1
            if done % 10 == 0:
                btn = [[InlineKeyboardButton("❌ CANCEL", callback_data="broadcast_cancel#groups")]]
                try:
                    await dreamxbotz_status_msg.edit(
                        f"📣 <b>Group broadcast progress:</b>\n\n"
                        f"👥 Total Groups: <code>{total_chats}</code>\n"
                        f"✅ Completed: <code>{done} / {total_chats}</code>\n"
                        f"📬 Success: <code>{success}</code>\n"
                        f"❌ Failed: <code>{failed}</code>",
                        reply_markup=InlineKeyboardMarkup(btn)
                    )
                except Exception:
                    pass
    time_taken = get_readable_time(time.time() - start_time)
    dreamxbotz_text = (
        f"{'❌ <b>Groups broadcast cancelled!</b>' if cancelled else '✅ <b>Group broadcast completed.</b>'}\n"
        f"⏱️ Completed in {time_taken}\n\n"
        f"👥 Total Groups: <code>{total_chats}</code>\n"
        f"✅ Completed: <code>{done} / {total_chats}</code>\n"
        f"📬 Success: <code>{success}</code>\n"
        f"❌ Failed: <code>{failed}</code>"
    )
    if temp_b_msg:
        try:
            await temp_b_msg.delete()
        except Exception:
            pass
    try:
        await dreamxbotz_status_msg.edit(dreamxbotz_text)
    except MessageTooLong:
        with open("reason.txt", "w+") as outfile:
            outfile.write(str(failed))
        await message.reply_document(
            "reason.txt", caption=dreamxbotz_text
        )
        os.remove("reason.txt")

@Client.on_message(filters.command("clear_junk") & filters.user(ADMINS))
async def remove_junkuser__db(bot, message):
    users = await db.get_all_users()
    b_msg = message
    sts = await message.reply_text('ɪɴ ᴘʀᴏɢʀᴇss.... ᴘʟᴇᴀsᴇ ᴡᴀɪᴛ')
    start_time = time.time()
    total_users = await db.total_users_count()
    blocked = 0
    deleted = 0
    failed = 0
    done = 0
    async for user in users:
        pti, sh = await clear_junk(int(user['id']), b_msg)
        if pti == False:
            if sh == "Blocked":
                blocked += 1
            elif sh == "Deleted":
                deleted += 1
            elif sh == "Error":
                failed += 1
        done += 1
        if not done % 50:
            await sts.edit(f"In Progress:\n\nTotal Users {total_users}\nCompleted: {done} / {total_users}\nBlocked: {blocked}\nDeleted: {deleted}")
    time_taken = datetime.timedelta(seconds=int(time.time() - start_time))
    await sts.delete()
    await bot.send_message(message.chat.id, f"Completed:\nCompleted in {time_taken} seconds.\n\nTotal Users {total_users}\nCompleted: {done} / {total_users}\nBlocked: {blocked}\nDeleted: {deleted}")

@Client.on_message(filters.command(["junk_group", "clear_junk_group"]) & filters.user(ADMINS))
async def junk_clear_group(bot, message):
    groups = await db.get_all_chats()
    if not groups:
        grp = await message.reply_text("❌ Nᴏ ɢʀᴏᴜᴘs ғᴏᴜɴᴅ ғᴏʀ ᴄʟᴇᴀʀ Jᴜɴᴋ ɢʀᴏᴜᴘs.")
        await asyncio.sleep(60)
        await grp.delete()
        return
    b_msg = message
    sts = await message.reply_text(text='..............')
    start_time = time.time()
    total_groups = await db.total_chat_count()
    done = 0
    failed = ""
    deleted = 0
    async for group in groups:
        pti, sh, ex = await junk_group(int(group['id']), b_msg)
        if pti == False:
            if sh == "deleted":
                deleted += 1
                failed += ex
                try:
                    await bot.leave_chat(int(group['id']))
                except Exception as e:
                    print(f"{e} > {group['id']}")
        done += 1
        if not done % 50:
            await sts.edit(f"in progress:\n\nTotal Groups {total_groups}\nCompleted: {done} / {total_groups}\nDeleted: {deleted}")
    time_taken = datetime.timedelta(seconds=int(time.time() - start_time))
    await sts.delete()
    try:
        await bot.send_message(message.chat.id, f"Completed:\nCompleted in {time_taken} seconds.\n\nTotal Groups {total_groups}\nCompleted: {done} / {total_groups}\nDeleted: {deleted}\n\nFiled Reson:- {failed}")
    except MessageTooLong:
        with open('junk.txt', 'w+') as outfile:
            outfile.write(failed)
        await message.reply_document('junk.txt', caption=f"Completed:\nCompleted in {time_taken} seconds.\n\nTotal Groups {total_groups}\nCompleted: {done} / {total_groups}\nDeleted: {deleted}")
        os.remove("junk.txt")
