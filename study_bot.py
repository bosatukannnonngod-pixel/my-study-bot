import http.server
import socketserver
import threading
import os
import discord
from discord.ext import commands, tasks
import sqlite3
from datetime import datetime, timedelta, timezone
import re
import asyncio
import matplotlib.pyplot as plt

# --- 1. Koyeb対策: 強制終了を防ぐサーバー ---
def keep_alive():
    class HealthHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"I am alive!")
    
    # Koyebが指定するポート、または8080で待機
    port = int(os.environ.get("PORT", 8080))
    try:
        with socketserver.TCPServer(("", port), HealthHandler) as httpd:
            print(f"Serving on port {port}")
            httpd.serve_forever()
    except Exception as e:
        print(f"Server Error: {e}")

# 別スレッドでサーバーを起動
threading.Thread(target=keep_alive, daemon=True).start()

# --- 2. 基本設定 ---
TOKEN = os.getenv('TOKEN')
JST = timezone(timedelta(hours=9)) 
KYOTSU_TEST_DATE = datetime(2027, 1, 16, tzinfo=JST)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True 
bot = commands.Bot(command_prefix="!", intents=intents)

DB_PATH = 'study_data.db'

# --- 3. データベース初期化 ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS study_logs (user_id INTEGER, minutes INTEGER, date TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS last_seen (user_id INTEGER PRIMARY KEY, last_datetime TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS rivals (user_id INTEGER PRIMARY KEY, rival_id INTEGER)')
    conn.commit()
    conn.close()

# --- 4. 役職更新 ---
async def update_roles(member, weekly_hrs):
    ranks = {
        "マスター": 20,
        "ゴールド": 11,
        "シルバー": 6,
        "メタル": 0
    }
    
    target_role_name = "メタル"
    for name, hrs in ranks.items():
        if weekly_hrs >= hrs:
            target_role_name = name
            break

    new_role = discord.utils.get(member.guild.roles, name=target_role_name)
    if new_role:
        try:
            to_remove = [r for r in member.roles if r.name in ranks.keys() and r.name != target_role_name]
            if to_remove: await member.remove_roles(*to_remove)
            if new_role not in member.roles: await member.add_roles(new_role)
            return target_role_name
        except:
            return f"{target_role_name}(権限不足:ボットの役職を上に上げてください)"
    return target_role_name

# --- 5. ポモドーロ機能 ---
@bot.command()
async def pomodoro(ctx):
    if not ctx.author.voice:
        await ctx.send("🍅 まずはボイスチャンネルに入ってください！")
        return
    
    vc = await ctx.author.voice.channel.connect()
    await ctx.send("🍅 **ポモドーロ開始！** (25分集中 / 5分休憩)")

    while True:
        # 集中 (本来はここで音を鳴らすが、mp3がないとエラーになるためメッセージのみ)
        await asyncio.sleep(1500) 
        await ctx.send(f"{ctx.author.mention} ☕ **25分経過！5分休憩です。**")
        
        await asyncio.sleep(300)
        await ctx.send(f"{ctx.author.mention} 🚀 **休憩終了！集中タイム再開！**")

@bot.command()
async def stop(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("🍅 ポモドーロを終了しました。")

# --- 6. 自動記録 & コマンド ---
@bot.event
async def on_message(message):
    if message.author.bot: return
    await bot.process_commands(message)

    # 勉強時間の抽出 (例: 1時間30分, 45分)
    minutes = 0
    h_match = re.search(r'(\d+)時間', message.content)
    m_match = re.search(r'(\d+)分', message.content)
    if h_match: minutes += int(h_match.group(1)) * 60
    if m_match: minutes += int(m_match.group(1))

    if minutes > 0:
        now = datetime.now(JST)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO study_logs VALUES (?, ?, ?)", (message.author.id, minutes, now.strftime('%Y-%m-%d')))
        c.execute("INSERT OR REPLACE INTO last_seen VALUES (?, ?)", (message.author.id, now.isoformat()))
        conn.commit()

        # 今週の合計計算
        monday = (now - timedelta(days=now.weekday())).strftime('%Y-%m-%d')
        c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id=? AND date >= ?", (message.author.id, monday))
        total_min = c.fetchone()[0] or 0
        conn.close()

        rank = await update_roles(message.author, total_min / 60)
        await message.channel.send(f"✅ 記録完了！今週の合計: **{total_min/60:.1f}時間**\n現在のランク: **{rank}**")

@bot.event
async def on_ready():
    init_db()
    print(f'Logged in as {bot.user}')

bot.run(TOKEN)
