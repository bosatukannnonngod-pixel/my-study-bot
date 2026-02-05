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

# --- 1. Koyeb対策: 強制終了を防ぐサーバー ---
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

# --- 5. 音声再生用関数 (デバッグ・強化版) ---
async def play_audio(vc, filename):
    if not vc or not vc.is_connected():
        print("❌ 音声再生エラー: VCに接続されていません。")
        return

    if not os.path.exists(filename):
        print(f"❌ ファイル未発見: {filename} がカレントディレクトリに存在しません。")
        # デバッグ用にファイル一覧を表示
        print(f"📂 現在のファイル一覧: {os.listdir('.')}")
        return

    try:
        if vc.is_playing():
            vc.stop()
        
        # FFmpegのパス特定
        ffmpeg_exe = shutil.which("ffmpeg")
        if not ffmpeg_exe:
            possible_paths = ["/app/.apt/usr/bin/ffmpeg", "/workspace/.apt/usr/bin/ffmpeg", "/usr/bin/ffmpeg"]
            for path in possible_paths:
                if os.path.exists(path):
                    ffmpeg_exe = path
                    break
        
        # ボイスクライアントの状態を確認
        print(f"🎵 再生準備中: {filename} (FFmpeg: {ffmpeg_exe})")

        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(
            filename,
            executable=ffmpeg_exe or "ffmpeg",
            before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            options="-vn"
        ))
        source.volume = 0.5  # 音量を50%に設定
        
        vc.play(source, after=lambda e: print(f"✅ 再生終了: {e}") if e else print("✅ 再生完了"))
        
        # 再生中の待機
        while vc.is_playing():
            await asyncio.sleep(1)

    except Exception as e:
        print(f"❌ Audio Play Error (詳細): {e}")

# --- 6. ポモドーロ機能 ---
active_pomodoros = {}

@bot.command()
async def pomodoro(ctx):
    if not ctx.author.voice:
        await ctx.send("🍅 まずはボイスチャンネルに入ってください！")
        return
    
    channel = ctx.author.voice.channel
    try:
        # すでに接続されているか確認
        if ctx.voice_client:
            vc = ctx.voice_client
            if vc.channel != channel:
                await vc.move_to(channel)
        else:
            vc = await channel.connect()
    except Exception as e:
        await ctx.send(f"⚠️ 接続エラー: {e}")
        return

    active_pomodoros[ctx.guild.id] = True
    await ctx.send("🍅 **ポモドーロ開始！** (25分集中 / 5分休憩)\n※移動しても追いかけます！")
    await play_audio(vc, "start.mp3")

    try:
        while active_pomodoros.get(ctx.guild.id):
            for _ in range(1500): # 25分
                if not active_pomodoros.get(ctx.guild.id): return
                await asyncio.sleep(1)
            
            if ctx.voice_client:
                await play_audio(ctx.voice_client, "start.mp3")
                mentions = " ".join([m.mention for m in ctx.voice_client.channel.members])
                await ctx.send(f"{mentions}\n☕ **25分経過！5分間の休憩タイムです。**")
            
            for _ in range(300): # 5分
                if not active_pomodoros.get(ctx.guild.id): return
                await asyncio.sleep(1)

            if ctx.voice_client:
                await play_audio(ctx.voice_client, "start.mp3")
                await ctx.send(f"🚀 **休憩終了！集中タイム再開！**")
    except Exception as e:
        print(f"Pomodoro Error: {e}")

@bot.command()
async def stop(ctx):
    active_pomodoros[ctx.guild.id] = False
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
    await ctx.send("🍅 ポモドーロを終了しました。")

# --- 自動追跡機能 ---
@bot.event
async def on_voice_state_update(member, before, after):
    if member.id == bot.user.id:
        return
    if active_pomodoros.get(member.guild.id) and bot.user in member.guild.members:
        vc = member.guild.voice_client
        if vc and after.channel and after.channel != vc.channel:
            await vc.move_to(after.channel)
            await asyncio.sleep(1)
            await play_audio(vc, "start.mp3")

# --- 7. 定期タスク ---
@tasks.loop(seconds=60)
async def daily_countdown():
    now = datetime.now(JST)
    if now.hour == 0 and now.minute == 0:
        days_left = max(0, (KYOTSU_TEST_DATE - now).days)
        for guild in bot.guilds:
            channel = discord.utils.get(guild.channels, name="共通テストカウントダウン")
            if channel: await channel.send(f"📅 **{now.strftime('%m月%d日')}**\n共通テストまであと **{days_left}日** です！")

