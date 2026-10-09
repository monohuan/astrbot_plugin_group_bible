from __future__ import annotations

import asyncio
import json
import random
import time
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
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS bible_draw_state (
                        group_id TEXT PRIMARY KEY,
                        known_ids_json TEXT NOT NULL,
                        remaining_ids_json TEXT NOT NULL,
                        cycle INTEGER NOT NULL DEFAULT 1,
                        last_entry_id INTEGER,
                        updated_at INTEGER NOT NULL
                    )
                    """
                )
                columns = {
                    str(row[1])
                    for row in await (
                        await db.execute("PRAGMA table_info(bible_draw_state)")
                    ).fetchall()
                }
                if "pool_copies" not in columns:
                    await db.execute(
                        "ALTER TABLE bible_draw_state "
                        "ADD COLUMN pool_copies INTEGER NOT NULL DEFAULT 1"
                    )
                if "cycle_started_at" not in columns:
                    await db.execute(
                        "ALTER TABLE bible_draw_state "
                        "ADD COLUMN cycle_started_at INTEGER NOT NULL DEFAULT 0"
                    )
                if "reset_seconds" not in columns:
                    await db.execute(
                        "ALTER TABLE bible_draw_state "
                        "ADD COLUMN reset_seconds INTEGER NOT NULL DEFAULT 0"
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

    async def random(
        self,
        group_id: str,
        *,
        pool_copies: int = 1,
        reset_seconds: int = 0,
    ) -> BibleEntry | None:
        """Draw from a persistent per-group shuffle bag.

        The bag contains ``pool_copies`` copies of every live entry. A new shuffled
        cycle starts after the bag is empty or its optional lifetime expires.
        The state lives in SQLite so reloads and process restarts do not reset it.
        """
        await self.initialize()
        pool_copies = max(1, min(100, int(pool_copies)))
        reset_seconds = max(0, int(reset_seconds))
        now = int(time.time())
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("BEGIN IMMEDIATE")
            id_rows = await (
                await db.execute(
                    "SELECT id FROM bible_entries WHERE group_id = ? ORDER BY id",
                    (group_id,),
                )
            ).fetchall()
            current_ids = [int(row["id"]) for row in id_rows]
            if not current_ids:
                await db.execute(
                    "DELETE FROM bible_draw_state WHERE group_id = ?", (group_id,)
                )
                await db.commit()
                return None

            state = await (
                await db.execute(
                    "SELECT * FROM bible_draw_state WHERE group_id = ?", (group_id,)
                )
            ).fetchone()
            known_ids: list[int] = []
            remaining_ids: list[int] = []
            cycle = 1
            last_entry_id: int | None = None
            stored_copies = pool_copies
            stored_reset_seconds = reset_seconds
            cycle_started_at = now
            reset_pool = False
            if state is not None:
                try:
                    known_ids = [int(value) for value in json.loads(state["known_ids_json"])]
                    remaining_ids = [
                        int(value) for value in json.loads(state["remaining_ids_json"])
                    ]
                    cycle = max(1, int(state["cycle"]))
                    stored_copies = max(1, int(state["pool_copies"]))
                    stored_reset_seconds = max(0, int(state["reset_seconds"]))
                    cycle_started_at = int(state["cycle_started_at"]) or now
                    if state["last_entry_id"] is not None:
                        last_entry_id = int(state["last_entry_id"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    # A damaged state row should reset only the draw order, never entries.
                    known_ids = []
                    remaining_ids = []
                    cycle = 1
                    stored_copies = pool_copies
                    stored_reset_seconds = reset_seconds
                    cycle_started_at = now
                    reset_pool = True

                if stored_copies != pool_copies:
                    reset_pool = True
                if reset_seconds > 0 and stored_reset_seconds != reset_seconds:
                    reset_pool = True
                if reset_seconds > 0 and now - cycle_started_at >= reset_seconds:
                    reset_pool = True

            if reset_pool:
                known_ids = []
                remaining_ids = []
                cycle += 1
                cycle_started_at = now

            current_set = set(current_ids)
            known_set = set(known_ids) & current_set
            # Preserve order while discarding deleted IDs and excess copies.
            occurrences: dict[int, int] = {}
            valid_remaining_ids: list[int] = []
            for entry_id in remaining_ids:
                amount = occurrences.get(entry_id, 0)
                if entry_id in known_set and amount < pool_copies:
                    occurrences[entry_id] = amount + 1
                    valid_remaining_ids.append(entry_id)
            remaining_ids = valid_remaining_ids

            added_ids = [entry_id for entry_id in current_ids if entry_id not in known_set]
            if added_ids:
                # New entries join the current cycle, so they cannot be starved by repeats.
                remaining_ids.extend(
                    entry_id for entry_id in added_ids for _ in range(pool_copies)
                )
                random.shuffle(remaining_ids)
                known_set.update(added_ids)

            if not remaining_ids:
                if state is not None and not reset_pool:
                    cycle += 1
                remaining_ids = [
                    entry_id for entry_id in current_ids for _ in range(pool_copies)
                ]
                random.shuffle(remaining_ids)
                # Avoid the same entry on both sides of a cycle boundary when possible.
                if last_entry_id is not None and remaining_ids[-1] == last_entry_id:
                    replacement = next(
                        (
                            index
                            for index, entry_id in enumerate(remaining_ids[:-1])
                            if entry_id != last_entry_id
                        ),
                        None,
                    )
                    if replacement is not None:
                        remaining_ids[replacement], remaining_ids[-1] = (
                            remaining_ids[-1],
                            remaining_ids[replacement],
                        )
                known_set = current_set.copy()
                cycle_started_at = now

            selected_id = remaining_ids.pop()
            await db.execute(
                """
                INSERT INTO bible_draw_state (
                    group_id, known_ids_json, remaining_ids_json,
                    cycle, last_entry_id, updated_at, pool_copies,
                    cycle_started_at, reset_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    known_ids_json = excluded.known_ids_json,
                    remaining_ids_json = excluded.remaining_ids_json,
                    cycle = excluded.cycle,
                    last_entry_id = excluded.last_entry_id,
                    updated_at = excluded.updated_at,
                    pool_copies = excluded.pool_copies,
                    cycle_started_at = excluded.cycle_started_at,
                    reset_seconds = excluded.reset_seconds
                """,
                (
                    group_id,
                    json.dumps(sorted(known_set)),
                    json.dumps(remaining_ids),
                    cycle,
                    selected_id,
                    now,
                    pool_copies,
                    cycle_started_at,
                    reset_seconds,
                ),
            )
            row = await (
                await db.execute(
                    "SELECT * FROM bible_entries WHERE group_id = ? AND id = ?",
                    (group_id, selected_id),
                )
            ).fetchone()
            await db.commit()
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
