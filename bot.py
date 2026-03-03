import os
import discord
import gspread
from google.oauth2.service_account import Credentials
from flask import Flask
from threading import Thread

# --- 1. THE RENDER DUMMY SERVER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is awake and running!"

def run():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- 2. CONFIGURATION ---
# We use environment variables here so your token isn't public on GitHub!
TOKEN = os.environ.get('DISCORD_TOKEN')
GUILD_ID = 123456789012345678      # Replace with your Server ID
VERIFY_CHANNEL_ID = 123456789012345678 # Replace with your #verify-here Channel ID
VERIFIED_ROLE_ID = 123456789012345678  # Replace with your @Verified Member Role ID
SPREADSHEET_NAME = 'Club Roster'

# --- 3. GOOGLE SHEETS SETUP ---
scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
# Render allows us to upload "Secret Files" directly to the dashboard
creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
gc = gspread.authorize(creds)

# --- 4. DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True 
intents.members = True         
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.channel.id == VERIFY_CHANNEL_ID:
        student_id_input = message.content.strip()
        
        sheet = gc.open(SPREADSHEET_NAME).sheet1
        records = sheet.get_all_records()
        
        matched_student = None
        for row in records:
            if str(row['Student ID']) == student_id_input:
                matched_student = row
                break
                
        if matched_student:
            try:
                new_nickname = f"{matched_student['Name']} - {matched_student['Student ID']}"
                guild = client.get_guild(GUILD_ID)
                role = guild.get_role(VERIFIED_ROLE_ID)
                
                await message.author.edit(nick=new_nickname, roles=[role])
                await message.delete()
                
            except discord.Forbidden:
                print(f"Error: Missing permissions to modify {message.author.name}")
            except Exception as e:
                print(f"An error occurred: {e}")

# Start the dummy web server, then start the bot
keep_alive()
client.run(TOKEN)
