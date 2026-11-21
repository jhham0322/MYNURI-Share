"""예약 저장과 백그라운드 실행."""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

logger = logging.getLogger("nuri.schedule")
KIND_ONCE = "once"
KIND_DAILY = "daily"
MSG_ALARM = "알람 시간이에요."
MSG_TASK_PREFIX = "예약하신 작업 결과예요. "
MSG_TASK_FAILED = "예약한 작업을 처리하지 못했어요. paseo 상태를 확인해 주세요."


@dataclass
class Schedule:
    kind: str
    id: str = ""
    at: str | None = None
    time: str | None = None
    command: str = ""
    label: str = ""
    created: str = ""
    last_fired: str | None = None
    enabled: bool = True

    def once_at(self) -> datetime | None:
        try:
            at = datetime.fromisoformat(self.at) if self.at else None
        except (ValueError, TypeError):
            return None
        return at if at is not None and at.tzinfo is None else None

    def today_at(self, now: datetime) -> datetime | None:
        try:
            clock = time.fromisoformat(self.time) if self.time else None
        except (ValueError, TypeError):
            return None
        if clock is None or clock.tzinfo is not None:
            return None
        return datetime.combine(now.date(), clock)

    def next_at(self, now: datetime) -> datetime | None:
        at = self.once_at() if self.kind == KIND_ONCE else self.today_at(now)
        if at is None:
            return None
        if self.kind == KIND_ONCE:
            return at if at > now else None
        return at if at > now else at + timedelta(days=1)

    def to_dict(self) -> dict[str, object]:
        return vars(self).copy()

    @classmethod
    def from_dict(cls, data: object) -> Schedule | None:
        if not isinstance(data, dict) or data.get("kind") not in (KIND_ONCE, KIND_DAILY):
            return None
        item = cls(kind=data["kind"], id=str(data.get("id") or uuid.uuid4().hex[:8]),
                   at=data.get("at"), time=data.get("time"), command=str(data.get("command") or ""),
                   label=str(data.get("label") or ""), created=str(data.get("created") or ""),
                   last_fired=data.get("last_fired"), enabled=data.get("enabled", True) is True)
        if (item.kind == KIND_ONCE and item.once_at() is None) or (item.kind == KIND_DAILY and item.today_at(datetime.now()) is None):
            return None
        return item


