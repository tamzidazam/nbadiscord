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

# ── Departmental Wing Roles ───────────────────────────────────────────────────
WING_ROLES = [
    ("💡 Programme Innovation",           1477920772818210907),
    ("📱 Marketing & PR",                  1477992720227237970),
    ("📝 Publications & QA",               1477992735242719273),
    ("⚙️ Data, Tech, & AI",               1477992855577559150),
    ("🤝 People & Culture",                1477992786069426246),
    ("🏢 Industry & Community Activation", 1477992820680818729),
    ("📋 Operations & Execution",          1477993082661245069),
    ("🎓 360° Student Empowerment",        1477993085874212924),
    ("🌍 Competitions & Global Exposure",  1477993171794526208),
    ("🔗 Cross-Functional Collab",         1477993234247450716),
    ("📊 Project Management",              1477994020155293758),
    ("🎨 Creative & Visuals",              1477994055341314182),
]

# In-memory claimed ID tracker
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
    role_ids = set()
    try:
        rows = get_sheet().get_all_values()
        for row in rows[1:]:
            if len(row) >= 3 and row[COL_ROLE_ID].strip().isdigit():
                role_ids.add(int(row[COL_ROLE_ID].strip()))
    except Exception:
        pass
    return role_ids


async def log_to_admin(guild: discord.Guild, **kwargs):
    if ADMIN_LOG_CHANNEL_ID:
        ch = guild.get_channel(ADMIN_LOG_CHANNEL_ID)
        if ch:
            await ch.send(**kwargs)


# ── Wing Selection Dropdown ───────────────────────────────────────────────────
class WingSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=name, value=str(role_id))
            for name, role_id in WING_ROLES
        ]
        super().__init__(
            placeholder="🏷️ Choose your departmental wing...",
            min_values=1,
            max_values=3,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)

        guild   = interaction.guild
        member  = interaction.user

        # Remove any previously selected wing roles first
        wing_role_ids = {rid for _, rid in WING_ROLES}
        roles_to_remove = [r for r in member.roles if r.id in wing_role_ids]
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove)

        # Assign all selected roles
        selected_roles = []
        for value in self.values:
            role = guild.get_role(int(value))
            if role:
                selected_roles.append(role)

        if not selected_roles:
            await interaction.followup.send("⚠️ Could not find the selected roles. Please contact an admin.")
            return

        # Also assign the Verified User role
        verified_user_role = guild.get_role(1478875840279482520)
        if verified_user_role:
            selected_roles.append(verified_user_role)

        await member.add_roles(*selected_roles)

        role_names = "\n".join(f"• **{r.name}**" for r in selected_roles if r.id != 1478875840279482520)

        # Get the user's verification role (EB, TL, SM, GM, etc.) — exclude wing roles and Verified User
        wing_role_ids_set = {rid for _, rid in WING_ROLES} | {1478875840279482520}
        verify_role = next((r for r in member.roles if r.id not in wing_role_ids_set and r.name != "@everyone"), None)
        verify_role_text = f"\n• **{verify_role.name}**" if verify_role else ""

        # Update the message to show selection is done
        embed = discord.Embed(
            title="🎉 Wings Selected!",
            description=(
                f"{member.mention} has joined:\n{role_names}\n\n"
                f"**Your Role:**{verify_role_text}\n\n"
                "Welcome to your teams. Let's get to work! 💪"
            ),
            color=discord.Color.blurple(),
        )
        await interaction.edit_original_response(embed=embed, view=None)

        await log_to_admin(guild, embed=discord.Embed(
            title="🏷️ Wings Assigned",
            description=f"{member.mention} (`{member}`) selected:\n{role_names}",
            color=discord.Color.blurple(),
        ))


class WingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)  # 5 minutes to choose
        self.add_item(WingSelect())

    async def on_timeout(self):
        # Disable the dropdown if user doesn't pick in time
        for item in self.children:
            item.disabled = True


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

    if not content.isdigit():
        try:
            await message.delete()
        except discord.Forbidden:
            pass
        return

    student_id = content.lower()
    member     = message.author
    guild      = message.guild

    try:
        await message.delete()
    except discord.Forbidden:
        pass

    # ── Check 1: Already verified? ───────────────────────────────────────
    verify_role_ids = get_all_verify_role_ids()
    member_role_ids = {r.id for r in member.roles}
    if member_role_ids & verify_role_ids:
        ch = guild.get_channel(VERIFY_CHANNEL_ID)
        await ch.send(
            embed=discord.Embed(
                title="⚠️ Already Verified",
                description=f"{member.mention} You have already been verified. Contact an admin if you need help.",
                color=discord.Color.yellow(),
            )
        )
        await log_to_admin(guild, embed=discord.Embed(
            title="🔁 Repeat Verification Attempt",
            description=f"{member.mention} (`{member}`) tried to verify again with ID `{student_id}`.",
            color=discord.Color.orange(),
        ))
        return

    # ── Check 2: Student ID already claimed? ─────────────────────────────
    if student_id in claimed_ids and claimed_ids[student_id] != member.id:
        ch = guild.get_channel(VERIFY_CHANNEL_ID)
        await ch.send(
            embed=discord.Embed(
                title="🚫 Student ID Already Used",
                description=f"{member.mention} This Student ID has already been used to verify another account. Contact an admin if you need help.",
                color=discord.Color.red(),
            )
        )
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
        ch = guild.get_channel(VERIFY_CHANNEL_ID)
        await ch.send(
            embed=discord.Embed(
                title="⚠️ Database Error",
                description=f"{member.mention} Could not reach the student database. Please try again later.",
                color=discord.Color.red(),
            )
        )
        print(f"[Sheet error] {e}")
        return

    if result is None:
        ch = guild.get_channel(VERIFY_CHANNEL_ID)
        await ch.send(
            embed=discord.Embed(
                title="❌ Student ID Not Found",
                description=f"{member.mention} Student ID **{student_id}** was not found. Please double-check your ID or contact an admin.",
                color=discord.Color.red(),
            )
        )
        await log_to_admin(guild, embed=discord.Embed(
            title="❌ Failed Verification",
            description=f"{member.mention} (`{member}`) entered unknown ID `{student_id}`.",
            color=discord.Color.red(),
        ))
        return

    name, role_id = result

    # ── Rename member ─────────────────────────────────────────────────────
    new_nick = f"{name} - {student_id}"
    try:
        await member.edit(nick=new_nick)
    except discord.Forbidden:
        pass

    # ── Assign verification role ──────────────────────────────────────────
    role = guild.get_role(int(role_id)) if role_id.isdigit() else None
    if role is None:
        ch = guild.get_channel(VERIFY_CHANNEL_ID)
        await ch.send(
            embed=discord.Embed(
                title="⚠️ Role Not Found",
                description=f"{member.mention} Role ID `{role_id}` not found. Please contact an admin.",
                color=discord.Color.red(),
            )
        )
        return

    try:
        await member.add_roles(role)
    except discord.Forbidden:
        ch = guild.get_channel(VERIFY_CHANNEL_ID)
        await ch.send(
            embed=discord.Embed(
                title="⚠️ Permission Error",
                description=f"{member.mention} I don't have permission to assign roles. Please contact an admin.",
                color=discord.Color.red(),
            )
        )
        return

    # ── Mark student ID as claimed ────────────────────────────────────────
    claimed_ids[student_id] = member.id

    # ── Success message + Wing dropdown ──────────────────────────────────
    ch = guild.get_channel(VERIFY_CHANNEL_ID)
    success_embed = discord.Embed(
        title="✅ Verification Successful!",
        description=(
            f"Welcome, **{name}**!\n\n"
            f"• Nickname set to **{new_nick}**\n"
            f"• Role assigned: **{role.name}**\n\n"
            "**Now choose your departmental wing below! ⬇️**"
        ),
        color=discord.Color.green(),
    )
    success_embed.set_footer(text=f"Verified with Student ID: {student_id}")
    await ch.send(embed=success_embed, view=WingView(), delete_after=86400)

    # ── Admin log ─────────────────────────────────────────────────────────
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
