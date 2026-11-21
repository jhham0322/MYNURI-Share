"""발화 의도 판정: 지시인지, 누리 자신에 대한 개선 요청인지, 질문인지.

음성 한 문장을 어디로 보낼지 가르는 기준을 한 곳에 모은다. 판정은 공백과
구두점을 제거한 문자열(compact)을 기준으로 하며, 정규식 하나로 모든 표현을
잡을 수 없으므로 "확실한 신호가 있을 때만" 참을 돌려주는 보수적인 규칙을 쓴다.
"""

from __future__ import annotations

import re

# 지시 동사(어간). 이 표현이 있으면 일상 대화가 아니라 작업 요청으로 본다.
# 예: "오늘 날씨 알려줘" 는 잡담이 아니라 작업이다.
_TASK_MARKER = re.compile(
    r"(?:해줘|해주세요|해라|해봐|할래|주세요|줘"
    r"|알려|말해|보여|찾아|검색|열어|닫아|켜|꺼|실행|재시작|종료"
    r"|정리|조사(?:해|하여|하라|하세요|하십시오)|복사|이동|삭제|지워|만들|추가|설치|다운|업데이트|빌드|테스트"
    r"|확인|요약|번역|계산|보내|써줘|적어|바꿔|고쳐|수정|개선|저장|불러|인쇄)"
)

# 질문 표지. app 의 직접 응답 판정과 같은 기준을 쓴다.
_QUESTION = re.compile(
    r"알려줘|알려주세요|말해줘|말해주세요|뭐야|뭔가요|인가요|일까요|어떻게|얼마|몇|무엇|언제|왜|어디|누구|날씨|시간|날짜|\?"
)

# 누리 자신을 가리키는 표현. "내 코드" 같은 사용자 소유물과 구분해야 한다.
_SELF_REF = re.compile(r"(?:너|네가|니가|누리|스스로|프로그램|기능|모듈|코드|소스|앱)")
# 개선을 요구하는 동사
_IMPROVE_VERB = re.compile(r"(?:개선|고쳐|고치|수정|바꿔|바꾸|추가|만들|업그레이드|개발|보완)")
# 사용자 자신의 코드/프로젝트를 가리키는 표현(자기 개선 요청이 아니다)
_OWN_CODE = re.compile(r"(?:내|제)(?:가|의)?(?:코드|소스|프로젝트|프로그램)")

# 처음부터 끝까지 읽어야 하는 요청. 뉴스·브리핑은 항목이 여러 개라 10초 요약으로
# 줄이면 첫 항목만 들려주고 끝나므로, 이런 요청은 결과 전체를 읽는다.
_FULL_READ_TOPIC = re.compile(r"(?:뉴스|헤드라인|브리핑|시황|속보|주요소식|오늘의이슈|주요기사)")
# "요약 말고 전문", "끝까지 읽어줘" 처럼 사용자가 직접 전체 낭독을 요구한 경우
_FULL_READ_ASK = re.compile(
    r"(?:전문|전체내용|전체기사|모두읽|전부읽|다읽|끝까지읽|생략없이|빠짐없이)"
)

_STRIP = re.compile(r"[\s,.!?~·\"'…]+")


def compact(text: str) -> str:
    """공백/구두점을 제거한 비교용 문자열."""
    return _STRIP.sub("", text).lower()


def has_task_marker(text: str) -> bool:
    """지시 동사가 들어 있는지. 참이면 일상 대화로 보지 않는다."""
    return bool(_TASK_MARKER.search(compact(text)))


def is_question(text: str) -> bool:
    """질문으로 볼 만한 발화인지(접수 안내를 생략할지 판단할 때 쓴다)."""
    return bool(_QUESTION.search(text))


def is_full_read_request(text: str) -> bool:
    """결과를 처음부터 끝까지 읽어야 하는 요청인지(뉴스·브리핑·전문 낭독).

    뉴스 요약처럼 항목이 여러 개인 결과를 10초 분량으로 함축하면 첫 항목만
    들려주고 끝난다. 이런 요청만 긴 낭독으로 처리하고, 나머지 작업은 짧게 요약한다.
    """
    body = compact(text)
    return bool(_FULL_READ_TOPIC.search(body) or _FULL_READ_ASK.search(body))


def is_self_improve(text: str) -> bool:
    """누리 자신을 고쳐 달라는 요청인지.

    자기 지칭 + 개선 동사가 함께 있어야 참이다. "이 문장 고쳐줘" 처럼
    대상이 누리가 아니면 일반 작업으로 남긴다. "앞으로 ~하도록 고쳐줘" 는
    누리의 앞으로의 동작을 바꾸라는 뜻이므로 자기 개선으로 본다.
    """
    body = compact(text)
    if not _IMPROVE_VERB.search(body):
        return False
    if _OWN_CODE.search(body):
        return False
    return bool(_SELF_REF.search(body)) or body.startswith("앞으로")
