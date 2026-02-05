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

# --- 5. 音声再生用関数 (音量指定版) ---
async def play_audio(vc, filename):
    if not vc or not vc.is_connected():
        return
    if not os.path.exists(filename):
        return

    try:
        if vc.is_playing():
            vc.stop()
        
        ffmpeg_exe = shutil.which("ffmpeg")
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(
            filename,
            executable=ffmpeg_exe or "ffmpeg",
            options="-vn"
        ))
        
        source.volume = 0.25 # 音量を 0.25 に固定
        
        vc.play(source)
        while vc.is_playing():
            await asyncio.sleep(1)
    except Exception as e:
        print(f"❌ Audio Play Error: {e}")

# --- 6. トラブルイベント管理タスク ---
@tasks.loop(hours=1)
async def check_bot_event():
    now = datetime.now(JST)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT status, message, target_hp, current_hp, deadline, last_event_date FROM bot_events")
    event_data = c.fetchone()
    if not event_data: return
    
    status, msg, target_hp, current_hp, deadline, last_date = event_data

    if status == 'trouble':
        if now > datetime.fromisoformat(deadline):
            c.execute("UPDATE bot_events SET status='normal', last_event_date=?", (now.isoformat(),))
            conn.commit()
            for guild in bot.guilds:
                ch = discord.utils.get(guild.channels, name="勉強時間報告")
                if ch: await ch.send("⏰ トラブルの期限が過ぎてしまいました…")

    elif status == 'normal':
        last_dt = datetime.fromisoformat(last_date)
        if (now - last_dt).days >= random.randint(7, 10):
            troubles = ["池の中に落ちちゃいました！", "怖いワニたちに囲まれます！！", "課題が多すぎて故障しそうです！！", "プリンを作りましょう！！"]
            new_msg = random.choice(troubles)
            hp = random.randint(15, 25)
            new_deadline = (now + timedelta(days=3)).isoformat()
            c.execute("UPDATE bot_events SET status='trouble', message=?, target_hp=?, current_hp=?, deadline=?", 
                      (new_msg, hp, hp, new_deadline))
            conn.commit()
            for guild in bot.guilds:
                ch = discord.utils.get(guild.channels, name="勉強時間報告")
                if ch: 
                    embed = discord.Embed(title="⚠️ トラブル発生！", description=f"**{new_msg}**", color=discord.Color.red())
                    embed.add_field(name="解決に必要な勉強量", value=f"{hp} 時間分")
                    await ch.send(embed=embed)
    conn.close()

# --- 7. ポモドーロ機能 ---
active_pomodoros = {}

@bot.command()
async def pomodoro(ctx):
    if not ctx.author.voice:
        await ctx.send("🍅 まずはボイスチャンネルに入ってください！")
        return
    channel = ctx.author.voice.channel
    try:
        if ctx.voice_client:
            vc = ctx.voice_client
            if vc.channel != channel: await vc.move_to(channel)
        else:
            vc = await channel.connect()
    except Exception as e:
        await ctx.send(f"⚠️ 接続エラー: {e}")
        return

    active_pomodoros[ctx.guild.id] = True
    await ctx.send("🍅 **ポモドーロ開始！**")
    await play_audio(vc, "start.mp3")

    try:
        while active_pomodoros.get(ctx.guild.id):
            for _ in range(1500): # 25分集中
                if not active_pomodoros.get(ctx.guild.id): return
                await asyncio.sleep(1)
            if ctx.voice_client:
                await play_audio(ctx.voice_client, "start.mp3")
                await ctx.send("☕ **休憩タイム(5分)です。**")
            for _ in range(300): # 5分休憩
                if not active_pomodoros.get(ctx.guild.id): return
                await asyncio.sleep(1)
            if ctx.voice_client:
                await play_audio(ctx.voice_client, "start.mp3")
                await ctx.send("🚀 **集中タイム再開！**")
    except Exception as e:
        print(f"Pomodoro Error: {e}")

