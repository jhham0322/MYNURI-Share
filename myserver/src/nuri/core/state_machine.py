"""누리 상태 머신.

IDLE → ACK → LISTEN → THINK → ACT → SPEAK → IDLE
전이는 이 클래스에서만 수행하며, 잘못된 전이는 예외로 막는다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum

logger = logging.getLogger("nuri.state")

StateListener = Callable[["State"], None]


class State(str, Enum):
    IDLE = "IDLE"
    ACK = "ACK"
    LISTEN = "LISTEN"
    THINK = "THINK"
    ACT = "ACT"
    SPEAK = "SPEAK"


_ALLOWED: dict[State, set[State]] = {
    State.IDLE: {State.ACK},
    State.ACK: {State.LISTEN},
    State.LISTEN: {State.THINK, State.IDLE},
    State.THINK: {State.ACT, State.IDLE},
    State.ACT: {State.SPEAK, State.IDLE},
    # SPEAK 다음의 LISTEN 은 결과를 안내한 뒤 이어지는 후속 지시를 듣는 구간이다.
    State.SPEAK: {State.IDLE, State.LISTEN},
}


class InvalidTransition(Exception):
    pass


class StateMachine:
    def __init__(self) -> None:
        self._state = State.IDLE
        self._listeners: list[StateListener] = []

    @property
    def state(self) -> State:
        return self._state

    def add_listener(self, listener: StateListener) -> None:
        """상태가 바뀔 때마다 알림받을 콜백을 등록한다(트레이 표시등 등)."""
        self._listeners.append(listener)

    def to(self, nxt: State) -> None:
        if nxt not in _ALLOWED[self._state]:
            raise InvalidTransition(f"{self._state} -> {nxt} 는 허용되지 않습니다.")
        logger.info("state %s -> %s", self._state.value, nxt.value)
        self._state = nxt
        self._notify()

    def reset(self) -> None:
        self._state = State.IDLE
        self._notify()

    def _notify(self) -> None:
        """리스너 오류가 상태 전이를 막지 않도록 개별로 격리한다."""
        for listener in tuple(self._listeners):
            try:
                listener(self._state)
            except Exception as exc:  # noqa: BLE001
                logger.debug("상태 리스너 오류 무시: %s", exc)
