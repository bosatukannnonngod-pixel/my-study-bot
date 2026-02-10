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
import shutil
import random

# --- 1. Koyeb/Hosting Keep Alive ---
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
    # group_id制にアップデート
    c.execute('CREATE TABLE IF NOT EXISTS rivals (user_id INTEGER PRIMARY KEY, group_id INTEGER)')
    
    c.execute('''CREATE TABLE IF NOT EXISTS bot_events 
                 (status TEXT, message TEXT, target_hp REAL, current_hp REAL, 
                  deadline TEXT, last_event_date TEXT,
                  config_difficulty REAL DEFAULT 20.0,
                  config_frequency INTEGER DEFAULT 7)''')
    
    c.execute("SELECT COUNT(*) FROM bot_events")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO bot_events (status, message, target_hp, current_hp, deadline, last_event_date) VALUES ('normal', '', 0, 0, '', ?)", (datetime.now(JST).isoformat(),))
    
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

# --- 5. 音声再生 ---
async def play_audio(vc, filename):
    if not vc or not vc.is_connected() or not os.path.exists(filename):
        return
    try:
        if vc.is_playing(): vc.stop()
        ffmpeg_exe = shutil.which("ffmpeg")
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(filename, executable=ffmpeg_exe or "ffmpeg", options="-vn"))
        source.volume = 0.25 
        vc.play(source)
    except Exception as e:
        print(f"❌ Audio Play Error: {e}")

# --- 6. トラブルイベント管理 ---
@tasks.loop(hours=1)
async def check_bot_event():
    now = datetime.now(JST)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT status, message, target_hp, current_hp, deadline, last_event_date, config_difficulty, config_frequency FROM bot_events")
    event_data = c.fetchone()
    if not event_data: return
    
    status, msg, target_hp, current_hp, deadline, last_date, config_diff, config_freq = event_data

    if status == 'trouble':
        if now > datetime.fromisoformat(deadline):
            c.execute("UPDATE bot_events SET status='normal', last_event_date=?", (now.isoformat(),))
            conn.commit()
            for guild in bot.guilds:
                ch = discord.utils.get(guild.channels, name="勉強時間報告")
                if ch: await ch.send("⏰ トラブルの期限が過ぎてしまいました…")
    elif status == 'normal':
        last_dt = datetime.fromisoformat(last_date)
        if (now - last_dt).days >= config_freq:
            troubles = ["池の中に落ちちゃいました！", "怖いワニたちに囲まれます！！", "課題が多すぎて故障しそうです！！", "プリンを作りましょう！！", "海に落ちちゃいました助けて！"]
            new_msg = random.choice(troubles)
            hp = config_diff 
            new_deadline = (now + timedelta(days=3)).isoformat()
            c.execute("UPDATE bot_events SET status='trouble', message=?, target_hp=?, current_hp=?, deadline=?", (new_msg, hp, hp, new_deadline))
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
    if ctx.voice_client:
        vc = ctx.voice_client
        if vc.channel != channel: await vc.move_to(channel)
    else:
        vc = await channel.connect()

    active_pomodoros[ctx.guild.id] = True
    await ctx.send("🍅 **ポモドーロ開始！**")
    await play_audio(vc, "start.mp3")

    try:
        while active_pomodoros.get(ctx.guild.id):
            await asyncio.sleep(1500) # 25分
            if not active_pomodoros.get(ctx.guild.id): break
            await play_audio(ctx.voice_client, "start.mp3")
            await ctx.send("☕ **休憩タイム(5分)です。**")
            await asyncio.sleep(300) # 5分
            if not active_pomodoros.get(ctx.guild.id): break
            await play_audio(ctx.voice_client, "start.mp3")
            await ctx.send("🚀 **集中タイム再開！**")
    except: pass

@bot.command()
async def stop(ctx):
    active_pomodoros[ctx.guild.id] = False
    if ctx.voice_client: await ctx.voice_client.disconnect()
    await ctx.send("🍅 ポモドーロを終了しました。")

