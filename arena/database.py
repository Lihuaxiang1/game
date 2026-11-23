"""SQLite 用户认证和持久化排行榜。"""

from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import threading
from pathlib import Path
from typing import Iterable

PBKDF2_ITERATIONS = 120_000


class Database:
    def __init__(self, path: str | os.PathLike[str]):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY COLLATE NOCASE,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    score INTEGER NOT NULL DEFAULT 0,
                    games INTEGER NOT NULL DEFAULT 0,
                    wins INTEGER NOT NULL DEFAULT 0,
                    kills INTEGER NOT NULL DEFAULT 0,
                    deaths INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    @staticmethod
    def _password_hash(password: str, salt: bytes) -> str:
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
        ).hex()

    def create_user(self, username: str, password: str) -> bool:
        salt = os.urandom(16)
        password_hash = self._password_hash(password, salt)
        with self._lock:
            try:
                with self._connection:
                    self._connection.execute(
                        "INSERT INTO users(username,password_salt,password_hash) VALUES(?,?,?)",
                        (username, salt.hex(), password_hash),
                    )
                return True
            except sqlite3.IntegrityError:
                return False

    def authenticate(self, username: str, password: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT password_salt,password_hash FROM users WHERE username=?",
                (username,),
            ).fetchone()
        if row is None:
            return False
        candidate = self._password_hash(password, bytes.fromhex(row[0]))
        return hmac.compare_digest(candidate, row[1])

    def leaderboard(self, limit: int = 20) -> list[dict[str, int | str]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT username,score,games,wins,kills,deaths
                FROM users ORDER BY score DESC,kills DESC,username ASC LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        return [
            {
                "username": row[0],
                "score": row[1],
                "games": row[2],
                "wins": row[3],
                "kills": row[4],
                "deaths": row[5],
            }
            for row in rows
        ]

    def record_match(self, results: Iterable[dict[str, int | str]]) -> None:
        """在一个事务中保存一整桌的成绩，避免部分玩家成绩写入。"""
        with self._lock:
            with self._connection:
                for result in results:
                    self._connection.execute(
                        """
                        UPDATE users SET
                            games=games+1,
                            wins=wins+?,
                            kills=kills+?,
                            deaths=deaths+?,
                            score=MAX(0,score+?)
                        WHERE username=?
                        """,
                        (
                            int(result["won"]),
                            int(result["kills"]),
                            int(result["deaths"]),
                            int(result["score_delta"]),
                            result["username"],
                        ),
                    )

    def close(self) -> None:
        with self._lock:
            self._connection.close()
