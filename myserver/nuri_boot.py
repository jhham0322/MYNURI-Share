"""작업 스케줄러(감시)용 누리 런처.

상시 실행은 이 파일을 Windows 작업 스케줄러로 띄운다. 에이전트 세션의 자식
프로세스로 띄우면 세션이 끝날 때 함께 강제 종료되어, 로그 한 줄 없이 누리가
사라진다(2026-11-16 08:10 사고). 작업 스케줄러가 띄운 프로세스는 세션과
무관하게 유지되고, 누리가 죽으면 1분 안에 다시 띄운다.

- 트레이에서 "누리 종료"를 누르면 `~/nuri/stopped.flag` 가 생긴다.
  이 파일이 있으면 감시 재시작을 건너뛴다(사용자가 끈 것을 되살리지 않는다).
- 실행 인자·환경변수는 여기 한 곳에서만 관리한다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOME = Path.home()


def _workspace_id() -> str | None:
    """paseo 작업 공간 id. 저장소에 값을 남기지 않도록 환경/로컬 파일에서 읽는다."""
    value = os.environ.get("NURI_PASEO_WORKSPACE_ID")
    if value:
        return value
    local = HOME / "nuri" / "workspace_id.txt"
    try:
        text = local.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def main() -> int:
    if (HOME / "nuri" / "stopped.flag").exists():
        return 0  # 사용자가 트레이에서 종료함 → 되살리지 않는다

    workspace = _workspace_id()
    if workspace:
        os.environ["NURI_PASEO_WORKSPACE_ID"] = workspace

    # 자기 개선 재기동은 `python -m nuri.core.restart` 도우미를 새로 띄우므로,
    # 자식 프로세스가 같은 코드를 찾도록 PYTHONPATH 를 남긴다.
    os.environ["PYTHONPATH"] = str(ROOT / "src")
    sys.path.insert(0, str(ROOT / "src"))
    from nuri.__main__ import main as nuri_main

    return nuri_main(
        [
            "--mic",
            "--stt",
            "--tts",
            "--continuous",
            "--mobile",
            "--paseo",
            "--paseo-cwd",
            str(HOME),
            "--log-file",
            str(HOME / "nuri" / "logs" / "nuri.log"),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
