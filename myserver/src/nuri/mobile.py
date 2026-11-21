"""인증 없는 로컬 네트워크 명령 접수. 실행은 누리 메인 루프가 담당한다."""
from __future__ import annotations

import ipaddress
import json
import sqlite3
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


class MobileCommands:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, text TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, result TEXT)")
            self.db.execute("""CREATE TABLE IF NOT EXISTS work_checkpoints (
                request_id TEXT NOT NULL, stage INTEGER NOT NULL,
                provider TEXT NOT NULL, report TEXT NOT NULL,
                PRIMARY KEY (request_id, stage))""")
            self.db.execute("UPDATE commands SET status='unknown' WHERE status='running'")

    def submit(self, data: object) -> tuple[int, dict]:
        if not isinstance(data, dict):
            return 400, {"error": "잘못된 요청"}
        request_id, text, kind = data.get("request_id"), data.get("text"), data.get("input_type")
        try:
            if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
                raise ValueError
        except ValueError:
            return 400, {"error": "잘못된 요청 ID"}
        if not isinstance(text, str) or not text.strip() or len(text) > 4000 or kind not in ("text", "voice"):
            return 400, {"error": "잘못된 명령"}
        text = text.strip()
        with self.lock:
            row = self.db.execute("SELECT text, kind, status FROM commands WHERE id=?", (request_id,)).fetchone()
            if row:
                if row[:2] != (text, kind):
                    return 409, {"error": "요청 ID 충돌"}
                return 200, {"request_id": request_id, "status": row[2]}
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                if self.db.execute("SELECT COUNT(*) FROM commands WHERE status='queued'").fetchone()[0] >= 100:
                    return 503, {"error": "대기열이 가득 찼습니다"}
                self.db.execute("INSERT INTO commands VALUES (?, ?, ?, 'queued', NULL)", (request_id, text, kind))
        return 202, {"request_id": request_id, "status": "queued"}

    def get(self, request_id: str) -> tuple[int, dict]:
        with self.lock:
            row = self.db.execute("SELECT status, result FROM commands WHERE id=?", (request_id,)).fetchone()
        if row is None:
            return 404, {"error": "요청을 찾을 수 없습니다"}
        result = {"request_id": request_id, "status": row[0]}
        if row[0] == "completed":
            result["result"] = {"text": row[1]}
        return 200, result

    def take(self) -> tuple[str, str] | None:
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT id, text FROM commands WHERE status='queued' ORDER BY rowid LIMIT 1"
            ).fetchone()
            if row:
                self.db.execute("UPDATE commands SET status='running' WHERE id=?", (row[0],))
            return row

    def checkpoints(self, request_id: str) -> list[tuple[str, str]]:
        with self.lock:
            return self.db.execute(
                "SELECT provider, report FROM work_checkpoints WHERE request_id=? ORDER BY stage", (request_id,)
            ).fetchall()

    def checkpoint(self, request_id: str, stage: int, provider: str, report: str) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO work_checkpoints VALUES (?, ?, ?, ?)", (request_id, stage, provider, report))
            self.db.execute("UPDATE commands SET status='queued' WHERE id=? AND status='running'", (request_id,))

    def finish(self, request_id: str, status: str, result: str | None = None) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE commands SET status=?, result=? WHERE id=?", (status, result, request_id))

    def has_work(self) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1 FROM commands WHERE status IN ('queued','running') LIMIT 1").fetchone() is not None


class MobileServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], commands: MobileCommands, settings=None, dreams=None) -> None:
        self.commands = commands
        self.settings = settings
        self.dreams = dreams
        super().__init__(address, MobileHandler)


_TAILSCALE_IPV4 = ipaddress.ip_network("100.64.0.0/10")


def allowed_client(address: str) -> bool:
    """사설망과 Tailscale IPv4 주소를 허용한다."""
    ip = ipaddress.ip_address(address)
    return ip.is_private or ip.is_loopback or ip in _TAILSCALE_IPV4


