"""Persist commands until the authorized mobile worker returns a result."""

import sqlite3
import uuid
from contextlib import closing


class DeviceRelay:
    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS relay (
                id TEXT PRIMARY KEY, text TEXT NOT NULL, kind TEXT NOT NULL,
                status TEXT NOT NULL, result TEXT)""")

    def submit(self, message):
        request_id = message.get("request_id")
        text = message.get("text")
        kind = message.get("input_type")
        try:
            valid_id = isinstance(request_id, str) and str(uuid.UUID(request_id)) == request_id
        except ValueError:
            valid_id = False
        if not valid_id or not isinstance(text, str) or not text.strip() or len(text) > 4000 or kind not in ("text", "voice"):
            return 400, {"error": "잘못된 명령입니다."}
        text = text.strip()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT text, kind, status FROM relay WHERE id=?", (request_id,)).fetchone()
            if row:
                if row[:2] != (text, kind):
                    return 409, {"error": "요청 ID가 충돌합니다."}
                return 200, {"request_id": request_id, "status": row[2]}
            if db.execute("SELECT COUNT(*) FROM relay WHERE status IN ('queued', 'running')").fetchone()[0] >= 100:
                return 503, {"error": "누리 대기열이 가득 찼습니다."}
            db.execute("INSERT INTO relay VALUES (?, ?, ?, 'queued', NULL)", (request_id, text, kind))
        return 202, {"request_id": request_id, "status": "queued"}

    def get(self, request_id):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT status, result FROM relay WHERE id=?", (request_id,)).fetchone()
        if row is None:
            return 404, {"error": "요청을 찾을 수 없습니다."}
        reply = {"request_id": request_id, "status": row[0]}
        if row[0] == "completed":
            reply["result"] = {"text": row[1]}
        return 200, reply

    def take(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id, text, kind FROM relay WHERE status IN ('queued', 'running') ORDER BY rowid LIMIT 1"
            ).fetchone()
            if row is None:
                return 200, {"command": None}
            db.execute("UPDATE relay SET status='running' WHERE id=?", (row[0],))
        return 200, {"command": dict(zip(("request_id", "text", "input_type"), row))}

    def has_work(self):
        with closing(sqlite3.connect(self.path, timeout=1)) as db:
            return db.execute("SELECT 1 FROM relay WHERE status IN ('queued', 'running') LIMIT 1").fetchone() is not None

    def is_forwarding(self, request_id, text):
        with closing(sqlite3.connect(self.path, timeout=1)) as db:
            return db.execute(
                "SELECT 1 FROM relay WHERE id=? AND text=? AND status='running'", (request_id, text),
            ).fetchone() is not None

    def finish(self, request_id, message):
        status = message.get("status")
        text = message.get("text")
        if status not in ("completed", "failed", "unknown") or (
            status == "completed" and (not isinstance(text, str) or len(text) > 4000)
        ):
            return 400, {"error": "잘못된 처리 결과입니다."}
        text = text if status == "completed" else None
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status, result FROM relay WHERE id=?", (request_id,)).fetchone()
            if row == (status, text):
                return 200, {"status": status}
            if row is None or row[0] != "running":
                return 409, {"error": "처리 중인 요청을 찾을 수 없습니다."}
            db.execute("UPDATE relay SET status=?, result=? WHERE id=?", (status, text, request_id))
        return 200, {"status": status}
