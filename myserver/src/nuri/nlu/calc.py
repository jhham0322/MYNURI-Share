"""한 문장짜리 사칙연산 질문을 로컬에서 즉답한다.

"300 곱하기 500은" 같은 질문을 paseo 에이전트로 보내면 수십 초를 기다린 뒤
`300 × 500 = 150,000` 같은 수식 문자열이 돌아온다. TTS 는 기호(×, =)와 천 단위
쉼표를 엉뚱하게 읽어 음성이 이상해지므로, 확실한 사칙연산 한 문장은 여기서
계산해 **읽어서 자연스러운 한국어 문장**으로 돌려준다.

판정은 공백/구두점을 제거한 문자열(compact) 전체 일치로만 한다. 계산식 일부가
섞인 지시("300 곱하기 500을 파일에 적어줘")는 작업으로 남긴다.

STT 는 같은 말도 조용한 환경에서 "300 곱하기 500" 으로, 조금 흐리면
"300, 800, 500" 이나 "300고파기 500" 처럼 적기도 한다. 그래서 수는 숫자와
한글 수사(삼백·십오만)를 모두 받고, 자주 틀리는 연산자 표기(도하기·고파기)도
같은 뜻으로 본다. 연산자 자체가 사라진 경우는 추측해 계산하지 않고 다시 묻는다.
"""

from __future__ import annotations

import re

from nuri.nlu.intent import compact

