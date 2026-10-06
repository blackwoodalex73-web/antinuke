import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import asyncio
from datetime import datetime, timezone, timedelta
from flask import Flask
from threading import Thread

TOKEN = os.getenv("DISCORD_TOKEN", "")

# ========== KEEP ALIVE FOR RENDER FREE WEB SERVICE ==========
app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "Stage Role Play Bot is alive! ✅ | /health for check"

@app_web.route('/health')
def health():
    return "OK", 200

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True
    t.start()

# ========== НАСТРОЙКИ STAGE ROLE PLAY ==========
# Вставь ID ролей! Включи режим разработчика -> ПКМ на роль -> Копировать ID
GOS_ROLES = {
    "Правительство": 0,
    "ФСБ": 0,
    "МВД г. Южный": 0,
    "МВД г. Арзамас": 0,
    "ВЧ": 0,
    "МЗ г. Южный": 0,
    "МЗ г. Арзамас": 0,
    "СМИ": 0,
}
OPG_ROLES = {
    "ОПГ": 0,
}

CHANNELS = {
    "roles_give": 0,      # канал где будет панель
    "applications": 0,    # канал куда летят заявки админам
    "mod_logs": 0,
    "anti_nuke_logs": 0,
}
WHITELIST = []  # ID кого не банит анти-нюк
CREATOR_ID = 875807854328692817

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
tree = bot.tree

def load_db():
    if os.path.exists("database.json"):
        with open("database.json", "r", encoding="utf-8") as f:
            return json.load(f)
    return {"applications": {}, "warns": {}, "backups": {}}

