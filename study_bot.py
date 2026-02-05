import discord
from discord.ext import commands, tasks
import sqlite3
from datetime import datetime, timedelta, timezone
import re
import os
import asyncio
import matplotlib.pyplot as plt

# --- 1. 基本設定 ---
TOKEN = os.getenv('TOKEN')
JST = timezone(timedelta(hours=9)) 
KYOTSU_TEST_DATE = datetime(2027, 1, 16, tzinfo=JST)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True 
bot = commands.Bot(command_prefix="!", intents=intents)

DB_PATH = 'study_data.db'

# --- 2. データベース初期化 ---
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
        except: return f"{target_role_name}(権限不足)"
    return target_role_name

# --- 4. 音声再生用関数 ---
async def play_audio(vc, filename):
    if vc and vc.is_connected():
        if not os.path.exists(filename):
            print(f"Error: {filename} が見つかりません。")
            return
        source = discord.FFmpegPCMAudio(filename)
        vc.play(source)
        while vc.is_playing():
            await asyncio.sleep(1)

# --- 5. コマンド: ポモドーロタイマー (25分終了時に音) ---
@bot.command()
async def pomodoro(ctx):
    if not ctx.author.voice:
        await ctx.send("🍅 まずはボイスチャンネルに入ってください！")
        return

    channel = ctx.author.voice.channel
    try:
        vc = await channel.connect()
    except discord.ClientException:
        vc = ctx.voice_client 

    await ctx.send(f"🍅 **ポモドーロタイマー開始！**\n25分間の集中タイムです。終了時に音でお知らせします。")

    try:
        while True:
            # 25分間待機 (1500秒)
            await asyncio.sleep(1500)

            # 25分経過時に音を鳴らす
            await play_audio(vc, "start.mp3")
            
            members = channel.members
            mentions = " ".join([m.mention for m in members])
            await ctx.send(f"{mentions}\n☕ **25分経過しました！5分間の休憩に入ってください。**")
            
            # 5分間休憩待機 (300秒)
            await asyncio.sleep(300)

            await ctx.send(f"{mentions}\n🚀 **休憩終了！次の25分間、集中しましょう！**")
            
    except Exception as e:
        print(f"タイマー停止: {e}")
        if ctx.voice_client:
            await ctx.voice_client.disconnect()

@bot.command()
async def stop(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("🍅 タイマーを終了しました。お疲れ様でした！")

# --- 6. 毎日 0:00 カウントダウン ---
@tasks.loop(seconds=60)
async def daily_countdown():
    now = datetime.now(JST)
    if now.hour == 0 and now.minute == 0:
        days_left = max(0, (KYOTSU_TEST_DATE - now).days)
        for guild in bot.guilds:
            channel = discord.utils.get(guild.channels, name="共通テストカウントダウン")
            if channel:
                await channel.send(f"📅 **{now.strftime('%m月%d日')}**\n共通テストまであと **{days_left}日** です！🔥")

# --- 7. 毎週月曜 0:00 順位発表 & グラフ ---
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
        plt.xlabel('Study Hours')
        plt.title('Weekly Study Ranking')
        plt.tight_layout()
        plt.savefig('ranking.png')
        plt.close()

        msg = "🏆 **週間 順位発表** 🏆\n━━━━━━━━━━━━━━━━━━\n"
        for i, (user_id, total_min) in enumerate(ranking, 1):
            msg += f"{i}位: <@{user_id}> ― **{total_min/60:.1f}時間**\n"

        for guild in bot.guilds:
            channel = discord.utils.get(guild.channels, name="順位決定戦")
            if channel: await channel.send(msg, file=discord.File('ranking.png'))

# --- 8. サボり防止 ---
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
                if channel: await channel.send(f"<@{user_id}> 勉強してください！！勝負はここからです！！")

# --- 9. コマンド: ライバル設定 ---
@bot.command()
async def rival(ctx, member: discord.Member):
    if member.id == ctx.author.id:
        await ctx.send("自分自身をライバルにはできません！")
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO rivals (user_id, rival_id) VALUES (?, ?)", (ctx.author.id, member.id))
    conn.commit()
    conn.close()
    await ctx.send(f"🔥 {member.display_name}さんをライバルに設定しました！")

# --- 10. メイン処理 ---
@bot.event
async def on_ready():
    init_db()
    if not daily_countdown.is_running(): daily_countdown.start()
    if not weekly_ranking_announcement.is_running(): weekly_ranking_announcement.start()
    if not check_lazy_users.is_running(): check_lazy_users.start()
    print(f'Logged in as {bot.user}')

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
        conn.commit()

        monday_str = (now - timedelta(days=now.weekday())).strftime('%Y-%m-%d')
        c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id=? AND date >= ?", (message.author.id, monday_str))
        my_weekly_mins = (c.fetchone()[0] or 0)
        
        c.execute("SELECT rival_id FROM rivals WHERE user_id=?", (message.author.id,))
        rival_row = c.fetchone()
        rival_msg = ""
        if rival_row:
            c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id=? AND date >= ?", (rival_row[0], monday_str))
            rival_weekly_mins = (c.fetchone()[0] or 0)
            diff = (my_weekly_mins - rival_weekly_mins) / 60
            rival_msg = f"\n🔥 ライバルに **{diff:.1f}時間** {'リード中！' if diff >= 0 else '負けています！'}"

        conn.close()
        current_rank = await update_roles(message.author, my_weekly_mins/60)
        await message.channel.send(f"📝 **{message.author.display_name}**\n✅ 今回: {int(minutes)}分\n📅 今週: {my_weekly_mins/60:.1f}h\n🎖️ ランク: {current_rank}{rival_msg}")

    if message.content == "順位" and (message.channel and message.channel.name == "勉強時間報告"):
        now = datetime.now(JST)
        monday = (now - timedelta(days=now.weekday())).strftime('%Y-%m-%d')
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT user_id, SUM(minutes) as total FROM study_logs WHERE date >= ? GROUP BY user_id ORDER BY total DESC", (monday,))
        ranking = c.fetchall()
        conn.close()

        for i, (user_id, total) in enumerate(ranking, 1):
            if user_id == message.author.id:
                res = f"📊 **{i}位** ({total/60:.1f}h)\n"
                res += "🥇 独走中！" if i == 1 else f"あと {(ranking[i-2][1]-total)/60:.1f}h で{i-1}位！"
                await message.channel.send(res)
                break

bot.run(TOKEN)
