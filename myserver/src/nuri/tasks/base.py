"""태스크 실행기가 공유하는 하위 프로세스 실행 유틸.

외부 CLI 호출을 한 곳에 모아 두고, 테스트에서는 러너를 갈아끼워
실제 프로세스 없이 검증할 수 있게 한다.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger("nuri.tasks")

RunResult = tuple[int, str, str]  # (returncode, stdout, stderr)


@dataclass(frozen=True)
class TaskResult:
    """태스크 실행 결과.

    `text` 는 음성으로 읽을 문장이고, `notes` 는 결과를 요약하는 과정에서 드러난
    다시 쓸 만한 사실(이름·경로·날짜·결정 사항)이다. 앱은 `notes` 를 기억
    저장소에 따로 남겨 다음 대화에서 참고한다.
    """

    text: str
    notes: tuple[str, ...] = ()
    provider: str | None = None
    fallback_from: str | None = None
    retry_safe: bool = False
    agent_id: str | None = None
    agent_reply_chars: int = 0


class CommandRunner(Protocol):
    """CLI 실행 인터페이스. 타임아웃은 예외로 알린다."""

    def __call__(self, args: Sequence[str], timeout: float) -> RunResult: ...


def _decode(data: bytes) -> str:
    """CLI 출력 디코딩. UTF-8 우선, 실패 시 cp949(한국어 Windows) 폴백."""
    for encoding in ("utf-8", "cp949"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def resolve_executable(name: str) -> str:
    """PATH 에서 실행 파일을 찾는다.

    Windows 의 `paseo` 는 `paseo.CMD` 로 배포되며, subprocess 는 PATHEXT 를
    스스로 확장하지 않으므로 shutil.which 로 먼저 절대 경로를 확보해야 한다.
    """
    return shutil.which(name) or name


def _command_args(args: Sequence[str]) -> list[str]:
    if os.name != "nt" or Path(args[0]).suffix.lower() not in (".cmd", ".bat"):
        return list(args)

    # npm의 Windows 배치 파일을 통하지 않고 Node에 인수를 직접 넘긴다.
    shim = Path(args[0])
    if shim.name.lower() == "paseo.cmd":
        package = Path("@getpaseo/cli/bin/paseo")
        npm_root = shim.parent.parent if shim.parent.name == ".bin" else shim.parent / "node_modules"
        entrypoint = npm_root / package
        bundled_node = shim.parent / "node.exe"
        node = str(bundled_node) if bundled_node.is_file() else shutil.which("node.exe")
        if entrypoint.is_file() and node:
            return [node, "--disable-warning=DEP0040", str(entrypoint), *args[1:]]
    raise OSError("Windows에서는 npm Paseo CLI 또는 네이티브 실행 파일을 사용해 주세요.")


def subprocess_runner(args: Sequence[str], timeout: float) -> RunResult:
    """하위 프로세스 실행. 타임아웃이면 subprocess.TimeoutExpired 를 그대로 올린다.

    pythonw(무콘솔) 실행에서 검은 창이 뜨지 않도록 CREATE_NO_WINDOW 를 준다.
    """
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    # 누리가 개발 에이전트에서 재기동돼도 음성 작업은 독립 Paseo 작업이어야 한다.
    # 홈·연결 설정은 유지하고 에이전트 소속만 제거한다.
    env = os.environ.copy()
    for name in ("PASEO_AGENT_ID", "PASEO_AGENT_CWD"):
        env.pop(name, None)
    proc = subprocess.run(
        _command_args(args),
        capture_output=True,
        timeout=timeout,
        check=False,
        creationflags=creationflags,
        env=env,
    )
    return proc.returncode, _decode(proc.stdout), _decode(proc.stderr)
