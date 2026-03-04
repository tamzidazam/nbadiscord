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

# Sheet columns: A=Name, B=Student ID, C=Discord Role ID
COL_NAME       = 0   # Column A (0-indexed)
COL_STUDENT_ID = 1   # Column B
COL_ROLE_ID    = 2   # Column C
# ─────────────────────────────────────────────────────────────────────────────

# ── Dummy HTTP server so Render's Web Service doesn't kill the process ────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")
    def log_message(self, format, *args):
        pass  # silence access logs

def run_web_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

# Start web server in background thread
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
    for row in rows[1:]:   # skip header
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

bot = commands.Bot(intents=intents)


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")


@bot.slash_command(name="verify", description="Verify yourself with your student ID")
async def verify(ctx: discord.ApplicationContext, student_id: str):
    await ctx.defer(ephemeral=True)

    guild  = ctx.guild
    member = ctx.author

    # 1. Lookup in Google Sheet
    try:
        result = lookup_student(student_id)
    except Exception as e:
        await ctx.followup.send("⚠️ Could not reach the student database. Please try again later.", ephemeral=True)
        print(f"[Sheet error] {e}")
        return

    if result is None:
        await ctx.followup.send(
            f"❌ Student ID **{student_id}** was not found.\nPlease double-check your ID or contact an admin.",
            ephemeral=True,
        )
        return

    name, role_id = result

    # 2. Rename member
    try:
        await member.edit(nick=name)
    except discord.Forbidden:
        pass  # can't rename server owner

    # 3. Assign role from sheet
    role = guild.get_role(int(role_id)) if role_id.isdigit() else None
    if role is None:
        await ctx.followup.send(
            f"⚠️ Role ID `{role_id}` not found on this server. Please contact an admin.",
            ephemeral=True,
        )
        return

    try:
        await member.add_roles(role)
    except discord.Forbidden:
        await ctx.followup.send("⚠️ I don't have permission to assign roles. Please contact an admin.", ephemeral=True)
        return

    # 4. Success embed
    embed = discord.Embed(
        title="✅ Verification Successful!",
        description=(
            f"Welcome, **{name}**!\n\n"
            f"• Your nickname has been set to **{name}**\n"
            f"• You've been given the **{role.name}** role\n\n"
            "You now have access to all student channels. Enjoy! 🎉"
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"Verified with Student ID: {student_id}")
    await ctx.followup.send(embed=embed, ephemeral=True)
    print(f"[Verified] {member} → {name} | Role: {role.name} (ID: {student_id})")


bot.run(DISCORD_TOKEN)
