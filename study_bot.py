import discord
from discord.ext import commands
import sqlite3
from datetime import datetime, timedelta
import re
import os #

# --- 設定 ---
TOKEN = os.getenv('TOKEN') #

ROLES_CONFIG = {
    (0, 5): "メタル",
    (6, 10): "シルバー",
    (11, 15): "ゴールド",
    (16, 20): "マスター"
}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# データベースの保存先（Koyebの仕様に合わせて少し変更）
# 起動時に一度だけテーブルを作る
def init_db():
    conn = sqlite3.connect('study_data.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS study_logs
                 (user_id INTEGER, minutes INTEGER, date TEXT)''')
    conn.commit()
    conn.close()

def parse_duration(text):
    minutes = 0
    # 「1.5時間」や「1時間30分」などに対応
    hr_match = re.search(r'(\d+(\.\d+)?)時間', text)
    min_match = re.search(r'(\d+)分', text)
    if hr_match:
        minutes += float(hr_match.group(1)) * 60
    if min_match:
        minutes += int(min_match.group(1))
    return int(minutes)

async def update_roles(member, weekly_hrs):
    target_role_name = None
    if weekly_hrs > 20:
        target_role_name = "マスター"
    else:
        for (low, high), name in ROLES_CONFIG.items():
            if low <= weekly_hrs <= high:
                target_role_name = name
                break
    
    if not target_role_name: return "なし"

    all_study_roles = list(ROLES_CONFIG.values())
    roles_to_remove = [r for r in member.roles if r.name in all_study_roles and r.name != target_role_name]
    
    new_role = discord.utils.get(member.guild.roles, name=target_role_name)
    
    if new_role:
        try:
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove)
            if new_role not in member.roles:
                await member.add_roles(new_role)
        except Exception as e:
            print(f"Role Error: {e}")
    return target_role_name

@bot.event
async def on_ready():
    init_db()
    print(f'Logged in as {bot.user}')

@bot.event
async def on_message(message):
    if message.author.bot: return

    duration = parse_duration(message.content)
    if duration > 0:
        user_id = message.author.id
        now = datetime.now()
        date_str = now.strftime('%Y-%m-%d')
        
        # データベースに接続
        conn = sqlite3.connect('study_data.db')
        c = conn.cursor()
        
        # 記録を保存
        c.execute("INSERT INTO study_logs VALUES (?, ?, ?)", (user_id, duration, date_str))
        conn.commit() # ★ここでしっかり保存を確定させる
        
        # 今週の合計を計算（月曜日を週の始まりとする）
        start_of_week = (now - timedelta(days=now.weekday())).strftime('%Y-%m-%d')
        c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id=? AND date >= ?", (user_id, start_of_week))
        weekly_min = c.fetchone()[0] or 0
        
        # 全累計を計算
        c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id=?", (user_id,))
        total_min = c.fetchone()[0] or 0
        
        conn.close()

        weekly_hrs = weekly_min / 60
        total_hrs = total_min / 60

        current_rank = await update_roles(message.author, weekly_hrs)

        await message.channel.send(
            f"✅ **{message.author.display_name}さんの記録完了！**\n"
            f"今回の学習: {duration}分\n"
            f"今週の合計: **{weekly_hrs:.1f}時間**\n"
            f"全累計: **{total_hrs:.1f}時間**\n"
            f"現在のランク: **{current_rank}**"
        )

    await bot.process_commands(message)