@tasks.loop(seconds=60)
async def weekly_ranking_announcement():
    now = datetime.now(JST)
    if now.weekday() == 0 and now.hour == 0 and now.minute == 0:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        one_week_ago = (now - timedelta(days=7)).strftime('%Y-%m-%d')
        c.execute("SELECT user_id, SUM(minutes) FROM study_logs WHERE date >= ? GROUP BY user_id ORDER BY SUM(minutes) DESC", (one_week_ago,))
        ranking = c.fetchall()
        conn.close()
        if not ranking: return

        names, hours = [], []
        for uid, mins in ranking[:5]:
            user = bot.get_user(uid)
            names.append(user.name if user else f"ID:{uid}")
            hours.append(mins / 60)
        plt.figure(figsize=(8, 5))
        plt.barh(names[::-1], hours[::-1], color='skyblue')
        plt.xlabel('Hours')
        plt.title('Weekly Ranking')
        plt.tight_layout()
        plt.savefig('ranking.png')
        plt.close()

        msg = "🏆 **週間ランキング発表** 🏆\n"
        for i, (user_id, total_min) in enumerate(ranking, 1):
            msg += f"{i}位: <@{user_id}> ({total_min/60:.1f}h)\n"

        for guild in bot.guilds:
            channel = discord.utils.get(guild.channels, name="順位決定戦")
            if channel: await channel.send(msg, file=discord.File('ranking.png'))

@tasks.loop(hours=1)
async def check_lazy_users():
    now = datetime.now(JST)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    three_days_ago = (now - timedelta(days=3)).isoformat()
    c.execute("SELECT user_id FROM last_seen WHERE last_datetime < ?", (three_days_ago,))
    lazy_users = c.fetchall()
    conn.close()
    for (user_id,) in lazy_users:
        for guild in bot.guilds:
            member = guild.get_member(user_id)
            if member:
                channel = discord.utils.get(guild.channels, name="勉強時間報告")
                if channel: await channel.send(f"<@{user_id}> 3日間報告がありません！勉強頑張りましょう！")

# --- 8. イベント処理 ---
@bot.event
async def on_ready():
    init_db()
    if not daily_countdown.is_running(): daily_countdown.start()
    if not weekly_ranking_announcement.is_running(): weekly_ranking_announcement.start()
    if not check_lazy_users.is_running(): check_lazy_users.start()
    print(f'Logged in as {bot.user}')

@bot.command()
async def rival(ctx, member: discord.Member):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO rivals (user_id, rival_id) VALUES (?, ?)", (ctx.author.id, member.id))
    conn.commit()
    conn.close()
    await ctx.send(f"🔥 {member.display_name}さんをライバルに設定しました！")

@bot.event
async def on_message(message):
    if message.author.bot: return
    await bot.process_commands(message)

    # --- 順位表示/特例/通常記録などの処理 (内容は一切変えていません) ---
    if message.content == "順位" and "勉強時間報告" in message.channel.name:
        now = datetime.now(JST)
        monday = (now - timedelta(days=now.weekday())).strftime('%Y-%m-%d')
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT user_id, SUM(minutes) as total FROM study_logs WHERE date >= ? GROUP BY user_id ORDER BY total DESC", (monday,))
        ranking = c.fetchall()
        conn.close()
        found_index = -1
        for i, (user_id, total) in enumerate(ranking):
            if user_id == message.author.id:
                found_index = i
                break
        if found_index == -1:
            await message.channel.send("まだ今週の記録がないようです！")
        else:
            current_total = ranking[found_index][1]
            msg = f"📊 {message.author.mention} さんの順位は **{found_index + 1}位** ({current_total/60:.1f}h) です！"
            await message.channel.send(msg)
        return

    # 勉強時間解析
    minutes = 0
    hr_match = re.search(r'(\d+(\.\d+)?)時間', message.content)
    min_match = re.search(r'(\d+)分', message.content)
    if hr_match: minutes += float(hr_match.group(1)) * 60
    if min_match: minutes += int(min_match.group(1))

    if minutes > 0:
        update_last_seen(message.author.id)
        now = datetime.now(JST)
        monday_str = (now - timedelta(days=now.weekday())).strftime('%Y-%m-%d')
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO study_logs VALUES (?, ?, ?)", (message.author.id, int(minutes), now.strftime('%Y-%m-%d')))
        conn.commit()
        c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id=? AND date >= ?", (message.author.id, monday_str))
        my_weekly_mins = (c.fetchone()[0] or 0)
        c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id=?", (message.author.id,))
        total_mins = (c.fetchone()[0] or 0)
        conn.close()
        current_rank = await update_roles(message.author, my_weekly_mins/60)
        embed = discord.Embed(title="📝 学習記録完了", color=discord.Color.green())
        embed.add_field(name="今回の記録", value=f"{int(minutes)}分", inline=False)
        embed.add_field(name="📅 今週の合計", value=f"{my_weekly_mins/60:.1f}時間", inline=True)
        embed.add_field(name="🎖️ ランク", value=current_rank, inline=False)
        await message.channel.send(embed=embed)

bot.run(TOKEN)
