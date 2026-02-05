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
import shutil
import random

# --- 1. Koyeb対策 ---
def keep_alive():
    class HealthHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"I am alive!")
    
    port = int(os.environ.get("PORT", 8080))
    try:
        with socketserver.TCPServer(("", port), HealthHandler) as httpd:
            print(f"Serving on port {port}")
            httpd.serve_forever()
    except Exception as e:
        print(f"Server Error: {e}")

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
    c.execute('''CREATE TABLE IF NOT EXISTS bot_events 
                 (status TEXT, message TEXT, target_hp REAL, current_hp REAL, 
                  deadline TEXT, last_event_date TEXT)''')
    c.execute("SELECT COUNT(*) FROM bot_events")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO bot_events VALUES ('normal', '', 0, 0, '', ?)", (datetime.now(JST).isoformat(),))
    conn.commit()
    conn.close()

def update_last_seen(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now_str = datetime.now(JST).isoformat()
    c.execute("INSERT OR REPLACE INTO last_seen (user_id, last_datetime) VALUES (?, ?)", (user_id, now_str))
    conn.commit()
    conn.close()

# --- 4. 役職更新 ---
async def update_roles(member, weekly_hrs):
    ranks = {"マスター": 20, "ゴールド": 11, "シルバー": 6, "メタル": 0}
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
            return f"{target_role_name}(権限不足)"
    return target_role_name

# --- 5. 音声再生用関数 ---
async def play_audio(vc, filename):
    if not vc or not vc.is_connected(): return
    if not os.path.exists(filename): return

    try:
        if vc.is_playing(): vc.stop()
        ffmpeg_exe = shutil.which("ffmpeg")
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(
            filename,
            executable=ffmpeg_exe or "ffmpeg",
            options="-vn"
        ))
        
        # 音量設定 0.25
        source.volume = 0.25
        vc.play(source)
        while vc.is_playing(): await asyncio.sleep(1)
    except Exception as e:
        print(f"Audio Error: {e}")

# --- 6. トラブルイベント ---
@tasks.loop(hours=1)
async def check_bot_event():
    now = datetime.now(JST)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT status, message, target_hp, current_hp, deadline, last_event_date FROM bot_events")
    event_data = c.fetchone()
    if not event_data: return
    
    status, msg, target_hp, current_hp, deadline, last_date = event_data
    if status == 'trouble' and now > datetime.fromisoformat(deadline):
        c.execute("UPDATE bot_events SET status='normal', last_event_date=?", (now.isoformat(),))
        conn.commit()
    elif status == 'normal':
        last_dt = datetime.fromisoformat(last_date)
        if (now - last_dt).days >= random.randint(7, 10):
            troubles = ["池の中に落ちちゃいました！", "ワニに囲まれました！", "課題が多すぎます！", "プリン作りましょう！"]
            new_msg, hp = random.choice(troubles), random.randint(15, 25)
            new_deadline = (now + timedelta(days=3)).isoformat()
            c.execute("UPDATE bot_events SET status='trouble', message=?, target_hp=?, current_hp=?, deadline=?", (new_msg, hp, hp, new_deadline))
            conn.commit()
    conn.close()

# --- 7. ポモドーロ ---
active_pomodoros = {}

@bot.command()
async def pomodoro(ctx):
    if not ctx.author.voice:
        await ctx.send("🍅 VCに入ってください！")
        return
    vc = await ctx.author.voice.channel.connect()
    active_pomodoros[ctx.guild.id] = True
    await ctx.send("🍅 **ポモドーロ開始！**")
    
    while active_pomodoros.get(ctx.guild.id):
        await play_audio(vc, "start.mp3")
        for _ in range(1500): # 25分
            if not active_pomodoros.get(ctx.guild.id): return
            await asyncio.sleep(1)
        await ctx.send("☕ **休憩タイム！**")
        await play_audio(vc, "start.mp3")
        for _ in range(300): # 5分
            if not active_pomodoros.get(ctx.guild.id): return
            await asyncio.sleep(1)
        await ctx.send("🚀 **集中タイム再開！**")

@bot.command()
async def stop(ctx):
    active_pomodoros[ctx.guild.id] = False
    if ctx.voice_client: await ctx.voice_client.disconnect()
    await ctx.send("🍅 終了しました。")

# --- 8. 学習記録（累計時間追加版） ---
@bot.event
async def on_message(message):
    if message.author.bot: return
    await bot.process_commands(message)

    minutes = 0
    hr_match = re.search(r'(\d+(\.\d+)?)時間', message.content)
    min_match = re.search(r'(\d+)分', message.content)
    if hr_match: minutes += float(hr_match.group(1)) * 60
    if min_match: minutes += int(min_match.group(1))

    if minutes > 0:
        update_last_seen(message.author.id)
        now = datetime.now(JST)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO study_logs VALUES (?, ?, ?)", (message.author.id, int(minutes), now.strftime('%Y-%m-%d')))
        
        # トラブル処理
        c.execute("SELECT status, current_hp FROM bot_events")
        status, current_hp = c.fetchone()
        trouble_msg = ""
        if status == 'trouble':
            new_hp = max(0, current_hp - (minutes / 60))
            c.execute("UPDATE bot_events SET current_hp=?", (new_hp,))
            if new_hp <= 0:
                c.execute("UPDATE bot_events SET status='normal', last_event_date=?", (now.isoformat(),))
                trouble_msg = "\n\n✨ **トラブル解決！**"
            else:
                trouble_msg = f"\n\n🛠️ あと **{new_hp:.1f}時間**"
        
        # 統計取得
        monday_str = (now - timedelta(days=now.weekday())).strftime('%Y-%m-%d')
        # 今週の合計
        c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id = ? AND date >= ?", (message.author.id, monday_str))
        weekly_mins = c.fetchone()[0] or 0
        # 累計の合計
        c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id = ?", (message.author.id,))
        total_mins = c.fetchone()[0] or 0
        # 順位
        c.execute("SELECT user_id, SUM(minutes) as s FROM study_logs WHERE date >= ? GROUP BY user_id ORDER BY s DESC", (monday_str,))
        ranking = c.fetchall()
        my_rank = next((i for i, (uid, _) in enumerate(ranking, 1) if uid == message.author.id), 0)
        
        conn.commit()
        conn.close()

        rank_name = await update_roles(message.author, weekly_mins/60)
        
        embed = discord.Embed(title="📝 学習記録完了", description=f"今回の記録: {int(minutes)}分{trouble_msg}", color=discord.Color.green())
        embed.add_field(name="📅 今週の合計", value=f"{weekly_mins/60:.1f}時間", inline=True)
        embed.add_field(name="📚 累計時間", value=f"{total_mins/60:.1f}時間", inline=True) # ここを追加
        embed.add_field(name="📊 現在の順位", value=f"**{my_rank}位**", inline=True)
        embed.add_field(name="🎖️ ランク", value=rank_name, inline=True)
        await message.channel.send(embed=embed)

# --- 9. 定期タスクと起動 ---
@tasks.loop(seconds=60)
async def daily_countdown():
    now = datetime.now(JST)
    if now.hour == 0 and now.minute == 0:
        days = max(0, (KYOTSU_TEST_DATE - now).days)
        for guild in bot.guilds:
            ch = discord.utils.get(guild.channels, name="共通テストカウントダウン")
            if ch: await ch.send(f"📅 **{now.strftime('%m月%d日')}**\nあと **{days}日** です！")

@bot.event
async def on_ready():
    init_db()
    daily_countdown.start()
    check_bot_event.start()
    print(f'Logged in as {bot.user}')

bot.run(TOKEN)
