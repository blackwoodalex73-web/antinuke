# -*- coding: utf-8 -*-
"""
Stage Role Play | T3 Bot
discord.py 2.4.0 | Railway-ready | без Flask

ENV:
  DISCORD_TOKEN  - токен бота (обязательно, в коде токен НЕ хранится)
  DATA_DIR       - папка для config.json / database.json
                   (на Railway подключите Volume и укажите его путь, например /data)

Права бота: Administrator (или минимум Manage Roles, Manage Channels,
Ban Members, Kick Members, View Audit Log, Moderate Members, Manage Messages).
Intents: в Developer Portal включить SERVER MEMBERS INTENT.
Роль бота должна стоять ВЫШЕ выдаваемых ролей и ролей, которые он восстанавливает.
"""

import asyncio
import copy
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("stage-t3")

TOKEN = os.getenv("DISCORD_TOKEN")
DATA_DIR = os.getenv("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
DB_PATH = os.path.join(DATA_DIR, "database.json")

GOS_NAMES = ["Правительство", "ФСБ", "МВД-А", "МВД-Ю", "ВЧ", "МЗ-А", "МЗ-Ю", "СМИ"]

# ============================================================
#  CONFIG / DATABASE
# ============================================================
DEFAULT_CONFIG = {
    "panel": {"gif_url": "", "image_url": ""},
    "moderator_roles": [],
    "gos_roles": {name: 0 for name in GOS_NAMES},
    "opg_roles": [],
    "channels": {"roles_give": 0, "applications": 0, "logs": 0, "anti_nuke": 0},
    "anti_nuke": {"enabled": True, "limit": 2, "time_window": 60, "action": "ban"},
    "whitelist": [],
}
DEFAULT_DB = {"applications": {}, "warns": {}, "backups": {}}


def _deep_merge(default: dict, loaded: dict) -> dict:
    result = copy.deepcopy(default)
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _write_json(path: str, data: dict) -> None:
    """Атомарная запись: сначала tmp, потом replace - файл не побьётся при падении."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_json(path: str, default: dict) -> dict:
    if not os.path.exists(path):
        _write_json(path, default)
        return copy.deepcopy(default)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _deep_merge(default, json.load(f))
    except (json.JSONDecodeError, OSError) as e:
        log.error("Файл %s повреждён (%s), сохраняю копию и создаю новый", path, e)
        try:
            os.replace(path, path + ".corrupt")
        except OSError:
            pass
        _write_json(path, default)
        return copy.deepcopy(default)


def load_config() -> dict:
    return _read_json(CONFIG_PATH, DEFAULT_CONFIG)


def save_config() -> None:
    _write_json(CONFIG_PATH, config)


def load_db() -> dict:
    return _read_json(DB_PATH, DEFAULT_DB)


def save_db() -> None:
    _write_json(DB_PATH, db)


config = load_config()
db = load_db()

# ============================================================
#  BOT
# ============================================================
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents, help_command=None)
tree = bot.tree

COLOR_MAIN = 0x2B2D31
COLOR_OK = 0x57F287
COLOR_BAD = 0xED4245
COLOR_WARN = 0xFEE75C


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(text: str) -> str:
    return text.replace("`", "'").strip()


def is_moderator(member: discord.Member) -> bool:
    mods = config["moderator_roles"]
    return member.guild_permissions.manage_roles or any(r.id in mods for r in member.roles)


def has_gos(member: discord.Member) -> bool:
    ids = {v for v in config["gos_roles"].values() if v}
    return any(r.id in ids for r in member.roles)


def has_opg(member: discord.Member) -> bool:
    return any(r.id in config["opg_roles"] for r in member.roles)


def check_can_apply(member: discord.Member) -> Optional[str]:
    app = db["applications"].get(str(member.id))
    if app and app.get("status") == "pending":
        return "У вас уже есть заявка на рассмотрении. Дождитесь решения или отмените её."
    if has_gos(member):
        return "У вас уже есть роль государственной структуры. Сначала снимите её."
    if has_opg(member):
        return "Нельзя одновременно состоять в ОПГ и государственной структуре."
    return None


async def send_log(guild: discord.Guild, key: str, embed: discord.Embed) -> None:
    cid = config["channels"].get(key)
    channel = guild.get_channel(cid) if cid else None
    if channel:
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as e:
            log.warning("Не удалось отправить лог в %s: %s", key, e)


# ============================================================
#  ПАНЕЛЬ
# ============================================================
def build_panel_embed() -> discord.Embed:
    structures = "\n".join(f"• **{n}**" for n in config["gos_roles"].keys())
    desc = (
        "Добро пожаловать в канал выдачи ролей для сотрудников государственных "
        "структур **Stage Role Play**.\n\n"
        f"**Доступные структуры:**\n{structures}\n\n"
        "**Условия:**\n"
        "• Можно подать только одну заявку одновременно.\n"
        "• Нельзя состоять в ОПГ и государственной структуре одновременно.\n"
        "• Можно иметь только одну государственную роль.\n"
        "• NickName в заявке должен соответствовать паспорту.\n\n"
        "**Как получить роль:** выберите структуру в меню ниже и заполните форму.\n"
        "**Как снять роль:** нажмите кнопку «Снять роль».\n"
        "**Как отменить заявку:** нажмите кнопку «Отменить заявку»."
    )
    embed = discord.Embed(title="Выдача ролей | Государственные структуры", description=desc, color=COLOR_MAIN)
    panel = config["panel"]
    img = panel.get("gif_url") or panel.get("image_url")
    if img:
        embed.set_image(url=img)
    embed.set_footer(text="Stage Role Play")
    return embed


class GOSSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=n, value=n) for n in list(config["gos_roles"].keys())[:25]]
        super().__init__(
            custom_id="gos_select",
            placeholder="Выберите роль для категории Государственные структуры...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        role_name = self.values[0]
        member = interaction.user
        err = None
        if not isinstance(member, discord.Member):
            err = "Используйте панель на сервере."
        else:
            err = check_can_apply(member)
            if not err and not config["gos_roles"].get(role_name):
                err = "Эта роль ещё не настроена администрацией."
        if err:
            await interaction.response.send_message(err, ephemeral=True)
        else:
            await interaction.response.send_modal(RoleApplicationModal(role_name))
        # сбрасываем отображаемый выбор в меню
        try:
            await interaction.message.edit(view=GOSSelectView())
        except discord.HTTPException:
            pass


class RemoveRoleButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Снять роль", style=discord.ButtonStyle.danger, custom_id="gos_remove_role", row=1)

    async def callback(self, interaction: discord.Interaction):
        member = interaction.user
        ids = {v for v in config["gos_roles"].values() if v}
        to_remove = [r for r in member.roles if r.id in ids]
        if not to_remove:
            return await interaction.response.send_message("У вас нет роли государственной структуры.", ephemeral=True)
        try:
            await member.remove_roles(*to_remove, reason="Снятие роли по запросу")
        except discord.Forbidden:
            return await interaction.response.send_message("У бота недостаточно прав, обратитесь к администрации.", ephemeral=True)
        await interaction.response.send_message(
            "Роль снята: " + ", ".join(r.name for r in to_remove), ephemeral=True
        )
        e = discord.Embed(title="Роль снята", color=COLOR_WARN, timestamp=datetime.now(timezone.utc))
        e.add_field(name="Пользователь", value=f"{member.mention} (`{member.id}`)")
        e.add_field(name="Роли", value=", ".join(r.name for r in to_remove))
        await send_log(interaction.guild, "logs", e)


class CancelApplicationButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Отменить заявку", style=discord.ButtonStyle.secondary, custom_id="gos_cancel_app", row=1)

    async def callback(self, interaction: discord.Interaction):
        app = db["applications"].get(str(interaction.user.id))
        if not app or app.get("status") != "pending":
            return await interaction.response.send_message("У вас нет активной заявки.", ephemeral=True)
        app["status"] = "cancelled"
        app["closed_time"] = now_iso()
        save_db()
        await interaction.response.send_message("Ваша заявка отменена.", ephemeral=True)


class GOSSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(GOSSelect())
        self.add_item(RemoveRoleButton())
        self.add_item(CancelApplicationButton())


# ============================================================
#  ЗАЯВКА
# ============================================================
def build_app_embed(app: dict) -> discord.Embed:
    status = app.get("status", "pending")
    color = {"pending": COLOR_WARN, "approved": COLOR_OK, "denied": COLOR_BAD}.get(status, COLOR_MAIN)
    embed = discord.Embed(title="Новая заявка | Stage RP T3", color=color, timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Роль", value=app["role_name"], inline=True)
    embed.add_field(name="Nick", value=f"`{app['nickname']}`", inline=True)
    embed.add_field(name="Ранг", value=f"`{app['rank']}`", inline=True)
    embed.add_field(name="Пользователь", value=f"<@{app['user_id']}> (`{app['user_id']}`)", inline=False)
    if status == "approved":
        embed.add_field(name="Статус", value=f"Одобрена: <@{app.get('moderator_id')}>", inline=False)
    elif status == "denied":
        embed.add_field(name="Статус", value=f"Отклонена: <@{app.get('moderator_id')}>\nПричина: {app.get('reason', '-')}", inline=False)
    embed.set_footer(text=f"Заявка от {app['user_name']}")
    return embed


async def dm_moderators(guild: discord.Guild, app: dict) -> int:
    mods = config["moderator_roles"]
    sent = 0
    for member in guild.members:
        if member.bot or not any(r.id in mods for r in member.roles):
            continue
        try:
            await member.send(embed=build_app_embed(app), view=ModerationView(app["user_id"]))
            sent += 1
        except (discord.Forbidden, discord.HTTPException):
            pass  # ЛС закрыты
        await asyncio.sleep(0.4)  # защита от рейт-лимита
    return sent


class RoleApplicationModal(discord.ui.Modal):
    # Лимит Discord на label - 45 символов, поэтому формулировка чуть короче.
    nickname = discord.ui.TextInput(
        label="Ваш NickName (должен совпадать с паспортом)",
        placeholder="Должен соответствовать паспорту",
        min_length=3,
        max_length=32,
        required=True,
    )
    rank = discord.ui.TextInput(label="Ранг", placeholder="Ваш ранг", min_length=1, max_length=50, required=True)

    def __init__(self, role_name: str):
        super().__init__(title=f"Запрос на роль | {role_name}"[:45], timeout=None)
        self.role_name = role_name

    async def on_submit(self, interaction: discord.Interaction):
        member = interaction.user
        err = check_can_apply(member) if isinstance(member, discord.Member) else "Ошибка."
        if err:
            return await interaction.response.send_message(err, ephemeral=True)
        if not config["gos_roles"].get(self.role_name):
            return await interaction.response.send_message("Эта роль ещё не настроена администрацией.", ephemeral=True)

        app = {
            "role_name": self.role_name,
            "nickname": clean(self.nickname.value),
            "rank": clean(self.rank.value),
            "user_name": str(member),
            "user_id": member.id,
            "guild_id": interaction.guild_id,
            "status": "pending",
            "time": now_iso(),
        }
        db["applications"][str(member.id)] = app
        save_db()

        await interaction.response.send_message("Ваша заявка отправлена на рассмотрение модераторам.", ephemeral=True)

        guild = interaction.guild
        cid = config["channels"].get("applications")
        channel = guild.get_channel(cid) if cid else None
        if channel:
            try:
                await channel.send(embed=build_app_embed(app), view=ModerationView(member.id))
            except discord.HTTPException as e:
                log.warning("Канал заявок недоступен: %s", e)
        # рассылка в ЛС - в фоне, чтобы не блокировать ответ
        asyncio.create_task(dm_moderators(guild, app))

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("Ошибка модалки: %s", error)
        if not interaction.response.is_done():
            await interaction.response.send_message("Произошла ошибка, попробуйте позже.", ephemeral=True)


# ============================================================
#  МОДЕРАЦИЯ ЗАЯВОК (в т.ч. из ЛС)
# ============================================================
async def resolve_moderator(interaction: discord.Interaction, app: dict):
    """Возвращает (guild, member|None). Работает и в ЛС."""
    guild = interaction.guild
    if guild is not None:
        m = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        return guild, (m if m and is_moderator(m) else None)

    gid = app.get("guild_id")
    known = bot.get_guild(gid) if gid else None
    candidates = [known] if known else list(bot.guilds)
    for g in candidates:
        m = g.get_member(interaction.user.id)
        if m and is_moderator(m):
            return g, m
    return known, None


async def fetch_member(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    m = guild.get_member(user_id)
    if m:
        return m
    try:
        return await guild.fetch_member(user_id)
    except discord.HTTPException:
        return None


class ApproveButton(discord.ui.Button):
    def __init__(self, user_id: int):
        super().__init__(label="Одобрить", style=discord.ButtonStyle.success, custom_id=f"mod_approve:{user_id}")
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        app = db["applications"].get(str(self.user_id))
        if not app or app.get("status") != "pending":
            return await interaction.response.send_message("Эта заявка уже обработана.", ephemeral=True)
        guild, mod = await resolve_moderator(interaction, app)
        if not guild or not mod:
            return await interaction.response.send_message("У вас нет прав для рассмотрения заявок.", ephemeral=True)

        await interaction.response.defer()
        member = await fetch_member(guild, self.user_id)
        if not member:
            return await interaction.followup.send("Пользователь не найден на сервере (мог выйти).", ephemeral=True)

        role = guild.get_role(config["gos_roles"].get(app["role_name"], 0))
        if not role:
            return await interaction.followup.send(
                f"Роль для «{app['role_name']}» не привязана. Используйте /config_role.", ephemeral=True
            )
        if has_gos(member) or has_opg(member):
            return await interaction.followup.send("У пользователя уже есть ГОС/ОПГ роль.", ephemeral=True)

        try:
            await member.add_roles(role, reason=f"Заявка одобрена: {mod}")
        except discord.Forbidden:
            return await interaction.followup.send("У бота нет прав выдать эту роль (проверьте иерархию ролей).", ephemeral=True)

        app["status"] = "approved"
        app["moderator_id"] = mod.id
        app["closed_time"] = now_iso()
        save_db()

        await interaction.edit_original_response(embed=build_app_embed(app), view=None)

        dm = discord.Embed(title="Заявка одобрена!", color=COLOR_OK, timestamp=datetime.now(timezone.utc))
        dm.add_field(name="Роль", value=role.name)
        dm.add_field(name="Nick", value=app["nickname"])
        dm.add_field(name="Ранг", value=app["rank"])
        dm.add_field(name="Сервер", value=guild.name)
        dm.add_field(name="Выдал", value=str(mod))
        try:
            await member.send(embed=dm)
        except (discord.Forbidden, discord.HTTPException):
            pass

        e = discord.Embed(title="Заявка одобрена", color=COLOR_OK, timestamp=datetime.now(timezone.utc))
        e.add_field(name="Пользователь", value=f"{member.mention} (`{member.id}`)")
        e.add_field(name="Роль", value=role.mention)
        e.add_field(name="Модератор", value=mod.mention)
        await send_log(guild, "logs", e)


class DenyModal(discord.ui.Modal):
    reason = discord.ui.TextInput(
        label="Причина отказа", style=discord.TextStyle.paragraph, min_length=3, max_length=500, required=True
    )

    def __init__(self, user_id: int):
        super().__init__(title="Отклонение заявки", timeout=None)
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        app = db["applications"].get(str(self.user_id))
        if not app or app.get("status") != "pending":
            return await interaction.response.send_message("Эта заявка уже обработана.", ephemeral=True)
        guild, mod = await resolve_moderator(interaction, app)
        if not guild or not mod:
            return await interaction.response.send_message("У вас нет прав для рассмотрения заявок.", ephemeral=True)

        app["status"] = "denied"
        app["moderator_id"] = mod.id
        app["reason"] = clean(self.reason.value)
        app["closed_time"] = now_iso()
        save_db()

        await interaction.response.edit_message(embed=build_app_embed(app), view=None)

        dm = discord.Embed(title="Заявка отклонена", color=COLOR_BAD, timestamp=datetime.now(timezone.utc))
        dm.add_field(name="Роль", value=app["role_name"])
        dm.add_field(name="Причина", value=app["reason"], inline=False)
        dm.add_field(name="Сервер", value=guild.name)
        member = await fetch_member(guild, self.user_id)
        if member:
            try:
                await member.send(embed=dm)
            except (discord.Forbidden, discord.HTTPException):
                pass

        e = discord.Embed(title="Заявка отклонена", color=COLOR_BAD, timestamp=datetime.now(timezone.utc))
        e.add_field(name="Пользователь", value=f"<@{self.user_id}> (`{self.user_id}`)")
        e.add_field(name="Роль", value=app["role_name"])
        e.add_field(name="Модератор", value=mod.mention)
        e.add_field(name="Причина", value=app["reason"], inline=False)
        await send_log(guild, "logs", e)


class DenyButton(discord.ui.Button):
    def __init__(self, user_id: int):
        super().__init__(label="Отклонить", style=discord.ButtonStyle.danger, custom_id=f"mod_deny:{user_id}")
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        app = db["applications"].get(str(self.user_id))
        if not app or app.get("status") != "pending":
            return await interaction.response.send_message("Эта заявка уже обработана.", ephemeral=True)
        guild, mod = await resolve_moderator(interaction, app)
        if not guild or not mod:
            return await interaction.response.send_message("У вас нет прав для рассмотрения заявок.", ephemeral=True)
        await interaction.response.send_modal(DenyModal(self.user_id))


class ModerationView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.add_item(ApproveButton(user_id))
        self.add_item(DenyButton(user_id))


# ============================================================
#  АНТИ-НЮК + АВТОВОССТАНОВЛЕНИЕ
# ============================================================
actions: dict = defaultdict(list)       # {user_id: [datetime]}
nuke_cache: dict = defaultdict(list)    # {user_id: [{"type", "snapshot", "name", "time"}]}
punished: dict = {}                     # {user_id: datetime}


def snapshot_role(role: discord.Role) -> dict:
    return {
        "id": role.id, "name": role.name, "color": role.color.value,
        "permissions": role.permissions.value, "hoist": role.hoist,
        "mentionable": role.mentionable, "position": role.position,
    }


def snapshot_channel(ch: discord.abc.GuildChannel) -> dict:
    return {
        "id": ch.id, "name": ch.name, "type": ch.type.name,
        "category_id": ch.category_id, "category_name": ch.category.name if ch.category else None,
        "position": ch.position, "topic": getattr(ch, "topic", None), "nsfw": getattr(ch, "nsfw", False),
        "overwrites": [
            (t.id, isinstance(t, discord.Role), ow.pair()[0].value, ow.pair()[1].value)
            for t, ow in ch.overwrites.items()
        ],
    }


def backup_lookup(guild: discord.Guild, kind: str, obj_id: int, name: str) -> Optional[dict]:
    b = db["backups"].get(str(guild.id))
    if not b:
        return None
    for item in b.get(kind, []):
        if item.get("id") == obj_id or item.get("name") == name:
            return item
    return None


async def restore_item(guild: discord.Guild, item: dict) -> None:
    t, s = item["type"], item["snapshot"]
    try:
        if t == "ROLE_DELETE":
            s = s or backup_lookup(guild, "roles", 0, item["name"])
            if not s or discord.utils.get(guild.roles, name=s["name"]):
                return
            role = await guild.create_role(
                name=s["name"], colour=discord.Colour(s["color"]),
                permissions=discord.Permissions(s["permissions"]),
                hoist=s["hoist"], mentionable=s["mentionable"], reason="Анти-нюк: автовосстановление",
            )
            try:
                await role.edit(position=max(1, min(s["position"], guild.me.top_role.position - 1)))
            except discord.HTTPException:
                pass
        elif t == "CHANNEL_DELETE":
            s = s or backup_lookup(guild, "channels", 0, item["name"])
            if not s:
                return
            ow = {}
            for tid, is_role, allow, deny in s.get("overwrites", []):
                target = guild.get_role(tid) if is_role else guild.get_member(tid)
                if target:
                    ow[target] = discord.PermissionOverwrite.from_pair(discord.Permissions(allow), discord.Permissions(deny))
            category = guild.get_channel(s.get("category_id") or 0) or (
                discord.utils.get(guild.categories, name=s["category_name"]) if s.get("category_name") else None
            )
            kw = {"name": s["name"], "overwrites": ow, "reason": "Анти-нюк: автовосстановление"}
            ctype = s.get("type", "text")
            if ctype == "category":
                ch = await guild.create_category(**kw)
            elif ctype == "voice":
                ch = await guild.create_voice_channel(category=category, **kw)
            elif ctype == "stage_voice":
                ch = await guild.create_stage_channel(category=category, **kw)
            else:
                ch = await guild.create_text_channel(category=category, topic=s.get("topic"), nsfw=s.get("nsfw", False), **kw)
            try:
                await ch.edit(position=s.get("position", 0))
            except discord.HTTPException:
                pass
        elif t == "MEMBER_BAN":
            await guild.unban(discord.Object(id=s["user_id"]), reason="Анти-нюк: откат бана")
    except discord.HTTPException as e:
        log.warning("Не удалось восстановить %s: %s", item.get("name"), e)


async def handle_nuke(guild: discord.Guild, executor, action_type: str, deleted_obj, snapshot: Optional[dict] = None):
    cfg = config["anti_nuke"]
    if not cfg["enabled"]:
        return
    if executor.id in config["whitelist"] or executor.id in (bot.user.id, guild.owner_id) or getattr(executor, "bot", False):
        return

    now = datetime.now(timezone.utc)
    item = {"type": action_type, "snapshot": snapshot, "name": str(deleted_obj), "time": now}

    # нарушитель уже наказан - просто откатываем остаточные действия
    if executor.id in punished and (now - punished[executor.id]).total_seconds() < 120:
        await restore_item(guild, item)
        return

    window = cfg["time_window"]
    actions[executor.id] = [t for t in actions[executor.id] if (now - t).total_seconds() < window] + [now]
    nuke_cache[executor.id] = [i for i in nuke_cache[executor.id] if (now - i["time"]).total_seconds() < window] + [item]

    if len(actions[executor.id]) < cfg["limit"]:
        return

    punish = cfg["action"]
    punished[executor.id] = now
    result = "не удалось наказать (проверьте иерархию ролей бота)"
    try:
        if punish == "kick":
            await guild.kick(discord.Object(id=executor.id), reason="Анти-нюк T3")
        else:
            await guild.ban(discord.Object(id=executor.id), reason="Анти-нюк T3", delete_message_days=0)
        result = f"{punish.upper()}+АвтоВосстановление"
    except discord.HTTPException as e:
        log.error("Анти-нюк: не удалось наказать %s: %s", executor.id, e)

    events = nuke_cache.pop(executor.id, [])
    actions.pop(executor.id, None)
    for ev in events:
        await restore_item(guild, ev)

    e = discord.Embed(title="АНТИ-НЮК T3 СРАБОТАЛ", color=COLOR_BAD, timestamp=datetime.now(timezone.utc))
    e.add_field(name="Нарушитель", value=f"{executor.mention} (`{executor.id}`)", inline=False)
    e.add_field(name="Действие", value=action_type, inline=True)
    e.add_field(name="Удалено", value=", ".join(ev["name"] for ev in events)[:1000] or str(deleted_obj), inline=True)
    e.add_field(name="Наказание", value=result, inline=False)
    await send_log(guild, "anti_nuke", e)


async def find_executor(guild: discord.Guild, action: discord.AuditLogAction, target_id: int):
    await asyncio.sleep(1)
    try:
        async for entry in guild.audit_logs(limit=5, action=action):
            if entry.target and entry.target.id == target_id and (datetime.now(timezone.utc) - entry.created_at).total_seconds() < 5:
                return entry.user
    except discord.Forbidden:
        log.warning("Нет права View Audit Log на %s", guild.name)
    return None


@bot.event
async def on_guild_role_delete(role: discord.Role):
    snap = snapshot_role(role)
    executor = await find_executor(role.guild, discord.AuditLogAction.role_delete, role.id)
    if executor:
        await handle_nuke(role.guild, executor, "ROLE_DELETE", role.name, snap)


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    snap = snapshot_channel(channel)
    executor = await find_executor(channel.guild, discord.AuditLogAction.channel_delete, channel.id)
    if executor:
        await handle_nuke(channel.guild, executor, "CHANNEL_DELETE", channel.name, snap)


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    executor = await find_executor(guild, discord.AuditLogAction.ban, user.id)
    if executor:
        await handle_nuke(guild, executor, "MEMBER_BAN", str(user), {"user_id": user.id})


# ============================================================
#  АВТОБЕКАП
# ============================================================
def snapshot_guild(guild: discord.Guild) -> dict:
    return {
        "time": now_iso(),
        "roles": [snapshot_role(r) for r in guild.roles if not r.is_default() and not r.managed],
        "channels": [
            {"id": c.id, "name": c.name, "type": c.type.name,
             "category": c.category.name if c.category else None, "position": c.position}
            for c in guild.channels
        ],
    }


@tasks.loop(hours=2)
async def auto_backup():
    for guild in bot.guilds:
        db["backups"][str(guild.id)] = snapshot_guild(guild)
    save_db()
    log.info("Автобекап выполнен для %d серверов", len(bot.guilds))


@auto_backup.before_loop
async def _before_backup():
    await bot.wait_until_ready()


ready_done = False


@bot.event
async def on_ready():
    global ready_done
    log.info("Вошёл как %s", bot.user)
    if ready_done:
        return
    ready_done = True
    bot.add_view(GOSSelectView())
    for uid, app in db["applications"].items():
        if app.get("status") == "pending":
            bot.add_view(ModerationView(int(uid)))
    await tree.sync()
    if not auto_backup.is_running():
        auto_backup.start()


# ============================================================
#  КОМАНДЫ: НАСТРОЙКА
# ============================================================
ADMIN = app_commands.default_permissions(administrator=True)


@tree.command(name="setup", description="Создать панель выдачи ролей")
@ADMIN
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
async def setup_cmd(interaction: discord.Interaction):
    cid = config["channels"].get("roles_give")
    channel = interaction.guild.get_channel(cid) if cid else None
    channel = channel or interaction.channel
    await channel.send(embed=build_panel_embed(), view=GOSSelectView())
    await interaction.response.send_message(f"Панель создана в {channel.mention}.", ephemeral=True)


MOD_ACTIONS = [app_commands.Choice(name="add", value="add"), app_commands.Choice(name="remove", value="remove"), app_commands.Choice(name="list", value="list")]


@tree.command(name="config_mod", description="Роли модераторов, получающих заявки в ЛС")
@ADMIN
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
@app_commands.choices(action=MOD_ACTIONS)
async def config_mod(interaction: discord.Interaction, action: app_commands.Choice[str], role: Optional[discord.Role] = None):
    mods = config["moderator_roles"]
    if action.value == "list":
        text = "\n".join(f"<@&{r}>" for r in mods) or "Список пуст."
        return await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    if not role:
        return await interaction.response.send_message("Укажите роль.", ephemeral=True)
    if action.value == "add":
        if role.id not in mods:
            mods.append(role.id)
    else:
        if role.id in mods:
            mods.remove(role.id)
    save_config()
    await interaction.response.send_message(f"Готово: {action.value} {role.mention}", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@tree.command(name="config_gif", description="Гифка/картинка панели")
@ADMIN
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
@app_commands.choices(action=[app_commands.Choice(name="set", value="set"), app_commands.Choice(name="remove", value="remove")])
async def config_gif(interaction: discord.Interaction, action: app_commands.Choice[str], url: Optional[str] = None):
    panel = config["panel"]
    if action.value == "remove":
        panel["gif_url"] = ""
        panel["image_url"] = ""
        save_config()
        return await interaction.response.send_message("Гифка и картинка удалены. Пересоздайте панель через /setup.", ephemeral=True)
    if not url or not url.lower().startswith(("http://", "https://")):
        return await interaction.response.send_message("Укажите корректную ссылку (http/https).", ephemeral=True)
    if url.split("?")[0].lower().endswith(".gif"):
        panel["gif_url"] = url
    else:
        panel["image_url"] = url
    save_config()
    await interaction.response.send_message("Сохранено. Пересоздайте панель через /setup.", ephemeral=True)


@tree.command(name="config_role", description="Привязать роль Discord к названию ГОС структуры")
@ADMIN
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
async def config_role(interaction: discord.Interaction, name: str, role: discord.Role):
    config["gos_roles"][name[:100]] = role.id
    save_config()
    await interaction.response.send_message(f"«{name}» -> {role.mention}", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@config_role.autocomplete("name")
async def _ac_gos(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=n, value=n) for n in config["gos_roles"] if current.lower() in n.lower()][:25]


@tree.command(name="config_opg", description="Роли ОПГ (нельзя совмещать с ГОС)")
@ADMIN
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
@app_commands.choices(action=MOD_ACTIONS)
async def config_opg(interaction: discord.Interaction, action: app_commands.Choice[str], role: Optional[discord.Role] = None):
    opg = config["opg_roles"]
    if action.value == "list":
        text = "\n".join(f"<@&{r}>" for r in opg) or "Список пуст."
        return await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    if not role:
        return await interaction.response.send_message("Укажите роль.", ephemeral=True)
    if action.value == "add" and role.id not in opg:
        opg.append(role.id)
    elif action.value == "remove" and role.id in opg:
        opg.remove(role.id)
    save_config()
    await interaction.response.send_message("Готово.", ephemeral=True)


@tree.command(name="config_channel", description="Настроить каналы бота")
@ADMIN
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
@app_commands.choices(type=[
    app_commands.Choice(name="roles_give", value="roles_give"),
    app_commands.Choice(name="applications", value="applications"),
    app_commands.Choice(name="logs", value="logs"),
    app_commands.Choice(name="anti_nuke", value="anti_nuke"),
])
async def config_channel(interaction: discord.Interaction, type: app_commands.Choice[str], channel: discord.TextChannel):
    config["channels"][type.value] = channel.id
    save_config()
    await interaction.response.send_message(f"{type.value} -> {channel.mention}", ephemeral=True)


@tree.command(name="config_antinuke", description="Настройки анти-нюка")
@ADMIN
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
@app_commands.choices(action=[app_commands.Choice(name="ban", value="ban"), app_commands.Choice(name="kick", value="kick")])
async def config_antinuke(
    interaction: discord.Interaction,
    enabled: Optional[bool] = None,
    limit: Optional[app_commands.Range[int, 1, 20]] = None,
    time_window: Optional[app_commands.Range[int, 5, 3600]] = None,
    action: Optional[app_commands.Choice[str]] = None,
):
    cfg = config["anti_nuke"]
    if enabled is not None:
        cfg["enabled"] = enabled
    if limit is not None:
        cfg["limit"] = limit
    if time_window is not None:
        cfg["time_window"] = time_window
    if action is not None:
        cfg["action"] = action.value
    save_config()
    await interaction.response.send_message(
        f"Анти-нюк: включен={cfg['enabled']}, лимит={cfg['limit']}, окно={cfg['time_window']}с, наказание={cfg['action']}",
        ephemeral=True,
    )


@tree.command(name="whitelist", description="Белый список анти-нюка")
@ADMIN
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
@app_commands.choices(action=MOD_ACTIONS)
async def whitelist_cmd(interaction: discord.Interaction, action: app_commands.Choice[str], user: Optional[discord.User] = None):
    wl = config["whitelist"]
    if action.value == "list":
        text = "\n".join(f"<@{u}> (`{u}`)" for u in wl) or "Список пуст."
        return await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    if not user:
        return await interaction.response.send_message("Укажите пользователя.", ephemeral=True)
    if action.value == "add" and user.id not in wl:
        wl.append(user.id)
    elif action.value == "remove" and user.id in wl:
        wl.remove(user.id)
    save_config()
    await interaction.response.send_message("Готово.", ephemeral=True)


# ============================================================
#  КОМАНДЫ: ЗАЯВКИ / БЕКАП
# ============================================================
@tree.command(name="apps", description="Список заявок на рассмотрении")
@app_commands.default_permissions(manage_roles=True)
@app_commands.guild_only()
async def apps_cmd(interaction: discord.Interaction):
    if not is_moderator(interaction.user):
        return await interaction.response.send_message("Нет прав.", ephemeral=True)
    pending = [a for a in db["applications"].values() if a.get("status") == "pending" and a.get("guild_id") == interaction.guild_id]
    if not pending:
        return await interaction.response.send_message("Заявок на рассмотрении нет.", ephemeral=True)
    e = discord.Embed(title=f"Заявки на рассмотрении ({len(pending)})", color=COLOR_WARN)
    for a in pending[:25]:
        e.add_field(name=f"{a['role_name']} | {a['nickname']}", value=f"<@{a['user_id']}> | Ранг: {a['rank']}", inline=False)
    await interaction.response.send_message(embed=e, ephemeral=True)


@tree.command(name="backup", description="Сделать бекап ролей и каналов сейчас")
@ADMIN
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
async def backup_cmd(interaction: discord.Interaction):
    snap = snapshot_guild(interaction.guild)
    db["backups"][str(interaction.guild_id)] = snap
    save_db()
    await interaction.response.send_message(
        f"Бекап готов: ролей {len(snap['roles'])}, каналов {len(snap['channels'])}.", ephemeral=True
    )


@tree.command(name="backup_load", description="Восстановить отсутствующие роли из бекапа")
@ADMIN
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
async def backup_load(interaction: discord.Interaction):
    b = db["backups"].get(str(interaction.guild_id))
    if not b:
        return await interaction.response.send_message("Бекап не найден.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    created = 0
    for r in sorted(b["roles"], key=lambda x: x["position"], reverse=True):
        if discord.utils.get(interaction.guild.roles, name=r["name"]):
            continue
        try:
            await interaction.guild.create_role(
                name=r["name"], colour=discord.Colour(r["color"]), permissions=discord.Permissions(r["permissions"]),
                hoist=r["hoist"], mentionable=r["mentionable"], reason="Восстановление из бекапа",
            )
            created += 1
        except discord.HTTPException as e:
            log.warning("backup_load: %s", e)
    await interaction.followup.send(f"Восстановлено ролей: {created}.", ephemeral=True)


# ============================================================
#  КОМАНДЫ: МОДЕРАЦИЯ
# ============================================================
def hierarchy_error(interaction: discord.Interaction, target: discord.Member) -> Optional[str]:
    guild = interaction.guild
    if target.id == interaction.user.id:
        return "Нельзя применить к себе."
    if target.id == guild.owner_id:
        return "Нельзя применить к владельцу сервера."
    if interaction.user.id != guild.owner_id and target.top_role >= interaction.user.top_role:
        return "Роль цели не ниже вашей."
    if target.top_role >= guild.me.top_role:
        return "Роль цели не ниже роли бота."
    return None


async def mod_log(interaction: discord.Interaction, title: str, target, reason: str, extra: str = ""):
    e = discord.Embed(title=title, color=COLOR_BAD, timestamp=datetime.now(timezone.utc))
    e.add_field(name="Цель", value=f"{target} (`{target.id}`)")
    e.add_field(name="Модератор", value=interaction.user.mention)
    if extra:
        e.add_field(name="Детали", value=extra)
    e.add_field(name="Причина", value=reason, inline=False)
    await send_log(interaction.guild, "logs", e)


@tree.command(name="ban", description="Забанить участника")
@app_commands.default_permissions(ban_members=True)
@app_commands.guild_only()
@app_commands.checks.has_permissions(ban_members=True)
async def ban_cmd(interaction: discord.Interaction, user: discord.Member, reason: str = "Не указана"):
    err = hierarchy_error(interaction, user)
    if err:
        return await interaction.response.send_message(err, ephemeral=True)
    try:
        await user.ban(reason=f"{interaction.user}: {reason}", delete_message_days=0)
    except discord.HTTPException as e:
        return await interaction.response.send_message(f"Ошибка: {e}", ephemeral=True)
    await interaction.response.send_message(f"{user} забанен.", ephemeral=True)
    await mod_log(interaction, "Бан", user, reason)


@tree.command(name="kick", description="Кикнуть участника")
@app_commands.default_permissions(kick_members=True)
@app_commands.guild_only()
@app_commands.checks.has_permissions(kick_members=True)
async def kick_cmd(interaction: discord.Interaction, user: discord.Member, reason: str = "Не указана"):
    err = hierarchy_error(interaction, user)
    if err:
        return await interaction.response.send_message(err, ephemeral=True)
    try:
        await user.kick(reason=f"{interaction.user}: {reason}")
    except discord.HTTPException as e:
        return await interaction.response.send_message(f"Ошибка: {e}", ephemeral=True)
    await interaction.response.send_message(f"{user} кикнут.", ephemeral=True)
    await mod_log(interaction, "Кик", user, reason)


@tree.command(name="mute", description="Выдать тайм-аут участнику")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
@app_commands.checks.has_permissions(moderate_members=True)
async def mute_cmd(interaction: discord.Interaction, user: discord.Member, minutes: app_commands.Range[int, 1, 40320], reason: str = "Не указана"):
    err = hierarchy_error(interaction, user)
    if err:
        return await interaction.response.send_message(err, ephemeral=True)
    try:
        await user.timeout(timedelta(minutes=minutes), reason=f"{interaction.user}: {reason}")
    except discord.HTTPException as e:
        return await interaction.response.send_message(f"Ошибка: {e}", ephemeral=True)
    await interaction.response.send_message(f"{user} в муте на {minutes} мин.", ephemeral=True)
    await mod_log(interaction, "Мут", user, reason, f"{minutes} мин.")


@tree.command(name="clear", description="Удалить сообщения в канале")
@app_commands.default_permissions(manage_messages=True)
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_messages=True)
async def clear_cmd(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"Удалено сообщений: {len(deleted)}.", ephemeral=True)


# ============================================================
#  ОБЩАЯ ОБРАБОТКА ОШИБОК
# ============================================================
@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "У вас недостаточно прав."
    else:
        log.exception("Ошибка команды: %s", error)
        msg = "Произошла ошибка при выполнении команды."
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Не задана переменная окружения DISCORD_TOKEN")
    bot.run(TOKEN, log_handler=None)
