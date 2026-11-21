"""시각이 걸린 요청("한 시간 후에 알람", "매일 오전 8시에 뉴스 알려")을 해석한다.

판정은 **시간 표현 + 알림 표지**가 함께 있을 때만 참이다. 시간 표현만으로 잡으면
"한 시간 후에 파일 정리해줘" 같은 즉시 지시를 예약으로 빼앗게 되고, 알림 표지만
보면 "알람 앱 열어줘" 같은 다른 지시를 가로챈다.

지원 범위(관찰된 말투 기준):
- 상대 시각: "한 시간 후", "30분 뒤", "1시간 30분 후", "10초 뒤"
- 절대 시각: "오늘/내일 오전 7시", "오후 3시 30분에", "저녁 8시"
- 매일: "매일 오전 8시에", "매일 저녁 7시 30분"
- 목록/취소: "알람 목록", "예약 취소", "알람 다 지워"

시각 표현 없이 "알람 알려줘"만 있으면 `ASK_TIME` 을 돌려주고, 호출부가
"몇 시에 알려드릴까요?" 로 되묻는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

# 한글 수사(시간·분에 쓰는 작은 수). "한 시간", "두 시간", "세 시" 처럼 쓴다.
_SMALL_NUMBERS = {
    "한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6,
    "일곱": 7, "여덟": 8, "아홉": 9, "열": 10, "열한": 11, "열두": 12,
}
_NUM_WORDS = "|".join(sorted(_SMALL_NUMBERS, key=len, reverse=True))
_NUM = rf"(?:\d{{1,3}}|{_NUM_WORDS})"

_AMPM = r"오전|오후|아침|점심|저녁|밤|새벽"
_DAY = r"오늘|내일|모레"
_PM_WORDS = ("오후", "점심", "저녁", "밤")
_NIGHT_WORDS = ("밤", "새벽")

# 알림을 요구하는 표지. 이게 없으면 예약으로 보지 않는다.
_TRIGGER = re.compile(r"(?:알람|알림|알려|깨워|리마인드|예약|스케줄|매일)")
# 시각 없이 "알람 알려줘"만 말했을 때 되묻기 위한 표지(단순 '알려줘'와 구분한다).
# '예약'은 "예약한 식당 알려줘" 같은 다른 뜻으로도 쓰여 여기서는 빼 둔다.
_ALARM_MARK = re.compile(r"(?:알람|알림|스케줄|리마인드|깨워)")

_LIST = re.compile(
    r"(?:알람|알림|예약|스케줄)[이가은는을]?\s*"
    r"(?:목록|뭐|무엇|뭐가|뭐있|있어|있나|보여|확인|어때)"
)
_CANCEL = re.compile(r"(?:알람|알림|예약|스케줄).*(?:취소|지워|지우|삭제|꺼|끄|없애)")
_CANCEL_ALL = re.compile(r"(?:다|모두|전부|전체|싹)\s*(?:취소|지워|지우|삭제|없애)")
_CANCEL_INDEX = re.compile(rf"(?P<n>{_NUM})\s*번")

_DAILY = re.compile(
    rf"매일(?:매일)?\s*(?:(?P<ampm>{_AMPM})\s*)?(?P<h>{_NUM})\s*시"
    rf"(?:\s*(?P<m>{_NUM})\s*분?)?\s*(?:에|에다|마다|쯤)?"
)
_RELATIVE = re.compile(
    rf"(?:(?P<h>{_NUM})\s*시간)?\s*(?:(?P<m>{_NUM})\s*분)?\s*(?:(?P<s>{_NUM})\s*초)?"
    rf"\s*(?:뒤|후|있다가|이따가|이후)\s*(?:에|에다|쯤)?"
)
_ABSOLUTE = re.compile(
    rf"(?:(?P<day>{_DAY})\s*)?(?:(?P<ampm>{_AMPM})\s*)?(?P<h>{_NUM})\s*시(?!간)"
    rf"(?:\s*(?P<m>{_NUM})\s*분?)?\s*(?:에|에다|쯤|까지)?"
)

# 명령에서 걷어낼 알림 단어와 끝맺음
_ALARM_WORDS = re.compile(r"(?:알람|알림|예약|스케줄|리마인드|깨워\s*줘|깨워)")
_TAIL_VERBS = re.compile(
    r"(?:알려\s*줘|알려\s*주세요|알려|말해\s*줘|말해\s*주세요|말해|해\s*줘|해\s*주세요|해|줘|주세요)"
    r"\s*[.!?~]*$"
)
_WAKE = re.compile(r"^(?:누리야?|노리야?|우리야?)[,.\s]*")
_SPACES = re.compile(r"\s+")

# 되묻는 문구(시각 없이 "알람 알려줘"만 말한 경우)
MSG_ASK_TIME = "몇 시에 알려드릴까요? '한 시간 뒤' 처럼 말씀하셔도 돼요."


class Action(str, Enum):
    ADD_ONCE = "add_once"
    ADD_DAILY = "add_daily"
    ASK_TIME = "ask_time"
    LIST = "list"
    CANCEL = "cancel"
    CANCEL_ALL = "cancel_all"


@dataclass(frozen=True)
class Request:
    """해석 결과. 예약 추가면 `at`(한 번) 또는 `hour/minute`(매일)이 채워진다."""

    action: Action
    at: datetime | None = None
    hour: int = 0
    minute: int = 0
    command: str = ""
    index: int | None = None          # 취소할 예약 번호(1부터)

    @property
    def label(self) -> str:
        """목록·안내에 쓰는 짧은 설명."""
        return self.command or "알람"


def _count(token: str | None) -> int | None:
    """숫자/한글 수사 표기를 정수로 바꾼다(해석 불가면 None)."""
    if not token:
        return None
    token = token.strip()
    if token.isdigit():
        return int(token)
    return _SMALL_NUMBERS.get(token)


def _clock(hour: int, minute: int, ampm: str | None) -> tuple[int, int] | None:
    """오전/오후 표기를 24시간제로 바꾼다. 범위를 벗어나면 None."""
    if not 0 <= minute <= 59:
        return None
    if ampm:
        if not 1 <= hour <= 12:
            return None
        if ampm in _NIGHT_WORDS and hour == 12:
            hour = 0
        elif ampm in _PM_WORDS:
            hour = hour % 12 + 12
        else:
            hour = hour % 12
    if not 0 <= hour <= 23:
        return None
    return hour, minute


def _strip_command(text: str, span: tuple[int, int]) -> str:
    """시간 표현을 걷어낸 나머지에서 실행할 지시만 남긴다."""
    body = text[: span[0]] + " " + text[span[1]:]
    body = _WAKE.sub("", body.strip())
    body = _ALARM_WORDS.sub(" ", body)
    body = _TAIL_VERBS.sub("", body.strip())
    body = _SPACES.sub(" ", body).strip(" ,.!?~·")
    if body and not body.endswith(("줘", "주세요", "해", "하세요")):
        # 예약 실행 결과를 사용자에게 알려야 하므로 요청형으로 맞춘다
        body = f"{body} 알려줘"
    return body


def _absolute(text: str, match: re.Match[str], now: datetime) -> datetime | None:
    """절대 시각 표현을 실제 시각으로 바꾼다(지난 시각이면 다음 날)."""
    hour = _count(match.group("h"))
    minute = _count(match.group("m")) or 0
    if hour is None:
        return None
    clock = _clock(hour, minute, match.group("ampm"))
    if clock is None:
        return None
    day_word = match.group("day")
    base = now.date()
    if day_word == "내일":
        base += timedelta(days=1)
    elif day_word == "모레":
        base += timedelta(days=2)
    target = datetime.combine(base, datetime.min.time()).replace(
        hour=clock[0], minute=clock[1]
    )
    # 요일/날짜를 말하지 않았고 오늘 그 시각이 이미 지났으면 내일로 본다
    if day_word is None and target <= now:
        target += timedelta(days=1)
    return target


def _relative(match: re.Match[str], now: datetime) -> datetime | None:
    hours = _count(match.group("h")) or 0
    minutes = _count(match.group("m")) or 0
    seconds = _count(match.group("s")) or 0
    if not (hours or minutes or seconds):
        return None
    at = now + timedelta(hours=hours, minutes=minutes, seconds=seconds)
    # 분 단위 예약은 초를 버려 시각을 깔끔하게 맞춘다. 초 단위 예약은 그대로 둔다
    # (초를 0으로 만들면 이미 지난 시각이 되어 곧바로 울릴 수 있다).
    return at.replace(microsecond=0)


def parse(text: str, now: datetime | None = None) -> Request | None:
    """발화를 예약 요청으로 해석한다. 예약과 무관한 발화면 None."""
    moment = now or datetime.now()
    body = text.strip()
    if not body or not _TRIGGER.search(body):
        return None

    # 1) 목록·취소 (시간 표현이 없어도 된다)
    if _CANCEL.search(body):
        if _CANCEL_ALL.search(body):
            return Request(Action.CANCEL_ALL)
        found = _CANCEL_INDEX.search(body)
        index = _count(found.group("n")) if found else None
        return Request(Action.CANCEL, index=index)
    if _LIST.search(body):
        return Request(Action.LIST)

    # 2) 매일 (절대 시각보다 먼저 본다: "매일 오전 8시" 는 둘 다 걸린다)
    daily = _DAILY.search(body)
    if daily:
        hour = _count(daily.group("h"))
        minute = _count(daily.group("m")) or 0
        clock = _clock(hour, minute, daily.group("ampm")) if hour is not None else None
        if clock is None:
            return None
        return Request(
            Action.ADD_DAILY,
            hour=clock[0],
            minute=clock[1],
            command=_strip_command(body, daily.span()),
        )

    # 3) 상대 시각 ("한 시간 후") — "8시간 후" 가 "8시" 로 잘못 잡히지 않게 먼저 본다
    relative = _RELATIVE.search(body)
    if relative:
        at = _relative(relative, moment)
        if at is not None:
            return Request(Action.ADD_ONCE, at=at, command=_strip_command(body, relative.span()))

    # 4) 절대 시각 ("내일 오전 7시")
    absolute = _ABSOLUTE.search(body)
    if absolute:
        at = _absolute(body, absolute, moment)
        if at is not None:
            return Request(Action.ADD_ONCE, at=at, command=_strip_command(body, absolute.span()))

    # 5) 시간 없이 알람만 요청 → 몇 시인지 되묻는다. 단순히 "알려줘"만 말한
    #    경우(다른 지시의 끝맺음)까지 붙잡지 않도록 알람 표지를 확인한다.
    if _ALARM_MARK.search(body):
        return Request(Action.ASK_TIME)
    return None


# --- 안내 문구용 표기 ---
_ORDINALS = ("첫째", "둘째", "셋째", "넷째", "다섯째", "여섯째", "일곱째", "여덟째", "아홉째", "열째")


def clock_text(moment: datetime) -> str:
    """'오후 5시 45분' 처럼 읽기 좋은 시각 표기(분이 0이면 생략)."""
    period = "오전" if moment.hour < 12 else "오후"
    hour = moment.hour % 12 or 12
    return f"{period} {hour}시" + (f" {moment.minute}분" if moment.minute else "")


def when_text(moment: datetime, now: datetime | None = None) -> str:
    """'오늘 오후 5시 45분'·'내일 오전 7시' 처럼 날짜까지 붙인 표기."""
    today = (now or datetime.now()).date()
    delta = (moment.date() - today).days
    if delta == 0:
        return f"오늘 {clock_text(moment)}"
    if delta == 1:
        return f"내일 {clock_text(moment)}"
    if delta == 2:
        return f"모레 {clock_text(moment)}"
    return f"{moment.month}월 {moment.day}일 {clock_text(moment)}"


def ordinal(index: int) -> str:
    """1부터 시작하는 순번을 '첫째' 처럼 읽는다(범위를 넘으면 '3번')."""
    return _ORDINALS[index - 1] if 1 <= index <= len(_ORDINALS) else f"{index}번"
