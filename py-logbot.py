import discord
from discord.ext import commands, tasks
import asyncio
import csv
import os
import re
import json
from datetime import datetime, timedelta
from collections import deque
from dotenv import load_dotenv
import requests

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
STEAM_API_KEY = os.getenv('STEAM_API_KEY')
LOGO_URL = 'https://mechinator-cc.vercel.app/ftp.PNG'
DATA_FILE = 'antispam_data.json'
TAG_FILE = 'tags.json'

ALLOWED_TAGS = {
    'Cheater': '⚠️',
    'Bot': '🤖',
    'Hoster': '⭐',
    'Pedo': '🔞',
    'Retard': '🧑‍🦼',
}

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='/', intents=intents)

queue = deque()
processed_lines = set()
anti_spam_data = {}
muted = {}
watching = set()
tags = {}
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

    if datetime.now() - anti_spam_data[steam_id]['last'] > timedelta(seconds=5):
        anti_spam_data[steam_id]['count'] = 0

    anti_spam_data[steam_id]['count'] += 1
    anti_spam_data[steam_id]['last'] = datetime.now()

    if anti_spam_data[steam_id]['count'] > 3:
        anti_spam_data[steam_id]['banned'] = datetime.now()
        muted[steam_id] = True
        save_antispam_data()
        for guild in bot.guilds:
            asyncio.create_task(send_mute_notification(guild, steam_id, 'Muted'))
        return False

    return True

def compose_embed(data, logo_url=LOGO_URL):
    steamid32, username, message, ipc_id = data[1], data[2], data[3], data[4]
    steam_profile_url = f"https://steamcommunity.com/profiles/{convert_steamid32_to_steamid64(steamid32)}"

    tag = tags.get(steamid32, '')
    emoji = ALLOWED_TAGS.get(tag, '') if tag else ''
    if tag:
        tag = f" [{emoji} {tag}]"

    embed = discord.Embed(
        description=message,
        color=0x9b59b6
    )
    embed.set_author(name=f"{username}", url=steam_profile_url, icon_url=logo_url)
    embed.set_footer(text=f"[Mechinator {ipc_id}] [U:1:{steamid32}] {tag}")
    embed.set_thumbnail(url=get_steam_avatar(steamid32))
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
    serializable_data = {
        'anti_spam_data': {
            k: {
                'count': v['count'],
                'last': v['last'].isoformat() if isinstance(v['last'], datetime) else v['last'],
                'banned': v.get('banned').isoformat() if isinstance(v.get('banned'), datetime) else v.get('banned')
            } for k, v in anti_spam_data.items()
        },
        'muted': muted
    }
    with open(DATA_FILE, 'w') as f:
        json.dump(serializable_data, f)

def load_antispam_data():
    global anti_spam_data, muted
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)
            anti_spam_data = {
                k: {
                    'count': v['count'],
                    'last': datetime.fromisoformat(v['last']) if v['last'] else datetime.min,
                    'banned': datetime.fromisoformat(v['banned']) if v.get('banned') else None
                } for k, v in data.get('anti_spam_data', {}).items()
            }
            muted = data.get('muted', {})
    else:
        save_antispam_data()

def save_tags():
    with open(TAG_FILE, 'w') as f:
        json.dump(tags, f)

def load_tags():
    global tags
    if os.path.exists(TAG_FILE):
        with open(TAG_FILE, 'r') as f:
            tags = json.load(f)
    else:
        save_tags()

def get_steam_username(steamid64):
    url = f"http://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/?key={STEAM_API_KEY}&steamids={steamid64}"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return data['response']['players'][0]['personaname']
    return "Unknown"

def get_steam_avatar(steamid32):
    steamid64 = convert_steamid32_to_steamid64(steamid32)
    url = f"http://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/?key={STEAM_API_KEY}&steamids={steamid64}"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return data['response']['players'][0]['avatarfull']
    return LOGO_URL

async def ensure_antispam_channel(guild):
    channel = discord.utils.get(guild.text_channels, name='antispam')
    if not channel:
        channel = await guild.create_text_channel('antispam', reason='Antispam notifications')
    return channel

async def send_mute_notification(guild, steamid32, action):
    channel = await ensure_antispam_channel(guild)
    steamid64 = convert_steamid32_to_steamid64(steamid32)
    steam_profile_url = f"https://steamcommunity.com/profiles/{steamid64}"
    username = get_steam_username(steamid64)
    embed = discord.Embed(
        title="AntiSpam Notification",
        description=f"{action} [{username}](<{steam_profile_url}>)",
        color=0xe74c3c if action == 'Muted' else 0x2ecc71
    )
    embed.set_thumbnail(url=get_steam_avatar(steamid32))
    embed.set_author(name=username, icon_url=LOGO_URL)
    await channel.send(embed=embed)

@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Game(name='Mechinator PREMS'))
    load_antispam_data()
    load_tags()

    global tasks_started
    if not tasks_started:
        locate_logs.start()
        send_messages.start()
        tasks_started = True

    for guild in bot.guilds:
        await ensure_antispam_channel(guild)
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

@bot.command(name='tag')
async def tag(ctx, steamid32: str):
    if not ctx.author.guild_permissions.manage_guild:
        await ctx.send("You don't have permission to use this command.")
        return

    steamid64 = convert_steamid32_to_steamid64(steamid32)
    username = get_steam_username(steamid64)
    avatar_url = get_steam_avatar(steamid32)

    embed = discord.Embed(
        title=f"Select a Tag for {username}",
        description="\n".join([f"{emoji} - {tag}" for tag, emoji in ALLOWED_TAGS.items()]),
        color=0x3498db
    )
    embed.set_footer(text="React with the corresponding emoji to select a tag.")
    embed.set_thumbnail(url=avatar_url)

    message = await ctx.send(embed=embed)

    for emoji in ALLOWED_TAGS.values():
        await message.add_reaction(emoji)

    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in ALLOWED_TAGS.values()

    try:
        reaction, user = await bot.wait_for('reaction_add', timeout=60.0, check=check)
    except asyncio.TimeoutError:
        await ctx.send("Tagging timed out.")
        return

    selected_tag = [tag for tag, emoji in ALLOWED_TAGS.items() if emoji == str(reaction.emoji)][0]
    tags[steamid32] = selected_tag
    save_tags()
    await ctx.send(f"Tagged [U:1:{steamid32}] with '{selected_tag}'")

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
