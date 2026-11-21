"""누리의 목소리·말투에 대한 요청 판정과 응답.

"억양을 바꿀 수 없어?" 처럼 누리 자신의 말소리를 바꿔 달라는 발화는 PC 작업이
아니라 TTS 설정 문제다. 에이전트에게 보내면 수십 초를 기다린 뒤 엉뚱한 답이
돌아오고, 잡담 계층에 맡기면 내용과 무관한 위로 문장이 나간다.

그래서 여기서 발화를 알아보고 **바꿀 수 있는 것(말 속도)과 바꿀 수 없는 것
(억양·음색)** 을 사실대로 안내한다. 방향(더 느리게/더 빠르게)이 분명하면
edge-tts 속도를 실제로 조정한다(edge-tts 는 호출 때마다 설정을 읽으므로 즉시 적용).

주의 ①: "목소리 좋다" 같은 칭찬은 바꿔 달라는 요청이 아니므로, 속성 표현과
**변경 의도**가 함께 있어야 요청으로 본다(칭찬은 기존 잡담 계층이 맡는다).
주의 ②: 스피커 볼륨("소리 줄여줘")은 누리의 말투가 아니라 PC 설정 작업이다.
"""

from __future__ import annotations

import re

from nuri.nlu.intent import compact

# 누리 자신 또는 말소리를 가리키는 표현. "너무 빨리 끝났어" 같은 문장을 말투 요청으로
# 잘못 읽지 않도록 "너무" 는 제외한다.
_SELF_VOICE = re.compile(
    r"(?:너(?!무)|네가|니가|누리|목소리|음성|말투|말소리|발음|억양|톤|말이|말하)"
)
# 말투 관련 속성. "말이 빠르다" 처럼 주어 없이도 성립한다.
_ATTRIBUTE = re.compile(
    r"(?:억양|목소리|음성|톤|말투|말소리|발음|속도|빠르|빨라|빨리|느리|느려|천천|말이)"
)
# 바꿔 달라는 의도. 이 신호가 없으면 칭찬·감상으로 보고 잡담 계층에 맡긴다.
_CHANGE = re.compile(
    r"(?:바꾸|바꿀|바꿔|바꿨|조절|설정|느리|느려|빠르|빨라|빨리|천천|줄이|줄여|늘리|늘려"
    r"|낮추|낮춰|높이|높여|올려|내려|안되|안돼|못하|가능|할수있|해줘|해줄래|줄래|없어|되나|돼)"
)
# 스피커 음량은 PC 작업이므로 제외한다
_VOLUME = re.compile(r"(?:볼륨|음량|소리크기|소리를|소리좀)")

# 방향 표현
_SLOWER = re.compile(r"(?:느리|느려|천천|줄여|줄이|낮추|낮춰|내려)")
_FASTER = re.compile(r"(?:빠르|빨라|빨리|늘려|늘리|높여|높이|올려)")
# 정도를 약하게 말한 경우(변화 폭을 작게)
_MILD = re.compile(r"(?:조금|약간|살짝|쪼끔|좀)")

RATE_STEP = 5           # 기본 변화 폭(%)
RATE_STEP_MILD = 3      # "조금" 이 붙으면 작게 바꾼다
RATE_MIN = -40
RATE_MAX = 40

MSG_CANNOT_CHANGE_TONE = (
    "지금 목소리에서 억양과 음색은 바꿀 수 없어요. 대신 말 속도는 조절할 수 있으니 "
    "더 느리게나 더 빠르게 원하시면 그렇게 말씀해 주세요."
)


def is_voice_request(text: str) -> bool:
    """누리의 말투·목소리를 바꿔 달라는 발화인지."""
    body = compact(text)
    if not body or _VOLUME.search(body):
        return False
    if not (_SELF_VOICE.search(body) and _ATTRIBUTE.search(body)):
        return False
    return bool(_CHANGE.search(body))


def direction(text: str) -> str | None:
    """속도 변경 방향. 'slower' / 'faster' / None(방향 없음)."""
    body = compact(text)
    slower = bool(_SLOWER.search(body))
    faster = bool(_FASTER.search(body))
    if slower == faster:  # 둘 다 없거나 둘 다 있으면 방향을 정할 수 없다
        return None
    return "slower" if slower else "faster"


def parse_rate(rate: str) -> int:
    """edge-tts 속도 문자열('+0%')을 정수로. 형식이 다르면 0."""
    match = re.fullmatch(r"\s*([+-]?\d{1,3})\s*%\s*", rate or "")
    return int(match.group(1)) if match else 0


def format_rate(value: int) -> str:
    """정수를 edge-tts 속도 문자열로."""
    return f"{value:+d}%"


def next_rate(current: str, direction_name: str, text: str = "") -> str:
    """현재 속도에서 방향·정도에 맞춰 다음 속도를 만든다(범위 제한)."""
    step = RATE_STEP_MILD if _MILD.search(compact(text)) else RATE_STEP
    value = parse_rate(current) + (step if direction_name == "faster" else -step)
    return format_rate(max(RATE_MIN, min(RATE_MAX, value)))


def changed_reply(direction_name: str) -> str:
    """속도를 바꾼 뒤의 안내 문구(속도 값은 음성으로 읽지 않는다)."""
    where = "느리게" if direction_name == "slower" else "빠르게"
    return f"네, 말 속도를 지금보다 조금 {where} 맞췄어요. 지금부터 이렇게 말할게요."