@bot.command()
async def stop(ctx):
    active_pomodoros[ctx.guild.id] = False
    if ctx.voice_client: await ctx.voice_client.disconnect()
    await ctx.send("🍅 ポモドーロを終了しました。")

# --- 8. 週次ランキンググラフ作成 ---
@tasks.loop(seconds=60)
async def weekly_ranking_announcement():
    now = datetime.now(JST)
    # 月曜日 00:00 に実行
    if now.weekday() == 0 and now.hour == 0 and now.minute == 0:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        one_week_ago = (now - timedelta(days=7)).strftime('%Y-%m-%d')
        c.execute("SELECT user_id, SUM(minutes) FROM study_logs WHERE date >= ? GROUP BY user_id ORDER BY SUM(minutes) DESC", (one_week_ago,))
        ranking = c.fetchall()
        conn.close()
        
        if not ranking: return

        # グラフ作成
        names, hours = [], []
        for uid, mins in ranking[:10]: # 上位10名
            user = bot.get_user(uid)
            names.append(user.display_name if user else f"ID:{uid}")
            hours.append(mins / 60)

        plt.figure(figsize=(10, 6))
        plt.barh(names[::-1], hours[::-1], color='skyblue')
        plt.xlabel('Hours')
        plt.title(f'Weekly Study Ranking ({one_week_ago} to {now.strftime("%Y-%m-%d")})')
        plt.tight_layout()
        plt.savefig('weekly_ranking.png')
        plt.close()

        msg = "🏆 **週間ランキング発表** 🏆\n"
        for i, (user_id, total_min) in enumerate(ranking, 1):
            msg += f"{i}位: <@{user_id}> ({total_min/60:.1f}h)\n"

        for guild in bot.guilds:
            channel = discord.utils.get(guild.channels, name="勉強時間報告")
            if channel:
                await channel.send(msg, file=discord.File('weekly_ranking.png'))

