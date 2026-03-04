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
DISCORD_TOKEN        = os.getenv("DISCORD_TOKEN")
GOOGLE_CREDS_JSON    = os.getenv("GOOGLE_CREDS_JSON")
SPREADSHEET_ID       = os.getenv("SPREADSHEET_ID")
SHEET_NAME           = os.getenv("SHEET_NAME", "Sheet1")
VERIFY_CHANNEL_ID    = 1478274143714672840
ADMIN_LOG_CHANNEL_ID = int(os.getenv("ADMIN_LOG_CHANNEL_ID", "0"))

# Sheet columns: A=Name, B=Student ID, C=Discord Role ID
COL_NAME       = 0
COL_STUDENT_ID = 1
COL_ROLE_ID    = 2

# ── In-memory claimed ID tracker (survives until bot restarts) ────────────────
# Maps student_id → discord member id who claimed it
claimed_ids: dict[str, int] = {}
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


def get_all_verify_role_ids() -> set[int]:
    """Fetch all unique role IDs from the sheet."""
    role_ids = set()
    try:
        rows = get_sheet().get_all_values()
        for row in rows[1:]:
            if len(row) >= 3 and row[COL_ROLE_ID].strip().isdigit():
                role_ids.add(int(row[COL_ROLE_ID].strip()))
    except Exception:
        pass
    return role_ids


async def dm(member: discord.Member, **kwargs):
    """Send a DM to the member."""
    try:
        await member.send(**kwargs)
    except discord.Forbidden:
        ch = member.guild.get_channel(VERIFY_CHANNEL_ID)
        if ch:
            await ch.send(
                f"{member.mention} Please enable DMs from server members so I can send you verification results!",
                delete_after=10,
            )


async def log_to_admin(guild: discord.Guild, **kwargs):
    """Send a log message to the admin log channel if configured."""
    if ADMIN_LOG_CHANNEL_ID:
        ch = guild.get_channel(ADMIN_LOG_CHANNEL_ID)
        if ch:
            await ch.send(**kwargs)


# ── Bot ───────────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(intents=intents)


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

    # Pre-load already-verified members on startup by checking roles
    # This prevents the claimed_ids dict from being empty after a restart
    print("[Startup] Bot ready. Claimed IDs will be tracked from this session onward.")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.channel.id != VERIFY_CHANNEL_ID:
        return

    content = message.content.strip()

    # Delete anything that isn't a pure integer silently
    if not content.isdigit():
        try:
            await message.delete()
        except discord.Forbidden:
            pass
        return

    student_id = content.lower()
    member     = message.author
    guild      = message.guild

    # Delete the user's message so the ID isn't visible to anyone
    try:
        await message.delete()
    except discord.Forbidden:
        pass

    # ── Check 1: Has this Discord account already verified? ──────────────
    verify_role_ids = get_all_verify_role_ids()
    member_role_ids = {r.id for r in member.roles}
    if member_role_ids & verify_role_ids:
        await dm(member, embed=discord.Embed(
            title="⚠️ Already Verified",
            description="You have already been verified. Contact an admin if you need help.",
            color=discord.Color.yellow(),
        ))
        await log_to_admin(guild, embed=discord.Embed(
            title="🔁 Repeat Verification Attempt",
            description=f"{member.mention} (`{member}`) tried to verify again with ID `{student_id}`.",
            color=discord.Color.orange(),
        ))
        return

    # ── Check 2: Has this student ID already been claimed by someone else? ──
    if student_id in claimed_ids and claimed_ids[student_id] != member.id:
        claimer_id = claimed_ids[student_id]
        await dm(member, embed=discord.Embed(
            title="🚫 Student ID Already Used",
            description=(
                "This Student ID has already been used to verify another account.\n\n"
                "If you believe this is a mistake, please contact an admin."
            ),
            color=discord.Color.red(),
        ))
        await log_to_admin(guild, embed=discord.Embed(
            title="🚨 Duplicate ID Attempt",
            description=(
                f"{member.mention} (`{member}`) tried to use Student ID `{student_id}` "
                f"which was already claimed by <@{claimer_id}>."
            ),
            color=discord.Color.red(),
        ))
        return

    # ── Check 3: Look up student ID in Google Sheet ──────────────────────
    try:
        result = lookup_student(student_id)
    except Exception as e:
        await dm(member, embed=discord.Embed(
            title="⚠️ Database Error",
            description="Could not reach the student database. Please try again later.",
            color=discord.Color.red(),
        ))
        print(f"[Sheet error] {e}")
        return

    if result is None:
        await dm(member, embed=discord.Embed(
            title="❌ Student ID Not Found",
            description=f"Student ID **{student_id}** was not found.\nPlease double-check your ID or contact an admin.",
            color=discord.Color.red(),
        ))
        await log_to_admin(guild, embed=discord.Embed(
            title="❌ Failed Verification",
            description=f"{member.mention} (`{member}`) entered unknown ID `{student_id}`.",
            color=discord.Color.red(),
        ))
        return

    name, role_id = result

    # ── Assign nickname ──────────────────────────────────────────────────
    new_nick = f"{name} - {student_id}"
    try:
        await member.edit(nick=new_nick)
    except discord.Forbidden:
        pass

    # ── Assign role ──────────────────────────────────────────────────────
    role = guild.get_role(int(role_id)) if role_id.isdigit() else None
    if role is None:
        await dm(member, embed=discord.Embed(
            title="⚠️ Role Not Found",
            description=f"Role ID `{role_id}` not found on this server. Please contact an admin.",
            color=discord.Color.red(),
        ))
        return

    try:
        await member.add_roles(role)
    except discord.Forbidden:
        await dm(member, embed=discord.Embed(
            title="⚠️ Permission Error",
            description="I don't have permission to assign roles. Please contact an admin.",
            color=discord.Color.red(),
        ))
        return

    # ── Mark this student ID as claimed ──────────────────────────────────
    claimed_ids[student_id] = member.id

    # ── Success message in verify channel (auto-deletes after 24 hours) ──
    ch = guild.get_channel(VERIFY_CHANNEL_ID)
    if ch:
        embed = discord.Embed(
            title="✅ Verification Successful!",
            description=(
                f"Welcome, **{name}**!\n\n"
                f"• Nickname set to **{new_nick}**\n"
                f"• Role assigned: **{role.name}**\n\n"
                "You now have access to all student channels. Enjoy! 🎉"
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"Verified with Student ID: {student_id}")
        await ch.send(embed=embed, delete_after=86400)

    # ── Admin log ────────────────────────────────────────────────────────
    await log_to_admin(guild, embed=discord.Embed(
        title="✅ New Verification",
        description=(
            f"**User:** {member.mention} (`{member}`)\n"
            f"**Nickname:** {new_nick}\n"
            f"**Role:** {role.name}\n"
            f"**Student ID:** {student_id}"
        ),
        color=discord.Color.green(),
    ))

    print(f"[Verified] {member} → {new_nick} | Role: {role.name}")


bot.run(DISCORD_TOKEN)
