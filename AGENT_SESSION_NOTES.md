# 🤖 Auto Filter Bot — Agent Session Notes
> **Repo:** `taqdeuuu/data-service` | **Platform:** Railway (auto-deploy on push to `main`) | **Updated:** 2026-07-06

---

## 📋 Bot Overview

A private Telegram **auto-filter movie bot** written in Python (pyrofork imported as `pyrogram`).  
Users search in a group → bot finds files in its database → sends them to user PM with a stream/download button.

| Stack | Detail |
|---|---|
| Language | Python 3 |
| Telegram lib | pyrofork (imported as `pyrogram`) |
| Database | MongoDB (Motor async driver) |
| File streaming | Custom aiohttp stream server (Railway) |
| Deploy | Railway — auto-deploys on every push to `main` |
| Bot name | Auto filter ADV (~165 monthly users) |

---

## 🏗 Project Structure

```
taqdeuuu/data-service/
├── bot.py                        # Entry point, client startup, loop restart logic
├── info.py                       # ALL config / env vars (single source of truth)
├── Script.py                     # All message text templates & scripts
├── utils.py                      # Shortlink helpers, get_shortlink(), file utils
├── plugins/
│   ├── commands.py               # /start handler, file delivery, verification flow
│   ├── pmfilter.py               # ALL callback query handlers (buttons)
│   ├── admin_features.py         # Admin-only commands
│   ├── broadcast.py              # Broadcast to all users/chats
│   ├── Premium.py                # Premium subscription logic
│   ├── join_req.py               # Join request approval
│   ├── misc.py                   # /id, /info commands
│   ├── route.py                  # aiohttp web routes (stream + static file server)
│   └── report.py / request.py / speed.py / stats.py / index.py / channel.py
├── database/
│   ├── users_chats_db.py         # User/chat/settings/verification/connection DB
│   └── ia_filterdb.py            # Media file database (search + store)
└── dreamxbotz/
    ├── Bot/__init__.py            # Multi-client setup
    ├── server/                    # Custom streaming server
    └── util/                     # file_properties (get_name, get_hash), etc.
```

---

## ⚙️ Key Environment Variables (info.py)

| Variable | Default | Purpose |
|---|---|---|
| `API_ID`, `API_HASH`, `BOT_TOKEN` | — | Telegram credentials (must be set) |
| `DATABASE_URI` | — | MongoDB connection string |
| `BIN_CHANNEL` | -100 ⚠️ | Channel where files are forwarded to generate stream URLs |
| `LOG_CHANNEL` | -100 ⚠️ | Channel for bot logs/tracebacks |
| `STREAM_MODE` | False | Enable fast stream download button |
| `PREMIUM_STREAM_MODE` | False | Restrict stream to premium users only |
| `URL` | — | Base URL of Railway stream server (e.g. `https://yourapp.railway.app/`) |
| `DELETE_TIME` | 300 (min 30) | Seconds before sent files auto-delete |
| `IS_VERIFY` | False | Enable shortlink verification gate |
| `TWO_VERIFY_GAP` | 1200 | Seconds before a second shortlink verify is required |
| `THREE_VERIFY_GAP` | — | Seconds before a third shortlink verify is required |
| `TUTORIAL` | `https://t.me/jrzitxtx/56` | URL for "HOW TO VERIFY" button |
| `TUTORIAL_2` | `https://t.me/BackupChannel5211` | Second-step verify tutorial |
| `TUTORIAL_3` | `https://t.me/Moviebot123` | Third-step verify tutorial |
| `SHORTENER_API` | `e1edb482...` ⚠️ | Default shortener API key (replace in production) |
| `UPDATE_CHNL_LNK` | — | URL for "Join Updates Channel" button |
| `ADMINS` / `SUDO_USERS` | — | Comma-separated Telegram user IDs |
| `OWNER_ID` | — | Bot owner Telegram ID |

⚠️ = **must be set properly in Railway env vars, default is wrong/insecure**

---

## 🔄 Download Flow (Current State — Post Session 9)

### STREAM_MODE = True (main path)

