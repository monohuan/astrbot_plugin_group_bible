from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from typing import Any

from astrbot.api import logger
from astrbot.api.star import Context

try:
    from quart import jsonify, request
except ImportError:  # pragma: no cover - AstrBot dashboard always provides Quart
    jsonify = None
    request = None

from .database import BibleStore


PLUGIN_NAME = "astrbot_plugin_group_bible"
EDITABLE_CONFIG = {
    "add_permission_level": (int, 0, 2),
    "enable_poke_random": (bool, None, None),
    "poke_cooldown_seconds": (int, 0, 3600),
    "list_page_size": (int, 5, 20),
    "max_text_length": (int, 1, 20000),
    "max_images": (int, 0, 20),
    "show_bible_id": (bool, None, None),
}


class GroupBibleWebController:
    def __init__(
        self,
        context: Context,
        config: dict[str, Any],
        store: BibleStore,
        on_config_changed: Callable[[], None],
    ):
        self.context = context
        self.config = config
        self.store = store
        self.on_config_changed = on_config_changed

    def register_routes(self) -> None:
        register = getattr(self.context, "register_web_api", None)
        if not callable(register) or jsonify is None or request is None:
            logger.warning("[群圣经] 当前 AstrBot 不支持插件 WebUI，已跳过管理页注册。")
            return
        routes = (
            ("/manage/bootstrap", self.bootstrap, ["GET"], "群圣经管理页初始化"),
            ("/manage/entries", self.entries, ["GET"], "查询群圣经条目"),
            ("/manage/delete", self.delete, ["POST"], "删除群圣经条目"),
            ("/manage/config", self.update_config, ["POST"], "更新群圣经配置"),
        )
        for path, handler, methods, desc in routes:
            register(
                f"/{PLUGIN_NAME}{path}",
                self._wrap(handler),
                methods,
                desc,
            )

    @staticmethod
    def _response(payload: dict[str, Any], status: int = 200):
        assert jsonify is not None
        response = jsonify(payload)
        response.status_code = status
        return response

    def _wrap(self, handler: Callable[[], Awaitable]):
        async def wrapped():
            try:
                return await handler()
            except ValueError as exc:
                return self._response({"ok": False, "message": str(exc)}, 400)
            except Exception as exc:
                logger.exception("[群圣经] WebUI 请求失败")
                return self._response({"ok": False, "message": str(exc)}, 500)

        wrapped.__name__ = f"group_bible_{handler.__name__}"
        return wrapped

    @staticmethod
    def _entry_payload(entry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "group_id": entry.group_id,
            "author_id": entry.author_id,
            "author_name": entry.author_name,
            "source_time": entry.source_time,
            "plain_text": entry.plain_text,
            "image_count": sum(1 for item in entry.segments if item.get("type") == "image"),
            "collector_id": entry.collector_id,
            "collector_name": entry.collector_name,
            "created_at": entry.created_at,
        }

    def _config_payload(self) -> dict[str, Any]:
        return {key: self.config.get(key) for key in EDITABLE_CONFIG}

    async def bootstrap(self):
        groups = await self.store.dashboard_groups()
        entries, total = await self.store.dashboard_list(page=1, page_size=20)
        return self._response(
            {
                "ok": True,
                "data": {
                    "groups": groups,
                    "total": total,
                    "entries": [self._entry_payload(item) for item in entries],
                    "page": 1,
                    "page_size": 20,
                    "total_pages": max(1, math.ceil(total / 20)),
                    "config": self._config_payload(),
                },
            }
        )

    async def entries(self):
        assert request is not None
        group_id = str(request.args.get("group_id", "")).strip()
        query = str(request.args.get("query", "")).strip()[:200]
        try:
            page = max(1, int(request.args.get("page", 1)))
            page_size = max(5, min(50, int(request.args.get("page_size", 20))))
        except (TypeError, ValueError) as exc:
            raise ValueError("分页参数必须是整数") from exc
        if group_id and not group_id.isdigit():
            raise ValueError("群号必须是纯数字")
        rows, total = await self.store.dashboard_list(
            group_id=group_id,
            query=query,
            page=page,
            page_size=page_size,
        )
        return self._response(
            {
                "ok": True,
                "data": {
                    "entries": [self._entry_payload(item) for item in rows],
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": max(1, math.ceil(total / page_size)),
                },
            }
        )

    async def delete(self):
        assert request is not None
        payload = await request.get_json(force=True, silent=True) or {}
        group_id = str(payload.get("group_id", "")).strip()
        try:
            entry_id = int(payload.get("id", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("条目编号不正确") from exc
        if not group_id.isdigit() or entry_id <= 0:
            raise ValueError("群号或条目编号不正确")
        removed = await self.store.delete(group_id, entry_id)
        if removed is None:
            return self._response({"ok": False, "message": "条目不存在或已被删除"}, 404)
        logger.info("[群圣经] Dashboard 删除条目 group=%s id=%s", group_id, entry_id)
        return self._response({"ok": True, "message": f"已删除 #{entry_id}"})

    async def update_config(self):
        assert request is not None
        payload = await request.get_json(force=True, silent=True) or {}
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON 对象")
        updated: dict[str, Any] = {}
        for key, value in payload.items():
            spec = EDITABLE_CONFIG.get(key)
            if spec is None:
                raise ValueError(f"不允许修改配置项：{key}")
            expected, minimum, maximum = spec
            if expected is bool:
                if not isinstance(value, bool):
                    raise ValueError(f"{key} 必须是布尔值")
                normalized = value
            else:
                if isinstance(value, bool):
                    raise ValueError(f"{key} 必须是整数")
                try:
                    normalized = int(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{key} 必须是整数") from exc
                if minimum is not None and normalized < minimum:
                    raise ValueError(f"{key} 不能小于 {minimum}")
                if maximum is not None and normalized > maximum:
                    raise ValueError(f"{key} 不能大于 {maximum}")
            updated[key] = normalized

        self.config.update(updated)
        save = getattr(self.config, "save_config", None)
        if callable(save):
            save()
        self.on_config_changed()
        logger.info("[群圣经] Dashboard 更新配置：%s", ", ".join(updated))
        return self._response(
            {"ok": True, "message": "配置已保存", "data": self._config_payload()}
        )

