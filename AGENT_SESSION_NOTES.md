
# Agent Session Notes & Roadmap (ADV-auto-filter-bot)

## Recent Upgrades (As of this commit)
1. **Speed & RAM Optimization**: Replaced `fuzzywuzzy` with the much faster `rapidfuzz` in `pmfilter.py`. Removed massive, unnecessary libraries (`numpy`, `opencv-python-headless`, `ffmpeg-python`) from `requirements.txt` to significantly reduce server RAM usage and deployment time.
2. **Maintenance Mode**: Ported `maintenance.py` from a reference repository. Admins can now type `/maintenance` to toggle a shield that blocks normal users from querying the database during updates, preventing database corruption.
3. **Admin Command List**: Added `/admincommandlist` to give the bot owner a clean, categorized text list of all admin commands.
4. **Safety Mechanisms**: Hard-disabled the dangerous `/deleteall` and `/deletefiles` commands deep inside the core (`commands.py`), and removed them from the bot's visual menu to prevent accidental database wipes.

## Future Roadmap & Recommended Features
The following features are highly recommended to turn this into a top-tier, viral bot:

### 1. The "Referral for Premium" System
- **Concept**: Users bypass ads/shorteners for 7 days if they invite 3 friends via a `/refer` link.
- **Why**: Turns users into a marketing team, causing viral growth.

### 2. Automated "Request" System
- **Concept**: If a movie isn't found, the bot offers a "Request Movie" button. Clicking it forwards the request to an admin-only group. Once uploaded, the bot PMs the user that it's ready.
- **Why**: Keeps users engaged even when content is missing.

### 3. Language & Quality Inline Filters
- **Concept**: Instead of dumping 50 files for "Spider-Man", the bot sends one message with interactive buttons (e.g., `[ 🔊 English ]`, `[ 💿 1080p ]`).
- **Why**: Drastically improves UX and chat cleanliness.

### 4. The "Hydra" Strategy & Private Vaults (Anti-Ban)
- **Concept**: Host movies in a Private Channel ("Vault") rather than a public one. The bot fetches files from the Vault and sends them to users in PM. Auto-delete the files after 15 minutes to prevent copyright sweeps.

## Notes for Future Agents
- **Environment**: This bot runs on an Oracle Cloud Always-Free VM (1GB RAM). **DO NOT** install heavy AI/rendering libraries or file-extraction scripts that load large files into memory. 
- **Database**: We use MongoDB via `motor.motor_asyncio`. Ensure any new features use asynchronous database calls to avoid blocking the Pyrogram event loop.
- **Service Restart**: After applying code changes on the VM, ALWAYS restart the bot and check its status:
  `sudo systemctl restart adv_filter_bot.service && sleep 2 && sudo systemctl status adv_filter_bot.service --no-pager`
