"""동작 모드 전환 지시("메모 모드로", "대기 모드로")와 메모 요약 지시 판정.

음성 한 문장을 모드 제어로 볼지 판단한다. 지시 동사가 들어 있어도("바꿔줘")
"메모/대기 모드" 처럼 대상이 분명할 때만 참으로 본다. 질문("메모 모드가 뭐야")은
모드 전환이 아니라 설명 요청이므로 건드리지 않는다.
"""

from __future__ import annotations

import re
from enum import Enum

from nuri.nlu.intent import compact, is_question


class ModeAction(str, Enum):
    MEMO = "memo"          # 메모 모드로 전환
    STANDBY = "standby"    # 대기 모드로 전환
    SUMMARIZE = "summarize"  # 지금까지 받아쓴 메모를 요약


# 메모 모드 진입: "메모 모드", "메모 시작", "기록 모드로", "받아쓰기 켜줘"
_MEMO = re.compile(r"(?:메모|기록|받아쓰기|녹음)(?:모드|시작|켜|온|전환)")
# 대기 모드 진입: "대기 모드", "일반 모드", "메모 그만", "메모 모드 꺼"
_STANDBY = re.compile(
    r"(?:대기|일반|기본|평상)모드"
    r"|(?:메모|기록|받아쓰기|녹음)(?:모드)?(?:를)?(?:끄|꺼|종료|중지|그만|해제|나가|빼|오프|off)"
)
# 메모 요약: 메모 문맥이 분명한 경우("메모 요약해줘", "지금까지 정리해줘" 아님)
_SUMMARIZE_MEMO = re.compile(
    r"(?:메모|받아쓴|받아쓰기|지금까지|여기까지)(?:한|된)?(?:내용|것|거)?(?:을|를)?(?:좀)?요약"
)
# 메모 모드 안에서는 "요약해줘" 만으로도 요약을 요청할 수 있다
_SUMMARIZE_BARE = re.compile(
    r"(?:지금까지|여기까지)?(?:내용|것|거)?(?:을|를)?(?:좀)?요약(?:해|해줘|해주세요|해봐|만|하자)"
)
# 문장이 지시처럼 끝나야 모드 전환으로 본다.
# "메모 모드 끄는 방법은 이렇습니다" 처럼 설명하는 문장을 전환 지시로 오인하지 않게 한다.
_ENDING = re.compile(r"(?:로|으로|줘|해|해줘|해주세요|바꿔|모드|시작|종료|그만|켜|꺼|온|전환)$")


def parse(text: str, in_memo: bool = False) -> ModeAction | None:
    """발화가 모드 제어 지시인지 판정한다. 아니면 None.

    `in_memo` 가 참이면(이미 메모 모드) "요약해줘" 같은 짧은 요청도 받아들인다.
    """
    body = compact(text)
    if not body:
        return None
    # 메모 모드에서는 "요약해줘?" 처럼 물음표가 붙어도 요약 요청으로 본다
    if in_memo and _SUMMARIZE_BARE.fullmatch(body):
        return ModeAction.SUMMARIZE
    if is_question(text):
        return None
    if _SUMMARIZE_MEMO.search(body):
        return ModeAction.SUMMARIZE
    if not _ENDING.search(body):
        return None
    # "메모 모드 끄고 대기 모드로" 처럼 둘 다 있으면 대기 모드를 따른다
    if _STANDBY.search(body):
        return ModeAction.STANDBY
    if _MEMO.search(body):
        return ModeAction.MEMO
    return None
