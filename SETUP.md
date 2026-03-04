# Discord Verification Bot — Setup Guide

A Discord bot that lets students type `/verify <student_id>` and instantly:
- Gets renamed to their name from your Google Sheet
- Receives the **Verified** role
- Sees a confirmation embed

---

## Step 1 — Create the Discord Bot

1. Go to https://discord.com/developers/applications → **New Application**
2. Name it (e.g. "Verify Bot") → **Create**
3. Go to **Bot** tab → **Add Bot** → confirm
4. Under **Token** click **Reset Token** and copy it → this is your `DISCORD_TOKEN`
5. Scroll down, enable **Server Members Intent** (toggle ON) → Save
6. Go to **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Manage Nicknames`, `Manage Roles`, `Send Messages`
7. Copy the generated URL, open it in browser, invite bot to your server

---

## Step 2 — Set Up Google Sheets API

1. Go to https://console.cloud.google.com → create a new project
2. Enable **Google Sheets API** and **Google Drive API**
3. Go to **IAM & Admin → Service Accounts** → **Create Service Account**
   - Give it any name → **Create and Continue** → **Done**
4. Click the service account → **Keys** tab → **Add Key → JSON** → download the file
5. Open the JSON file — you'll paste its entire contents (on one line) as `GOOGLE_CREDS_JSON`
6. In your Google Sheet:
   - Copy the Sheet's URL ID (the long string between `/d/` and `/edit`) → `SPREADSHEET_ID`
   - Click **Share** → paste the service account email (looks like `name@project.iam.gserviceaccount.com`) → give **Viewer** access

### Sheet format
| Column A (Name) | Column B (Student ID) | Column C (Discord Role ID) |
|-----------------|----------------------|---------------------------|
| Ahmed Khan      | 12345                | 123456789012345678         |
| Sara Malik      | 67890                | 987654321098765432         |

Row 1 can be a header row — the bot skips it automatically.

**How to get a Discord Role ID:**
1. In Discord → Server Settings → Roles
2. Right-click the role → **Copy Role ID**
   *(You need Developer Mode on: User Settings → Advanced → Developer Mode)*

---

## Step 3 — Create the Verified Role in Discord

1. In your Discord server → **Server Settings → Roles → Create Role**
2. Name it exactly `Verified` (or whatever you set `VERIFIED_ROLE_NAME` to)
3. Give it whatever channel permissions verified students should have
4. **Important:** drag the bot's role ABOVE the `Verified` role in the list — Discord requires this

---

## Step 4 — Deploy on Render (Free)

1. Push all files to a **GitHub repository** (make sure `.env` is in `.gitignore`)
2. Go to https://render.com → sign up free → **New → Web Service... → Background Worker**
   - Or just connect your repo and Render will detect `render.yaml` automatically
3. Set **Environment Variables** in the Render dashboard:

| Key | Value |
|-----|-------|
| `DISCORD_TOKEN` | your bot token |
| `GOOGLE_CREDS_JSON` | entire contents of the JSON key file (one line) |
| `SPREADSHEET_ID` | your sheet ID |
| `SHEET_NAME` | `Sheet1` (or your tab name) |
| `COL_STUDENT_ID` | `1` |
| `COL_NAME` | `2` |
| `VERIFIED_ROLE_NAME` | `Verified` |

4. Click **Deploy** — Render will install dependencies and start the bot
5. Check the logs — you should see `✅ Logged in as Verify Bot`

---

## How It Works

```
Student types:  /verify 12345
Bot checks:     Google Sheet → finds "Ahmed Khan"
Bot does:       Renames member to "Ahmed Khan"
                Gives "Verified" role
                Sends green embed confirmation (visible only to them)
```

Response is instant — no polling, no 15-minute delay.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Bot can't rename | Bot role must be higher than member's roles in server settings |
| Bot can't assign role | Bot role must be above the Verified role |
| Sheet not found | Check SPREADSHEET_ID and that you shared sheet with service account email |
| Slash command not showing | Wait up to 1 hour for Discord to propagate, or kick/re-invite bot |
| GOOGLE_CREDS_JSON error | Make sure you paste the entire JSON as a single line, no line breaks |