def save_db(data):
    with open("database.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

actions = {}

# ========== ТЕКСТ ПАНЕЛИ STAGE ROLE PLAY ==========
PANEL_TEXT = """🏛️ **Добро пожаловать в канал выдачи ролей для __сотрудников государственных структур__ проекта Stage Role Play!** 🏛️

🌟 **Ваша роль определяет ваш статус и приверженность проекту.** Присоединившись к одной из государственных структур, вы становитесь частью важного механизма, который поддерживает стабильность и развитие штата Stage. Выберите свою роль среди ключевых структур:

- **Правительство** — Руководящий орган, определяющий стратегическое направление штата
- **ФСБ** — Федеральная служба безопасности, хранители национальной безопасности
- **МВД (г. Южный или г. Арзамас)** — Министерство внутренних дел, охранители порядка и законности
- **ВЧ** — Воинская часть, воплощение силы и чести
- **МЗ (г. Южный или г. Арзамас)** — Министерство здравоохранения, защитники жизни и здоровья
- **СМИ** — Средства массовой информации, голоса объективности и правды

📜 **Как получить свою роль:**
1. **Выберите свою структуру** из списка внизу. Это ваш первый шаг к официальному признанию.

2. После выбора ваша заявка отправляется на **модерацию**. Ожидайте результата — роль будет выдана или причина отказа поступит в личные сообщения от бота.

⚠️ **Условия подачи заявки:**
- Допускается только __одна__ активная заявка на выдачу роли от одного участника.
- Нельзя подавать запрос на роль гос. структур, если у вас уже имеется активная роль структуры ОПГ, и наоборот.
- Вы можете иметь только одну роль в одной из структур (нельзя получить одновременно роль и ОПГ, и ГОС).

❌ Чтобы снять свою роль, нажмите на кнопку **'Снять роль'**.

🚫 Чтобы отменить заявку на выдачу роли, нажмите на кнопку **'Отменить заявку'**.

🤖 **Бот создан для Stage Role Play. По вопросам работы бота обращайтесь в ЛС.**
"""

class RoleApplicationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for name in GOS_ROLES.keys():
            self.add_item(RoleButton(name))
        self.add_item(RemoveRoleButton())
        self.add_item(CancelApplicationButton())

class RoleButton(discord.ui.Button):
    def __init__(self, role_name):
        super().__init__(label=role_name, style=discord.ButtonStyle.primary, custom_id=f"apply_{role_name}")
        self.role_name = role_name
    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        db = load_db()
        if user_id in db["applications"] and db["applications"][user_id]["status"] == "pending":
            return await interaction.response.send_message("❌ У вас уже есть активная заявка! Отмените ее кнопкой 'Отменить заявку'.", ephemeral=True)
        user_roles = [r.id for r in interaction.user.roles]
        has_gos = any(rid in user_roles for rid in GOS_ROLES.values() if rid != 0)
        has_opg = any(rid in user_roles for rid in OPG_ROLES.values() if rid != 0)
        if has_opg:
            return await interaction.response.send_message("❌ Нельзя подавать на ГОС если у вас есть роль ОПГ! Снимите ее сначала.", ephemeral=True)
        if has_gos:
            return await interaction.response.send_message("❌ У вас уже есть роль гос. структуры! Можно иметь только одну.", ephemeral=True)
        db["applications"][user_id] = {"role_name": self.role_name, "user_name": str(interaction.user), "user_id": interaction.user.id, "status": "pending", "time": datetime.now(timezone.utc).isoformat()}
        save_db(db)
        app_channel_id = CHANNELS["applications"]
        if app_channel_id != 0:
            app_channel = interaction.guild.get_channel(app_channel_id)
            if app_channel:
                embed = discord.Embed(title="📩 Новая заявка | Stage Role Play", color=0x2b2d31)
                embed.add_field(name="Пользователь", value=f"{interaction.user.mention} ({interaction.user.id})", inline=False)
                embed.add_field(name="Роль", value=self.role_name, inline=True)
                embed.add_field(name="Проект", value="Stage Role Play", inline=True)
                embed.set_thumbnail(url=interaction.user.display_avatar.url)
                embed.set_footer(text=f"ID: {user_id}")
                view = ModerationView(user_id)
                await app_channel.send(embed=embed, view=view)
        await interaction.response.send_message(f"✅ Ваша заявка на роль **{self.role_name}** отправлена на модерацию в Stage Role Play! Ожидайте.", ephemeral=True)

class RemoveRoleButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Снять роль", style=discord.ButtonStyle.secondary, custom_id="remove_role", emoji="❌")
    async def callback(self, interaction: discord.Interaction):
        roles_to_remove = []
        for role_id in list(GOS_ROLES.values()) + list(OPG_ROLES.values()):
            if role_id == 0: continue
            role = interaction.guild.get_role(role_id)
            if role and role in interaction.user.roles:
                roles_to_remove.append(role)
        if not roles_to_remove:
            return await interaction.response.send_message("❌ У вас нет ролей гос. структур.", ephemeral=True)
        try:
            await interaction.user.remove_roles(*roles_to_remove, reason="Снятие роли Stage RP")
            await interaction.response.send_message(f"✅ Сняты роли: {', '.join([r.name for r in roles_to_remove])}", ephemeral=True)
        except:
            await interaction.response.send_message("❌ Роль бота должна быть выше!", ephemeral=True)

class CancelApplicationButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Отменить заявку", style=discord.ButtonStyle.danger, custom_id="cancel_app", emoji="🚫")
    async def callback(self, interaction: discord.Interaction):
        db = load_db()
        user_id = str(interaction.user.id)
        if user_id not in db["applications"] or db["applications"][user_id]["status"] != "pending":
            return await interaction.response.send_message("❌ У вас нет активной заявки.", ephemeral=True)
        del db["applications"][user_id]
        save_db(db)
        await interaction.response.send_message("✅ Заявка отменена.", ephemeral=True)

class EditModal(discord.ui.Modal):
    def __init__(self, applicant_id, original_message):
        super().__init__(title="Редактировать заявку | Stage RP")
        self.applicant_id = applicant_id
        self.original_message = original_message
        db = load_db()
        current = db.get("applications", {}).get(applicant_id, {}).get("role_name", "")
        self.new_role = discord.ui.TextInput(label="Новая роль", placeholder="Правительство, ФСБ, МВД г. Южный...", default=current, max_length=100)
        self.comment = discord.ui.TextInput(label="Комментарий", required=False, max_length=200, style=discord.TextStyle.paragraph, placeholder="Причина изменения")
        self.add_item(self.new_role)
        self.add_item(self.comment)
    async def on_submit(self, interaction: discord.Interaction):
        db = load_db()
        app = db["applications"].get(self.applicant_id)
        if not app:
            return await interaction.response.send_message("❌ Заявка не найдена", ephemeral=True)
        old = app["role_name"]
        new = self.new_role.value.strip()
        if new not in GOS_ROLES and new not in OPG_ROLES:
            return await interaction.response.send_message(f"❌ Роль `{new}` не найдена. Доступные: {', '.join(GOS_ROLES.keys())}", ephemeral=True)
        app["role_name"] = new
        app["edited_by"] = str(interaction.user)
        app["edit_note"] = self.comment.value
        app["edit_time"] = datetime.now(timezone.utc).isoformat()
        save_db(db)
        embed = self.original_message.embeds[0]
        for i, f in enumerate(embed.fields):
            if f.name == "Роль":
                embed.set_field_at(i, name="Роль", value=f"{new} (было: {old})", inline=True)
                break
        embed.add_field(name="✏️ Отредактировано", value=f"{interaction.user.mention}\n{self.comment.value or 'Без комментария'}", inline=False)
        await self.original_message.edit(embed=embed, view=ModerationView(self.applicant_id))
        await interaction.response.send_message(f"✅ `{old}` → `{new}`", ephemeral=True)

class DenyModal(discord.ui.Modal, title="Отклонить заявку | Stage RP"):
    def __init__(self, applicant_id, original_message):
        super().__init__()
        self.applicant_id = applicant_id
        self.original_message = original_message
        self.reason = discord.ui.TextInput(label="Причина отказа", placeholder="Не по форме, нет 14 лет...", max_length=500, required=True)
        self.add_item(self.reason)
    async def on_submit(self, interaction: discord.Interaction):
        db = load_db()
        app = db["applications"].get(self.applicant_id)
        if not app:
            return await interaction.response.send_message("Не найдена", ephemeral=True)
        app["status"] = "denied"
        app["reason"] = self.reason.value
        app["moderator"] = str(interaction.user)
        save_db(db)
        member = interaction.guild.get_member(int(self.applicant_id))
        if member:
            try:
                await member.send(f"❌ Ваша заявка на **{app['role_name']}** в **Stage Role Play** отклонена.\nПричина: {self.reason.value}")
            except:
                pass
        embed = self.original_message.embeds[0]
        embed.color = 0xff0000
        embed.add_field(name="Статус", value=f"❌ Отклонено {interaction.user.mention}\nПричина: {self.reason.value}", inline=False)
        await self.original_message.edit(embed=embed, view=None)
        await interaction.response.send_message("✅ Отклонена", ephemeral=True)

class ModerationView(discord.ui.View):
    def __init__(self, applicant_id):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id
    @discord.ui.button(label="Одобрить", style=discord.ButtonStyle.success, emoji="✅", custom_id="approve_btn")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        db = load_db()
        app = db["applications"].get(self.applicant_id)
        if not app or app["status"] != "pending":
            return await interaction.response.send_message("❌ Уже обработана", ephemeral=True)
        guild = interaction.guild
        member = guild.get_member(int(self.applicant_id))
        role_name = app["role_name"]
        role_id = GOS_ROLES.get(role_name) or OPG_ROLES.get(role_name)
        if role_id == 0:
            return await interaction.response.send_message(f"❌ ID роли {role_name} не настроен в коде! Вставь ID в GOS_ROLES", ephemeral=True)
        role = guild.get_role(role_id)
        if not role or not member:
            return await interaction.response.send_message("❌ Роль или пользователь не найден", ephemeral=True)
        try:
            await member.add_roles(role, reason=f"Одобрил {interaction.user} | Stage RP")
            app["status"] = "approved"
            app["moderator"] = str(interaction.user)
            save_db(db)
            try:
                await member.send(f"✅ Ваша заявка на **{role_name}** одобрена на **Stage Role Play**!")
            except:
                pass
            embed = interaction.message.embeds[0]
            embed.color = 0x00ff00
            embed.add_field(name="Статус", value=f"✅ Одобрено {interaction.user.mention}", inline=False)
            await interaction.message.edit(embed=embed, view=None)
            await interaction.response.send_message(f"✅ Выдал {role.mention} → {member.mention}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
    @discord.ui.button(label="Редактировать", style=discord.ButtonStyle.secondary, emoji="✏️", custom_id="edit_btn")
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_roles:
            return await interaction.response.send_message("❌ Только для админов", ephemeral=True)
        await interaction.response.send_modal(EditModal(self.applicant_id, interaction.message))
    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.danger, emoji="❌", custom_id="deny_btn")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DenyModal(self.applicant_id, interaction.message))