```
User searches in group
  → Bot finds file → sends PM with file + "🚀 FAST DOWNLOAD 🚀" button
      ↓
  Button is already a URL button (pre-generated stream URL)
      ↓ USER TAPS (Click 1)
  Telegram "Open Link" dialog
      ↓ USER TAPS OPEN (Click 2)
  Stream starts in browser / download begins
```

**Total: 2 clicks** — minimum possible (Telegram's Open Link dialog cannot be bypassed for external URLs).

### How pre-generation works (`plugins/commands.py`)

```python
async def _pregenerate_stream_url(client, file_id, user_id, username):
    log_msg = await client.send_cached_media(chat_id=BIN_CHANNEL, file_id=file_id)
    fileName = quote_plus(get_name(log_msg))
    url = f"{URL}{str(log_msg.id)}/{fileName}?hash={get_hash(log_msg)}"
    return url  # or None on failure
```

Called before `send_cached_media` to user. Falls back to `generate_stream_link` callback if it fails.

### Fallback: `generate_stream_link` callback (`plugins/pmfilter.py`)

Used when pre-generation fails. Handler:
1. `await query.answer(MSG_ALRT)` — **first** (clears spinner immediately)
2. `send_cached_media` to BIN_CHANNEL
3. Build URL, log to BIN_CHANNEL
4. `edit_message_reply_markup` → replace button with URL button
5. `await asyncio.sleep(DELETE_TIME)` — **outside try**
6. `await dreamcinezone.delete()` — **inside try/except** (safe if manually deleted)

---

## 🔐 Verification System (Current State — Post Session 9)

### How it works

The bot uses a 3-step shortlink verification system gated by `IS_VERIFY`:

```
Request movie
  → bot checks is_user_verified() / use_second_shortener() / use_third_shortener()
  → if verification needed: sends a shortened link the user must open
  → user opens link, completes ad, is redirected to bot deep link
  → /start notcopy_{user_id}_{verify_id}_{file_id} is hit
  → bot marks verify_id as used, delivers file, then resets timestamps
```

### Key DB functions (`database/users_chats_db.py`)

| Function | Purpose |
|---|---|
| `is_user_verified(user_id)` | Returns True if `last_verified` > today's midnight IST |
| `use_second_shortener(user_id, gap)` | True if gap seconds have passed since `last_verified` |
| `use_third_shortener(user_id, gap)` | True if gap seconds have passed since `second_time_verified` |
| `user_verified(user_id)` | Returns True if `second_time_verified` > today's midnight IST |
| `update_notcopy_user(user_id, {key: value})` | MongoDB `$set` on user's verify fields |

Verification fields stored per user:
- `last_verified` — timestamp of 1st verify
- `second_time_verified` — timestamp of 2nd verify
- `third_time_verified` — timestamp of 3rd verify

### ✅ SESSION 9 CHANGE — Per-Request Verification (confirmed working)

**User request:** Every movie request must require a fresh verification — no 20-minute free window.

**What was changed:** In `plugins/commands.py`, inside the `notcopy`/`sendall` handler, verification timestamps are reset to old sentinel dates **immediately AFTER `_start_handler` delivers the file**.

```python
# Deliver file first
prefix = "allfiles" if message.command[1].startswith('sendall') else "file"
m.command = ["start", f"{prefix}_{grp_id}_{file_id}"]
await _start_handler(client, m)
# Reset AFTER delivery — next request will always require a fresh verify
await db.update_notcopy_user(user_id, {
    "last_verified":        ist_timezone.localize(datetime(2020, 5, 17, 0, 0, 0)),
    "second_time_verified": ist_timezone.localize(datetime(2019, 5, 17, 0, 0, 0)),
    "third_time_verified":  ist_timezone.localize(datetime(2018, 5, 17, 0, 0, 0)),
})
return
```

### ⚠️ CRITICAL ORDERING RULE — DO NOT CHANGE

**The reset MUST come AFTER `_start_handler`, never before.**

`_start_handler` re-runs the full verification check (`is_user_verified`, `use_second_shortener`, `use_third_shortener`) at the top of its own logic. If you reset timestamps before calling it:
- It sees the user as unverified
- Sends another verify link instead of the file
- User completes verify 1 → gets verify link again → completes verify 2 → gets verify link again → never receives the movie

This was a confirmed live bug during session 9 — do not repeat it.

**Why sentinel dates work:** `is_user_verified()` checks `total_seconds <= seconds_since_midnight`. The 2020/2019/2018 dates give ~6 years of seconds, always greater than today's elapsed seconds → always returns False.

**Why `ist_timezone.localize(datetime(...))` not `datetime(..., tzinfo=ist_timezone)`:** The latter is a known pytz anti-pattern that can produce wrong UTC offsets for historical timestamps. `ist_timezone` is already defined earlier in the handler block — no need to redefine it.

---

## 🐛 Bug Fixes Applied (All Sessions)

### Session 7 — 2026-07-05 (Download Click Reduction)
- **commands.py**: Added `_pregenerate_stream_url()` + imports (`quote_plus`, `get_name`, `get_hash`)
- **pmfilter.py**: Fixed `generate_stream_link` — `query.answer()` moved before I/O; restored edit+delete flow

### Session 8 — 2026-07-05 (Deep Audit + Security)
| File | Fix | Severity |
|---|---|---|
| `plugins/route.py` | Path traversal in `/static/{filename}`: replaced naive `..`/`/` check with `os.path.realpath()` jail + `IsADirectoryError` guard | CRITICAL |
| `database/users_chats_db.py` | `add_user()`: `insert_one` → `update_one($setOnInsert, upsert=True)` to prevent `DuplicateKeyError` | HIGH |
| `database/users_chats_db.py` | `add_chat()`: same upsert fix | HIGH |
| `database/users_chats_db.py` | `connect_group()`: race condition (read-then-write) → `update_one($addToSet, upsert=True)` | MEDIUM |
| `info.py` | `DELETE_TIME` enforces `max(30, ...)` + `try/except ValueError` so bad env value cannot crash startup | MEDIUM |
| `plugins/pmfilter.py` | Two `asyncio.sleep(DELETE_TIME) + .delete()` blocks: sleep moved outside try; only `.delete()` guarded | MEDIUM |

### Session 9 — 2026-07-06 (Per-Request Verification)
| File | Change |
|---|---|
| `plugins/commands.py` | Reset `last_verified`, `second_time_verified`, `third_time_verified` to sentinel dates POST-SEND in notcopy/sendall handler. Uses `ist_timezone.localize()` (correct pytz API). Confirmed working in production. |

---

## 🔐 Security Audit Findings

### Fixed ✅
1. **Path traversal** in `/static/{filename}` — uses `realpath` jail now
2. **Race conditions** in user/chat/connection DB — atomic upserts
3. **DELETE_TIME crash** on bad env value — guarded
4. **Spinner timeout risk** in callbacks — `query.answer()` always called first

### Remaining (not code-fixable — operational/config) ⚠️
| Issue | Severity | Action Needed |
|---|---|---|
| `SHORTENER_API` has a hardcoded default key (`e1edb482...`) | HIGH | Set your own key in Railway env vars |
| `BIN_CHANNEL` / `LOG_CHANNEL` default to `-100` | HIGH | Must be set to real channel IDs |
| `ADMINS` list mixes int/string IDs if `_safe_int_env` fails | MEDIUM | Validate Railway env vars contain only integers |
| Tracebacks sent to `LOG_CHANNEL` may expose env/stack details | MEDIUM | Ensure `LOG_CHANNEL` is private, admin-only |
| Broadcast system can pin to all users if admin account is compromised | MEDIUM | Use 2FA on all admin Telegram accounts |
| `$natural` sort in `ia_filterdb.py` bypasses indexes (slow at scale) | LOW | Add indexes on `file_name`, `file_id` fields |
| MongoDB: no unique indexes confirmed on `users.id`, `chats.id` | LOW | Run `db.users.createIndex({id:1},{unique:true})` |

### NOT backdoors (verified safe)
- `query.answer(url=f"https://t.me/...")` calls — these are t.me deep links (allowed by Telegram API)
- `eval()`/`exec()` — not found in any handler
- Admin checks in `admin_features.py` — properly gate on `ADMINS` filter from Pyrogram

---

## 📌 Critical Rules for Future Agents

1. **NEVER push to GitHub in parallel** — Railway auto-deploys every push; parallel pushes cause SHA conflicts. Push files one at a time, fetch fresh SHA each time.
2. **NEVER edit Railway directly** — all changes go through GitHub → Railway auto-deploys
3. **Always fetch fresh SHA** before pushing any file (prior SHA from a previous turn is stale)
4. **PAT is in Replit secret** `GITHUB_PERSONAL_ACCESS_TOKEN` — access via `$GITHUB_PERSONAL_ACCESS_TOKEN` in shell
5. **Repo is private** — always use the PAT in `Authorization: token $PAT` header
6. **`query.answer(url=external_url)` DOES NOT WORK** — Telegram only allows t.me links or game URLs; use pre-generated URL buttons instead
7. **Minimum 2 clicks** for external stream URLs — Telegram's "Open Link" dialog cannot be bypassed
8. **`from datetime import datetime`** is the import style in this codebase — use `datetime(...)` not `datetime.datetime(...)`
9. **Use `ist_timezone.localize(datetime(...))`** not `datetime(..., tzinfo=ist_timezone)` — the latter is a pytz anti-pattern
10. **`_start_handler` re-checks verification internally** — any verification reset in the notcopy handler MUST come AFTER `await _start_handler(client, m)`, never before. Resetting before it runs causes it to send another verify link instead of delivering the file (confirmed live bug).

---

## 🗂 File SHA Reference (as of 2026-07-06 Session 10)

> Always fetch fresh SHAs — these go stale immediately after any push.
> Shell command: `curl -s -H "Authorization: token $PAT" "https://api.github.com/repos/taqdeuuu/data-service/contents/<path>" | node -e "const j=JSON.parse(require('fs').readFileSync('/dev/stdin','utf8')); console.log(j.sha);"`

```
plugins/admin_features.py  f89bb3679cdad7f33513fd03d9054bf4d99f5769  ← changed session 10
plugins/commands.py        e711aabfb3d250ea5c674e068b69ee53e1c7b83c  ← changed session 9
plugins/route.py           (fetch fresh)
database/users_chats_db.py (fetch fresh)
info.py                    (fetch fresh)
plugins/pmfilter.py        (fetch fresh)
```

---

## 📝 Session 10 — 2026-07-06

### What Was Done
- Added `/enableverify` and `/disableverify` admin-only commands to `plugins/admin_features.py`
- Single handler using `filters.command(["enableverify", "disableverify"]) & filters.user(ADMINS)`
- Works in **groups** (toggles for the current group) and **PM** (requires group ID argument: `/enableverify -100123456789`)
- Reads current state first — sends "already enabled/disabled" if no change needed (no-op guard)
- Calls `save_group_settings(grp_id, "is_verify", True/False)` — same DB write used by existing settings panel
- Updated `admin_features.py` import: added `save_group_settings, get_settings` to `from utils import ...`
- Logs every toggle to `LOG_CHANNEL` with `#VERIFY_TOGGLE` tag

### Files Changed
| File | Change |
|---|---|
| `plugins/admin_features.py` | Import updated + `/enableverify`/`/disableverify` commands appended |

### Commands Usage
```
/enableverify              — in a group, enables verify for that group
/disableverify             — in a group, disables verify for that group
/enableverify -100123456   — in PM, enables verify for the given group ID
/disableverify -100123456  — in PM, disables verify for the given group ID
```
Only users in the `ADMINS` list can use these commands.

---

## 🚀 What To Do Next (Suggested)

1. **Set proper Railway env vars**: `BIN_CHANNEL`, `LOG_CHANNEL`, `SHORTENER_API` (your own key)
2. **Add MongoDB indexes**: `db.users.createIndex({id:1},{unique:true})`, `db.chats.createIndex({id:1},{unique:true})`
3. **Complete deep dry run** — bugs found in previous session but not yet fixed: `from_user` None guard in `pmfilter.py` lines 64/69 (outside guard), `message.from_user.mention` in support-chat branch line 81, `movie_list.remove(movie)` missing `try/except ValueError` line 2080
4. **Optional**: Add per-user rate limiting on `generate_stream_link` callback to prevent BIN_CHANNEL spam
5. **Optional**: Move from `$natural` to indexed sort in `ia_filterdb.py` for better search performance at scale
