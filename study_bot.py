# --- データベースから全累計を取得 ---
        c = conn.cursor()
        c.execute("SELECT SUM(minutes) FROM study_logs WHERE user_id=?", (message.author.id,))
        total_min = c.fetchone()[0] or 0
        total_hrs = total_min / 60
        conn.close()

        # --- ロール更新 ---
        current_rank = await update_roles(message.author, weekly_hrs)
        
        # --- カウントダウン送信 ---
        countdown_channel = discord.utils.get(message.guild.channels, name="共通テストカウントダウン")
        days_left = max(0, (KYOTSU_TEST_DATE - now).days)
        if countdown_channel:
            await countdown_channel.send(f"📢 **カウントダウン更新**\n{message.author.display_name}さんが勉強したよ！\n共通テストまであと **{days_left}日** 📅")

        # --- 本人への返信（累計を復活！） ---
        await message.channel.send(
            f"📝 **{message.author.display_name}さんの学習記録**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"✅ 今回: {duration}分\n"
            f"📅 今週: **{weekly_hrs:.1f}時間** ({rank_num}位)\n"
            f"🏆 全累計: **{total_hrs:.1f}時間**\n"
            f"🎖️ ランク: **{current_rank}**"
        )