# ========== СЛЕШ КОМАНДЫ ==========
@tree.command(name="setup_roles", description="Создать панель выдачи ролей для Stage Role Play")
@app_commands.default_permissions(administrator=True)
async def setup_roles_slash(interaction: discord.Interaction):
    embed = discord.Embed(title="🏛️ Добро пожаловать в канал выдачи ролей для сотрудников государственных структур проекта Stage Role Play! 🏛️", description=PANEL_TEXT, color=0x2b2d31)
    view = RoleApplicationView()
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ Панель Stage Role Play создана!", ephemeral=True)

@tree.command(name="apps", description="Показать все активные заявки")
@app_commands.default_permissions(manage_roles=True)
async def apps_slash(interaction: discord.Interaction):
    db = load_db()
    pending = {k:v for k,v in db["applications"].items() if v["status"] == "pending"}
    if not pending:
        return await interaction.response.send_message("✅ Активных заявок нет", ephemeral=True)
    embed = discord.Embed(title="📋 Активные заявки | Stage Role Play", color=0x2b2d31)
    for uid, app in pending.items():
        edited = f" (ред. {app.get('edited_by')})" if "edited_by" in app else ""
        embed.add_field(name=f"{app['user_name']} - {app['role_name']}{edited}", value=f"<@{uid}> | ID: {uid} | {app['time'][:16]}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="edit_app", description="Отредактировать заявку пользователя")
@app_commands.describe(user="Пользователь", new_role="Новая роль")
@app_commands.choices(new_role=[app_commands.Choice(name=k, value=k) for k in list(GOS_ROLES.keys()) + list(OPG_ROLES.keys())])
@app_commands.default_permissions(manage_roles=True)
async def edit_app_slash(interaction: discord.Interaction, user: discord.Member, new_role: app_commands.Choice[str]):
    db = load_db()
    uid = str(user.id)
    if uid not in db["applications"] or db["applications"][uid]["status"] != "pending":
        return await interaction.response.send_message("❌ Нет активной заявки", ephemeral=True)
    old = db["applications"][uid]["role_name"]
    db["applications"][uid]["role_name"] = new_role.value
    db["applications"][uid]["edited_by"] = str(interaction.user)
    db["applications"][uid]["edit_time"] = datetime.now(timezone.utc).isoformat()
    save_db(db)
    await interaction.response.send_message(f"✅ {user.mention}: `{old}` → `{new_role.value}`", ephemeral=True)

@tree.command(name="approve", description="Одобрить заявку")
@app_commands.describe(user="Кому выдать роль")
@app_commands.default_permissions(manage_roles=True)
async def approve_slash(interaction: discord.Interaction, user: discord.Member):
    db = load_db()
    uid = str(user.id)
    app = db["applications"].get(uid)
    if not app or app["status"] != "pending":
        return await interaction.response.send_message("❌ Нет активной заявки", ephemeral=True)
    role_id = GOS_ROLES.get(app["role_name"]) or OPG_ROLES.get(app["role_name"])
    if role_id == 0:
        return await interaction.response.send_message("❌ ID роли не настроен", ephemeral=True)
    role = interaction.guild.get_role(role_id)
    if not role:
        return await interaction.response.send_message("❌ Роль не найдена", ephemeral=True)
    await user.add_roles(role, reason=f"Одобрил {interaction.user}")
    app["status"] = "approved"
    app["moderator"] = str(interaction.user)
    save_db(db)
    try:
        await user.send(f"✅ Ваша заявка на **{app['role_name']}** одобрена на Stage Role Play!")
    except:
        pass
    await interaction.response.send_message(f"✅ Выдал {role.mention} → {user.mention}", ephemeral=True)

@tree.command(name="deny", description="Отклонить заявку")
@app_commands.describe(user="Кому отклонить", reason="Причина")
@app_commands.default_permissions(manage_roles=True)
async def deny_slash(interaction: discord.Interaction, user: discord.Member, reason: str):
    db = load_db()
    uid = str(user.id)
    app = db["applications"].get(uid)
    if not app or app["status"] != "pending":
        return await interaction.response.send_message("❌ Нет активной заявки", ephemeral=True)
    app["status"] = "denied"
    app["reason"] = reason
    app["moderator"] = str(interaction.user)
    save_db(db)
    try:
        await user.send(f"❌ Ваша заявка на **{app['role_name']}** в Stage Role Play отклонена.\nПричина: {reason}")
    except:
        pass
    await interaction.response.send_message(f"✅ {user.mention} отклонена: {reason}", ephemeral=True)

@tree.command(name="ban", description="Забанить")
@app_commands.describe(user="Кого", reason="Причина")
@app_commands.default_permissions(ban_members=True)
async def ban_slash(interaction: discord.Interaction, user: discord.Member, reason: str = "Не указана"):
    await interaction.guild.ban(user, reason=f"{interaction.user}: {reason}")
    await interaction.response.send_message(f"🔨 {user} забанен. {reason}")

@tree.command(name="kick", description="Кикнуть")
@app_commands.describe(user="Кого", reason="Причина")
@app_commands.default_permissions(kick_members=True)
async def kick_slash(interaction: discord.Interaction, user: discord.Member, reason: str = "Не указана"):
    await user.kick(reason=reason)
    await interaction.response.send_message(f"✅ {user} кикнут.")

@tree.command(name="mute", description="Замутить")
@app_commands.describe(user="Кого", minutes="Минут", reason="Причина")
@app_commands.default_permissions(moderate_members=True)
async def mute_slash(interaction: discord.Interaction, user: discord.Member, minutes: int = 10, reason: str = "Не указана"):
    until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    await user.timeout(until, reason=reason)
    await interaction.response.send_message(f"✅ {user.mention} замучен на {minutes}м.")

@tree.command(name="clear", description="Очистить чат")
@app_commands.describe(amount="Кол-во до 100")
@app_commands.default_permissions(manage_messages=True)
async def clear_slash(interaction: discord.Interaction, amount: int = 10):
    if amount > 100: amount = 100
    await interaction.response.defer(ephemeral=True)
    await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"✅ Удалено {amount}", ephemeral=True)

