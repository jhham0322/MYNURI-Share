"""메모 모드에서 받아쓴 발화를 모아 요약하는 도우미.

요약은 paseo 에이전트 호출이라 수십 초 걸릴 수 있다. 그동안에도 받아쓰기는 계속돼야
하므로 요약은 별도 스레드에서 처리하고, 완료된 요약만 큐에 쌓아 두었다가 앱이
조용한 시점에 꺼내 안내한다.

`start()` 를 부르지 않으면 스레드를 띄우지 않는다. 그때는 `drain()` 으로 지금
스레드에서 처리한다(텍스트 모드·테스트에서 결정론적으로 검증하기 위함).
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

from nuri.tasks.base import TaskResult

logger = logging.getLogger("nuri.memo")

Summary = Callable[[str], "str | TaskResult"]


class MemoSession:
    """받아쓴 발화를 모아 두었다가 요약 단위(`every`)가 되면 넘긴다."""

    def __init__(self, every: int = 5, max_chars: int = 1200) -> None:
        self.every = max(1, every)
        self.max_chars = max_chars
        self.total = 0  # 이번 메모 세션에서 받아쓴 발화 수
        self._lines: list[str] = []

    def __len__(self) -> int:
        return len(self._lines)

    def add(self, text: str) -> bool:
        """발화를 더한다. 요약할 분량이 되면 True."""
        body = " ".join(text.split())
        if not body:
            return False
        self._lines.append(body)
        self.total += 1
        return len(self._lines) >= self.every

    def take(self) -> str:
        """모아 둔 발화를 꺼내고 비운다(없으면 빈 문자열)."""
        body = " ".join(self._lines)[: self.max_chars]
        self._lines.clear()
        return body

    def reset(self) -> None:
        """새 메모 세션을 시작한다(모드 진입 시)."""
        self._lines.clear()
        self.total = 0


class MemoSummarizer:
    """요약 요청을 백그라운드에서 처리하고 완료된 결과를 쌓아 둔다."""

    def __init__(self, summarize: Summary, max_pending: int = 4) -> None:
        self._summarize = summarize
        self._jobs: "queue.Queue[str | None]" = queue.Queue(maxsize=max_pending)
        self._results: "queue.Queue[TaskResult]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # --- 공개 ---
    def start(self) -> None:
        """백그라운드 요약 스레드를 띄운다(이미 떠 있으면 아무것도 하지 않는다)."""
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._loop, name="nuri-memo", daemon=True)
            self._thread.start()

    def submit(self, body: str) -> bool:
        """요약할 발화 뭉치를 맡긴다. 대기열이 가득 차면 False."""
        if not body.strip():
            return False
        try:
            self._jobs.put_nowait(body)
        except queue.Full:
            logger.warning("요약 대기열이 가득 차 이번 분량은 건너뜁니다")
            return False
        return True

    def take(self) -> list[TaskResult]:
        """완료된 요약을 모두 꺼낸다."""
        done: list[TaskResult] = []
        while True:
            try:
                done.append(self._results.get_nowait())
            except queue.Empty:
                return done

    def drain(self) -> None:
        """남은 요약 요청을 지금 스레드에서 처리한다."""
        while True:
            try:
                body = self._jobs.get_nowait()
            except queue.Empty:
                return
            if body is not None:
                self._run(body)

    def pending(self) -> int:
        return self._jobs.qsize()

    # --- 내부 ---
    def _loop(self) -> None:
        while True:
            body = self._jobs.get()
            if body is None:
                return
            self._run(body)

    def _run(self, body: str) -> None:
        try:
            result = self._summarize(body)
        except Exception as exc:  # noqa: BLE001 - 요약 실패가 받아쓰기를 막지 않는다
            logger.warning("메모 요약 실패: %s", exc)
            return
        if isinstance(result, TaskResult):
            text, notes = result.text, result.notes
        else:
            text, notes = str(result), ()
        text = " ".join(text.split())
        if not text:
            return
        self._results.put(TaskResult(text, tuple(notes)))
