import discord
import asyncio
import csv
from datetime import datetime, timedelta
import os
from discord.ext import tasks
from collections import deque
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

client = discord.Client()

queue = deque()
stack = deque(maxlen=10)
anti_spam_data = {}
muted = {}

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

def compose_message(data):
    steam_id, username, message, ipc_id = data[1], data[2], data[3], data[4]
    return f"`[Mechinator {ipc_id}] [U:1:{steam_id}]` **{username}:** {message}"

def compose_message_raw(data):
    steam_id, username, message, ipc_id = data[1], data[2], data[3], data[4]
    return f"[Mechinator {ipc_id}] [U:1:{steam_id}] {username}: {message}"

def test_and_set(msg):
    if msg in stack:
        return False
    stack.append(msg)
    return True

async def on_line(data):
    if test_and_set(data):
        queue.append(data)

@client.event
async def on_ready():
    print(f'Logged in as {client.user}!')
    await client.change_presence(activity=discord.Game(name='Mechinator PREMS'))
    for guild in client.guilds:
        if not discord.utils.get(guild.text_channels, name='mechinator-chats-mitch'):
            channel = await guild.create_text_channel('mechinator-chats-mitch', reason='Need somewhere to send the salt')
            await channel.send("This channel will relay the chat of all bots.\n\nUse $$mute (steamid32) in order to (un)mute a given player.\n\nThis command will work from any channel, as long as you have Guild Management permissions.\n\nI Also recommend setting up the permissions such that no one can talk in this channel.")

@client.event
async def on_message(msg):
    if not msg.author.guild_permissions.manage_guild:
        return
    if msg.content.startswith('$$mute'):
        id_match = re.search(r'(\d+)', msg.content)
        if id_match:
            steam_id = id_match.group(1)
            if muted.get(steam_id):
                await msg.channel.send(f'Unmuting [U:1:{steam_id}]')
                muted[steam_id] = False
            else:
                await msg.channel.send(f'Muting [U:1:{steam_id}]')
                muted[steam_id] = True

@tasks.loop(seconds=8)
async def send_messages():
    if not queue:
        return
    msg = ''
    msg_raw = ''
    while queue:
        csv_string = queue.popleft()
        data = split_csv(csv_string)
        if not anti_spam(data):
            continue
        if re.match(r'(just disable vac tf|cat-bot) \d+$', data[2]):
            continue
        message = compose_message(data)
        msg += message + '\n'
        msg_raw += compose_message_raw(data) + '\n'
    if msg_raw:
        print(msg_raw)
        for channel in client.get_all_channels():
            if channel.name == 'mechinator-chats-mitch' and isinstance(channel, discord.TextChannel):
                await channel.send(msg)

@tasks.loop(seconds=20)
async def locate_logs():
    data_dir = '/opt/cathook/data'
    for filename in os.listdir(data_dir):
        if filename.startswith('chat-') and filename.endswith('.csv'):
            filepath = os.path.join(data_dir, filename)
            if filepath not in watching:
                print(f'Found log file: {filepath}')
                watching[filepath] = asyncio.create_task(tail_file(filepath))

async def tail_file(filepath):
    with open(filepath, 'r') as file:
        file.seek(0, os.SEEK_END)
        while True:
            line = file.readline()
            if line:
                await on_line(line.strip())
            await asyncio.sleep(1)

watching = {}
client.run(TOKEN)
send_messages.start()
locate_logs.start()
