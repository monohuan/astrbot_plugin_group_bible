from __future__ import annotations

import asyncio
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import aiosqlite


@dataclass(slots=True)
class BibleEntry:
    id: int
    group_id: str
    source_message_id: str
    author_id: str
    author_name: str
    source_time: int
    segments: list[dict[str, Any]]
    plain_text: str
    collector_id: str
    collector_name: str
    created_at: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DuplicateBibleError(RuntimeError):
    pass


class BibleStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA foreign_keys=ON")
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS bible_entries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        group_id TEXT NOT NULL,
                        source_message_id TEXT NOT NULL,
                        author_id TEXT NOT NULL,
                        author_name TEXT NOT NULL,
                        source_time INTEGER NOT NULL,
                        segments_json TEXT NOT NULL,
                        plain_text TEXT NOT NULL,
                        collector_id TEXT NOT NULL,
                        collector_name TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        UNIQUE(group_id, source_message_id)
                    )
                    """
                )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_bible_group_id ON bible_entries(group_id, id DESC)"
                )
                await db.commit()
            self._initialized = True

    @staticmethod
    def _row_to_entry(row: aiosqlite.Row | None) -> BibleEntry | None:
        if row is None:
            return None
        return BibleEntry(
            id=int(row["id"]),
            group_id=str(row["group_id"]),
            source_message_id=str(row["source_message_id"]),
            author_id=str(row["author_id"]),
            author_name=str(row["author_name"]),
            source_time=int(row["source_time"]),
            segments=json.loads(row["segments_json"]),
            plain_text=str(row["plain_text"]),
            collector_id=str(row["collector_id"]),
            collector_name=str(row["collector_name"]),
            created_at=int(row["created_at"]),
        )

    async def add(self, data: dict[str, Any]) -> BibleEntry:
        await self.initialize()
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    """
                    INSERT INTO bible_entries (
                        group_id, source_message_id, author_id, author_name,
                        source_time, segments_json, plain_text,
                        collector_id, collector_name, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        data["group_id"],
                        data["source_message_id"],
                        data["author_id"],
                        data["author_name"],
                        int(data["source_time"]),
                        json.dumps(data["segments"], ensure_ascii=False),
                        data["plain_text"],
                        data["collector_id"],
                        data["collector_name"],
                        int(data["created_at"]),
                    ),
                )
                await db.commit()
                row = await (
                    await db.execute(
                        "SELECT * FROM bible_entries WHERE id = ?", (cur.lastrowid,)
                    )
                ).fetchone()
        except aiosqlite.IntegrityError as exc:
            raise DuplicateBibleError from exc
        entry = self._row_to_entry(row)
        assert entry is not None
        return entry

    async def get(self, group_id: str, entry_id: int) -> BibleEntry | None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await (
                await db.execute(
                    "SELECT * FROM bible_entries WHERE group_id = ? AND id = ?",
                    (group_id, entry_id),
                )
            ).fetchone()
        return self._row_to_entry(row)

    async def random(self, group_id: str) -> BibleEntry | None:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            count_row = await (
                await db.execute(
                    "SELECT COUNT(*) AS amount FROM bible_entries WHERE group_id = ?",
                    (group_id,),
                )
            ).fetchone()
            amount = int(count_row["amount"] if count_row else 0)
            if amount == 0:
                return None
            offset = random.randrange(amount)
            row = await (
                await db.execute(
                    "SELECT * FROM bible_entries WHERE group_id = ? ORDER BY id LIMIT 1 OFFSET ?",
                    (group_id, offset),
                )
            ).fetchone()
        return self._row_to_entry(row)

    async def count(self, group_id: str) -> int:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            row = await (
                await db.execute(
                    "SELECT COUNT(*) FROM bible_entries WHERE group_id = ?",
                    (group_id,),
                )
            ).fetchone()
        return int(row[0] if row else 0)

    async def list_page(
        self, group_id: str, page: int, page_size: int
    ) -> tuple[list[BibleEntry], int]:
        await self.initialize()
        total = await self.count(group_id)
        offset = max(0, page - 1) * page_size
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    "SELECT * FROM bible_entries WHERE group_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                    (group_id, page_size, offset),
                )
            ).fetchall()
        return [self._row_to_entry(row) for row in rows if row is not None], total

    async def delete(self, group_id: str, entry_id: int) -> BibleEntry | None:
        entry = await self.get(group_id, entry_id)
        if entry is None:
            return None
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM bible_entries WHERE group_id = ? AND id = ?",
                (group_id, entry_id),
            )
            await db.commit()
        return entry

    async def dashboard_groups(self) -> list[dict[str, Any]]:
        """Return per-group counters for the authenticated plugin dashboard."""
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """
                    SELECT group_id, COUNT(*) AS amount, MAX(created_at) AS latest_at
                    FROM bible_entries
                    GROUP BY group_id
                    ORDER BY latest_at DESC, group_id
                    """
                )
            ).fetchall()
        return [
            {
                "group_id": str(row["group_id"]),
                "amount": int(row["amount"]),
                "latest_at": int(row["latest_at"] or 0),
            }
            for row in rows
        ]

    async def dashboard_list(
        self,
        *,
        group_id: str = "",
        query: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[BibleEntry], int]:
        """Search entries across groups for the authenticated plugin dashboard."""
        await self.initialize()
        clauses: list[str] = []
        params: list[Any] = []
        if group_id:
            clauses.append("group_id = ?")
            params.append(group_id)
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            clauses.append(
                "(plain_text LIKE ? ESCAPE '\\' OR author_name LIKE ? ESCAPE '\\' "
                "OR author_id LIKE ? ESCAPE '\\' OR collector_name LIKE ? ESCAPE '\\' "
                "OR CAST(id AS TEXT) = ?)"
            )
            params.extend([like, like, like, like, query.removeprefix("#")])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        offset = max(0, page - 1) * page_size
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            count_row = await (
                await db.execute(
                    f"SELECT COUNT(*) AS amount FROM bible_entries{where}", params
                )
            ).fetchone()
            rows = await (
                await db.execute(
                    f"SELECT * FROM bible_entries{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                    [*params, page_size, offset],
                )
            ).fetchall()
        total = int(count_row["amount"] if count_row else 0)
        return [self._row_to_entry(row) for row in rows if row is not None], total
