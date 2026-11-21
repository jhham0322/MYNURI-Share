"""지시가 아닌 일상 대화에 자연스럽게 흥응하는 로컬 응답.

paseo 에이전트는 한 번 부를 때마다 수십 초가 걸리므로 잡담에는 쓸 수 없다.
여기서는 짧은 규칙으로 유형을 고르고 준비된 문장 중 하나를 돌려준다(오프라인·즉답).
확신이 없는 진술은 `reflect_statements` 가 켜져 있을 때 짧게 반응만 한다.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

from nuri.nlu.intent import compact, has_task_marker


@dataclass(frozen=True)
class _Rule:
    name: str
    pattern: re.Pattern[str]
    replies: tuple[str, ...]


# 응답 후보. 같은 말을 반복하지 않도록 유형마다 여러 문장을 둔다.
REPLIES: dict[str, tuple[str, ...]] = {
    "complaint": (
        "죄송해요. 어떤 점이 불편하셨는지 알려주시면 고쳐볼게요.",
        "부족했군요. 말씀해 주시면 개선해 볼게요.",
    ),
    "compliment": (
        "그렇게 말씀해 주시니 기분이 좋네요.",
        "고맙습니다. 더 열심히 할게요.",
        "칭찬 감사합니다.",
    ),
    # 짧은 감탄형 칭찬("굿잡", "나이스")은 작업 요청이 아니라 놀리는 맛의 응답으로 받는다.
    # 진짜 지시가 섞이면 지시 동사 검사에서 먼저 걸러진다.
    "praise": (
        "칭찬 감사합니다. 오늘은 이 정도면 제가 좀 뿌듯해도 되겠죠?",
        "고맙습니다. 다음엔 박수까지 기대해 볼게요.",
        "칭찬은 언제나 환영이에요. 더 잘하라는 뜻으로 알겠습니다.",
        "어깨가 조금 올라가네요. 잠깐만 우쭐해 보겠습니다.",
    ),
    "encourage": ("감사해요. 힘이 나네요.", "응원해 주셔서 고마워요."),
    "tired": (
        "많이 지치셨군요. 잠깐이라도 쉬어가세요.",
        "고생 많으셨어요. 따뜻한 물 한 잔 어떠세요?",
    ),
    "happy": (
        "좋은 일이 있으셨나 봐요. 저도 기분이 좋아지네요.",
        "그거 잘됐네요.",
    ),
    "self_intro": (
        "저는 누리예요. 말씀하시면 PC 작업을 대신 처리해 드려요.",
        "누리라고 불러 주세요. 음성으로 이것저것 도와드려요.",
    ),
    "capability": (
        "시간과 날짜를 알려드리고, 말씀하신 PC 작업을 대신 처리해요. 기억도 해 둘 수 있어요.",
        "간단한 질문에 답하고, 부탁하신 일을 PC에서 처리해 드려요.",
    ),
    "farewell": ("안녕히 계세요. 필요하시면 다시 불러 주세요.",),
    "laugh": ("재미있으셨나 봐요.", "저도 웃음이 나네요."),
    "agreement": ("네, 말씀하세요.", "알겠습니다."),
    "joke_response": (
        "농담을 하시면 저도 재미있게 받아칠게요. 한번 해보세요.",
        "농담이라면 저도 웃으며 받아볼게요. 어떤 농담인데요?",
    ),
    "weather": ("요즘 날씨가 참 그러네요.", "창밖 날씨는 좀 어떠세요?"),
    "reflect": (
        "그렇군요.",
        "그러시군요.",
        "듣고 있어요.",
        "그런 일이 있으셨군요.",
    ),
}

_RULES: tuple[_Rule, ...] = (
    _Rule(
        "complaint",
        re.compile(r"바보|멍청|못알아|못알아듣|이상해|왜이래|답답|한심|실망"),
        REPLIES["complaint"],
    ),
    _Rule(
        "compliment",
        re.compile(r"대단|멋지|멋있|훌륭|최고|똑똑|잘했|잘하|기특|귀엽|짱|사랑해|천재"),
        REPLIES["compliment"],
    ),
    # 감탄형 칭찬은 작업이 아니라 칭찬이므로 에이전트로 보내지 않는다.
    # "굿샷"·"굿좝" 처럼 음성 인식이 흔들린 표기도 함께 받는다.
    _Rule(
        "praise",
        re.compile(
            r"굿잡|굿샷|굿좝|굿쨥|굿굿|굳잡|나이스|나이쓰|완벽|브라보"
            r"|수고해|수고했|고생했|잘한다|goodjob|nice|perfect|awesome|great|bravo"
        ),
        REPLIES["praise"],
    ),
    _Rule("encourage", re.compile(r"힘내|화이팅|파이팅|응원|잘부탁"), REPLIES["encourage"]),
    _Rule(
        "tired",
        re.compile(r"힘들|피곤|지쳤|졸려|스트레스|우울|속상|슬퍼|외로|아파|짜증"),
        REPLIES["tired"],
    ),
    _Rule("happy", re.compile(r"기분.{0,3}좋|신나|행복|즐거|재밌|좋은일|기뻐"), REPLIES["happy"]),
    _Rule(
        "self_intro",
        re.compile(r"(?:너|네가|누리)(?:는|가)?누구|정체가|누구야|누구세요|이름이뭐|뭐하는애|너뭐야"),
        REPLIES["self_intro"],
    ),
    _Rule(
        "capability",
        re.compile(r"뭘?할수있|무엇을할수|어떤거할수|어떻게도와|능력이|뭐할줄알|무슨일을할"),
        REPLIES["capability"],
    ),
    _Rule("farewell", re.compile(r"잘자|안녕히|이따봐|다음에봐|바이바이"), REPLIES["farewell"]),
    _Rule("laugh", re.compile(r"ㅋ{2,}|ㅎ{2,}|하하|호호|웃겨|웃기네"), REPLIES["laugh"]),
    _Rule("weather", re.compile(r"날씨|비가오|눈이오|덥다|춥다|더워|추워"), REPLIES["weather"]),
)

# 짧은 맞장구는 문장 전체가 일치할 때만 인정한다("네" 가 들어간 지시와 구분).
_AGREEMENT = re.compile(r"(?:응|어|네|예|그래|맞아|알겠어|오케이|ㅇㅇ|그렇구나)")

# 농담 자체를 판단하기보다, 농담을 했을 때의 누리의 반응을 묻는 질문만 받는다.
_JOKE_RESPONSE = re.compile(
    r"농담.*(?:어떻게|뭐라고|어떤반응|무슨반응).*(?:대답|답하|반응|받아)"
    r"|농담.*(?:대답|반응|받아).*(?:어떻게|뭐라고|어때|하니|하냐)"
)

# 지시도 질문도 아닌 진술의 끝맺음. 이럴 때는 짧게 반응만 한다.
# ("닫아/열어/켜" 같은 지시형은 위의 지시 동사 검사에서 먼저 걸러진다)
_STATEMENT_END = re.compile(r"(?:다|요|네|지|는데|군|구나|잖아|더라|었어|았어|해|야|아|어)$")

# 질문을 나타내는 의문사. "볼륨 얼마야"처럼 끝이 "야"여도 진술이 아니라 질문이다.
# 이런 말에 "그렇군요." 로 맞장구치면 사용자는 답을 못 들은 것으로 느낀다.
_ASK_WORD = re.compile(r"얼마|몇|뭐|무엇|어디|언제|누구|왜|어떻게")
_ASK_END = re.compile(r"(?:야|까|니|나|냐|가)$")

_RNG = random.Random()
# 같은 유형이 연달아 나올 때 바로 앞과 똑같은 문장을 다시 고르지 않기 위한 기억
_LAST: dict[str, str] = {}


def category(text: str) -> str | None:
    """발화 유형을 고른다. 일상 대화로 볼 수 없으면 None.

    지시 동사가 있으면 잡담으로 보지 않는다("오늘 날씨 알려줘" → 작업).
    """
    body = compact(text)
    if not body:
        return None
    if _AGREEMENT.fullmatch(body):
        return "agreement"
    if len(body) < 2 or has_task_marker(text):
        return None
    if _JOKE_RESPONSE.search(body):
        return "joke_response"
    for rule in _RULES:
        if rule.pattern.search(body):
            return rule.name
    if _STATEMENT_END.search(body):
        # 의문사 + 질문형 끝맺음("볼륨 얼마야", "몇 시야")은 답을 기대하는 말이다.
        # 여기서 "그렇군요." 로 받으면 반응이 없는 것처럼 들리므로 작업 경로로 넘긴다.
        if _ASK_WORD.search(body) and _ASK_END.search(body):
            return None
        return "reflect"
    return None


def reply(text: str, reflect: bool = True) -> str | None:
    """일상 대화 응답. 해당하지 않으면 None(호출부가 작업으로 보낸다).

    `reflect` 가 꺼져 있으면 확신이 낮은 일반 진술에는 반응하지 않는다.
    """
    name = category(text)
    if name is None:
        return None
    if name == "reflect" and not reflect:
        return None
    return _pick(name)


def _pick(name: str) -> str:
    """유형의 응답 후보에서 하나를 고른다. 바로 앞 응답은 다시 고르지 않는다."""
    options = REPLIES[name]
    last = _LAST.get(name)
    if last is not None and len(options) > 1:
        options = tuple(option for option in options if option != last) or options
    chosen = _RNG.choice(options)
    _LAST[name] = chosen
    return chosen
