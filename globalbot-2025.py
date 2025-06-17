import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
import os
import io
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import aiohttp
from PIL import Image, ImageDraw, ImageFont

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.message_content = True 

bot = commands.Bot(command_prefix='!', intents=intents)

global_chat_channels = {}
user_message_times = defaultdict(list)
GLOBAL_CHAT_IDENTIFIER = '9c70933aff6b2a6d08c687a6cbb6b765'
ANTI_SPAM_TOPIC_IDENTIFIER = '9c70933aff6b2a6d08c687a6cbb6b765' 
MUTE_DIR = 'mutes'

if not os.path.exists(MUTE_DIR):
    os.makedirs(MUTE_DIR)

async def create_profile_image(pfp_url: str, display_name: str) -> discord.File:
    import aiohttp
    from PIL import Image, ImageDraw, ImageFont
    import io

    async with aiohttp.ClientSession() as session:
        async with session.get(pfp_url) as resp:
            if resp.status != 200:
                raise Exception("Failed to download avatar")
            avatar_data = await resp.read()

    SCALE = 16
    AVATAR_SIZE = 32 * SCALE
    FONT_SIZE = 14 * SCALE
    PADDING_X = 0 * SCALE
    PADDING_Y = 5 * SCALE
    PADDING_XX = 5 * SCALE

    avatar = Image.open(io.BytesIO(avatar_data)).convert("RGBA")
    avatar = avatar.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)

    mask = Image.new("L", (AVATAR_SIZE, AVATAR_SIZE), 0)
    draw_mask = ImageDraw.Draw(mask)
    draw_mask.ellipse((0, 0, AVATAR_SIZE, AVATAR_SIZE), fill=255)
    circular_avatar = Image.new("RGBA", (AVATAR_SIZE, AVATAR_SIZE), (255, 255, 255, 0))
    circular_avatar.paste(avatar, (0, 0), mask=mask)

    try:
        font = ImageFont.truetype("arial.ttf", size=FONT_SIZE)
    except:
        font = ImageFont.load_default()

    bbox = font.getbbox(display_name)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    text_x_offset = AVATAR_SIZE + PADDING_XX
    width = text_x_offset + text_width + PADDING_X
    height = max(AVATAR_SIZE, text_height + 2 * PADDING_Y)

    img = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)

    avatar_y = (height - AVATAR_SIZE) // 2
    img.paste(circular_avatar, (PADDING_X, avatar_y), circular_avatar)

    text_y = (height - text_height) // 2
    draw.text((text_x_offset, text_y), display_name, font=font, fill=(204, 204, 204, 255))  # #cccccc

    final_size = (width // SCALE, height // SCALE)
    img = img.resize(final_size, Image.LANCZOS)

    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return discord.File(fp=buffer, filename="profile_header.png")

def load_muted_users(guild_id):
    file_path = os.path.join(MUTE_DIR, f'{guild_id}.json')
    if os.path.exists(file_path):
        with open(file_path, 'r') as file:
            return json.load(file)
    return []

def save_muted_users(guild_id, data):
    file_path = os.path.join(MUTE_DIR, f'{guild_id}.json')
    with open(file_path, 'w') as file:
        json.dump(data, file, indent=4)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    await update_global_chat_channels()
    await update_activity_status()
    update_activity_status_task.start() 
    check_channels.start()
    prune_message_history.start()
    await bot.tree.sync()

@bot.event
async def on_guild_join(guild):
    print(f"Bot joined guild: {guild.name}")
    await update_global_chat_channels()
    await update_activity_status()

@bot.event
async def on_guild_remove(guild):
    print(f"Bot removed from guild: {guild.name}")
    global global_chat_channels
    global_chat_channels = {k: v for k, v in global_chat_channels.items() if v.guild.id != guild.id}
    mute_file_path = os.path.join(MUTE_DIR, f'{guild.id}.json')
    if os.path.exists(mute_file_path):
        os.remove(mute_file_path)
    await update_activity_status()

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.author == bot.user:
        return

    now = datetime.now(timezone.utc)

    if isinstance(message.channel, discord.DMChannel):
        user_message_times[(message.channel.id, message.author.id)].append(now)

        user_message_times[(message.channel.id, message.author.id)] = [
            timestamp for timestamp in user_message_times[(message.channel.id, message.author.id)]
            if now - timestamp < timedelta(minutes=20)
        ]

        if len(user_message_times[(message.channel.id, message.author.id)]) > 1 and \
                now - user_message_times[(message.channel.id, message.author.id)][-2] < timedelta(seconds=5):
            await message.channel.send(f":no_entry: **Message Not Sent** 5 second slow-mode enabled!")
            return

        if message.content.strip().lower() == '/start':
            if message.channel.id not in global_chat_channels:
                global_chat_channels[message.channel.id] = message.channel
                await message.channel.send("Global chat started. Your messages will be shared with global channels.")
        elif message.content.strip().lower() == '/stop':
            if message.channel.id in global_chat_channels:
                del global_chat_channels[message.channel.id]
                await message.channel.send("Global chat stopped. Your messages will no longer be shared with global channels.")
        else:
            if message.channel.id in global_chat_channels:
                for channel_id, channel in global_chat_channels.items():
                    if channel_id != message.channel.id:
                        if isinstance(channel, discord.TextChannel):
                            muted_usernames = load_muted_users(channel.guild.id)
                            
                            if message.author.name in muted_usernames:
                                continue

                            try:
                                pfp_url = message.author.avatar.replace(size=128).url
                                display_name = message.author.display_name

                                try:
                                    profile_file = await create_profile_image(pfp_url, display_name)
                                    await channel.send(file=profile_file)
                                    if message.attachments:
                                        for attachment in message.attachments:
                                            await channel.send(f"[File]({attachment.url})")
                                    else:
                                        await channel.send(f"\u00A0\u00A0\u00A0{message.content}")
                                except Exception as e:
                                    print(f"Error creating profile image: {e}")
                            except Exception as e:
                                print(f"Failed to send message to channel {channel.id} in guild {channel.guild.id}: {e}")
                        elif isinstance(channel, discord.DMChannel):
                            try:
                                pfp_url = message.author.avatar.replace(size=128).url
                                display_name = message.author.display_name

                                try:
                                    profile_file = await create_profile_image(pfp_url, display_name)
                                    await channel.send(file=profile_file)

                                    
                                    if message.attachments:
                                        for attachment in message.attachments:
                                            await channel.send(f"[File]({attachment.url})")
                                    else:
                                        await channel.send(f"\u00A0\u00A0\u00A0{message.content}")


                                except Exception as e:
                                    print(f"Error creating profile image: {e}")
                            except Exception as e:
                                print(f"Failed to send message to DM channel {channel.id}: {e}")
        return

    if message.channel.topic and ANTI_SPAM_TOPIC_IDENTIFIER in message.channel.topic:
        user_message_times[(message.guild.id, message.author.id)].append(now)

        user_message_times[(message.guild.id, message.author.id)] = [
            timestamp for timestamp in user_message_times[(message.guild.id, message.author.id)]
            if now - timestamp < timedelta(minutes=20)
        ]

        if len(user_message_times[(message.guild.id, message.author.id)]) > 50:
            muted_usernames = load_muted_users(message.guild.id)
            muted_usernames.append(message.author.name)
            save_muted_users(message.guild.id, muted_usernames)
            await message.channel.send(f"{message.author.mention} has been automatically muted for excessive messaging.")
            return

        if len(user_message_times[(message.guild.id, message.author.id)]) > 1 and \
                now - user_message_times[(message.guild.id, message.author.id)][-2] < timedelta(seconds=5):
            await message.channel.send(f":no_entry: {message.author.mention} **Message Not Sent** 5 second slow-mode enabled!")
            return

        if '@everyone' in message.content:
            await message.channel.send(f":no_entry: {message.author.mention} **Message Not Sent** Messages containing `@everyone` are not allowed!")
            return
        else:
            await message.add_reaction('\u2705')

    if message.guild:
        muted_usernames = load_muted_users(message.guild.id)
        
        if message.author.name in muted_usernames:
            await message.channel.send(f"{message.author.mention}, you are currently muted in this server.")
            return

    if message.content.startswith(bot.command_prefix):
        await bot.process_commands(message)
        return

    if message.channel.id in global_chat_channels:
        username_match = re.match(r"Username:\s*`(\w+)`", message.content)
        if username_match:
            username = username_match.group(1)
        else:
            username = message.author.name

        for channel_id, channel in global_chat_channels.items():
            if channel_id != message.channel.id:
                if isinstance(channel, discord.TextChannel):
                    muted_usernames = load_muted_users(channel.guild.id)

                    if username in muted_usernames:
                        continue

                    try:
                        pfp_url = message.author.avatar.replace(size=128).url
                        display_name = message.author.display_name

                        try:
                            profile_file = await create_profile_image(pfp_url, display_name)
                            await channel.send(file=profile_file)
                            if message.attachments:
                                for attachment in message.attachments:
                                    await channel.send(f"[File]({attachment.url})")
                            else:
                                await channel.send(f"\u00A0\u00A0\u00A0{message.content}")
                        except Exception as e:
                            print(f"Error creating profile image: {e}")

                    except Exception as e:
                        print(f"Failed to send message to channel {channel.id} in guild {channel.guild.id}: {e}")
                elif isinstance(channel, discord.DMChannel):
                    try:
                        pfp_url = message.author.avatar.replace(size=128).url
                        display_name = message.author.display_name

                        try:
                            profile_file = await create_profile_image(pfp_url, display_name)
                            await channel.send(file=profile_file)
                            if message.attachments:
                                for attachment in message.attachments:
                                    await channel.send(f"[File]({attachment.url})")
                            else:
                                await channel.send(f"\u00A0\u00A0\u00A0{message.content}")
                        except Exception as e:
                            print(f"Error creating profile image: {e}")
                    except Exception as e:
                        print(f"Failed to send message to DM channel {channel.id}: {e}")

@bot.tree.command(name="mute")
@app_commands.checks.has_permissions(manage_messages=True)
async def mute(interaction: discord.Interaction, username: str):
    guild = interaction.guild
    muted_usernames = load_muted_users(guild.id)
    if username not in muted_usernames:
        muted_usernames.append(username)
        save_muted_users(guild.id, muted_usernames)
        await interaction.response.send_message(f"User `{username}` has been muted in this server.", ephemeral=True)
    else:
        await interaction.response.send_message(f"User `{username}` is already muted in this server.", ephemeral=True)


@bot.tree.command(name="unmute")
@app_commands.checks.has_permissions(manage_messages=True)
async def unmute(interaction: discord.Interaction, username: str):
    guild = interaction.guild
    muted_usernames = load_muted_users(guild.id)
    if username in muted_usernames:
        muted_usernames.remove(username)
        save_muted_users(guild.id, muted_usernames)
        await interaction.response.send_message(f"User `{username}` has been unmuted in this server.", ephemeral=True)
    else:
        await interaction.response.send_message(f"User `{username}` is not muted in this server.", ephemeral=True)


@bot.tree.command(name="mutelist")
@app_commands.checks.has_permissions(manage_messages=True)
async def mutelist(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    muted_usernames = load_muted_users(guild_id)
    
    if muted_usernames:
        muted_users_string = "\n".join(muted_usernames)
        await interaction.response.send_message(f"Muted users in this server:\n{muted_users_string}", ephemeral=True)
    else:
        await interaction.response.send_message("No users are currently muted in this server.", ephemeral=True)


async def update_global_chat_channels():
    global global_chat_channels
    global_chat_channels = {}

    for guild in bot.guilds:
        for channel in guild.text_channels:
            if channel.topic and GLOBAL_CHAT_IDENTIFIER in channel.topic:
                global_chat_channels[channel.id] = channel
                print(f"Found global-chat channel: {channel.name} in guild: {guild.name}")

    for dm_channel_id in global_chat_channels:
        dm_channel = bot.get_channel(dm_channel_id)
        if isinstance(dm_channel, discord.DMChannel):
            global_chat_channels[dm_channel_id] = dm_channel
            print(f"Added DM global-chat channel: {dm_channel.id}")

async def update_activity_status():
    global_chat_channel_count = 0
    dm_channel_count = 0
    for channel_id, channel in global_chat_channels.items():
        if isinstance(channel, discord.TextChannel) or isinstance(channel, discord.DMChannel):
            global_chat_channel_count += 1
            if isinstance(channel, discord.DMChannel):
                dm_channel_count += 1
    activity = discord.Activity(type=discord.ActivityType.watching, 
                                name=f'{global_chat_channel_count} channels ({dm_channel_count} DM\'s)')
    await bot.change_presence(activity=activity)

@tasks.loop(minutes=1)
async def update_activity_status_task():
    await update_activity_status()

@tasks.loop(minutes=1)
async def check_channels():
    for guild in bot.guilds:
        for channel in guild.text_channels:
            if channel.topic and GLOBAL_CHAT_IDENTIFIER in channel.topic:
                if channel.id not in global_chat_channels:
                    global_chat_channels[channel.id] = channel
                    print(f"Added new global-chat channel: {channel.name} in guild: {guild.name}")

    for dm_channel_id in list(global_chat_channels.keys()):
        dm_channel = bot.get_channel(dm_channel_id)
        if isinstance(dm_channel, discord.DMChannel):
            if dm_channel.id not in global_chat_channels:
                global_chat_channels[dm_channel.id] = dm_channel
                print(f"Added new DM global-chat channel: {dm_channel.id}")

@tasks.loop(minutes=10)
async def prune_message_history():
    now = datetime.now(timezone.utc)
    prune_before = now - timedelta(minutes=10)
    for key in list(user_message_times.keys()):
        user_message_times[key] = [timestamp for timestamp in user_message_times[key] if timestamp > prune_before]

bot.run(TOKEN)