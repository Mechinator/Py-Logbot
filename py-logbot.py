import discord
from discord.ext import commands, tasks
import asyncio
import csv
import os
import re
import json
from datetime import datetime, timedelta
from collections import deque, defaultdict
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
LOGO_URL = 'https://mechinator-cc.vercel.app/ftp.PNG'
DATA_FILE = 'antispam_data.json'

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='/', intents=intents)

queue = deque()
processed_lines = set()
anti_spam_data = {}
muted = {}
watching = set()
tasks_started = False

def convert_steamid32_to_steamid64(steamid32):
    return int(steamid32) + 76561197960265728

def split_csv(csv_string):
    reader = csv.reader([csv_string], quotechar='"', delimiter=',', doublequote=True, skipinitialspace=True)
    return next(reader)

def anti_spam(data):
    steam_id = data[1]
    if muted.get(steam_id):
        return False
    if steam_id not in anti_spam_data:
        anti_spam_data[steam_id] = {'count': 0, 'last': datetime.min}
    if datetime.now() - anti_spam_data[steam_id]['last'] > timedelta(seconds=15):
        anti_spam_data[steam_id]['count'] = 0
    anti_spam_data[steam_id]['count'] += 1
    anti_spam_data[steam_id]['last'] = datetime.now()
    if anti_spam_data[steam_id]['count'] > 8:
        anti_spam_data[steam_id]['banned'] = datetime.now()
    return 'banned' not in anti_spam_data[steam_id]

def compose_embed(data, logo_url=LOGO_URL):
    steamid32, username, message, ipc_id = data[1], data[2], data[3], data[4]
    steam_profile_url = f"https://steamcommunity.com/profiles/{convert_steamid32_to_steamid64(steamid32)}"
    
    embed = discord.Embed(
        description=message,
        color=0x9b59b6
    )
    embed.set_author(name=username, url=steam_profile_url, icon_url=logo_url)
    embed.set_footer(text=f"[Mechinator {ipc_id}] [U:1:{steamid32}]")
    embed.set_thumbnail(url=logo_url)
    return embed

def test_and_set(line_hash):
    if line_hash in processed_lines:
        return False
    processed_lines.add(line_hash)
    return True

async def on_line(data):
    line_hash = hash(data)
    if test_and_set(line_hash):
        queue.append(data)

def save_antispam_data():
    with open(DATA_FILE, 'w') as f:
        json.dump({'anti_spam_data': anti_spam_data, 'muted': muted}, f)

def load_antispam_data():
    global anti_spam_data, muted
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)
            anti_spam_data = data.get('anti_spam_data', {})
            muted = data.get('muted', {})

async def ensure_antispam_channel(guild):
    channel = discord.utils.get(guild.text_channels, name='antispam')
    if not channel:
        channel = await guild.create_text_channel('antispam', reason='Antispam notifications')
    return channel

async def send_mute_notification(guild, steamid32, action):
    channel = await ensure_antispam_channel(guild)
    steam_profile_url = f"https://steamcommunity.com/profiles/{convert_steamid32_to_steamid64(steamid32)}"
    embed = discord.Embed(
        title="AntiSpam Notification",
        description=f"{action} [U:1:{steamid32}]",
        color=0xe74c3c if action == 'Muted' else 0x2ecc71
    )
    embed.set_thumbnail(url=LOGO_URL)
    embed.set_footer(text=f"Steam Profile: {steam_profile_url}")
    await channel.send(embed=embed)

@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Game(name='Mechinator PREMS'))
    load_antispam_data()

    global tasks_started
    if not tasks_started:
        locate_logs.start()
        send_messages.start()
        tasks_started = True

    for guild in bot.guilds:
        if not discord.utils.get(guild.text_channels, name='mechinator-chats-mitch'):
            channel = await guild.create_text_channel('mechinator-chats-mitch', reason='Need somewhere to send the salt')
            await channel.send("This channel will relay the chat of all bots.\n\nUse $$mute (steamid32) in order to (un)mute a given player.\n\nThis command will work from any channel, as long as you have Guild Management permissions.\n\nI Also recommend setting up the permissions such that no one can talk in this channel.")

@bot.event
async def on_message(msg):
    if msg.author == bot.user:
        return

    # Handle mute/unmute
    if not isinstance(msg.channel, discord.DMChannel):
        if msg.content.startswith('$$mute'):
            if not msg.author.guild_permissions.manage_guild:
                return
            id_match = re.search(r'(\d+)', msg.content)
            if id_match:
                steamid32 = id_match.group(1)
                if muted.get(steamid32):
                    muted[steamid32] = False
                    await send_mute_notification(msg.guild, steamid32, 'Unmuted')
                else:
                    muted[steamid32] = True
                    await send_mute_notification(msg.guild, steamid32, 'Muted')
                save_antispam_data()
                await msg.channel.send(f"{'Unmuting' if not muted[steamid32] else 'Muting'} [U:1:{steamid32}]")

    await bot.process_commands(msg)

@tasks.loop(seconds=8)
async def send_messages():
    if not queue:
        return
    while queue:
        csv_string = queue.popleft()
        data = split_csv(csv_string)
        if not anti_spam(data):
            continue
        if re.match(r'(just disable vac tf|cat-bot) \d+$', data[2]):
            continue
        embed = compose_embed(data)
        for channel in bot.get_all_channels():
            if channel.name == 'mechinator-chats-mitch' and isinstance(channel, discord.TextChannel):
                await channel.send(embed=embed)

@tasks.loop(seconds=20)
async def locate_logs():
    data_dir = '/opt/cathook/data'
    for filename in os.listdir(data_dir):
        if filename.startswith('chat-') and filename.endswith('.csv'):
            filepath = os.path.join(data_dir, filename)
            if filepath not in watching:
                watching.add(filepath)
                asyncio.create_task(tail_file(filepath))

async def tail_file(filepath):
    with open(filepath, 'r', errors='ignore') as file:
        file.seek(0, os.SEEK_END)
        while True:
            line = file.readline()
            if line:
                await on_line(line.strip())
            await asyncio.sleep(1)

bot.run(TOKEN)
