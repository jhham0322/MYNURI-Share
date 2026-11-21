"""최근 며칠의 대화를 그대로 남기는 이력 로그.

`memories.jsonl`(기억)과 역할이 다르다.
- 기억: 다시 쓸 만한 **사실**만 골라 담아 질문에 답하는 데 쓴다.
- 이 로그: 주고받은 말을 **그대로** 시간순으로 남겨 무슨 이야기를 했는지 되짚는 데 쓴다.

보관 기간(기본 3일)이 지난 날짜는 처리기가 요약한 뒤 원문에서 제거한다.
요약 저장에 실패하면 원문을 보존한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from nuri.tasks.base import TaskResult

logger = logging.getLogger("nuri.rag")

ROLE_USER = "user"
ROLE_NURI = "nuri"
ROLE_MEMO = "memo"  # 메모 모드에서 받아쓴 발화(호출어 없이 기록된 말)


@dataclass(frozen=True)
class Turn:
    """대화 한 줄. `at` 은 `YYYY-MM-DDTHH:MM:SS` 형식이라 문자열 비교로 시간 순서를 판단한다."""

    at: str
    role: str
    text: str
    provider: str | None = None
    fallback_from: str | None = None

    def to_json(self) -> dict[str, str]:
        data = {"at": self.at, "role": self.role, "text": self.text}
        if self.provider:
            data["provider"] = self.provider
        if self.fallback_from:
            data["fallback_from"] = self.fallback_from
        return data

    @classmethod
    def from_json(cls, data: object) -> Turn | None:
        if not isinstance(data, dict):
            return None
        at = data.get("at")
        text = data.get("text")
        if not isinstance(at, str) or not isinstance(text, str) or not at:
            return None
        provider = data.get("provider")
        fallback = data.get("fallback_from")
        return cls(at=at, role=str(data.get("role", ROLE_USER)), text=text,
                   provider=provider if isinstance(provider, str) else None,
                   fallback_from=fallback if isinstance(fallback, str) else None)


def read_turns(path: Path, keep_days: int = 3, max_items: int = 3000) -> list[Turn]:
    """파일에서 대화를 **읽기만** 한다(정리·저장 없음).

    대화 보기 창처럼 다른 프로세스가 여는 경우에 쓴다. `ConversationLog` 는
    읽으면서 오래된 줄을 정리해 파일을 다시 쓰는데, 그 사이 앱이 덧붙인 줄을
    덮어쓸 수 있기 때문이다.
    """
    file = Path(path)
    if not file.exists():
        return []
    try:
        raw = file.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("대화 이력을 읽지 못했습니다(%s): %s", file, exc)
        return []

    # 요약 실패로 보존 중인 오래된 대화도 기록 창에서 볼 수 있어야 한다.
    return parse_turns(raw)


def parse_turns(raw: str) -> list[Turn]:
    """jsonl 본문을 `Turn` 목록으로 바꾼다(손상된 줄은 건너뛴다)."""
    turns: list[Turn] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            turn = Turn.from_json(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("손상된 대화 줄을 건너뜁니다: %s", line[:60])
            continue
        if turn is not None:
            turns.append(turn)
    return turns


def read_daily_summaries(path: Path) -> dict[str, dict]:
    """일자별 요약을 읽는다. 손상된 파일은 덮어쓰지 않도록 오류를 전달한다."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or any(
        not isinstance(v, dict) or not isinstance(v.get("ids"), list)
        or not isinstance(v.get("excerpts"), list) or not isinstance(v.get("count"), int)
        for v in data.values()
    ):
        raise ValueError("일자별 대화 요약 형식이 올바르지 않습니다")
    return data


