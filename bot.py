import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import asyncio
from datetime import datetime, timezone, timedelta

TOKEN = os.getenv("DISCORD_TOKEN", "")

# ========== НАСТРОЙКИ STAGE ROLE PLAY ==========
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
    "roles_give": 0,
    "applications": 0,
    "mod_logs": 0,
    "anti_nuke_logs": 0,
}
WHITELIST = []
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
                embed.set_thumbnail(url=interaction.user.display_avatar.url)
                view = ModerationView(user_id)
                await app_channel.send(embed=embed, view=view)
        await interaction.response.send_message(f"✅ Заявка на **{self.role_name}** отправлена на модерацию!", ephemeral=True)

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
            return await interaction.response.send_message("❌ У вас нет ролей.", ephemeral=True)
        await interaction.user.remove_roles(*roles_to_remove, reason="Снятие роли Stage RP")
        await interaction.response.send_message(f"✅ Сняты роли: {', '.join([r.name for r in roles_to_remove])}", ephemeral=True)

class CancelApplicationButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Отменить заявку", style=discord.ButtonStyle.danger, custom_id="cancel_app", emoji="🚫")
    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        db = load_db()
        if user_id not in db["applications"] or db["applications"][user_id]["status"] != "pending":
            return await interaction.response.send_message("❌ У вас нет активной заявки.", ephemeral=True)
        del db["applications"][user_id]
        save_db(db)
        await interaction.response.send_message("✅ Заявка отменена.", ephemeral=True)

class ModerationView(discord.ui.View):
    def __init__(self, app_user_id):
        super().__init__(timeout=None)
        self.app_user_id = app_user_id
        self.add_item(ApproveButton(app_user_id))
        self.add_item(DenyButton(app_user_id))
class ApproveButton(discord.ui.Button):
    def __init__(self, user_id):
        super().__init__(label="Одобрить", style=discord.ButtonStyle.success, custom_id=f"approve_{user_id}")
        self.user_id = user_id
    async def callback(self, interaction: discord.Interaction):
        db = load_db()
        app = db["applications"].get(self.user_id)
        if not app or app["status"] != "pending": return await interaction.response.send_message("❌ Заявка уже обработана", ephemeral=True)
        guild = interaction.guild
        user = guild.get_member(int(self.user_id))
        if not user: return await interaction.response.send_message("❌ Юзер не найден", ephemeral=True)
        role_id = GOS_ROLES.get(app["role_name"]) or OPG_ROLES.get(app["role_name"])
        role = guild.get_role(role_id) if role_id else None
        if not role: return await interaction.response.send_message("❌ Роль не настроена", ephemeral=True)
        await user.add_roles(role, reason=f"Одобрил {interaction.user}")
        app["status"] = "approved"
        app["moderator"] = str(interaction.user)
        save_db(db)
        try: await user.send(f"✅ Ваша заявка на **{app['role_name']}** одобрена!")
        except: pass
        await interaction.response.send_message(f"✅ Выдал {role.mention} → {user.mention}", ephemeral=True)
class DenyButton(discord.ui.Button):
    def __init__(self, user_id):
        super().__init__(label="Отклонить", style=discord.ButtonStyle.danger, custom_id=f"deny_{user_id}")
        self.user_id = user_id
    async def callback(self, interaction: discord.Interaction):
        db = load_db()
        app = db["applications"].get(self.user_id)
        if not app or app["status"] != "pending": return await interaction.response.send_message("❌ Заявка уже обработана", ephemeral=True)
        app["status"] = "denied"
        save_db(db)
        guild = interaction.guild
        user = guild.get_member(int(self.user_id))
        if user:
            try: await user.send(f"❌ Заявка на **{app['role_name']}** отклонена.")
            except: pass
        await interaction.response.send_message(f"❌ Заявка {self.user_id} отклонена", ephemeral=True)

@tree.command(name="setup", description="Создать панель выдачи ролей Stage RP")
@app_commands.default_permissions(administrator=True)
async def setup_slash(interaction: discord.Interaction):
    embed = discord.Embed(title="Выдача ролей | Stage Role Play", description=PANEL_TEXT, color=0x2b2d31)
    view = RoleApplicationView()
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ Панель создана!", ephemeral=True)

@tree.command(name="approve", description="Одобрить заявку")
@app_commands.default_permissions(manage_roles=True)
async def approve_slash(interaction: discord.Interaction, user: discord.Member):
    db = load_db()
    app = db["applications"].get(str(user.id))
    if not app or app["status"] != "pending": return await interaction.response.send_message("❌ Нет заявки", ephemeral=True)
    role_id = GOS_ROLES.get(app["role_name"]) or OPG_ROLES.get(app["role_name"])
    role = interaction.guild.get_role(role_id) if role_id else None
    if not role: return await interaction.response.send_message("❌ Роль не найдена", ephemeral=True)
    await user.add_roles(role, reason=f"Одобрил {interaction.user}")
    app["status"] = "approved"
    save_db(db)
    await interaction.response.send_message(f"✅ Выдал {role.mention} → {user.mention}", ephemeral=True)

@tree.command(name="deny", description="Отклонить заявку")
@app_commands.default_permissions(manage_roles=True)
async def deny_slash(interaction: discord.Interaction, user: discord.Member, reason: str):
    db = load_db()
    app = db["applications"].get(str(user.id))
    if not app or app["status"] != "pending": return await interaction.response.send_message("❌ Нет заявки", ephemeral=True)
    app["status"] = "denied"
    app["reason"] = reason
    save_db(db)
    await interaction.response.send_message(f"✅ {user.mention} отклонена: {reason}", ephemeral=True)

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

bot.run(TOKEN)