# --- 8. 週次ランキング発表 (全体ランキング ＋ 対戦グループ結果) ---
@tasks.loop(seconds=60)
async def weekly_ranking_announcement():
    now = datetime.now(JST)
    if now.weekday() == 0 and now.hour == 0 and now.minute == 0:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        monday_str = (now - timedelta(days=7)).strftime('%Y-%m-%d')

        # 1. 全体ランキング
        c.execute("SELECT user_id, SUM(minutes) FROM study_logs WHERE date >= ? GROUP BY user_id ORDER BY SUM(minutes) DESC", (monday_str,))
        overall = c.fetchall()
        if not overall:
            conn.close()
            return

        msg = "🏆 **今週の全体ランキング発表** 🏆\n"
        for i, (user_id, total_min) in enumerate(overall, 1):
            msg += f"{i}位: <@{user_id}> ({total_min/60:.1f}h)\n"

        # 2. 対戦グループ結果
        rival_msg = "\n🔥 **ライバル・大乱闘セクション** 🔥\n"
        c.execute("SELECT DISTINCT group_id FROM rivals")
        groups = c.fetchall()
        has_group = False
        for (g_id,) in groups:
            c.execute("SELECT user_id FROM rivals WHERE group_id = ?", (g_id,))
            m_ids = [row[0] for row in c.fetchall()]
            if len(m_ids) < 2: continue
            has_group = True
            res_list = []
            for mid in m_ids:
                c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id = ? AND date >= ?", (mid, monday_str))
                res_list.append((mid, c.fetchone()[0] or 0))
            res_list.sort(key=lambda x: x[1], reverse=True)
            rival_msg += f"\n⚔️ **グループ({g_id})内結果:**\n"
            for i, (mid, mins) in enumerate(res_list, 1):
                rival_msg += f"  {i}位: <@{mid}> ({mins/60:.1f}h)\n"

        final_announcement = msg + (rival_msg if has_group else "")
        c.execute("DELETE FROM rivals")
        conn.commit()
        conn.close()

        for guild in bot.guilds:
            channel = discord.utils.get(guild.channels, name="勉強時間報告")
            if channel: await channel.send(final_announcement)

# --- 9. 放置通知 ---
@tasks.loop(hours=1)
async def check_inactive_users():
    now = datetime.now(JST)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, last_datetime FROM last_seen")
    users = c.fetchall()
    conn.close()
    for user_id, last_dt_str in users:
        if now - datetime.fromisoformat(last_dt_str) > timedelta(days=3):
            for guild in bot.guilds:
                member = guild.get_member(user_id)
                if member and not member.bot:
                    ch = discord.utils.get(guild.channels, name="勉強時間報告")
                    if ch: await ch.send(f"⚠️ {member.mention} さん、3日間記録がありません！")

