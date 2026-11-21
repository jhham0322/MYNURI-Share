"""Dispatch Gemini requests through Google's Antigravity CLI."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from nuri.tasks.base import TaskResult
from nuri.tasks.paseo import condense_for_speech, is_full_read_request


def executable():
    found = shutil.which("agy")
    if found:
        return found
    local = os.getenv("LOCALAPPDATA")
    if local:
        path = Path(local) / "agy" / "bin" / "agy.exe"
        if path.is_file():
            return str(path)
    return None


def run_gemini(cfg, text: str) -> TaskResult:
    full = is_full_read_request(text)
    limit = cfg.max_full_chars if full else cfg.max_reply_chars
    prompt = text if full else f"{text}\n\n답변은 한국어로 작성하고 마지막 줄에 요약: 으로 시작하는 {limit}자 이내 요약을 써 주세요."
    if cfg.provider == "gemini-2":
        return TaskResult("두 번째 Antigravity 계정이 연결되지 않았습니다. 같은 Windows 사용자에서는 Antigravity 로그인이 공유됩니다.",
                          provider=cfg.provider)
    exe = executable()
    if not exe:
        return TaskResult("PC에 Google Antigravity CLI를 설치해 주세요.", provider=cfg.provider)
    env = os.environ.copy()
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "AGY_ADC_AUTH"):
        env.pop(name, None)
    args = [exe, "--output-format", "json", "--disable-slash-commands"]
    if cfg.agent_id:
        args += ["--conversation", cfg.agent_id]
    args.append("--print=" + prompt)
    try:
        result = subprocess.run(
            args, cwd=cfg.cwd, env=env, capture_output=True, encoding="utf-8",
            errors="replace", timeout=cfg.wait_timeout_seconds,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        data = json.loads(result.stdout)
        reply = data.get("response")
        if result.returncode == 0 and data.get("status") == "SUCCESS" and isinstance(reply, str) and reply.strip():
            session = data.get("conversation_id")
            return TaskResult(
                condense_for_speech(reply, limit, full=full), provider=cfg.provider,
                agent_id=session if isinstance(session, str) else None, agent_reply_chars=len(reply),
            )
    except subprocess.TimeoutExpired:
        return TaskResult("Gemini 응답 대기 시간이 지났습니다.", provider=cfg.provider)
    except (OSError, ValueError, AttributeError):
        pass
    return TaskResult("Gemini 응답을 받지 못했습니다. PC에서 해당 계정의 Google 로그인 상태를 확인해 주세요.",
                      provider=cfg.provider)
