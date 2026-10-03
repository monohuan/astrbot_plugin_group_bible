from __future__ import annotations

import base64
import html
import mimetypes
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.star import Star

from .database import BibleEntry


class BibleRenderer:
    def __init__(self, star: Star, assets_dir: Path, show_id: bool = True):
        self.star = star
        self.assets_dir = assets_dir
        self.show_id = show_id
        self.card_template = (assets_dir / "bible.html").read_text(encoding="utf-8")
        self.list_template = (assets_dir / "bible_list.html").read_text(encoding="utf-8")

    @staticmethod
    def _data_uri(path: str) -> str:
        file_path = Path(path)
        if not file_path.is_file():
            return ""
        mime = mimetypes.guess_type(file_path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    @staticmethod
    def _format_time(timestamp: int) -> str:
        if not timestamp:
            return "未知时间"
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def _fallback_avatar(name: str) -> str:
        letter = html.escape((name or "?")[:1])
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96">'
            '<rect width="96" height="96" rx="48" fill="#25252a"/>'
            f'<text x="48" y="61" text-anchor="middle" font-size="42" fill="#7CB342">{letter}</text>'
            "</svg>"
        )
        return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()

    def _entry_context(self, entry: BibleEntry) -> dict[str, Any]:
        images: list[str] = []
        avatar = ""
        for seg in entry.segments:
            if seg.get("type") == "image" and seg.get("path"):
                uri = self._data_uri(str(seg["path"]))
                if uri:
                    images.append(uri)
            elif seg.get("type") == "avatar" and seg.get("path"):
                avatar = self._data_uri(str(seg["path"]))
        return {
            "entry_id": entry.id if self.show_id else "",
            "author_name": html.escape(entry.author_name),
            "author_id": html.escape(entry.author_id),
            "avatar": avatar or self._fallback_avatar(entry.author_name),
            "timestamp": self._format_time(entry.source_time),
            "text": html.escape(entry.plain_text),
            "images": images,
            "image_count": len(images),
            "collector_name": html.escape(entry.collector_name),
        }

    async def _render(self, tmpl: str, data: dict[str, Any]) -> str | None:
        options = {
            "full_page": True,
            "type": "jpeg",
            "quality": 95,
            "scale": "device",
            "device_scale_factor_level": "ultra",
            "viewport_height": 1,
        }
        for attempt in range(2):
            try:
                result = await self.star.html_render(
                    tmpl=tmpl, data=data, return_url=False, options=options
                )
                if result and os.path.isfile(result) and os.path.getsize(result) > 1024:
                    return result
            except Exception as exc:
                logger.error("群圣经图片渲染失败（第 %s 次）：%s", attempt + 1, exc)
        return None

    async def render_entry(self, entry: BibleEntry) -> str | None:
        return await self._render(self.card_template, self._entry_context(entry))

    async def render_list(
        self, entries: list[BibleEntry], page: int, total_pages: int, total: int
    ) -> str | None:
        rows = []
        for entry in entries:
            summary = " ".join(entry.plain_text.split()) or "[图片]"
            if len(summary) > 72:
                summary = summary[:72] + "…"
            rows.append(
                {
                    "id": entry.id,
                    "author": html.escape(entry.author_name),
                    "summary": html.escape(summary),
                    "time": self._format_time(entry.source_time),
                }
            )
        return await self._render(
            self.list_template,
            {
                "entries": rows,
                "page": page,
                "total_pages": total_pages,
                "total": total,
            },
        )

