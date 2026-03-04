import discord
from discord import app_commands
from discord.ext import commands
import gspread
from google.oauth2.service_account import Credentials
import os
import json
from dotenv import load_dotenv

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────────
DISCORD_TOKEN      = os.getenv("DISCORD_TOKEN")
GOOGLE_CREDS_JSON  = os.getenv("GOOGLE_CREDS_JSON")   # full JSON string
SPREADSHEET_ID     = os.getenv("SPREADSHEET_ID")
SHEET_NAME         = os.getenv("SHEET_NAME", "Sheet1")

# Column positions (1-indexed) in your Google Sheet
COL_STUDENT_ID     = int(os.getenv("COL_STUDENT_ID", "1"))   # e.g. column A
COL_NAME           = int(os.getenv("COL_NAME", "2"))          # e.g. column B

VERIFIED_ROLE_NAME = os.getenv("VERIFIED_ROLE_NAME", "Verified")
# ────────────────────────────────────────────────────────────────────────────

# Google Sheets setup
def get_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    return spreadsheet.worksheet(SHEET_NAME)


def lookup_student(student_id: str):
    """Return student name if ID found, else None."""
    sheet = get_sheet()
    records = sheet.get_all_values()
    for row in records[1:]:  # skip header row
        if len(row) >= 2:
            sheet_id = str(row[COL_STUDENT_ID - 1]).strip()
            name     = str(row[COL_NAME - 1]).strip()
            if sheet_id.lower() == student_id.strip().lower():
                return name
    return None


# Bot setup
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅  Logged in as {bot.user} — slash commands synced.")


@tree.command(name="verify", description="Verify yourself with your student ID")
@app_commands.describe(student_id="Enter your student ID")
async def verify(interaction: discord.Interaction, student_id: str):
    await interaction.response.defer(ephemeral=True)   # show "thinking…" only to user

    guild  = interaction.guild
    member = interaction.user

    # ── 1. Look up student in Google Sheet ───────────────────────────────
    try:
        name = lookup_student(student_id)
    except Exception as e:
        await interaction.followup.send(
            "⚠️ Could not reach the student database right now. Please try again later.",
            ephemeral=True,
        )
        print(f"[Sheet error] {e}")
        return

    if name is None:
        await interaction.followup.send(
            f"❌ Student ID **{student_id}** was not found. "
            "Please double-check your ID or contact an admin.",
            ephemeral=True,
        )
        return

    # ── 2. Rename the member ──────────────────────────────────────────────
    try:
        await member.edit(nick=name)
    except discord.Forbidden:
        pass   # bot can't rename server owner — that's fine

    # ── 3. Give Verified role ─────────────────────────────────────────────
    role = discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME)
    if role is None:
        await interaction.followup.send(
            f"⚠️ Role **{VERIFIED_ROLE_NAME}** not found on this server. "
            "Please ask an admin to create it.",
            ephemeral=True,
        )
        return

    try:
        await member.add_roles(role)
    except discord.Forbidden:
        await interaction.followup.send(
            "⚠️ I don't have permission to assign roles. Please contact an admin.",
            ephemeral=True,
        )
        return

    # ── 4. Success message ────────────────────────────────────────────────
    embed = discord.Embed(
        title="✅ Verification Successful!",
        description=(
            f"Welcome, **{name}**!\n\n"
            f"• Your nickname has been set to **{name}**\n"
            f"• You've been given the **{VERIFIED_ROLE_NAME}** role\n\n"
            "You now have access to all student channels. Enjoy! 🎉"
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"Verified with Student ID: {student_id}")
    await interaction.followup.send(embed=embed, ephemeral=True)
    print(f"[Verified] {member} → {name} (ID: {student_id})")


bot.run(DISCORD_TOKEN)
