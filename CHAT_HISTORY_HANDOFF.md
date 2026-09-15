# ADV-auto-filter-bot - Complete Chat History & Agent Handoff

**Repository**: https://github.com/azizthekiller2/ADV-auto-filter-bot  
**Branch**: `main`  
**Server Instance**: Telegram heavy bot (`ubuntu@144.24.154.135` - OCI Ubuntu 22.04 LTS)  
**Systemd Service**: `adv_filter_bot.service`  
**Companion Service**: `health_bot.service` (`/home/ubuntu/HealthCheckBot`)  
**Reverse Proxy**: Nginx on port 80 forwarding to internal port 8080 (`NO_PORT=True`)  
**Domain & SSL**: `rtcord.dpdns.org` (Cloudflare DNS Proxy + SSL)  
**Owner / GitHub**: azizthekiller2 (abdulazizshaik521@gmail.com)

---

## 1. Context for Future Agents

This repository hosts **ADV-auto-filter-bot** (Auto filter ADV, @Moviebot123), running on an Oracle Cloud Infrastructure (OCI) Ubuntu compute instance.

---

## 2. Chronological Log of All Updates & Changes in this Chat

### A. Verification & Link Shortener Bypass
- **Background**: The bot previously forced users through link shorteners / verification steps before sending the download file.
- **Action Taken**:
  - Disabled verification by default across `plugins/channel.py` and `plugins/pm_filter.py`.
  - Added support for admin command `/disableverify <group_id>` or in PM.
  - Resolved indentation and KeyError issues in `verify_status` database lookup.
  - Commits pushed: `62b6b03`, `abdc594`, `c63df44`.

### B. Streaming URL & Cloudflare Port 8080 Timeout Fix
- **Background**: Streaming links were being generated with `:8080` (`https://rtcord.dpdns.org:8080/watch/...`). Because Cloudflare DNS proxying was active, port 8080 connections timed out or failed.
- **Action Taken**:
  - In `info.py`, updated URL formatting to honor `NO_PORT=True` (`https://rtcord.dpdns.org/watch/...` via standard HTTPS port 443/80).
  - Nginx reverse proxy on the server forwards port 80 to internal port 8080 with `proxy_buffering off` for high-throughput streaming.

### C. Telegram Message Caption Speed Tip Added
- **Background**: Users downloading directly via standard mobile browsers (Chrome) were capped at ~4-5 MB/s because single-thread browser downloads from Telegram DCs have throughput limitations.
- **Action Taken**:
  - Updated `CAPTION` in `Script.py` to include:
    ```html
    <b><a href="https://t.me/backupchannek">{file_name}</a></b>

    <b>❤️ ꜰɪʟᴇ ᴀᴅᴅᴇᴅ ʙʏ ᴀᴢɪᴢ ꜱᴇʀ ❤️</b>

    ⚡ <b>Speed Tip:</b> <i>Chrome downloads in 1 single thread (~4 MB/s). For maximum 15-25 MB/s speed, open download link in 1DM / ADM!</i>

    <b>⚜️ Powered By : <a href="https://t.me/backupchannek">[ Moviebot123 ]</a></b>
    ```

### D. Web Streaming Player UI Enhanced (`req.html`)
- **Action Taken**:
  - In `dreamxbotz/template/req.html`, added a 1-tap **⚡ 1DM / Fast DL** button.
  - Integrated an Android intent launcher (`fast_1dm_download`) that opens 1DM automatically with multi-threaded downloading (8-16 threads, reaching 15-25 MB/s).

### E. Downgrade / Clean-up Actions (VideoGrid Removal)
- **Background**: An automated prompt in this environment mistakenly triggered adding a React skeleton loading screen to `VideoGrid.tsx` in this web applet workspace.
- **Action Taken**:
  - Confirmed this was unrelated to the Telegram bot.
  - Completely reverted and deleted the skeleton loading screen and all associated code from `VideoGrid.tsx` and `App.tsx`.

---

## 3. Server Management Runbook for Future Agents

- **SSH into ADV Bot Server**:
  ```bash
  ssh -i key_server1.pem ubuntu@144.24.154.135
  ```
- **Check Bot Service Status**:
  ```bash
  sudo systemctl status adv_filter_bot --no-pager
  ```
- **View Live Logs**:
  ```bash
  journalctl -u adv_filter_bot -f
  ```
- **Restart Service**:
  ```bash
  sudo systemctl restart adv_filter_bot
  ```
- **Deploy Changes from Git**:
  ```bash
  cd /home/ubuntu/ADV-auto-filter-bot && git pull && sudo systemctl restart adv_filter_bot
  ```
