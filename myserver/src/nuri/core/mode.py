"""누리 동작 모드(대기/메모)와 그 상태 저장.

- 대기 모드(`standby`): 호출어("누리야")로 부르면 지시를 처리한다(기본값).
- 메모 모드(`memo`): 호출어 없이 들어오는 말을 자동으로 받아쓰고 요약한다.

모드는 파일에 저장해 두고 앱이 구동될 때 다시 읽는다. 그래서 재기동·재부팅
뒤에도 마지막 모드로 이어진다.
"""

from __future__ import annotations

import json
import logging
import os
from enum import Enum
from pathlib import Path

logger = logging.getLogger("nuri.mode")


class Mode(str, Enum):
    STANDBY = "standby"
    MEMO = "memo"

    @property
    def label(self) -> str:
        """음성 안내/로그에 쓰는 한국어 이름."""
        return "메모 모드" if self is Mode.MEMO else "대기 모드"


class ModeStore:
    """마지막 모드를 `mode.json` 한 파일에 저장한다."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> Mode:
        """저장된 모드를 읽는다. 파일이 없거나 손상됐으면 대기 모드."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return Mode.STANDBY
        value = str(data.get("mode", "")).lower() if isinstance(data, dict) else ""
        if value == Mode.MEMO.value:
            return Mode.MEMO
        return Mode.STANDBY

    def save(self, mode: Mode) -> None:
        """모드를 저장한다(임시 파일에 쓴 뒤 교체)."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"mode": mode.value}), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.warning("모드 저장에 실패했습니다(%s): %s", self.path, exc)
