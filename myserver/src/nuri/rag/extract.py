"""발화에서 기억할 거리를 뽑아내고, 질문이 무엇을 묻는지 판정한다.

LLM 없이 규칙으로만 동작한다. 잡아내는 범위를 넓히면 엉뚱한 말까지 저장되므로,
"내 이름은 …", "나는 … 좋아해", "… 기억해줘" 처럼 형태가 분명한 문장만 다룬다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nuri.nlu.intent import compact, is_question

# 주제(subject) 사전: 질문·삭제 대상 판정에 함께 쓴다.
_SLOTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("이름", ("이름",)),
    ("생일", ("생일",)),
    ("나이", ("나이", "몇살")),
    ("취미", ("취미",)),
    ("직업", ("직업", "무슨일")),
    ("사는 곳", ("사는곳", "어디살", "집이어디")),
    ("싫어하는 것", ("싫어하는",)),
    ("좋아하는 것", ("좋아하는",)),
)

# 문장 끝에 붙는 서술 어미(값에서 떼어낸다)
_ENDINGS = r"(?:이야|이에요|예요|입니다|이랍니다|라고\s*해|라고\s*해요|야)?"

_EXPLICIT = re.compile(
    r"(?P<value>.+?)\s*(?:을|를|은|는)?\s*"
    r"(?:기억해\s*줘|기억해주세요|기억해|외워\s*줘|외워|잊지\s*마|저장해\s*줘)"
)
_NAME = re.compile(rf"(?:내|제)\s*이름(?:은|는|이)?\s*(?P<value>.+?)\s*{_ENDINGS}$")
_BIRTHDAY = re.compile(rf"(?:내|제)\s*생일(?:은|는|이)?\s*(?P<value>.+?)\s*{_ENDINGS}$")
_AGE = re.compile(rf"(?:내|제)\s*나이(?:는|가|이)?\s*(?P<value>.+?)\s*{_ENDINGS}$")
_HOBBY = re.compile(rf"(?:내|제)\s*취미(?:는|가|이)?\s*(?P<value>.+?)\s*{_ENDINGS}$")
_JOB = re.compile(r"(?:나는|난|저는|제가)\s*(?P<value>.+?)\s*(?:에서|에)\s*일해")
_LIVE = re.compile(r"(?:나는|난|저는|제가)\s*(?P<value>.+?)\s*(?:에|에서)\s*살아")
_LIKE = re.compile(r"(?:나는|난|저는|내가)\s*(?P<value>.+?)\s*(?:을|를)?\s*좋아해")
_DISLIKE = re.compile(r"(?:나는|난|저는|내가)\s*(?P<value>.+?)\s*(?:을|를)?\s*싫어해")
_RULE = re.compile(r"^앞으로\s*(?P<value>.+?)\s*(?:해\s*줘|해주세요|하도록\s*해|해|줘)?$")

# 주제별 추출 규칙. 위에서부터 먼저 맞는 것을 쓴다(구체적인 것 우선).
_SLOT_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("profile", "이름", _NAME),
    ("profile", "생일", _BIRTHDAY),
    ("profile", "나이", _AGE),
    ("profile", "취미", _HOBBY),
    ("profile", "직업", _JOB),
    ("profile", "사는 곳", _LIVE),
    ("preference", "좋아하는 것", _LIKE),
    ("preference", "싫어하는 것", _DISLIKE),
)

_LIST_QUERY = re.compile(r"뭐기억|무엇을기억|기억하고있|기억나는|기억한거|기억목록")
_FORGET = re.compile(r"잊어|잊어줘|지워|삭제해")
# 값 안에 저장 요청 표현이 섞이면 주제 규칙을 적용하지 않는다
# (예: "내 이름은 홍길동이야 기억해줘" → note 로 저장)
_REJECT_VALUE = re.compile(r"기억해|외워|잊지|저장해")
_MIN_VALUE_CHARS = 1
_MAX_VALUE_CHARS = 60


@dataclass(frozen=True)
class Candidate:
    """저장 후보. subject 가 비면 자유 메모(note)다."""

    kind: str
    subject: str
    value: str
    text: str


def slot_of(text: str) -> str | None:
    """질문이 묻는 주제를 찾는다(없으면 None)."""
    body = compact(text)
    for subject, words in _SLOTS:
        if any(word in body for word in words):
            return subject
    return None


def is_list_query(text: str) -> bool:
    """기억 목록을 묻는 발화인지."""
    return bool(_LIST_QUERY.search(compact(text)))


def is_forget(text: str) -> bool:
    """기억 삭제를 요청하는 발화인지."""
    return bool(_FORGET.search(compact(text)))


def _clean(value: str) -> str:
    return value.strip(" \t,.'\"'?!~·")


def extract(text: str) -> Candidate | None:
    """발화에서 기억 후보를 하나 뽑는다. 해당 없으면 None.

    질문은 사실을 진술한 것이 아니므로 저장하지 않는다
    ("내 이름 뭐야" 를 이름="뭐" 로 저장하던 문제 방지).
    """
    body = text.strip()
    if not body or is_question(body):
        return None

    for kind, subject, pattern in _SLOT_RULES:
        match = pattern.search(body)
        if match:
            value = _clean(match.group("value"))
            if _REJECT_VALUE.search(value):
                continue
            if _MIN_VALUE_CHARS <= len(value) <= _MAX_VALUE_CHARS:
                return Candidate(kind=kind, subject=subject, value=value, text=body)

    match = _RULE.match(body)
    if match:
        value = _clean(match.group("value"))
        if not _REJECT_VALUE.search(value) and _MIN_VALUE_CHARS <= len(value) <= _MAX_VALUE_CHARS:
            return Candidate(kind="rule", subject="행동 규칙", value=value, text=body)

    match = _EXPLICIT.search(body)
    if match:
        value = _clean(match.group("value"))
        if _MIN_VALUE_CHARS <= len(value) <= _MAX_VALUE_CHARS:
            return Candidate(kind="note", subject="", value=value, text=body)

    return None