class MobileHandler(BaseHTTPRequestHandler):
    server: MobileServer

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format: str, *args: object) -> None:
        pass

    def reply(self, status: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def allowed(self) -> bool:
        return allowed_client(self.client_address[0]) and not self.headers.get("Origin")

    def _live_role(self, path: str) -> str | None:
        if getattr(self.server, "dreams", None) is None or not ipaddress.ip_address(self.client_address[0]).is_loopback:
            return None
        return {"/v1/live/assistant": "assistant", "/v1/live/nuri": "nuri"}.get(path)

    def do_GET(self) -> None:
        if not self.allowed():
            self.reply(403, {"error": "접근할 수 없습니다"})
            return
        path = urlsplit(self.path).path
        role = self._live_role(path)
        if role:
            self.reply(200, self.server.dreams.status(role))
        elif path == "/v1/status":
            self.reply(200, {"status": "ready"})
        elif path == "/v1/settings/provider" and self.server.settings is not None:
            from nuri.core.settings import PROVIDERS

            self.reply(200, {"provider": self.server.settings.current().provider, "providers": PROVIDERS})
        elif path.startswith("/v1/commands/"):
            self.reply(*self.server.commands.get(path.removeprefix("/v1/commands/")))
        else:
            self.reply(404, {"error": "찾을 수 없습니다"})

    def read_body(self) -> bytes:
        """길이 지정 또는 chunked 방식의 JSON을 크기 제한 안에서 읽는다."""
        limit = 16384
        transfer = self.headers.get("Transfer-Encoding", "").lower().strip()
        length = self.headers.get("Content-Length")
        if transfer:
            if transfer != "chunked" or length is not None:
                raise ValueError("잘못된 전송 방식")
            body = bytearray()
            while True:
                line = self.rfile.readline(128)
                if not line.endswith(b"\r\n"):
                    raise ValueError("잘못된 조각 크기")
                size = int(line[:-2].split(b";", 1)[0], 16)
                if size < 0 or size > limit - len(body):
                    raise ValueError("본문 크기 초과")
                if size == 0:
                    # 모바일 앱은 trailer를 사용하지 않는다.
                    if self.rfile.readline(128) != b"\r\n":
                        raise ValueError("지원하지 않는 trailer")
                    return bytes(body)
                chunk = self.rfile.read(size)
                if len(chunk) != size or self.rfile.read(2) != b"\r\n":
                    raise ValueError("불완전한 본문")
                body.extend(chunk)
        size = int(length or "0")
        if not 0 < size <= limit:
            raise ValueError("잘못된 본문 크기")
        body = self.rfile.read(size)
        if len(body) != size:
            raise ValueError("불완전한 본문")
        return body

    def do_POST(self) -> None:
        if not self.allowed():
            self.reply(403, {"error": "접근할 수 없습니다"})
            return
        role = self._live_role(self.path)
        if (not role and self.path not in ("/v1/commands", "/v1/settings/provider")) or self.headers.get_content_type() != "application/json":
            self.reply(400, {"error": "잘못된 요청"})
            return
        try:
            data = json.loads(self.read_body().decode("utf-8"))
        except (ValueError, UnicodeDecodeError, TimeoutError):
            self.reply(400, {"error": "잘못된 요청"})
            return
        if role:
            if isinstance(data, dict) and type(data.get("sleeping")) is bool:
                self.reply(200, self.server.dreams.set_sleeping(role, data["sleeping"]))
            elif isinstance(data, dict) and type(data.get("autonomy")) is bool:
                self.reply(200, self.server.dreams.set_autonomy(role, data["autonomy"]))
            elif isinstance(data, dict) and isinstance(data.get("memory"), str) and len(data["memory"]) <= 600:
                self.reply(200, self.server.dreams.set_memory(role, data["memory"].strip()))
            else:
                self.reply(400, {"error": "Live 모드 설정이 올바르지 않습니다."})
            return
        if self.path == "/v1/settings/provider":
            if self.server.settings is None:
                self.reply(404, {"error": "설정을 사용할 수 없습니다"})
                return
            from nuri.core.settings import PROVIDERS

            if (not isinstance(data, dict) or not isinstance(data.get("provider"), str)
                    or data["provider"] not in PROVIDERS):
                self.reply(400, {"error": "잘못된 처리기"})
                return
            if data["provider"] == "gemini-2":
                self.reply(409, {"error": "두 번째 Antigravity 계정이 독립적으로 연결되지 않았습니다."})
                return
            try:
                updated = self.server.settings.set_provider(data["provider"])
            except ValueError as exc:
                self.reply(409, {"error": str(exc)})
                return
            except OSError:
                self.reply(500, {"error": "설정을 저장하지 못했습니다"})
                return
            self.reply(200, {"provider": updated.provider})
            return
        self.reply(*self.server.commands.submit(data))
