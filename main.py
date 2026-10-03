from __future__ import annotations

import asyncio
import base64
import hashlib
import math
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Reply
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.star.filter.command import GreedyStr

from .database import BibleEntry, BibleStore, DuplicateBibleError
from .renderer import BibleRenderer


PLUGIN_NAME = "astrbot_plugin_group_bible"
HELP_TEXT = """【群圣经 / Group Bible】
引用消息后：/入典 或 /gbible add
随机爆典：/爆典 或 /gbible random
查看条目：/群圣经 #ID 或 /gbible #ID
删除条目：/删典 #ID 或 /gbible del #ID
条目列表：/群圣经列表 [页码] 或 /gbible list [页码]
插件首页：/群圣经 或 /gbible
帮助：/群圣经帮助 或 /gbible help"""


@register(PLUGIN_NAME, "Tyrkb", "群聊圣经收录与随机爆典", "1.0.0")
class GroupBiblePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self.data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        self.cache_dir = self.data_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.store = BibleStore(self.data_dir / "bible.db")
        self.renderer = BibleRenderer(
            self,
            Path(__file__).parent / "assets",
            show_id=bool(self.config.get("show_bible_id", True)),
        )
        self._poke_last_at: dict[str, float] = {}
        self._asset_lock = asyncio.Lock()

    def _cfg_int(self, key: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(self.config.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _entry_id(raw: str) -> int | None:
        value = str(raw or "").strip().removeprefix("#")
        if not value.isdigit():
            return None
        number = int(value)
        return number if number > 0 else None

    @staticmethod
    def _sender_name(event: AstrMessageEvent) -> str:
        sender = getattr(getattr(event, "message_obj", None), "sender", None)
        return str(getattr(sender, "nickname", "") or event.get_sender_id())

    async def _group_role(self, event: AstrMessageEvent, user_id: str) -> str:
        group_id = event.get_group_id()
        if not group_id or not group_id.isdigit() or not str(user_id).isdigit():
            return "unknown"
        bot = getattr(event, "bot", None)
        if bot is None:
            return "unknown"
        try:
            if hasattr(bot, "get_group_member_info"):
                info = await bot.get_group_member_info(
                    group_id=int(group_id), user_id=int(user_id), no_cache=True
                )
            else:
                info = await bot.call_action(
                    "get_group_member_info",
                    group_id=int(group_id),
                    user_id=int(user_id),
                    no_cache=True,
                )
            return str((info or {}).get("role", "unknown"))
        except Exception as exc:
            logger.warning("群圣经读取群权限失败 group=%s user=%s: %s", group_id, user_id, exc)
            return "unknown"

    async def _can_add(self, event: AstrMessageEvent) -> bool:
        level = self._cfg_int("add_permission_level", 1, 0, 2)
        if event.is_admin():
            return True
        if level == 0:
            return False
        if level == 2:
            return True
        return await self._group_role(event, event.get_sender_id()) in {"owner", "admin"}

    async def _can_delete(self, event: AstrMessageEvent, entry: BibleEntry) -> bool:
        if event.is_admin() or entry.collector_id == str(event.get_sender_id()):
            return True
        return await self._group_role(event, event.get_sender_id()) in {"owner", "admin"}

    async def _cache_bytes(self, content: bytes, suffix: str = ".jpg") -> str:
        digest = hashlib.sha256(content).hexdigest()
        suffix = suffix.lower() if suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"} else ".jpg"
        path = self.cache_dir / f"{digest}{suffix}"
        async with self._asset_lock:
            if not path.exists():
                path.write_bytes(content)
        return str(path.resolve())

    async def _cache_source(self, source: str) -> str:
        source = str(source or "").strip()
        if not source:
            return ""
        try:
            if source.startswith("base64://"):
                return await self._cache_bytes(base64.b64decode(source[9:]))
            normalized = source.removeprefix("file:///")
            local = Path(normalized)
            if local.is_file():
                return await self._cache_bytes(local.read_bytes(), local.suffix)
            if source.startswith(("http://", "https://")):
                timeout = aiohttp.ClientTimeout(total=15)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(source) as response:
                        response.raise_for_status()
                        if int(response.headers.get("Content-Length", 0) or 0) > 12 * 1024 * 1024:
                            return ""
                        content = await response.read()
                        if len(content) > 12 * 1024 * 1024:
                            return ""
                        suffix = Path(urlparse(source).path).suffix or ".jpg"
                        return await self._cache_bytes(content, suffix)
        except Exception as exc:
            logger.warning("群圣经缓存图片失败 source=%s: %s", source[:120], exc)
        return ""

    async def _cache_avatar(self, author_id: str) -> str:
        if not author_id.isdigit():
            return ""
        return await self._cache_source(
            f"https://q1.qlogo.cn/g?b=qq&nk={author_id}&s=640"
        )

    @staticmethod
    def _raw_event_data(event: AstrMessageEvent) -> Any:
        return getattr(getattr(event, "message_obj", None), "raw_message", None)

    async def _reply_payload(self, event: AstrMessageEvent) -> dict[str, Any] | None:
        reply = next((part for part in event.get_messages() if isinstance(part, Reply)), None)
        if reply is None:
            return None

        payload: dict[str, Any] = {
            "message_id": str(getattr(reply, "id", "") or ""),
            "sender_id": str(getattr(reply, "sender_id", "") or ""),
            "sender_name": str(getattr(reply, "sender_nickname", "") or ""),
            "time": int(getattr(reply, "time", 0) or 0),
            "chain": list(getattr(reply, "chain", None) or []),
            "message_str": str(getattr(reply, "message_str", "") or ""),
        }
        if payload["chain"]:
            return payload

        bot = getattr(event, "bot", None)
        if bot is None or not payload["message_id"]:
            return payload
        try:
            raw = await bot.call_action("get_msg", message_id=int(payload["message_id"]))
            raw = raw or {}
            sender = raw.get("sender") or {}
            payload["sender_id"] = str(
                raw.get("user_id") or sender.get("user_id") or payload["sender_id"]
            )
            payload["sender_name"] = str(
                sender.get("card")
                or sender.get("nickname")
                or payload["sender_name"]
                or payload["sender_id"]
            )
            payload["time"] = int(raw.get("time") or payload["time"] or 0)
            payload["chain"] = list(raw.get("message") or [])
            payload["message_str"] = str(raw.get("raw_message") or payload["message_str"])
        except Exception as exc:
            logger.warning("群圣经 get_msg 读取引用失败 id=%s: %s", payload["message_id"], exc)
        return payload

    async def _normalize_segments(
        self, chain: list[Any], fallback_text: str
    ) -> tuple[list[dict[str, Any]], str]:
        segments: list[dict[str, Any]] = []
        text_parts: list[str] = []
        image_limit = self._cfg_int("max_images", 9, 0, 20)
        image_count = 0

        for part in chain:
            if isinstance(part, dict):
                kind = str(part.get("type", "")).lower()
                data = part.get("data") or {}
            else:
                kind = part.__class__.__name__.lower()
                data = vars(part)

            if kind in {"plain", "text"}:
                value = str(data.get("text", ""))
                if value:
                    text_parts.append(value)
                    segments.append({"type": "text", "text": value})
            elif kind in {"image", "imagecomponent"}:
                if image_count >= image_limit:
                    continue
                source = str(data.get("url") or data.get("file") or "")
                cached = await self._cache_source(source)
                if cached:
                    image_count += 1
                    segments.append({"type": "image", "path": cached})
            elif kind == "at":
                qq = str(data.get("qq", ""))
                value = "@全体成员" if qq == "all" else f"@{qq}"
                text_parts.append(value)
                segments.append({"type": "text", "text": value})
            elif kind == "face":
                value = f"[表情:{data.get('id', '')}]"
                text_parts.append(value)
                segments.append({"type": "text", "text": value})
            elif kind == "reply":
                continue
            else:
                label = str(kind or "消息")
                value = f"[{label}]"
                text_parts.append(value)
                segments.append({"type": "text", "text": value})

        plain = "".join(text_parts).strip()
        if not plain and fallback_text and not segments:
            plain = fallback_text.strip()
            segments.append({"type": "text", "text": plain})
        return segments, plain

    async def _send_text(self, event: AstrMessageEvent, text: str) -> None:
        await event.send(event.plain_result(text))
        event.stop_event()

    async def _send_entry(self, event: AstrMessageEvent, entry: BibleEntry) -> None:
        image = await self.renderer.render_entry(entry)
        if image:
            await event.send(MessageChain().file_image(image))
        else:
            body = entry.plain_text or "[图片]"
            await event.send(event.plain_result(f"群圣经 #{entry.id}\n{entry.author_name}：{body}"))

    async def _add(self, event: AstrMessageEvent) -> None:
        group_id = str(event.get_group_id() or "")
        if not group_id:
            await self._send_text(event, "群圣经只能在群聊中使用。")
            return
        if not await self._can_add(event):
            level = self._cfg_int("add_permission_level", 1, 0, 2)
            names = {0: "仅 AstrBot 管理员", 1: "仅 Bot 管理员、群主和群管理员", 2: "所有成员"}
            await self._send_text(event, f"你没有入典权限。当前等级 {level}：{names[level]}。")
            return

        quoted = await self._reply_payload(event)
        if quoted is None:
            await self._send_text(event, "请引用一条群消息后再使用 /入典 或 /gbible add。")
            return
        if not quoted["message_id"]:
            await self._send_text(event, "无法取得被引用消息的 ID，暂时不能入典。")
            return

        segments, plain = await self._normalize_segments(
            quoted["chain"], quoted["message_str"]
        )
        max_text = self._cfg_int("max_text_length", 3000, 1, 20000)
        if len(plain) > max_text:
            await self._send_text(event, f"消息文字过长，最多允许 {max_text} 个字符。")
            return
        if not segments:
            await self._send_text(event, "这条消息没有可保存的文字或图片。")
            return

        author_id = str(quoted["sender_id"] or "未知")
        author_name = str(quoted["sender_name"] or author_id)
        avatar = await self._cache_avatar(author_id)
        if avatar:
            segments.append({"type": "avatar", "path": avatar})
        now = int(time.time())
        try:
            entry = await self.store.add(
                {
                    "group_id": group_id,
                    "source_message_id": quoted["message_id"],
                    "author_id": author_id,
                    "author_name": author_name,
                    "source_time": int(quoted["time"] or now),
                    "segments": segments,
                    "plain_text": plain,
                    "collector_id": str(event.get_sender_id()),
                    "collector_name": self._sender_name(event),
                    "created_at": now,
                }
            )
        except DuplicateBibleError:
            await self._send_text(event, "这条消息已经入典，不能重复收录。")
            return
        await self._send_entry(event, entry)
        event.stop_event()

    async def _random(self, event: AstrMessageEvent, empty_reply: bool = True) -> None:
        group_id = str(event.get_group_id() or "")
        if not group_id:
            await self._send_text(event, "群圣经只能在群聊中使用。")
            return
        entry = await self.store.random(group_id)
        if entry is None:
            if empty_reply:
                await self._send_text(event, "本群还没有圣经。引用一条消息并使用 /入典 吧。")
            return
        await self._send_entry(event, entry)
        event.stop_event()

    async def _show(self, event: AstrMessageEvent, raw_id: str) -> None:
        entry_id = self._entry_id(raw_id)
        if entry_id is None:
            await self._send_text(event, "编号格式错误，例如：/群圣经 #42 或 /gbible #42")
            return
        entry = await self.store.get(str(event.get_group_id() or ""), entry_id)
        if entry is None:
            await self._send_text(event, f"本群不存在圣经 #{entry_id}。")
            return
        await self._send_entry(event, entry)
        event.stop_event()

    async def _delete(self, event: AstrMessageEvent, raw_id: str) -> None:
        group_id = str(event.get_group_id() or "")
        entry_id = self._entry_id(raw_id)
        if not group_id:
            await self._send_text(event, "群圣经只能在群聊中使用。")
            return
        if entry_id is None:
            await self._send_text(event, "用法：/删典 #ID 或 /gbible del #ID")
            return
        entry = await self.store.get(group_id, entry_id)
        if entry is None:
            await self._send_text(event, f"本群不存在圣经 #{entry_id}。")
            return
        if not await self._can_delete(event, entry):
            await self._send_text(event, "只有收录者、群主、群管理员或 AstrBot 管理员可以删除该条目。")
            return
        await self.store.delete(group_id, entry_id)
        await self._send_text(event, f"已删除群圣经 #{entry_id}。")

    async def _list(self, event: AstrMessageEvent, raw_page: str = "1") -> None:
        group_id = str(event.get_group_id() or "")
        if not group_id:
            await self._send_text(event, "群圣经只能在群聊中使用。")
            return
        page = int(raw_page) if str(raw_page).isdigit() else 1
        page_size = self._cfg_int("list_page_size", 10, 5, 20)
        entries, total = await self.store.list_page(group_id, max(1, page), page_size)
        if total == 0:
            await self._send_text(event, "本群还没有圣经。")
            return
        total_pages = max(1, math.ceil(total / page_size))
        page = min(max(1, page), total_pages)
        if page > 1 and not entries:
            entries, total = await self.store.list_page(group_id, page, page_size)
        image = await self.renderer.render_list(entries, page, total_pages, total)
        if image:
            await event.send(MessageChain().file_image(image))
            event.stop_event()
            return
        lines = [f"群圣经列表 {page}/{total_pages}（共 {total} 条）"]
        for item in entries:
            summary = " ".join(item.plain_text.split()) or "[图片]"
            lines.append(f"#{item.id} {item.author_name}：{summary[:40]}")
        await self._send_text(event, "\n".join(lines))

    async def _home(self, event: AstrMessageEvent) -> None:
        group_id = str(event.get_group_id() or "")
        if not group_id:
            await self._send_text(event, "群圣经只能在群聊中使用。")
            return
        total = await self.store.count(group_id)
        level = self._cfg_int("add_permission_level", 1, 0, 2)
        labels = {0: "仅 Bot 管理员", 1: "群管理及 Bot 管理员", 2: "所有成员"}
        await self._send_text(
            event,
            f"本群已收录 {total} 条圣经。\n入典权限：{level} 级（{labels[level]}）\n使用 /群圣经帮助 或 /gbible help 查看指令。",
        )

    @filter.command("入典")
    async def add_bible_cn(self, event: AstrMessageEvent):
        await self._add(event)

    @filter.command("爆典")
    async def random_bible_cn(self, event: AstrMessageEvent):
        await self._random(event)

    @filter.command("删典")
    async def delete_bible_cn(self, event: AstrMessageEvent, entry_id: str = ""):
        await self._delete(event, entry_id)

    @filter.command("群圣经帮助")
    async def help_bible_cn(self, event: AstrMessageEvent):
        await self._send_text(event, HELP_TEXT)

    @filter.command("群圣经列表")
    async def list_bible_cn(self, event: AstrMessageEvent, page: str = "1"):
        await self._list(event, page)

    @filter.command("群圣经")
    async def group_bible_cn(self, event: AstrMessageEvent, query: str = ""):
        if str(query).strip():
            await self._show(event, query)
        else:
            await self._home(event)

    @filter.command("gbible")
    async def group_bible_en(self, event: AstrMessageEvent, raw_args: GreedyStr = ""):
        args = str(raw_args or "").strip().split()
        if not args:
            await self._home(event)
            return
        action = args[0].lower()
        tail = args[1] if len(args) > 1 else ""
        if action == "add":
            await self._add(event)
        elif action == "random":
            await self._random(event)
        elif action == "del":
            await self._delete(event, tail)
        elif action == "help":
            await self._send_text(event, HELP_TEXT)
        elif action == "list":
            await self._list(event, tail or "1")
        elif self._entry_id(action) is not None:
            await self._show(event, action)
        else:
            await self._send_text(event, "未知指令。使用 /gbible help 查看帮助。")

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=10_000)
    async def poke_random_bible(self, event: AstrMessageEvent):
        if not bool(self.config.get("enable_poke_random", True)):
            return

        target = ""
        found_poke = False
        for part in event.get_messages():
            if part.__class__.__name__.lower() != "poke":
                continue
            found_poke = True
            target = str(getattr(part, "id", "") or getattr(part, "qq", "") or "")
            break
        if not found_poke:
            return

        raw = self._raw_event_data(event)
        if hasattr(raw, "get"):
            target = str(raw.get("target_id") or target)
        if not target or target != str(event.get_self_id()):
            return

        group_id = str(event.get_group_id() or "")
        entry = await self.store.random(group_id)
        if entry is None:
            # 这是刻意保留的例外：无圣经时不接管 Poke，继续 AstrBot 默认对话。
            return

        event.should_call_llm(False)
        event.stop_event()
        cooldown = self._cfg_int("poke_cooldown_seconds", 10, 0, 3600)
        now = time.monotonic()
        if now - self._poke_last_at.get(group_id, 0.0) < cooldown:
            return
        self._poke_last_at[group_id] = now
        await self._send_entry(event, entry)

    async def terminate(self):
        self._poke_last_at.clear()

