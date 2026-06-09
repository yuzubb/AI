import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
from brain import Brain

load_dotenv()

ADMIN_ID = 1455012819291340862  

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
brain = Brain()

# フィードバック用：直前の返答メッセージID記録
last_reply: dict[str, int] = {}

def is_admin(user: discord.User | discord.Member) -> bool:
    return user.id == ADMIN_ID


@bot.event
async def on_ready():
    print(f"起動: {bot.user}")
    await bot.tree.sync()
    print("スラッシュコマンド同期完了")

    # 起動時に承認待ちがあれば管理者にDM通知
    pending = brain.db.get_pending_proposals()
    if pending:
        try:
            admin = await bot.fetch_user(ADMIN_ID)
            await admin.send(f"承認待ちのコマンド提案が{len(pending)}件あります。`/proposals`で確認してください。")
        except:
            pass


@bot.event
async def on_message(message):
    if message.author.bot:
        return
    await bot.process_commands(message)
    if message.content.startswith("!"):
        return

    async with message.channel.typing():
        reply = await brain.think(
            user_id=str(message.author.id),
            username=message.author.display_name,
            text=message.content,
        )

    sent = await message.channel.send(reply[:1900])
    await sent.add_reaction("👍")
    await sent.add_reaction("👎")
    last_reply[str(message.author.id)] = sent.id

    # 新しいコマンド提案が生まれたら管理者にDM通知
    pending = brain.db.get_pending_proposals()
    new_proposals = [p for p in pending]
    if new_proposals:
        try:
            admin = await bot.fetch_user(ADMIN_ID)
            latest = new_proposals[0]
            # 同じIDで通知済みか簡易チェックは省略（起動後初回のみ通知）
        except:
            pass


@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    uid = str(user.id)
    if last_reply.get(uid) != reaction.message.id:
        return
    if str(reaction.emoji) == "👍":
        brain.feedback(uid, True)
    elif str(reaction.emoji) == "👎":
        brain.feedback(uid, False)


# ========== 通常コマンド ==========

@bot.tree.command(name="search", description="ネット検索して答える")
async def search_cmd(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    reply = await brain.think(
        user_id=str(interaction.user.id),
        username=interaction.user.display_name,
        text=query,
        force_search=True,
    )
    await interaction.followup.send(reply[:1900])


@bot.tree.command(name="forget", description="自分の会話履歴を削除")
async def forget(interaction: discord.Interaction):
    brain.db.clear_history(str(interaction.user.id))
    await interaction.response.send_message("会話履歴を削除しました", ephemeral=True)


@bot.tree.command(name="stats", description="AIの学習状況を確認")
async def stats(interaction: discord.Interaction):
    s = brain.db.get_stats(str(interaction.user.id))
    msg = (
        f"総会話数: {s['total']}\n"
        f"あなたとの会話: {s['user_total']}\n"
        f"学習済み知識: {s['knowledge']}件\n"
        f"ブロック済みパターン: {s['bad_patterns']}件\n"
        f"動的コマンド数: {s['dynamic_commands']}個\n"
        f"承認待ち提案: {s['pending_proposals']}件\n"
        f"平均フィードバックスコア: {s['avg_score']}"
    )
    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="evolution", description="AIの進化ログを見る")
async def evolution(interaction: discord.Interaction):
    logs = brain.db.get_evolution_log(limit=10)
    if not logs:
        await interaction.response.send_message("まだ進化ログがありません", ephemeral=True)
        return
    lines = [f"`{l[2][:16]}` {l[0]}: {l[1]}" for l in logs]
    await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)


@bot.tree.command(name="commands_list", description="追加されたコマンド一覧を見る")
async def commands_list(interaction: discord.Interaction):
    cmds = brain.db.get_approved_commands()
    if not cmds:
        await interaction.response.send_message("まだ動的コマンドはありません", ephemeral=True)
        return
    lines = [f"`/{r[0]}` - {r[1]}" for r in cmds]
    await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)


@bot.tree.command(name="cmd", description="追加されたコマンドを実行する")
async def run_cmd(interaction: discord.Interaction, name: str, input: str = ""):
    await interaction.response.defer()
    result = await brain.run_dynamic_command(name, input)
    if result is None:
        await interaction.followup.send(f"`/{name}` というコマンドは存在しません。`/commands_list`で確認してください。")
    else:
        await interaction.followup.send(result[:1900])


# ========== 管理者専用コマンド ==========

@bot.tree.command(name="proposals", description="[管理者] コマンド提案一覧を確認")
async def proposals(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        await interaction.response.send_message("管理者専用コマンドです", ephemeral=True)
        return
    pending = brain.db.get_pending_proposals()
    if not pending:
        await interaction.response.send_message("承認待ちの提案はありません", ephemeral=True)
        return
    lines = []
    for p in pending:
        pid, name, desc, tmpl, ctx = p
        lines.append(f"**ID:{pid}** `/{name}` - {desc}\n　理由: {ctx}\n　返答: {tmpl[:80]}...")
    await interaction.response.send_message("\n\n".join(lines)[:1900], ephemeral=True)


@bot.tree.command(name="approve", description="[管理者] コマンド提案を承認する")
async def approve(interaction: discord.Interaction, proposal_id: int):
    if not is_admin(interaction.user):
        await interaction.response.send_message("管理者専用コマンドです", ephemeral=True)
        return
    result = brain.db.approve_proposal(proposal_id, str(interaction.user.id))
    if result is None:
        await interaction.response.send_message(f"ID:{proposal_id} の提案が見つかりません（既に処理済みか存在しない）", ephemeral=True)
        return
    brain.db.log_evolution("command_approved", f"/{result['name']}: {result['description']}")
    # コマンドツリーを再同期
    await bot.tree.sync()
    await interaction.response.send_message(
        f"`/{result['name']}` を承認しました。\n説明: {result['description']}\n`/cmd {result['name']}` で使えます。",
        ephemeral=True
    )


@bot.tree.command(name="reject", description="[管理者] コマンド提案を却下する")
async def reject(interaction: discord.Interaction, proposal_id: int):
    if not is_admin(interaction.user):
        await interaction.response.send_message("管理者専用コマンドです", ephemeral=True)
        return
    brain.db.reject_proposal(proposal_id)
    brain.db.log_evolution("command_rejected", f"proposal_id={proposal_id}")
    await interaction.response.send_message(f"ID:{proposal_id} の提案を却下しました", ephemeral=True)


@bot.tree.command(name="delete_cmd", description="[管理者] 動的コマンドを削除する")
async def delete_cmd(interaction: discord.Interaction, name: str):
    if not is_admin(interaction.user):
        await interaction.response.send_message("管理者専用コマンドです", ephemeral=True)
        return
    brain.db.delete_dynamic_command(name)
    brain.db.log_evolution("command_deleted", f"/{name}")
    await bot.tree.sync()
    await interaction.response.send_message(f"`/{name}` を削除しました", ephemeral=True)

bot.run(os.getenv("DISCORD_TOKEN"))
