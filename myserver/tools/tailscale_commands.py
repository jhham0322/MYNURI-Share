"""Forward command requests from one authenticated Tailscale device."""

import argparse
import http.client
import ipaddress
import json
import re
from http.server import ThreadingHTTPServer

from nuri.mobile import MobileHandler
from nuri.device_relay import DeviceRelay


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", required=True)
    parser.add_argument("--device-ip", action="append", type=ipaddress.ip_address, required=True)
    parser.add_argument("--port", type=int, default=18773)
    parser.add_argument("--upstream-port", type=int, default=18772)
    parser.add_argument("--role", choices=("sender", "worker"))
    parser.add_argument("--relay-db")
    config = parser.parse_args()
    if bool(config.role) != bool(config.relay_db):
        parser.error("--role and --relay-db must be set together")
    relay = DeviceRelay(config.relay_db) if config.relay_db else None

    class Handler(MobileHandler):
        def allowed(self):
            login = self.headers.get_all("Tailscale-User-Login", [])
            forwarded = self.headers.get_all("X-Forwarded-For", [])
            if self.client_address[0] != "127.0.0.1" or login != [config.login] or len(forwarded) != 1:
                return False
            try:
                return ipaddress.ip_address(forwarded[0]) in config.device_ip
            except ValueError:
                return False

        def forward(self, method):
            if not self.allowed():
                self.reply(403, {"error": "이 주소에 등록된 기기만 연결할 수 있습니다."})
                return
            live = {"sender": "/v1/live/assistant", "worker": "/v1/live/nuri"}.get(config.role)
            shared = self.path == "/v1/settings/provider" or self.path == live
            command_id = re.fullmatch(r"/v1/commands/([0-9a-f-]{36})", self.path)
            worker_take = method == "POST" and config.role == "worker" and self.path == "/v1/worker/take"
            worker_result = None
            if method == "POST" and config.role == "worker":
                worker_result = re.fullmatch(r"/v1/worker/([0-9a-f-]{36})/result", self.path)
            if method == "GET":
                valid = shared or self.path == "/v1/status" or command_id
            else:
                valid = shared or self.path == "/v1/commands" or worker_take or worker_result
            if not valid:
                self.reply(404, {"error": "경로를 찾을 수 없습니다."})
                return
            body = None
            headers = {}
            if method == "POST":
                if self.headers.get_content_type() != "application/json":
                    self.reply(415, {"error": "JSON 요청이 필요합니다."})
                    return
                try:
                    body = self.read_body()
                except (ValueError, OSError):
                    self.reply(400, {"error": "요청 본문을 읽지 못했습니다."})
                    return
                headers["Content-Type"] = "application/json"
            if relay is not None:
                if config.role == "sender" and method == "GET" and command_id:
                    status, response = relay.get(command_id[1])
                    if status != 404:
                        self.reply(status, response)
                        return
                if worker_take or worker_result or (config.role == "sender" and self.path == "/v1/commands"):
                    try:
                        message = json.loads(body)
                        if not isinstance(message, dict):
                            raise ValueError
                    except (ValueError, TypeError):
                        self.reply(400, {"error": "잘못된 JSON 요청입니다."})
                        return
                    if worker_take:
                        self.reply(*relay.take())
                    elif worker_result:
                        self.reply(*relay.finish(worker_result[1], message))
                    else:
                        self.reply(*relay.submit(message))
                    return
            upstream = http.client.HTTPConnection("127.0.0.1", config.upstream_port, timeout=10)
            try:
                upstream.request(method, self.path, body=body, headers=headers)
                response = upstream.getresponse()
                status = response.status
                data = response.read(256 * 1024 + 1)
                if len(data) > 256 * 1024:
                    raise ValueError("Response too large")
            except (OSError, ValueError, http.client.HTTPException):
                self.reply(502, {"error": "PC 명령 서버에 연결할 수 없습니다."})
                return
            finally:
                upstream.close()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.forward("GET")

        def do_POST(self):
            self.forward("POST")

    with ThreadingHTTPServer(("127.0.0.1", config.port), Handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
