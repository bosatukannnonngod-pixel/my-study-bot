import discord
from discord.ext import commands
import sqlite3
from datetime import datetime, timedelta, timezone
import re
import os

TOKEN = os.getenv('TOKEN')
JST = timezone(timedelta(hours=9)) 
KYOTSU_TEST_DATE = datetime(2027, 1, 16, tzinfo=JST)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
DB_PATH = '/tmp/study_data.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS study_logs (user_id INTEGER, minutes INTEGER, date TEXT)')
    conn.commit()
    conn.close()

async def update_roles(member, weekly_hrs):
    # ランク判定のしきい値
    if weekly_hrs >= 20: target_role_name = "マスター"
    elif weekly_hrs >= 11: target_role_name = "ゴールド"
    elif weekly_hrs >= 6: target_role_name = "シルバー"
    else: target_role_name = "メタル"

    new_role = discord.utils.get(member.guild.roles, name=target_role_name)
    if new_role:
        try:
            all_ranks = ["メタル", "シルバー", "ゴールド", "マスター"]
            to_remove = [r for r in member.roles if r.name in all_ranks and r.name != target_role_name]
            if to_remove: await member.remove_roles(*to_remove)
            if new_role not in member.roles: await member.add_roles(new_role)
            return target_role_name
        except:
            return f"{target_role_name}(権限不足)"
    return target_role_name

@bot.event
async def on_ready():
    init_db()
    print(f'Logged in as {bot.user}')

@bot.event
async def on_message(message):
    if message.author.bot: return
    
    # 時間の解析
    minutes = 0
    hr_match = re.search(r'(\d+(\.\d+)?)時間', message.content)
    min_match = re.search(r'(\d+)分', message.content)
    if hr_match: minutes += float(hr_match.group(1)) * 60
    if min_match: minutes += int(min_match.group(1))
    
    if minutes > 0:
        now = datetime.now(JST)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO study_logs VALUES (?, ?, ?)", (message.author.id, int(minutes), now.strftime('%Y-%m-%d')))
        conn.commit()
        
        monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0)
        c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id=? AND date >= ?", (message.author.id, monday.strftime('%Y-%m-%d')))
        weekly_hrs = (c.fetchone()[0] or 0) / 60
        
        c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id=?", (message.author.id,))
        total_hrs = (c.fetchone()[0] or 0) / 60
        
        c.execute("SELECT user_id, SUM(minutes) as total FROM study_logs WHERE date >= ? GROUP BY user_id ORDER BY total DESC", (monday.strftime('%Y-%m-%d'),))
        ranking = c.fetchall()
        rank_num = next((i+1 for i, r in enumerate(ranking) if r[0] == message.author.id), 0)
        conn.close()

        current_rank = await update_roles(message.author, weekly_hrs)
        
        countdown_channel = discord.utils.get(message.guild.channels, name="共通テストカウントダウン")
        days_left = max(0, (KYOTSU_TEST_DATE - now).days)
        if countdown_channel:
            await countdown_channel.send(f"📢 **カウントダウン更新**\n{message.author.display_name}さんが勉強したよ！\n共通テストまであと **{days_left}日** 📅")

        await message.channel.send(
            f"📝 **{message.author.display_name}さんの学習記録**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"✅ 今回: {int(minutes)}分\n"
            f"📅 今週: **{weekly_hrs:.1f}時間** ({rank_num}位)\n"
            f"🏆 全累計: **{total_hrs:.1f}時間**\n"
            f"🎖️ ランク: **{current_rank}**"
        )

bot.run(TOKEN)
