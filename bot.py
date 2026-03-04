import discord
from discord.ext import commands
import gspread
from google.oauth2.service_account import Credentials
import os
import json
from dotenv import load_dotenv
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

load_dotenv()

# ── Config ───────────────────────────────────────────────────────────────────
DISCORD_TOKEN     = os.getenv("DISCORD_TOKEN")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")
SPREADSHEET_ID    = os.getenv("SPREADSHEET_ID")
SHEET_NAME        = os.getenv("SHEET_NAME", "Sheet1")

VERIFY_CHANNEL_ID = 1478274143714672840  # only listen in this channel

# Sheet columns: A=Name, B=Student ID, C=Discord Role ID
COL_NAME       = 0
COL_STUDENT_ID = 1
COL_ROLE_ID    = 2
# ─────────────────────────────────────────────────────────────────────────────

# ── Dummy HTTP server for Render free tier ────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")
    def log_message(self, format, *args):
        pass

def run_web_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

Thread(target=run_web_server, daemon=True).start()
# ─────────────────────────────────────────────────────────────────────────────

def get_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)


def lookup_student(student_id: str):
    """Return (name, role_id) if found, else None."""
    sheet = get_sheet()
    rows = sheet.get_all_values()
    for row in rows[1:]:
        if len(row) >= 3:
            name    = row[COL_NAME].strip()
            sid     = row[COL_STUDENT_ID].strip()
            role_id = row[COL_ROLE_ID].strip()
            if sid.lower() == student_id.strip().lower():
                return name, role_id
    return None


# ── Bot ───────────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True
intents.message_content = True  # needed to read message content

bot = commands.Bot(intents=intents)


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")


@bot.event
async def on_message(message: discord.Message):
    # Ignore bots and messages outside the verify channel
    if message.author.bot:
        return
    if message.channel.id != VERIFY_CHANNEL_ID:
        return

    # Only process if message is purely an integer
    content = message.content.strip()
    if not content.isdigit():
        await message.delete()
        return

    student_id = content
    member = message.author
    guild = message.guild

    # Delete the message so ID isn't visible in chat
    try:
        await message.delete()
    except discord.Forbidden:
        pass

    # 1. Lookup in Google Sheet
    try:
        result = lookup_student(student_id)
    except Exception as e:
        await message.channel.send(
            f"{member.mention} ⚠️ Could not reach the student database. Please try again later.",
            delete_after=8,
        )
        print(f"[Sheet error] {e}")
        return

    if result is None:
        await message.channel.send(
            f"{member.mention} ❌ Student ID **{student_id}** was not found. "
            "Please double-check your ID or contact an admin.",
            delete_after=8,
        )
        return

    name, role_id = result

    # 2. Rename member → "Name - ID"
    new_nick = f"{name} - {student_id}"
    try:
        await member.edit(nick=new_nick)
    except discord.Forbidden:
        pass  # can't rename server owner

    # 3. Assign role from sheet
    role = guild.get_role(int(role_id)) if role_id.isdigit() else None
    if role is None:
        await message.channel.send(
            f"{member.mention} ⚠️ Role ID `{role_id}` not found. Please contact an admin.",
            delete_after=8,
        )
        return

    try:
        await member.add_roles(role)
    except discord.Forbidden:
        await message.channel.send(
            f"{member.mention} ⚠️ I don't have permission to assign roles. Please contact an admin.",
            delete_after=8,
        )
        return

    # 4. Success embed (auto-deletes after 10 seconds)
    embed = discord.Embed(
        title="✅ Verification Successful!",
        description=(
            f"Welcome, **{name}**!\n\n"
            f"• Your nickname has been set to **{new_nick}**\n"
            f"• You've been given the **{role.name}** role\n\n"
            "You now have access to all student channels. Enjoy! 🎉"
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"Verified with Student ID: {student_id}")
    await message.channel.send(embed=embed, delete_after=10)
    print(f"[Verified] {member} → {new_nick} | Role: {role.name}")


bot.run(DISCORD_TOKEN)
