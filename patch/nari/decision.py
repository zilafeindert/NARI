from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class DecisionCore:
    """Memoria de decisiones internas resumidas.

    No guarda razonamiento token a token ni cadenas ocultas del modelo.
    Guarda únicamente estados operativos cortos: objetivo, observación, plan,
    acción, confianza y una nota breve sobre la decisión.
    """

    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=8.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS internal_decisions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                context TEXT NOT NULL,
                goal TEXT NOT NULL,
                observation TEXT NOT NULL,
                plan TEXT NOT NULL,
                decision TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0,
                created REAL NOT NULL DEFAULT (strftime('%s','now'))
            )
            """
        )
        self.db.commit()

    def record(
        self,
        context: str,
        goal: str,
        observation: str = "",
        plan: str = "",
        decision: str = "",
        confidence: float = 0.0,
    ):
        self.db.execute(
            """
            INSERT INTO internal_decisions
            (context,goal,observation,plan,decision,confidence,created)
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                str(context)[:80],
                str(goal)[:500],
                str(observation)[:500],
                str(plan)[:600],
                str(decision)[:120],
                max(0.0, min(1.0, float(confidence))),
                time.time(),
            ),
        )
        self.db.commit()

    def latest(self, context: str | None = None) -> dict:
        if context:
            row = self.db.execute(
                """
                SELECT * FROM internal_decisions
                WHERE context=?
                ORDER BY id DESC LIMIT 1
                """,
                (str(context),),
            ).fetchone()
        else:
            row = self.db.execute(
                "SELECT * FROM internal_decisions ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else {}

    def recent(self, limit: int = 15, context: str | None = None) -> list[dict]:
        if context:
            rows = self.db.execute(
                """
                SELECT * FROM internal_decisions
                WHERE context=?
                ORDER BY id DESC LIMIT ?
                """,
                (str(context), max(1, int(limit))),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM internal_decisions ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(x) for x in rows]

    def close(self):
        try:
            self.db.close()
        except Exception:
            pass