# --- 9. イベント処理 (記録・トラブル解決・順位・ライバル比較・特例) ---
@bot.event
async def on_message(message):
    if message.author.bot: return
    await bot.process_commands(message)

    # --- 特例機能 (修正済み) ---
    if message.content.startswith("特例") and message.mentions:
        target_user = message.mentions[0]
        clean_content = message.content.replace(f"<@{target_user.id}>", "").replace(f"<@!{target_user.id}>", "")
        added_minutes = 0
        hr_match = re.search(r'(\d+(\.\d+)?)時間', clean_content)
        min_match = re.search(r'(\d+)分', clean_content)
        if hr_match: added_minutes += float(hr_match.group(1)) * 60
        if min_match: added_minutes += int(min_match.group(1))
        
        if added_minutes > 0:
            now = datetime.now(JST)
            record_date = "2000-01-01" if "累計" in clean_content else now.strftime('%Y-%m-%d')
            type_label = "🏆 累計のみ" if "累計" in clean_content else "📅 今週＋累計"
            
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            # メンションされたユーザーIDを指定して挿入
            c.execute("INSERT INTO study_logs VALUES (?, ?, ?)", (target_user.id, int(added_minutes), record_date))
            conn.commit()
            
            # 確認用の集計もメンションされたユーザーIDで行う
            monday_str = (now - timedelta(days=now.weekday())).strftime('%Y-%m-%d')
            c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id=? AND date >= ?", (target_user.id, monday_str))
            target_weekly = (c.fetchone()[0] or 0)
            c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id=?", (target_user.id, ))
            target_total = (c.fetchone()[0] or 0)
            conn.close()
            
            await message.channel.send(f"⚠️ **特例処理完了 ({type_label})**\n{target_user.mention} に **{int(added_minutes)}分** 追加しました。\n📊 今週合計: {target_weekly/60:.1f}h / 🏆 累計: {target_total/60:.1f}h")
            return

    # 通常解析
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
        
        # トラブル進行
        c.execute("SELECT status, current_hp FROM bot_events")
        status, current_hp = c.fetchone()
        trouble_msg = ""
        if status == 'trouble':
            new_hp = max(0, current_hp - (minutes / 60))
            c.execute("UPDATE bot_events SET current_hp=?", (new_hp,))
            if new_hp <= 0:
                c.execute("UPDATE bot_events SET status='normal', last_event_date=?", (now.isoformat(),))
                trouble_msg = "\n\n✨ **トラブル解決！助かりました！**"
            else:
                trouble_msg = f"\n\n🛠️ トラブル解決まであと **{new_hp:.1f}時間** 分！"
        
        monday_str = (now - timedelta(days=now.weekday())).strftime('%Y-%m-%d')
        c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id = ?", (message.author.id,))
        total_mins = c.fetchone()[0] or 0
        
        c.execute("SELECT user_id, SUM(minutes) as s FROM study_logs WHERE date >= ? GROUP BY user_id ORDER BY s DESC", (monday_str,))
        ranking = c.fetchall()
        my_rank = 0
        my_weekly_mins = 0
        for i, (uid, total) in enumerate(ranking, 1):
            if uid == message.author.id:
                my_rank = i
                my_weekly_mins = total
                break
        
        c.execute("SELECT rival_id FROM rivals WHERE user_id = ?", (message.author.id,))
        rival_data = c.fetchone()
        rival_msg = "未設定"
        if rival_data:
            rival_id = rival_data[0]
            c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id = ? AND date >= ?", (rival_id, monday_str))
            rival_mins = c.fetchone()[0] or 0
            diff = (my_weekly_mins - rival_mins) / 60
            rival_user = bot.get_user(rival_id)
            rival_name = rival_user.display_name if rival_user else f"ID:{rival_id}"
            rival_msg = f"{rival_name}に **{diff:.1f}h** リード！" if diff >= 0 else f"{rival_name}に **{abs(diff):.1f}h** 負けてる！"

        conn.commit()
        conn.close()

        current_rank_name = await update_roles(message.author, my_weekly_mins/60)
        
        embed = discord.Embed(title="📝 学習記録完了", description=f"今回の記録: {int(minutes)}分{trouble_msg}", color=discord.Color.green())
        embed.add_field(name="📅 今週の合計", value=f"{my_weekly_mins/60:.1f}時間", inline=True)
        embed.add_field(name="📚 累計学習時間", value=f"{total_mins/60:.1f}時間", inline=True)
        embed.add_field(name="📊 現在の順位", value=f"**{my_rank}位**", inline=True)
        embed.add_field(name="🔥 ライバルとの差", value=rival_msg, inline=True)
        embed.add_field(name="🎖️ ランク", value=current_rank_name, inline=True)
        await message.channel.send(embed=embed)

# --- 10. 起動と定期タスク ---
@tasks.loop(seconds=60)
async def daily_countdown():
    now = datetime.now(JST)
    if now.hour == 0 and now.minute == 0:
        days_left = max(0, (KYOTSU_TEST_DATE - now).days)
        for guild in bot.guilds:
            channel = discord.utils.get(guild.channels, name="共通テストカウントダウン")
            if channel: await channel.send(f"📅 **{now.strftime('%m月%d日')}**\n共通テストまであと **{days_left}日**！")

@bot.command()
async def rival(ctx, member: discord.Member):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO rivals (user_id, rival_id) VALUES (?, ?)", (ctx.author.id, member.id))
    conn.commit()
    conn.close()
    await ctx.send(f"🔥 {member.display_name}さんをライバルに設定しました！")

@bot.event
async def on_ready():
    init_db()
    if not daily_countdown.is_running(): daily_countdown.start()
    if not check_bot_event.is_running(): check_bot_event.start()
    if not weekly_ranking_announcement.is_running(): weekly_ranking_announcement.start()
    print(f'Logged in as {bot.user}')

bot.run(TOKEN)
