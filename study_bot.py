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
                if not active_pomodoros.get(ctx.guild.id): break
                await asyncio.sleep(1)
            
            # ループを抜ける条件を確認
            if not active_pomodoros.get(ctx.guild.id): break

            if ctx.voice_client:
                await play_audio(ctx.voice_client, "start.mp3")
                await ctx.send("☕ **休憩タイム(5分)です。**")
            
            for _ in range(300): # 5分休憩
                if not active_pomodoros.get(ctx.guild.id): break
                await asyncio.sleep(1)
            
            if not active_pomodoros.get(ctx.guild.id): break

            if ctx.voice_client:
                await play_audio(ctx.voice_client, "start.mp3")
                await ctx.send("🚀 **集中タイム再開！**")
    except Exception as e:
        print(f"Pomodoro Error: {e}")

@bot.command()
async def stop(ctx):
    # フラグを倒してループを止める
    active_pomodoros[ctx.guild.id] = False
    
    # ボイスチャンネルに接続している場合は切断する
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("🍅 ポモドーロを終了し、ボイスチャンネルから退出しました。")
    else:
        await ctx.send("🍅 ポモドーロを終了しました。")
