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
SHEET_NAME           = os.getenv("SHEET_NAME", "Member list")
VERIFY_CHANNEL_ID    = 1478274143714672840
ADMIN_LOG_CHANNEL_ID = int(os.getenv("ADMIN_LOG_CHANNEL_ID", "0"))
VERIFIED_USER_ROLE   = 1478875840279482520

# Sheet columns (0-indexed): Name, ID, Rank Role ID, Dept1 Role ID, Dept2 Role ID
COL_NAME    = 0
COL_ID      = 1
COL_RANK    = 2
COL_DEPT1   = 3
COL_DEPT2   = 4

# In-memory claimed ID tracker: student_id → discord member id
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
    port = int(os.getenv("PORT", 10000))
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


def parse_role_id(value) -> int | None:
    """Return int role ID or None if value is #N/A, empty, or invalid."""
    if value is None:
        return None
    s = str(value).strip()
    if s in ("#N/A", "N/A", "", "None"):
        return None
    if s.isdigit():
        return int(s)
    return None


def lookup_student(student_id: str):
    """Return (name, [role_ids]) if found, else None."""
    sheet = get_sheet()
    rows = sheet.get_all_values()
    for row in rows[1:]:  # skip header
        if len(row) < 2:
            continue
        name = row[COL_NAME].strip()
        sid  = row[COL_ID].strip()
        if sid.lower() == student_id.strip().lower():
            # Collect all valid role IDs (rank + dept1 + dept2)
            role_ids = []
            for col in [COL_RANK, COL_DEPT1, COL_DEPT2]:
                val = row[col] if len(row) > col else None
                rid = parse_role_id(val)
                if rid:
                    role_ids.append(rid)
            return name, role_ids
    return None


def get_all_assigned_role_ids() -> set[int]:
    """Get all role IDs ever assigned by the bot (for already-verified check)."""
    role_ids = set()
    try:
        rows = get_sheet().get_all_values()
        for row in rows[1:]:
            for col in [COL_RANK, COL_DEPT1, COL_DEPT2]:
                val = row[col] if len(row) > col else None
                rid = parse_role_id(val)
                if rid:
                    role_ids.add(rid)
        role_ids.add(VERIFIED_USER_ROLE)
    except Exception:
        pass
    return role_ids


async def log_to_admin(guild: discord.Guild, **kwargs):
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
    ch         = guild.get_channel(VERIFY_CHANNEL_ID)

    try:
        await message.delete()
    except discord.Forbidden:
        pass

    # ── Check 1: Already verified? ───────────────────────────────────────
    assigned_role_ids = get_all_assigned_role_ids()
    member_role_ids   = {r.id for r in member.roles}
    if member_role_ids & assigned_role_ids:
        await ch.send(embed=discord.Embed(
            title="⚠️ Already Verified",
            description=f"{member.mention} You have already been verified. Contact an admin if you need help.",
            color=discord.Color.yellow(),
        ))
        await log_to_admin(guild, embed=discord.Embed(
            title="🔁 Repeat Verification Attempt",
            description=f"{member.mention} (`{member}`) tried to verify again with ID `{student_id}`.",
            color=discord.Color.orange(),
        ))
        return

    # ── Check 2: Student ID already claimed by someone else? ─────────────
    if student_id in claimed_ids and claimed_ids[student_id] != member.id:
        await ch.send(embed=discord.Embed(
            title="🚫 Student ID Already Used",
            description=f"{member.mention} This Student ID has already been used. Contact an admin if you need help.",
            color=discord.Color.red(),
        ))
        await log_to_admin(guild, embed=discord.Embed(
            title="🚨 Duplicate ID Attempt",
            description=(
                f"{member.mention} (`{member}`) tried to use Student ID `{student_id}` "
                f"which was already claimed by <@{claimed_ids[student_id]}>."
            ),
            color=discord.Color.red(),
        ))
        return

    # ── Check 3: Lookup in Google Sheet ──────────────────────────────────
    try:
        result = lookup_student(student_id)
    except Exception as e:
        await ch.send(embed=discord.Embed(
            title="⚠️ Database Error",
            description=f"{member.mention} Could not reach the student database. Please try again later.",
            color=discord.Color.red(),
        ))
        print(f"[Sheet error] {e}")
        return

    if result is None:
        await ch.send(embed=discord.Embed(
            title="❌ Student ID Not Found",
            description=f"{member.mention} Student ID **{student_id}** was not found. Please double-check or contact an admin.",
            color=discord.Color.red(),
        ))
        await log_to_admin(guild, embed=discord.Embed(
            title="❌ Failed Verification",
            description=f"{member.mention} (`{member}`) entered unknown ID `{student_id}`.",
            color=discord.Color.red(),
        ))
        return

    name, role_ids = result

    # ── Rename member: Name - ID ──────────────────────────────────────────
    new_nick = f"{name} - {student_id}"
    try:
        await member.edit(nick=new_nick)
    except discord.Forbidden:
        pass

    # ── Assign all roles (rank + dept1 + dept2 + Verified User) ──────────
    roles_to_assign = []
    role_names      = []

    for rid in role_ids:
        role = guild.get_role(rid)
        if role:
            roles_to_assign.append(role)
            role_names.append(role.name)

    # Always add Verified User role
    verified_role = guild.get_role(VERIFIED_USER_ROLE)
    if verified_role and verified_role not in roles_to_assign:
        roles_to_assign.append(verified_role)

    if not roles_to_assign:
        await ch.send(embed=discord.Embed(
            title="⚠️ Roles Not Found",
            description=f"{member.mention} Could not find the assigned roles. Please contact an admin.",
            color=discord.Color.red(),
        ))
        return

    try:
        await member.add_roles(*roles_to_assign)
    except discord.Forbidden:
        await ch.send(embed=discord.Embed(
            title="⚠️ Permission Error",
            description=f"{member.mention} I don't have permission to assign roles. Please contact an admin.",
            color=discord.Color.red(),
        ))
        return

    # ── Mark student ID as claimed ────────────────────────────────────────
    claimed_ids[student_id] = member.id

    # ── Success message (stays 24 hours) ─────────────────────────────────
    roles_display = "\n".join(f"• **{n}**" for n in role_names)
    embed = discord.Embed(
        title="✅ Verification Successful!",
        description=(
            f"Welcome, **{name}**!\n\n"
            f"• Nickname set to **{new_nick}**\n\n"
            f"**Roles Assigned:**\n{roles_display}"
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"Verified with Student ID: {student_id}")
    await ch.send(embed=embed, delete_after=86400)

    # ── Admin log ─────────────────────────────────────────────────────────
    await log_to_admin(guild, embed=discord.Embed(
        title="✅ New Verification",
        description=(
            f"**User:** {member.mention} (`{member}`)\n"
            f"**Nickname:** {new_nick}\n"
            f"**Roles:** {', '.join(role_names)}\n"
            f"**Student ID:** {student_id}"
        ),
        color=discord.Color.green(),
    ))

    print(f"[Verified] {member} → {new_nick} | Roles: {', '.join(role_names)}")


bot.run(DISCORD_TOKEN)