# --- 10. メインイベント処理 ---
@bot.event
async def on_message(message):
    if message.author.bot: return
    await bot.process_commands(message)

    # 難易度・頻度設定
    if message.content.startswith("!トラブル難易度"):
        val = float(re.search(r'(\d+)', message.content).group(1))
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("UPDATE bot_events SET config_difficulty=?", (val,)); conn.commit(); conn.close()
        await message.channel.send(f"⚙️ 難易度を **{val}時間** に設定しました。")
        return

    # ★ 対戦・大乱闘コマンド
    if "対戦" in message.content and len(message.mentions) >= 2:
        m1, m2 = message.mentions[0], message.mentions[1]
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT group_id FROM rivals WHERE user_id = ? OR user_id = ?", (m1.id, m2.id))
        res = c.fetchone()
        if res:
            existing_group = res[0]
            await message.channel.send(f"⚔️ **{m1.display_name}** か **{m2.display_name}** は既に対戦中です。統合しますか？（はい/いいえ）")
            def check(m): return m.author == message.author and m.content in ["はい", "いいえ"]
            try:
                ans = await bot.wait_for('message', check=check, timeout=30)
                if ans.content == "はい":
                    c.execute("INSERT OR REPLACE INTO rivals VALUES (?, ?)", (m1.id, existing_group))
                    c.execute("INSERT OR REPLACE INTO rivals VALUES (?, ?)", (m2.id, existing_group))
                    await message.channel.send("🔥 「大乱闘スタディブラザーズ」開始！")
                else:
                    new_g = int(datetime.now().timestamp())
                    c.execute("INSERT OR REPLACE INTO rivals VALUES (?, ?)", (m1.id, new_g))
                    c.execute("INSERT OR REPLACE INTO rivals VALUES (?, ?)", (m2.id, new_g))
                    await message.channel.send("⚔️ 個別対戦を開始！")
                conn.commit()
            except asyncio.TimeoutError: await message.channel.send("⌛ タイムアウト。")
        else:
            new_g = int(datetime.now().timestamp())
            c.execute("INSERT OR REPLACE INTO rivals VALUES (?, ?)", (m1.id, new_g))
            c.execute("INSERT OR REPLACE INTO rivals VALUES (?, ?)", (m2.id, new_g))
            conn.commit()
            await message.channel.send(f"⚔️ **{m1.display_name}** vs **{m2.display_name}** 開始！")
        conn.close(); return

    # ★ 特例機能 (今週のみ/累計のみ/減算対応)
    if message.content.startswith("特例") and message.mentions:
        target = message.mentions[0]
        hr = re.search(r'(-?\d+(\.\d+)?)時間', message.content)
        mn = re.search(r'(-?\d+)分', message.content)
        added = (float(hr.group(1))*60 if hr else 0) + (int(mn.group(1)) if mn else 0)
        if added != 0:
            now = datetime.now(JST)
            date = "2000-01-01" if "累計" in message.content and "今週" not in message.content else ("2099-12-31" if "今週のみ" in message.content else now.strftime('%Y-%m-%d'))
            conn = sqlite3.connect(DB_PATH); c = conn.cursor()
            c.execute("INSERT INTO study_logs VALUES (?, ?, ?)", (target.id, int(added), date))
            conn.commit(); conn.close()
            await message.channel.send(f"⚠️ 特例処理: {target.mention} に **{int(added)}分** 適用しました。")
            return

    # --- 通常報告 ---
    hr = re.search(r'(\d+(\.\d+)?)時間', message.content)
    mn = re.search(r'(\d+)分', message.content)
    minutes = (float(hr.group(1))*60 if hr else 0) + (int(mn.group(1)) if mn else 0)

    if minutes > 0:
        update_last_seen(message.author.id)
        now = datetime.now(JST); conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("INSERT INTO study_logs VALUES (?, ?, ?)", (message.author.id, int(minutes), now.strftime('%Y-%m-%d')))
        
        # トラブルHP
        c.execute("SELECT status, current_hp FROM bot_events"); status, hp = c.fetchone()
        t_msg = ""
        if status == 'trouble':
            new_hp = max(0, hp - (minutes/60))
            c.execute("UPDATE bot_events SET current_hp=?", (new_hp,))
            t_msg = f"\n\n✨ 解決！" if new_hp <= 0 else f"\n\n🛠️ あと **{new_hp:.1f}h**"
            if new_hp <= 0: c.execute("UPDATE bot_events SET status='normal', last_event_date=?", (now.isoformat(),))

        # 集計
        mon = (now - timedelta(days=now.weekday())).strftime('%Y-%m-%d')
        c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id=?", (message.author.id,)); total = c.fetchone()[0] or 0
        c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id=? AND date >= ?", (message.author.id, mon)); weekly = c.fetchone()[0] or 0
        
        # ライバル差分
        c.execute("SELECT group_id FROM rivals WHERE user_id=?", (message.author.id,))
        grp = c.fetchone()
        rival_msg = "未設定"
        if grp:
            c.execute("SELECT user_id FROM rivals WHERE group_id=? AND user_id!=?", (grp[0], message.author.id))
            others = c.fetchall()
            if others:
                diff_list = []
                for (oid,) in others:
                    c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id=? AND date >= ?", (oid, mon))
                    diff_list.append((oid, c.fetchone()[0] or 0))
                rid, rmins = min(diff_list, key=lambda x: abs(weekly - x[1]))
                r_user = bot.get_user(rid)
                rival_msg = f"{r_user.display_name if r_user else rid}と **{(weekly-rmins)/60:+.1f}h** 差"

        # ランキング順位
        c.execute("SELECT user_id, SUM(minutes) as s FROM study_logs WHERE date >= ? GROUP BY user_id ORDER BY s DESC", (mon,))
        rank = next((i for i, (u, _) in enumerate(c.fetchall(), 1) if u == message.author.id), 0)
        
        conn.commit(); conn.close()
        cur_rank = await update_roles(message.author, weekly/60)
        
        embed = discord.Embed(title="📝 記録完了", description=f"今回の記録: {int(minutes)}分{t_msg}", color=discord.Color.green())
        embed.add_field(name="📅 今週/🏆 累計", value=f"{weekly/60:.1f}h / {total/60:.1f}h")
        embed.add_field(name="📊 順位/🔥 ライバル", value=f"{rank}位 / {rival_msg}")
        embed.add_field(name="🎖️ ランク", value=cur_rank)
        await message.channel.send(embed=embed)

# --- 11. 起動・カウントダウン ---
@tasks.loop(seconds=60)
async def daily_countdown():
    now = datetime.now(JST)
    if now.hour == 0 and now.minute == 0:
        days = max(0, (KYOTSU_TEST_DATE - now).days)
        for guild in bot.guilds:
            ch = discord.utils.get(guild.channels, name="共通テストカウントダウン")
            if ch: await ch.send(f"📅 **{now.strftime('%m月%d日')}**\n共テまであと **{days}日**！")

@bot.event
async def on_ready():
    init_db()
    if not daily_countdown.is_running(): daily_countdown.start()
    if not check_bot_event.is_running(): check_bot_event.start()
    if not weekly_ranking_announcement.is_running(): weekly_ranking_announcement.start()
    if not check_inactive_users.is_running(): check_inactive_users.start()
    print(f'Logged in as {bot.user}')

bot.run(TOKEN)
