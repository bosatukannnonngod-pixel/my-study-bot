import discord
from discord.ext import commands
import sqlite3
from datetime import datetime, timedelta, timezone
import re
import os

# --- 1. 基本設定 ---
TOKEN = os.getenv('TOKEN')
JST = timezone(timedelta(hours=9)) # 日本時間

# 共通テストの日付（2027年1月16日）
KYOTSU_TEST_DATE = datetime(2027, 1, 16, tzinfo=JST)

# ランクのしきい値
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

DB_PATH = '/tmp/study_data.db'

# --- 2. データベースの初期化 ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS study_logs
                 (user_id INTEGER, minutes INTEGER, date TEXT)''')
    conn.commit()
    conn.close()

# --- 3. 解析・計算補助 ---
def parse_duration(text):
    minutes = 0
    hr_match = re.search(r'(\d+(\.\d+)?)時間', text)
    min_match = re.search(r'(\d+)分', text)
    if hr_match:
        minutes += float(hr_match.group(1)) * 60
    if min_match:
        minutes += int(min_match.group(1))
    return int(minutes)

# --- 4. ロールの更新・付与機能 ---
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

    # サーバー内のロールオブジェクトを取得
    all_study_role_names = list(ROLES_CONFIG.values())
    new_role = discord.utils.get(member.guild.roles, name=target_role_name)
    
    if new_role:
        try:
            # 他のランク用ロールを持っていたら削除
            roles_to_remove = [r for r in member.roles if r.name in all_study_role_names and r.name != target_role_name]
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove)
            
            # ターゲットのロールを付与
            if new_role not in member.roles:
                await member.add_roles(new_role)
        except Exception as e:
            print(f"ロール付与エラー (Botの権限順位を確認してください): {e}")
            return f"{target_role_name} (付与失敗)"
            
    return target_role_name

# --- 5. メインイベント ---
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
        now = datetime.now(JST)
        
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            # 1. 記録を保存
            c.execute("INSERT INTO study_logs VALUES (?, ?, ?)", 
                      (user_id, duration, now.strftime('%Y-%m-%d')))
            conn.commit()
            
            # 2. 今週の月曜日からの時間を計算
            monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            start_date = monday.strftime('%Y-%m-%d')
            
            # 自分の時間
            c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id=? AND date >= ?", (user_id, start_date))
            weekly_min = c.fetchone()[0] or 0
            
            c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id=?", (user_id,))
            total_min = c.fetchone()[0] or 0
            
            # 3. ランキング計算（今週分）
            c.execute("""SELECT user_id, SUM(minutes) as total FROM study_logs 
                         WHERE date >= ? GROUP BY user_id ORDER BY total DESC""", (start_date,))
            ranking_list = c.fetchall()
            
            rank_num = 0
            for i, row in enumerate(ranking_list):
                if row[0] == user_id:
                    rank_num = i + 1
                    break
            
            conn.close()

            weekly_hrs = weekly_min / 60
            total_hrs = total_min / 60

            # 4. 共通テストカウントダウン
            diff = KYOTSU_TEST_DATE - now
            days_left = diff.days if diff.days >= 0 else 0

            # 5. ロール更新
            current_rank = await update_roles(message.author, weekly_hrs)

            # 6. メッセージ送信
            await message.channel.send(
                f"📝 **{message.author.display_name}さんの学習を記録！**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"✅ 今回の学習: {duration}分\n"
                f"📅 今週の合計: **{weekly_hrs:.1f}時間** (サーバー内 **{rank_num}位**)\n"
                f"🏆 全累計時間: **{total_hrs:.1f}時間**\n"
                f"🎖️ 現在ランク: **{current_rank}**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🗓️ 共通テストまであと **{days_left}日** ！"
            )
        except Exception as e:
            await message.channel.send(f"⚠️ エラーが発生しました: {e}")

    await bot.process_commands(message)

if __name__ == "__main__":
    bot.run(TOKEN)