# 읽기 좋은 한국어 수 표기(만/억 단위). 숫자를 그대로 넘기면 TTS 가 쉼표를
# "쉼표"로 읽거나 자리마다 끊어 읽는 경우가 있어 말로 풀어 쓴다.
_DIGIT_WORDS = ("영", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구")
_SMALL_UNITS = ("천", "백", "십", "")
_BIG_UNITS = ("", "만", "억", "조", "경")

# 연산자 표기(말/기호) → 읽을 말. 기호는 STT 결과에 그대로 들어오는 경우가 있다.
# 왼쪽 표기는 STT 가 실제로 적어 내는 오인식 형태를 관찰해 모은 것이다
# ("45 더하기 55" → "45도 하기 55", "300 곱하기 500" → "300고파기 500").
_OP_WORDS: dict[str, str] = {
    "더하기": "더하기",
    "도하기": "더하기",
    "더허기": "더하기",
    "플러스": "더하기",
    "+": "더하기",
    "빼기": "빼기",
    "뺴기": "빼기",
    "마이너스": "빼기",
    "-": "빼기",
    "곱하기": "곱하기",
    "곱허기": "곱하기",
    "곡하기": "곱하기",
    "고파기": "곱하기",
    "곡파기": "곱하기",
    "꼬파기": "곱하기",
    "곱셈": "곱하기",
    "*": "곱하기",
    "x": "곱하기",
    "×": "곱하기",
    "나누기": "나누기",
    "나눠기": "나누기",
    "/": "나누기",
    "÷": "나누기",
}

# 수는 숫자(300)와 한글 수사(삼백)를 모두 받는다. STT 가 같은 말을 어느 쪽으로
# 적을지 일정하지 않아서, 한쪽만 받으면 계산이 작업으로 새어 나간다.
_NUMBER_CHARS = "0-9영일이삼사오육칠팔구십백천만억조"
_OPERAND = rf"[{_NUMBER_CHARS}]{{1,12}}"
_OPERATORS = "|".join(re.escape(op) for op in sorted(_OP_WORDS, key=len, reverse=True))

_DIGIT_VALUES = {
    "영": 0, "일": 1, "이": 2, "삼": 3, "사": 4,
    "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9,
}
_SMALL_UNIT_VALUES = {"십": 10, "백": 100, "천": 1000}
_BIG_UNIT_VALUES = {"만": 10**4, "억": 10**8, "조": 10**12}

# 질문 끝맺음("은", "얼마야", "알려줘" 등)은 있어도 없어도 계산한다.
_TAIL = (
    r"(?:은|는|이|가)?"
    r"(?:얼마(?:야|인가요|예요|입니까|냐)?|뭐야|뭔가요|알려줘|알려주세요"
    r"|계산해줘|계산해주세요|이야|인가요|입니까|이에요|예요)?"
)
_EXPR = re.compile(
    rf"^(?:누리야?)?(?P<a>{_OPERAND})(?P<op>{_OPERATORS})(?P<b>{_OPERAND})"
    + _TAIL
    + r"$"
)

# 연산자만 사라지고 숫자가 쉼표로 나열된 질문("300, 800, 500은?"). 계산은 추측하지
# 않지만, 이대로 작업 실행기로 보내면 몇 분을 기다린 뒤 엉뚱한 답이 나온다.
_BARE_NUMBERS = re.compile(
    rf"^(?:누리야?)?\s*{_OPERAND}(?:\s*[,·]\s*{_OPERAND})+\s*"
    + _TAIL
    + r"\s*[?!.]*$"
)

MSG_DIVIDE_BY_ZERO = "0으로 나눌 수는 없어요."
MSG_CALC_RETRY = "계산식이 정확히 안 들렸어요. 곱하기인지 더하기인지 넣어서 다시 말해 줄래요."


def parse_number(token: str) -> int | None:
    """'300'·'삼백'·'십오만' 같은 수 표기를 정수로 바꾼다(해석 불가면 None)."""
    total = 0
    section = 0
    number = 0
    for char in token:
        if char.isdigit():
            number = number * 10 + int(char)
        elif char in _DIGIT_VALUES:
            number = _DIGIT_VALUES[char]
        elif char in _SMALL_UNIT_VALUES:
            section += (number or 1) * _SMALL_UNIT_VALUES[char]
            number = 0
        elif char in _BIG_UNIT_VALUES:
            section += number
            total += (section or 1) * _BIG_UNIT_VALUES[char]
            section = 0
            number = 0
        else:
            return None
    return total + section + number


def _group_words(value: int) -> str:
    """0~9999 를 한국어 읽기로 바꾼다(1000 → '천', 15 → '십오')."""
    words = ""
    for power, unit in zip((3, 2, 1, 0), _SMALL_UNITS):
        digit = (value // 10**power) % 10
        if not digit:
            continue
        # 1 은 단위 앞에서 읽지 않는다(1000 → '일천' 대신 '천')
        words += ("" if digit == 1 and unit else _DIGIT_WORDS[digit]) + unit
    return words


def number_words(value: int | float) -> str:
    """숫자를 TTS 가 자연스럽게 읽는 한국어 표기로 바꾼다.

    예: 150000 → '십오만', 1234 → '천이백삼십사', 3.33 → '삼 점 삼삼'
    """
    if isinstance(value, float) and not value.is_integer():
        whole, dot, fraction = f"{value:.2f}".partition(".")
        return f"{number_words(int(whole))} 점 {''.join(_DIGIT_WORDS[int(d)] for d in fraction)}"

    number = int(value)
    if number == 0:
        return _DIGIT_WORDS[0]
    sign = "마이너스 " if number < 0 else ""
    number = abs(number)

    # 4자리씩 끊어 만/억/조 단위를 붙인다(150000 → 15만).
    groups: list[str] = []
    index = 0
    while number > 0:
        group = number % 10000
        number //= 10000
        if not group:
            index += 1
            continue
        words = _group_words(group)
        unit = _BIG_UNITS[index] if index < len(_BIG_UNITS) else ""
        # 1 은 큰 단위 앞에서도 읽지 않는다(10000 → '일만' 대신 '만')
        if group == 1 and unit:
            words = ""
        groups.append(words + unit)
        index += 1
    return sign + "".join(reversed(groups))


def _operator(op: str) -> str:
    """연산자 표기를 읽을 말로 바꾼다(×, * → '곱하기')."""
    return _OP_WORDS.get(op, op)


def answer(text: str) -> str | None:
    """사칙연산 한 문장이면 계산 결과를 한국어 문장으로 돌려준다.

    계산식이 아니면 None(호출부가 다른 경로로 보낸다).
    """
    body = compact(text)
    match = _EXPR.fullmatch(body)
    if match is None:
        return None

    left = parse_number(match.group("a"))
    right = parse_number(match.group("b"))
    if left is None or right is None:
        return None
    op = _operator(match.group("op"))

    if op == "나누기" and right == 0:
        return MSG_DIVIDE_BY_ZERO
    if op == "더하기":
        result: int | float = left + right
    elif op == "빼기":
        result = left - right
    elif op == "곱하기":
        result = left * right
    else:
        result = left / right
        # 나누어떨어지면 정수로 읽어 "삼 점 영영" 같은 소리를 피한다
        if float(result).is_integer():
            result = int(result)
        else:
            result = round(float(result), 2)

    lead = "약 " if isinstance(result, float) else ""
    return (
        f"네, {number_words(left)} {op} {number_words(right)}"
        f"{'은' if _has_batchim(number_words(right)) else '는'} "
        f"{lead}{number_words(result)}입니다."
    )


def _has_batchim(word: str) -> bool:
    """받침 유무로 조사(은/는)를 고른다(오백 → 은, 삼십 → 은, 오 → 는)."""
    last = word[-1]
    if "가" <= last <= "힣":
        return (ord(last) - ord("가")) % 28 != 0
    return False


def is_bare_numbers(text: str) -> bool:
    """연산자가 빠진 숫자 나열 질문인지("300, 800, 500은?").

    STT 가 "곱하기" 를 쉼표로 적어 버린 경우다. 어떤 계산인지 알 수 없으므로
    계산하지 않고, 작업 실행기로 보내지 않도록 호출부가 다시 묻는 데 쓴다.
    """
    return bool(_BARE_NUMBERS.fullmatch(text.strip()))
