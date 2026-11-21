"""대화 중 요청받은 개선을 코드에 반영하고, 검증을 통과하면 재기동을 예약한다.

에이전트(fullAccess)가 코드를 망가뜨릴 수 있으므로 다음 순서를 지킨다.
1) 기준선: 지금 테스트가 통과하는지, 테스트가 몇 건인지 기록
2) 스냅샷: `backup/selfimprove_<시각>/` 에 src/nuri·tests·pyproject.toml 사본
3) 에이전트에게 최소 변경으로 구현하도록 지시
4) 변경 감지: 파일 해시가 그대로면 재기동하지 않는다
5) 검증: compileall + unittest, **테스트 수가 줄면 실패**(테스트 약화 감지)
6) 실패하면 스냅샷 복원(추가된 파일 삭제, 삭제된 파일 복구 포함)
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from nuri.config import SelfImproveConfig

logger = logging.getLogger("nuri.improve")

MSG_APPLIED = "개선을 적용했어요. 잠시 후 누리를 다시 시작할게요."
MSG_UNCHANGED = "요청은 전달했지만 코드가 바뀌지 않았어요."
MSG_VERIFY_FAILED = "개선을 적용하지 않았어요. 검증이 실패해서 이전 상태로 되돌렸어요."
MSG_TASK_FAILED = "개선 작업을 마치지 못했어요. 이전 상태로 되돌렸어요."
MSG_UNAVAILABLE = "지금은 스스로 개선할 수 없어요."

STATUS_APPLIED = "applied"
STATUS_UNCHANGED = "unchanged"
STATUS_VERIFY_FAILED = "verify_failed"
STATUS_FAILED = "failed"
STATUS_UNAVAILABLE = "unavailable"

_TEST_COUNT = re.compile(r"Ran (\d+) tests?")
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")

# 하위 프로세스 실행: (args, timeout, cwd, env) → (returncode, stdout, stderr)
Runner = Callable[[Sequence[str], float, Path, dict[str, str]], tuple[int, str, str]]


def subprocess_run(
    args: Sequence[str], timeout: float, cwd: Path, env: dict[str, str]
) -> tuple[int, str, str]:
    """검증 명령 실행. 무콘솔 실행에서 검은 창이 뜨지 않도록 플래그를 준다."""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.run(
        list(args),
        capture_output=True,
        timeout=timeout,
        check=False,
        cwd=str(cwd),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


@dataclass(frozen=True)
class ImproveResult:
    status: str
    message: str
    provider: str | None = None
    fallback_from: str | None = None


class SelfImprover:
    """개선 요청 → 코드 반영 → 검증 → (성공 시) 재기동 예약."""

    def __init__(
        self,
        root: Path,
        cfg: SelfImproveConfig,
        task: Callable[[str], str],
        runner: Runner = subprocess_run,
    ) -> None:
        self.root = Path(root)
        self.cfg = cfg
        self.task = task
        self._run = runner

    # --- 공개 ---
    def available(self) -> bool:
        """소스 저장소에서 실행 중일 때만 자기 개선이 가능하다."""
        return (
            self.cfg.enabled
            and (self.root / "AGENTS.md").exists()
            and (self.root / "src" / "nuri").is_dir()
            and (self.root / "tests").is_dir()
        )

    def run(self, request: str) -> ImproveResult:
        if not self.available():
            logger.warning("자기 개선 비활성(저장소 구조 아님): %s", self.root)
            return ImproveResult(STATUS_UNAVAILABLE, MSG_UNAVAILABLE)

        baseline_ok, baseline_count = self._run_tests()
        if not baseline_ok:
            logger.warning("기준선 테스트가 실패해 개선을 시작하지 않습니다")
            return ImproveResult(STATUS_UNAVAILABLE, MSG_UNAVAILABLE)

        snapshot = self._snapshot()
        before = self._digest()
        try:
            task_result = self.task(self._prompt(request))
        except Exception as exc:  # noqa: BLE001 - 실패해도 이전 상태로 되돌린다
            logger.exception("개선 작업 실패: %s", exc)
            self._restore(snapshot)
            return ImproveResult(STATUS_FAILED, MSG_TASK_FAILED)

        if self._digest() == before:
            logger.info("개선 요청 후 코드 변경 없음")
            return ImproveResult(STATUS_UNCHANGED, MSG_UNCHANGED, getattr(task_result, "provider", None), getattr(task_result, "fallback_from", None))

        ok, detail = self._verify(baseline_count)
        if not ok:
            logger.warning("개선 검증 실패(%s) → 이전 상태로 복원", detail)
            self._restore(snapshot)
            return ImproveResult(STATUS_VERIFY_FAILED, MSG_VERIFY_FAILED, getattr(task_result, "provider", None), getattr(task_result, "fallback_from", None))

        logger.info("개선 적용 완료(테스트 %d건 이상 통과)", baseline_count)
        return ImproveResult(STATUS_APPLIED, MSG_APPLIED, getattr(task_result, "provider", None), getattr(task_result, "fallback_from", None))

    # --- 에이전트 지시 ---
    def _prompt(self, request: str) -> str:
        """에이전트에게 보낼 지시문.

        `paseo run` 은 프롬프트의 첫 줄만 전달하므로 줄바꿈 없이 한 줄로 만든다.
        """
        return (
            f"저장소(현재 디렉터리)의 누리 음성 비서 코드를 다음 요청에 맞게 개선하세요: {request}. "
            "규칙: (1) src/nuri, tests, pyproject.toml 범위만 최소 변경 "
            "(2) 기존 테스트를 삭제하거나 약화하지 말 것 "
            "(3) 수정 전 backup/<작업코드>_<파일명> 사본 생성 "
            "(4) PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' 로 전체 통과 확인 "
            "(5) AGENTS.md 규칙 준수. 완료하면 무엇을 바꿨는지 한 문장으로 알려주세요."
        )

    # --- 검증 ---
    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.root / "src")
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def _run_tests(self) -> tuple[bool, int]:
        """테스트 통과 여부와 건수(기준선·검증 공용)."""
        code, out, err = self._run(
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_*.py",
            ],
            self.cfg.verify_timeout_seconds,
            self.root,
            self._env(),
        )
        match = _TEST_COUNT.search(f"{out}\n{err}")
        count = int(match.group(1)) if match else 0
        return code == 0, count

    def _verify(self, baseline_count: int) -> tuple[bool, str]:
        code, out, err = self._run(
            [sys.executable, "-m", "compileall", "-q", "src"],
            self.cfg.verify_timeout_seconds,
            self.root,
            self._env(),
        )
        if code != 0:
            return False, f"compileall 실패: {(err or out).strip()[:200]}"

        ok, count = self._run_tests()
        if not ok:
            return False, "테스트 실패"
        if count < baseline_count:
            return False, f"테스트 수 감소({baseline_count} → {count})"
        return True, ""

    # --- 스냅샷/복원 ---
    def _snapshot(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = self.root / "backup" / f"selfimprove_{stamp}"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.root / "src" / "nuri", dest / "nuri", ignore=_IGNORE)
        shutil.copytree(self.root / "tests", dest / "tests", ignore=_IGNORE)
        shutil.copy2(self.root / "pyproject.toml", dest / "pyproject.toml")
        logger.info("개선 전 스냅샷: %s", dest)
        self._prune_snapshots()
        return dest

    def _restore(self, snapshot: Path) -> None:
        """스냅샷 시점으로 되돌린다(추가된 파일은 지우고, 삭제된 파일은 되살린다)."""
        for name, target in (
            ("nuri", self.root / "src" / "nuri"),
            ("tests", self.root / "tests"),
        ):
            source = snapshot / name
            if not source.is_dir():
                continue
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)
        pyproject = snapshot / "pyproject.toml"
        if pyproject.exists():
            shutil.copy2(pyproject, self.root / "pyproject.toml")
        self._clear_pycache()
        logger.warning("개선 이전 상태로 복원: %s", snapshot)

    def _clear_pycache(self) -> None:
        for base in (self.root / "src", self.root / "tests"):
            for path in base.rglob("__pycache__"):
                shutil.rmtree(path, ignore_errors=True)

    def _prune_snapshots(self) -> None:
        keep = self.cfg.keep_snapshots
        if keep <= 0:
            return
        snapshots = sorted((self.root / "backup").glob("selfimprove_*"))
        for old in snapshots[:-keep]:
            shutil.rmtree(old, ignore_errors=True)

    def _digest(self) -> dict[str, str]:
        """변경 감지용 파일 해시(파이썬 소스만)."""
        files = sorted(
            list((self.root / "src" / "nuri").rglob("*.py"))
            + list((self.root / "tests").rglob("*.py"))
        )
        return {
            str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files
        }
