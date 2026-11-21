"""사용자가 선택한 설정의 검증과 저장."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from nuri.config import Config, PaseoConfig

logger = logging.getLogger("nuri.settings")
PROVIDERS = {"codex": "Codex", "codebuddy-code": "CodeBuddy",
             "gemini": "Antigravity 계정 1", "gemini-2": "Antigravity 계정 2 · 연결 안 됨"}


@dataclass(frozen=True)
class UserSettings:
    provider: str = "codebuddy-code"
    wait_seconds: int = 180
    speech_seconds: int = 10
    followup_enabled: bool = True
    followup_seconds: int = 8
    # 말 속도(edge-tts rate). 음성으로 "더 느리게" 요청하면 여기에 저장한다.
    tts_rate: str = "+0%"

    def validate(self) -> None:
        if self.provider not in PROVIDERS:
            raise ValueError("작업 처리기를 선택해 주세요.")
        for value, low, high, name in (
            (self.wait_seconds, 30, 1800, "작업 대기 시간"),
            (self.speech_seconds, 3, 120, "일반 응답 길이"),
            (self.followup_seconds, 1, 60, "후속 대화 대기 시간"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{name}은 {low}~{high}초로 입력해 주세요.")
        if not isinstance(self.followup_enabled, bool):
            raise ValueError("후속 대화 설정이 올바르지 않아요.")
        match = re.fullmatch(r"([+-]?\d{1,3})%", str(self.tts_rate))
        if not match or not -40 <= int(match.group(1)) <= 40:
            raise ValueError("말 속도는 -40% ~ +40% 범위로 입력해 주세요.")


class SettingsStore:
    def __init__(self, cfg: Config, path: Path) -> None:
        self.cfg = cfg
        self.path = Path(path)
        self._lock = threading.RLock()

    def current(self) -> UserSettings:
        with self._lock:
            return UserSettings(
                self.cfg.paseo.provider, int(self.cfg.paseo.wait_timeout_seconds),
                int(self.cfg.paseo.speech_seconds), self.cfg.loop.followup_enabled,
                int(self.cfg.loop.followup_timeout_ms / 1000), self.cfg.tts.rate,
            )

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("설정 파일 형식이 올바르지 않습니다")
            data = asdict(self.current())
            data.update({key: raw[key] for key in data if key in raw})
            settings = UserSettings(**data)
            settings.validate()
            self._apply(settings)
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("설정을 읽지 못해 기본값을 사용합니다: %s", exc)

    def save(self, settings: UserSettings) -> None:
        settings.validate()
        if self.cfg.paseo.agent_id and settings.provider != self.cfg.paseo.provider:
            raise ValueError("기존 에이전트를 지정한 실행에서는 처리기를 변경할 수 없어요.")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
            self._apply(settings)

    def set_provider(self, provider: str) -> UserSettings:
        """다른 설정을 보존하며 처리기를 원자적으로 저장한다."""
        with self._lock:
            updated = replace(self.current(), provider=provider)
            self.save(updated)
            return updated

    def _apply(self, settings: UserSettings) -> None:
        with self._lock:
            self.cfg.paseo.provider = settings.provider
            # 두 제공자는 같은 전권 모드를 서로 다른 ID로 사용한다.
            if settings.provider in ("codex", "codebuddy-code") and self.cfg.paseo.mode in ("fullAccess", "full-access"):
                self.cfg.paseo.mode = "full-access" if settings.provider == "codex" else "fullAccess"
            elif settings.provider == "codex" and self.cfg.paseo.mode not in ("auto", "auto-review"):
                self.cfg.paseo.mode = "auto"
            elif settings.provider == "codebuddy-code" and self.cfg.paseo.mode not in (
                "default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions"
            ):
                self.cfg.paseo.mode = "auto"
            self.cfg.paseo.wait_timeout_seconds = settings.wait_seconds
            self.cfg.paseo.speech_seconds = settings.speech_seconds
            self.cfg.loop.followup_enabled = settings.followup_enabled
            self.cfg.loop.followup_timeout_ms = settings.followup_seconds * 1000
            # edge-tts 는 발화할 때마다 cfg.tts.rate 를 읽으므로 즉시 반영된다
            self.cfg.tts.rate = settings.tts_rate

    def task_config(self, **overrides: object) -> PaseoConfig:
        with self._lock:
            return replace(self.cfg.paseo, **overrides)