class ConversationLog:
    """최근 대화를 남기고, 만료된 날짜는 백그라운드 요약 성공 후 정리한다."""

    def __init__(
        self,
        path: Path,
        keep_days: int = 3,
        max_items: int = 3000,
        max_text_chars: int = 300,
    ) -> None:
        self.path = Path(path)
        self.keep_days = max(1, keep_days)
        self.max_items = max(1, max_items)
        self.max_text_chars = max_text_chars
        self._turns: list[Turn] = []
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._load()

    # --- 조회 ---
    def recent(self, limit: int = 10) -> list[Turn]:
        """최근 대화를 시간순으로 돌려준다."""
        with self._lock:
            return self._turns[-limit:] if limit > 0 else []

    def __len__(self) -> int:
        with self._lock:
            return len(self._turns)

    # --- 변경 ---
    def append(self, role: str, text: str, provider: str | None = None, fallback_from: str | None = None) -> bool:
        """대화 한 줄을 남긴다. 빈 문자열은 저장하지 않는다."""
        body = " ".join(text.split())
        if not body:
            return False
        with self._lock:
            self._turns.append(
                Turn(at=datetime.now().isoformat(timespec="seconds"), role=role, text=body,
                     provider=provider, fallback_from=fallback_from)
            )
            self._save()
        return True

    # --- 보관 기간 정리 ---
    def _cutoff(self) -> str:
        return (datetime.now() - timedelta(days=self.keep_days)).isoformat(timespec="seconds")

    def start_summary_worker(
        self, summarize: Callable[[str, list[Turn], str], TaskResult], interval: float = 300.0
    ) -> None:
        """대화가 없어도 만료분을 검사한다. 외부 처리기 호출은 청취 스레드 밖에서 한다."""
        with self._lock:
            if self._worker is not None:
                return
            def run() -> None:
                while not self._stop.is_set():
                    try:
                        self.summarize_expired(summarize)
                    except Exception as exc:  # noqa: BLE001 - 실패 시 원문을 남기고 다음 주기에 재시도
                        logger.warning("대화 요약 실패, 원문 보존: %s", exc)
                    self._stop.wait(max(1.0, interval))
            self._worker = threading.Thread(target=run, name="nuri-daily-summary", daemon=True)
            self._worker.start()

    def summarize_expired(self, summarize: Callable[[str, list[Turn], str], TaskResult]) -> None:
        """완전히 만료된 날짜를 요약 후 원자 저장한다. 결과가 없으면 원문을 남긴다."""
        with self._lock:
            cutoff_day = self._cutoff()[:10]
            days = sorted({t.at[:10] for t in self._turns if t.at[:10] < cutoff_day})
        for day in days:
            with self._lock:
                batch = [t for t in self._turns if t.at[:10] == day]
            if not batch:
                continue
            payload = json.dumps([t.to_json() for t in batch], ensure_ascii=False, sort_keys=True)
            batch_id = hashlib.sha256(payload.encode()).hexdigest()
            archive_path = self.path.with_name("daily_summaries.json")
            pending_path = self.path.with_name("daily_summary_pending.json")
            archive = read_daily_summaries(archive_path)
            old = archive.get(day, {"count": 0, "excerpts": [], "ids": []})
            if batch_id not in old["ids"]:
                pending = json.loads(pending_path.read_text(encoding="utf-8")) if pending_path.exists() else {}
                if not isinstance(pending, dict):
                    raise ValueError("대화 요약 실행 상태가 올바르지 않습니다")
                if batch_id in pending:
                    logger.warning("%s 대화 요약의 이전 실행 상태 불명: 자동 재실행하지 않습니다", day)
                    continue
                # 중간 종료나 불확실한 실패 뒤 도구 작업이 중복 실행되지 않게 먼저 기록한다.
                pending[batch_id] = {"day": day, "status": "running_or_uncertain"}
                tmp_pending = pending_path.with_name(pending_path.name + ".tmp")
                pending_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_pending.write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp_pending, pending_path)
                result = summarize(day, batch, str(old.get("summary", "")))
                if not isinstance(result, TaskResult) or not result.text.strip():
                    logger.warning("%s 대화 요약 실패: 원문 보존", day)
                    if isinstance(result, TaskResult) and result.retry_safe:
                        pending.pop(batch_id, None)
                        tmp_pending.write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")
                        os.replace(tmp_pending, pending_path)
                    continue
                old["summary"] = result.text.strip()
                old["provider"] = result.provider
                old["fallback_from"] = result.fallback_from
                old["count"] += len(batch)
                old["ids"].append(batch_id)
                archive[day] = old
                archive_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = archive_path.with_name(archive_path.name + ".tmp")
                with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                    json.dump(archive, handle, ensure_ascii=False, indent=2)
                os.replace(tmp, archive_path)
            if pending_path.exists():
                pending = json.loads(pending_path.read_text(encoding="utf-8"))
                if isinstance(pending, dict) and batch_id in pending:
                    pending.pop(batch_id)
                    tmp_pending = pending_path.with_name(pending_path.name + ".tmp")
                    tmp_pending.write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")
                    os.replace(tmp_pending, pending_path)
            with self._lock:
                remaining = Counter(batch)
                kept = []
                for turn in self._turns:
                    if remaining[turn]:
                        remaining[turn] -= 1
                    else:
                        kept.append(turn)
                self._turns = kept
                self._save()

    # --- 저장소 입출력 ---
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("대화 이력을 읽지 못했습니다(%s): %s", self.path, exc)
            return
        self._turns.extend(parse_turns(raw))


    def _save(self) -> None:
        """임시 파일에 쓴 뒤 교체한다(중간에 죽어도 파일이 깨지지 않게)."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                for turn in self._turns:
                    handle.write(json.dumps(turn.to_json(), ensure_ascii=False) + "\n")
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.warning("대화 이력 저장에 실패했습니다(%s): %s", self.path, exc)