@tree.command(name="backup", description="Сделать бекап ролей")
@app_commands.default_permissions(administrator=True)
async def backup_slash(interaction: discord.Interaction):
    guild = interaction.guild
    data = {"roles": []}
    for role in guild.roles:
        if role.is_default() or role.managed or role.is_bot_managed(): continue
        data["roles"].append({"name": role.name, "color": role.color.value, "permissions": role.permissions.value, "hoist": role.hoist, "mentionable": role.mentionable, "position": role.position})
    db = load_db()
    db["backups"] = db.get("backups", {})
    db["backups"][str(guild.id)] = data
    save_db(db)
    await interaction.response.send_message(f"✅ Бекап: {len(data['roles'])} ролей", ephemeral=True)

async def handle_nuke(guild, executor, action_type, deleted_obj):
    if executor.id in WHITELIST or executor.id == bot.user.id: return
    if executor.bot: return
    if executor.id == guild.owner_id: return
    now = datetime.now(timezone.utc)
    uid = executor.id
    if uid not in actions: actions[uid] = []
    actions[uid].append(now)
    actions[uid] = [t for t in actions[uid] if (now - t).total_seconds() < 60]
    if len(actions[uid]) >= 2:
        try:
            await guild.ban(executor, reason=f"Anti-Nuke {action_type}")
            db = load_db()
            backup = db.get("backups", {}).get(str(guild.id))
            if backup and action_type == "ROLE_DELETE":
                rd = next((r for r in backup["roles"] if r["name"] == deleted_obj.name), None)
                if rd:
                    await guild.create_role(name=rd["name"], colour=discord.Colour(rd["color"]), permissions=discord.Permissions(rd["permissions"]), hoist=rd["hoist"], mentionable=rd["mentionable"], reason="Anti-Nuke Restore")
            log_id = CHANNELS["anti_nuke_logs"]
            if log_id:
                ch = guild.get_channel(log_id)
                if ch:
                    await ch.send(embed=discord.Embed(title="🚨 АНТИ-НЮК | Stage RP", description=f"Нарушитель: {executor} ({executor.id})\nДействие: {action_type}\nОбъект: {deleted_obj.name}", color=0xff0000))
            actions[uid] = []
        except Exception as e:
            print(e)

@bot.event
async def on_guild_role_delete(role):
    await asyncio.sleep(1)
    try:
        async for entry in role.guild.audit_logs(limit=1, action=discord.AuditLogAction.role_delete):
            if (datetime.now(timezone.utc) - entry.created_at).total_seconds() < 5:
                await handle_nuke(role.guild, entry.user, "ROLE_DELETE", role)
    except: pass

@bot.event
async def on_guild_channel_delete(channel):
    await asyncio.sleep(1)
    try:
        async for entry in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
            if (datetime.now(timezone.utc) - entry.created_at).total_seconds() < 5:
                await handle_nuke(channel.guild, entry.user, "CHANNEL_DELETE", channel)
    except: pass

@bot.event
async def on_ready():
    print(f"✅ {bot.user} запущен | Stage Role Play")
    bot.add_view(RoleApplicationView())
    db = load_db()
    for app_id, app_data in db.get("applications", {}).items():
        if app_data["status"] == "pending":
            bot.add_view(ModerationView(app_id))
    try:
        synced = await tree.sync()
        print(f"Синхронизировано {len(synced)} слеш-команд")
    except Exception as e:
        print(e)

keep_alive()
bot.run(TOKEN)
