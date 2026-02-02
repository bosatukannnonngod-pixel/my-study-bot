import discord
from discord.ext import commands, tasks
import sqlite3
from datetime import datetime, timedelta, timezone
import re
import os

# --- 1. 基本設定 ---
TOKEN = os.getenv('TOKEN')
JST = timezone(timedelta(hours=9)) 
# 共通テストの日付（必要に応じて年を修正してください）
KYOTSU_TEST_DATE = datetime(2027, 1, 16, tzinfo=JST)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
DB_PATH = '/tmp/study_data.db'

# --- 2. データベース初期化 ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS study_logs (user_id INTEGER, minutes INTEGER, date TEXT)')
    conn.commit()
    conn.close()

# --- 3. ランク更新機能 ---
async def update_roles(member, weekly_hrs):
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

# --- 4. 毎日 0:00 にカウントダウンを投稿するタスク ---
@tasks.loop(seconds=60)
async def daily_countdown():
    now = datetime.now(JST)
    # 日本時間の 0時 0分 に実行
    if now.hour == 0 and now.minute == 0:
        days_left = max(0, (KYOTSU_TEST_DATE - now).days)
        for guild in bot.guilds:
            channel = discord.utils.get(guild.channels, name="共通テストカウントダウン")
            if channel:
                await channel.send(
                    f"📅 **{now.strftime('%m月%d日')} のお知らせ**\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"共通テストまであと **{days_left}日** です！\n"
                    f"今日もコツコツ積み上げましょう！🔥"
                )

# --- 5. メインイベント ---
@bot.event
async def on_ready():
    init_db()
    if not daily_countdown.is_running():
        daily_countdown.start()
    print(f'Logged in as {bot.user}')

@bot.event
async def on_message(message):
    if message.author.bot: return
    
    # 時間の解析（「1時間」「30分」などを探す）
    minutes = 0
    hr_match = re.search(r'(\d+(\.\d+)?)時間', message.content)
    min_match = re.search(r'(\d+)分', message.content)
    if hr_match: minutes += float(hr_match.group(1)) * 60
    if min_match: minutes += int(min_match.group(1))
    
    # 時間が入力されたら、即座に記録と返信を行う
    if minutes > 0:
        now = datetime.now(JST)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO study_logs VALUES (?, ?, ?)", (message.author.id, int(minutes), now.strftime('%Y-%m-%d')))
        conn.commit()
        
        # 今週の月曜日からの集計
        monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0)
        c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id=? AND date >= ?", (message.author.id, monday.strftime('%Y-%m-%d')))
        weekly_hrs = (c.fetchone()[0] or 0) / 60
        
        # 全期間の累計
        c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id=?", (message.author.id,))
        total_hrs = (c.fetchone()[0] or 0) / 60
        
        # 今週のサーバー内ランキング
        c.execute("SELECT user_id, SUM(minutes) as total FROM study_logs WHERE date >= ? GROUP BY user_id ORDER BY total DESC", (monday.strftime('%Y-%m-%d'),))
        ranking = c.fetchall()
        rank_num = next((i+1 for i, r in enumerate(ranking) if r[0] == message.author.id), 0)
        conn.close()

        # ランク（ロール）の更新
        current_rank = await update_roles(message.author, weekly_hrs)

        # 【即時返信】学習記録の報告
        await message.channel.send(
            f"📝 **{message.author.display_name}さんの学習を記録しました！**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"✅ 今回の学習: **{int(minutes)}分**\n"
            f"📅 今週の合計: **{weekly_hrs:.1f}時間** (サーバー内 **{rank_num}位**)\n"
            f"🏆 全累計時間: **{total_hrs:.1f}時間**\n"
            f"🎖️ 現在ランク: **{current_rank}**"
        )

    await bot.process_commands(message)

# 起動
bot.run(TOKEN)
