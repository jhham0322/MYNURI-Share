"""누리 프로세스를 새 코드로 다시 띄운다.

자기 개선이 끝난 뒤 새 코드를 적용하려면 프로세스를 다시 시작해야 한다. 그런데
현재 프로세스가 직접 새 프로세스를 띄우면 중복 실행 방지용 명명 뮤텍스
(`NuriVoiceAssistant`) 때문에 새 프로세스가 곧바로 종료된다.

그래서 **부모 종료를 기다리는 도우미**를 먼저 띄우고, 부모는 스스로 종료한다.
도우미는 부모 PID 가 사라진 것을 확인한 뒤 잠깐 기다렸다가(핸들 정리 여유)
새 프로세스를 띄운다. 도우미를 띄우지 못하면 부모는 종료하지 않고 실패를 알린다.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from collections.abc import Sequence

logger = logging.getLogger("nuri.restart")

DEFAULT_GRACE = 0.4  # 부모 종료 후 새 프로세스 시작까지 여유(뮤텍스 정리 대기)
DEFAULT_TIMEOUT = 60.0  # 부모 종료를 기다리는 최대 시간


def _detached_flags() -> int:
    """부모와 분리된 무콘솔 프로세스로 띄우기 위한 플래그."""
    if os.name != "nt":
        return 0
    return (
        getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )


def restart_process(
    cmd: Sequence[str],
    grace: float = DEFAULT_GRACE,
    timeout: float = DEFAULT_TIMEOUT,
) -> bool:
    """도우미를 띄워 새 프로세스를 준비시킨다. 성공하면 호출부가 종료하면 된다.

    도우미를 띄우지 못하면 False 를 돌려주고 앱은 계속 살아 있어야 한다
    (실패했는데 종료하면 누리가 사라진다).
    """
    if not cmd:
        logger.warning("재기동 명령이 비어 있습니다")
        return False

    helper = [
        sys.executable,
        "-m",
        "nuri.core.restart",
        "--pid",
        str(os.getpid()),
        "--grace",
        str(grace),
        "--timeout",
        str(timeout),
        "--",
        *cmd,
    ]
    try:
        subprocess.Popen(
            helper,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_detached_flags(),
        )
    except OSError as exc:
        logger.exception("재기동 도우미 실행 실패: %s", exc)
        return False

    logger.info("재기동 도우미 시작(부모 pid=%s)", os.getpid())
    return True


def _wait_windows(pid: int, timeout: float) -> None:
    """Windows: 프로세스 핸들로 종료를 기다린다(os.kill 은 위험해 쓰지 않는다)."""
    import ctypes

    synchronize = 0x00100000
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        time.sleep(0.5)  # 이미 종료된 프로세스
        return
    try:
        kernel32.WaitForSingleObject(handle, int(timeout * 1000))
    finally:
        kernel32.CloseHandle(handle)


def _alive_posix(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def wait_for_exit(pid: int, timeout: float = DEFAULT_TIMEOUT) -> None:
    """지정한 프로세스가 끝날 때까지 기다린다(최대 timeout)."""
    if os.name == "nt":
        _wait_windows(pid, timeout)
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive_posix(pid):
            return
        time.sleep(0.1)


def main(argv: list[str] | None = None) -> int:
    """도우미 진입점: 부모가 끝나면 같은 명령으로 누리를 다시 띄운다."""
    parser = argparse.ArgumentParser(prog="nuri-restart", description="누리 재기동 도우미")
    parser.add_argument("--pid", type=int, required=True, help="기다릴 부모 프로세스 id")
    parser.add_argument("--grace", type=float, default=DEFAULT_GRACE)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- 뒤에 새로 실행할 명령")
    args = parser.parse_args(argv)

    command = [part for part in args.command if part != "--"]
    if not command:
        print("재기동 명령이 없습니다", file=sys.stderr)
        return 2

    wait_for_exit(args.pid, args.timeout)
    time.sleep(max(0.0, args.grace))
    try:
        subprocess.Popen(
            command,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=os.getcwd(),
            env=os.environ.copy(),
            creationflags=_detached_flags(),
        )
    except OSError as exc:
        print(f"누리를 다시 띄우지 못했습니다: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
