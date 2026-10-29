import os
import asyncio
from datetime import date

import discord
from discord.ext import commands, tasks

# ==========================================
# NexTech HostBot V5
# NexTech Interactive Development LLC
# ==========================================

TOKEN = os.getenv("BOT_OTHER_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "Missing BOT_OTHER_TOKEN. Add your bot token "
        "under Replit Secrets."
    )

# ==========================================
# Company Configuration
# ==========================================

COMPANY_NAME = "NexTech Interactive Development LLC"
FOUNDING_DATE = date(2021, 6, 15)
HQ_BOT_ID = 1274493636540633128

# Rotate to a new activity every 60 seconds.
STATUS_ROTATION_SECONDS = 60

# ==========================================
# Discord Bot Setup
# ==========================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

status_index = 0

# ==========================================
# Operating Years Calculation
# ==========================================

def get_years_operating():
    """Return the number of completed years since founding."""
    today = date.today()

    years = today.year - FOUNDING_DATE.year

    if (today.month, today.day) < (
        FOUNDING_DATE.month,
        FOUNDING_DATE.day
    ):
        years -= 1

    return years


def get_founding_date_text():
    return FOUNDING_DATE.strftime("%B %d, %Y")


# ==========================================
# Rotating Discord Status Messages
# ==========================================

def get_rotating_statuses():
    years = get_years_operating()

    return [
        f"Do Not Ping Staff | {years} Years Since 2021",
        "NexTech HostBot | V5",
        "Hosting Status: Coming Soon",
        "Dashboard: Coming Soon",
        "Synced to HQ",
        COMPANY_NAME,
        f"Founded {get_founding_date_text()}",
        f"Building the Future | {years} Years Operating",
    ]


# ==========================================
# Status Rotation Task
# ==========================================

@tasks.loop(seconds=STATUS_ROTATION_SECONDS)
async def rotate_status():
    global status_index

    statuses = get_rotating_statuses()
    current_status = statuses[status_index % len(statuses)]

    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Game(name=current_status)
    )

    print(f"Discord activity updated: {current_status}")

    status_index += 1


@rotate_status.before_loop
async def before_rotate_status():
    await bot.wait_until_ready()


# ==========================================
# Bot Startup
# ==========================================

@bot.event
async def on_ready():
    print("=" * 45)
    print("NexTech HostBot V5 is online.")
    print(f"Connected as: {bot.user}")
    print(f"Company: {COMPANY_NAME}")
    print(f"Founded: {get_founding_date_text()}")
    print(f"Completed operating years: {get_years_operating()}")
    print("=" * 45)

    # Set the first status immediately.
    statuses = get_rotating_statuses()

    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Game(name=statuses[0])
    )

    # Start rotation only once, including after reconnects.
    if not rotate_status.is_running():
        rotate_status.start()

    # Synchronize slash commands with Discord.
    try:
        synced = await bot.tree.sync()
        print(f"Successfully synced {len(synced)} slash command(s).")
    except Exception as error:
        print(f"Slash command sync failed: {error}")


# ==========================================
# /profile Slash Command
# ==========================================

@bot.tree.command(
    name="profile",
    description="View NexTech HostBot information"
)
async def profile(interaction: discord.Interaction):
    years = get_years_operating()

    embed = discord.Embed(
        title="NexTech HostBot — V5",
        description=(
            "Welcome to NexTech's utility services.\n\n"
            "🟢 **Bot Status:** Online\n"
            "🟡 **Hosting Status:** Coming Soon — V5\n"
            "🌐 **Dashboard:** Coming soon\n\n"
            "⚠️ **Staff Notice:** Please avoid unnecessary staff "
            "mentions. Follow the applicable server rules.\n\n"
            f"🏢 **Company:** {COMPANY_NAME}\n"
            f"📅 **Founded:** {get_founding_date_text()}\n"
            f"🎉 **Years Operating:** {years}\n"
            f"🔄 **Status Rotation:** Every "
            f"{STATUS_ROTATION_SECONDS} seconds"
        ),
        color=discord.Color.blue()
    )

    embed.add_field(
        name="NexTech HQ Bot",
        value=f"<@{HQ_BOT_ID}>",
        inline=False
    )

    embed.set_footer(
        text=f"{COMPANY_NAME} | {years} Years Since 2021"
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# ==========================================
# HostBot Mention Response
# ==========================================

@bot.event
async def on_message(message):
    # Do not respond to other bots or itself.
    if message.author.bot:
        return

    if bot.user and bot.user in message.mentions:
        async with message.channel.typing():
            await asyncio.sleep(2)

        years = get_years_operating()

        response = (
            f"👋 Hello, {message.author.mention}!\n\n"
            f"You have reached **NexTech HostBot V5**.\n\n"
            f"For NexTech HQ assistance, please contact "
            f"the appropriate channel or use <@{HQ_BOT_ID}>.\n\n"
            "⚠️ **Staff Notice:** Please avoid unnecessary staff "
            "mentions and follow the applicable server rules.\n\n"
            f"🏢 **Company:** {COMPANY_NAME}\n"
            f"📅 **Founded:** {get_founding_date_text()}\n"
            f"🎉 **Operating Years:** {years}"
        )

        await message.reply(
            response,
            mention_author=False
        )

    # Preserve prefix-command processing.
    await bot.process_commands(message)


# ==========================================
# Run HostBot
# ==========================================

if __name__ == "__main__":
    bot.run(TOKEN)