class ScheduleBook:
    def __init__(self, path: Path, max_items: int = 20) -> None:
        self.path, self.max_items = Path(path), max_items
        self._lock = threading.RLock()
        self._items: list[Schedule] = []
        self.load()

    def load(self) -> None:
        with self._lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return
            rows = raw.get("schedules", []) if isinstance(raw, dict) else raw
            if not isinstance(rows, list):
                raise ValueError("예약 파일 형식이 올바르지 않습니다")
            self._items = [item for row in rows if (item := Schedule.from_dict(row)) is not None]

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"schedules": [i.to_dict() for i in self._items]}, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)

    def items(self) -> tuple[Schedule, ...]:
        with self._lock:
            return tuple(self._items)

    def ordered(self, now: datetime) -> list[Schedule]:
        return sorted(self.items(), key=lambda i: (i.next_at(now) is None, i.next_at(now) or datetime.max))

    def add(self, item: Schedule) -> Schedule | None:
        with self._lock:
            if len(self._items) >= self.max_items:
                return None
            item.id = item.id or uuid.uuid4().hex[:8]
            item.created = item.created or datetime.now().replace(microsecond=0).isoformat()
            self._items.append(item)
            try:
                self.save()
            except OSError:
                self._items.remove(item)
                raise
            return item

    def remove(self, target: Schedule) -> bool:
        with self._lock:
            for index, item in enumerate(self._items):
                if item.id == target.id:
                    self._items.pop(index)
                    try:
                        self.save()
                    except OSError:
                        self._items.insert(index, item)
                        raise
                    return True
            return False

    def set_enabled(self, item_id: str, enabled: bool) -> bool:
        with self._lock:
            for item in self._items:
                if item.id == item_id:
                    old = item.enabled
                    item.enabled = enabled
                    try:
                        self.save()
                    except OSError:
                        item.enabled = old
                        raise
                    return True
            return False

    def update(self, item_id: str, kind: str, when: str, command: str) -> bool:
        """예약 시각과 작업을 검증해 같은 잠금 안에서 저장한다."""
        if kind == KIND_ONCE:
            try:
                moment = datetime.fromisoformat(when.strip())
            except ValueError as exc:
                raise ValueError("날짜와 시각을 YYYY-MM-DD HH:MM 형식으로 입력해 주세요.") from exc
            if moment.tzinfo is not None or moment <= datetime.now():
                raise ValueError("일회성 예약은 현재보다 이후의 현지 시각이어야 해요.")
            at, clock = moment.replace(microsecond=0).isoformat(), None
        elif kind == KIND_DAILY:
            try:
                parsed = datetime.strptime(when.strip(), "%H:%M")
            except ValueError as exc:
                raise ValueError("매일 예약 시각을 HH:MM 형식으로 입력해 주세요.") from exc
            at, clock = None, parsed.strftime("%H:%M")
        else:
            raise ValueError("예약 종류를 선택해 주세요.")
        command = command.strip()
        with self._lock:
            for item in self._items:
                if item.id != item_id:
                    continue
                old = item.to_dict()
                previous_kind = item.kind
                item.kind, item.at, item.time = kind, at, clock
                item.command, item.label = command, command or "알람"
                if previous_kind != kind:
                    item.last_fired = None
                # 지난 오늘 시각으로 수정해도 저장 직후 실행되지 않게 한다.
                if kind == KIND_DAILY and item.today_at(datetime.now()) <= datetime.now():
                    item.last_fired = datetime.now().date().isoformat()
                try:
                    self.save()
                except OSError:
                    for key, value in old.items():
                        setattr(item, key, value)
                    raise
                return True
            return False

    def clear(self) -> int:
        with self._lock:
            old = self._items
            self._items = []
            try:
                self.save()
            except OSError:
                self._items = old
                raise
            return len(old)

    def take_due(self, now: datetime, grace_minutes: float) -> list[Schedule]:
        with self._lock:
            due, keep = [], []
            grace = timedelta(minutes=max(0, grace_minutes))
            old = [Schedule.from_dict(i.to_dict()) for i in self._items]
            changed = False
            for item in self._items:
                if not item.enabled:
                    keep.append(item)
                    continue
                at = item.once_at() if item.kind == KIND_ONCE else item.today_at(now)
                if at is not None and at <= now and (item.kind == KIND_ONCE or item.last_fired != now.date().isoformat()):
                    changed = True
                    if now - at <= grace:
                        due.append(item)
                    if item.kind == KIND_ONCE:
                        continue
                    item.last_fired = now.date().isoformat()
                keep.append(item)
            self._items = keep
            if changed:
                try:
                    self.save()
                except OSError:
                    self._items = [item for item in old if item is not None]
                    raise
            return due


@dataclass(frozen=True)
class DueSpeech:
    text: str
    notes: tuple[str, ...] = ()
    provider: str | None = None
    fallback_from: str | None = None


class ScheduleRunner:
    def __init__(self, book: ScheduleBook, run_task: Callable[[str], object], notify: Callable[[DueSpeech], None], poll_seconds: float = 1.0, grace_minutes: float = 60.0, clock: Callable[[], datetime] = datetime.now) -> None:
        self.book, self.run_task, self.notify = book, run_task, notify
        self.poll_seconds, self.grace_minutes, self.clock = max(0.2, poll_seconds), grace_minutes, clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="nuri-schedule", daemon=True)
        self._thread.start()
        logger.info("예약 감시 시작")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.fire_due()
            except Exception:
                logger.exception("예약 처리 중 오류")
            self._stop.wait(self.poll_seconds)

    def fire_due(self, now: datetime | None = None) -> int:
        items = self.book.take_due(now or self.clock(), self.grace_minutes)
        for item in items:
            self.notify(self._speech_for(item))
        return len(items)

    def _speech_for(self, item: Schedule) -> DueSpeech:
        if not item.command:
            return DueSpeech(MSG_ALARM)
        try:
            result = self.run_task(item.command)
            text = getattr(result, "text", result)
            notes = getattr(result, "notes", ())
            return DueSpeech(MSG_TASK_PREFIX + str(text), tuple(notes), getattr(result, "provider", None), getattr(result, "fallback_from", None)) if text else DueSpeech(MSG_TASK_FAILED)
        except Exception:
            logger.exception("예약 작업 실패")
            return DueSpeech(MSG_TASK_FAILED)
