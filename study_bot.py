# --- 5. コマンド: ポモドーロタイマー (開始時と25分終了時に音を鳴らす) ---
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

    await ctx.send(f"🍅 **ポモドーロタイマー開始！**\n25分間の集中タイムです。開始時と終了時に音でお知らせします。")

    try:
        while True:
            # 1. 集中開始：音(start.mp3)を鳴らす
            await play_audio(vc, "start.mp3")
            
            # 25分間待機 (1500秒)
            await asyncio.sleep(1500)

            # 2. 25分経過（集中終了）：音(start.mp3)を鳴らす
            await play_audio(vc, "start.mp3")
            
            # 通知とメンション
            members = channel.members
            mentions = " ".join([m.mention for m in members])
            await ctx.send(f"{mentions}\n☕ **25分経過しました！5分間の休憩に入ってください。**")
            
            # 3. 5分間休憩待機 (300秒)
            await asyncio.sleep(300)

            # 4. 休憩終了の通知（音なし・次はまた開始音が鳴る）
            await ctx.send(f"{mentions}\n🚀 **5分経過！休憩終了です。次の25分間、集中しましょう！**")
            
    except Exception as e:
        print(f"タイマー停止: {e}")
        if ctx.voice_client:
            await ctx.voice_client.disconnect()
