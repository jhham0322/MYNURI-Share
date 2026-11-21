"""누리 기본 설정."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def default_rag_dir() -> Path:
    """기억 저장소 기본 위치.

    환경변수 → 저장소 루트의 `rag` 폴더 → 사용자 홈 순으로 고른다.
    설치본(site-packages)에서 실행되면 저장소 루트 판별에 실패하므로 홈을 쓴다.
    """
    env = os.getenv("NURI_RAG_DIR")
    if env:
        return Path(env)
    root = Path(__file__).resolve().parents[2]
    if (root / "AGENTS.md").exists():
        return root / "rag"
    return Path.home() / "nuri" / "rag"


@dataclass
class AudioConfig:
    samplerate: int = 16000
    channels: int = 1
    block_ms: int = 30  # VAD 프레임 길이
    device: int | str | None = None


@dataclass
class VadConfig:
    engine: str = "silero"             # 모델 또는 런타임이 없으면 에너지 방식으로 복귀
    silero_path: str | None = None
    speech_threshold: float = 0.5
    continue_threshold: float = 0.35
    # 에너지 기반 VAD
    abs_threshold: float = 0.012       # 절대 최소 RMS
    noise_multiplier: float = 3.0      # 노이즈 플로어 대비 배수
    start_frames: int = 3              # 연속 몇 프레임 이상이면 발화 시작
    end_silence_ms: int = 600          # 이만큼 조용하면 발화 종료
    min_speech_ms: int = 250           # 최소 발화 길이
    max_speech_ms: int = 12000         # 한 발화 최대 길이(무한 대기 방지)
    preroll_ms: int = 600              # VAD 판정이 늦어도 짧은 호출어의 앞부분을 보존


@dataclass
class LoopConfig:
    wake_keyword: str = "누리"
    wake_aliases: tuple[str, ...] = ("누리", "누리야", "노리", "누리야,", "우리야")
    wake_inline_max_chars: int = 12    # 이 길이 이하 발화에서만 문장 중간 호출어 인정
    min_command_chars: int = 2         # 이보다 짧은 명령은 무시(호출어만 발화 방지)
    listen_timeout_ms: int = 8000      # 호출 후 명령 대기 최대 시간
    # 호출어만 다시 들렸을 때(사용자가 응답을 기다리는 중) 안내 후 다시 들을 횟수.
    # 0 이면 예전처럼 조용히 IDLE 로 돌아가므로, 반응 없는 것처럼 들린다.
    listen_retries: int = 2
    error_text: str = "죄송해요, 다시 한 번 말씀해 주세요."
    max_turns: int = 1                 # 한 번 호출에 처리할 턴 수

    # 결과를 안내한 뒤, 호출어 없이 이어 말하는 지시를 받아들이는 창(연속 대화).
    # 사용자가 결과를 듣고 바로 "현재 시간 알려줘"처럼 이어 말해도 다시 부르지 않게 한다.
    followup_enabled: bool = True
    followup_timeout_ms: int = 8000     # 안내 직후 이 시간 안에 말을 시작하면 후속 지시로 본다
    followup_max_turns: int = 3         # 한 번의 호출로 호출어 없이 처리할 최대 지시 수
    followup_listen_attempts: int = 2   # 창 안에서 살펴볼 최대 발화 수(호출어만 다시 말한 경우 등)
    followup_quiet_ms: int = 60         # 이만큼 조용하면 에코가 끝난 것으로 보고 바로 듣는다
    followup_settle_ms: int = 250       # 에코가 잦아들 때까지 기다리는 최대 시간(넘으면 그대로 듣는다)


@dataclass
class STTConfig:
    model_size: str = "small"          # tiny/base/small/medium/large-v3
    # 호출어 판정용 모델. 호출어만 들렸는지 빨리 가려 "네, 말씀하세요." 안내를
    # 곧바로 시작한다. 비우면 주 모델을 그대로 쓴다(느리지만 정확).
    quick_model_size: str = "base"
    device: str = "cpu"                # cpu | cuda
    compute_type: str = "int8"         # int8 | float16 | float32
    language: str = "ko"
    beam_size: int = 5
    min_peak: float = 0.08             # 이보다 작은 신호는 무음으로 보고 STT 생략
    no_speech_threshold: float = 0.6   # Whisper 환각 억제
    log_prob_threshold: float = -1.0
    # 호출어 인식 편향(짧은 '누리야' 누락 완화). 뒤에 계산 연산자를 붙여 두면
    # "300 곱하기 500" 의 '곱하기' 가 쉼표로 뭉개지는 오인식이 크게 줄어든다.
    initial_prompt: str = "누리야, 곱하기 더하기 빼기 나누기"


@dataclass
class TTSConfig:
    engine: str = "edge"               # edge | pyttsx3
    voice: str = "ko-KR-SunHiNeural"   # edge-tts 한국어 음성
    rate: str = "+0%"                  # edge-tts 속도
    pyttsx3_rate: int = 180            # pyttsx3 말하기 속도


@dataclass
class PaseoConfig:
    """paseo CLI 로 명령을 실제 에이전트에게 실행시킬 때의 설정."""

    executable: str = "paseo"
    provider: str = "codebuddy-code"   # 기본 에이전트 제공자
    mode: str = "fullAccess"           # 에이전트 권한 모드(음성은 승인 불가 → 전권 허용)
    agent_id: str | None = None        # 지정 시 새 에이전트 대신 기존 에이전트 재사용
    cwd: str | None = None             # 에이전트 작업 디렉터리(기본: 누리 실행 위치)
    workspace_id: str | None = field(default_factory=lambda: os.getenv("NURI_PASEO_WORKSPACE_ID"))
    title: str = "누리 지시"
    start_timeout_seconds: float = 60.0    # 에이전트 생성 요청 상한
    wait_timeout_seconds: float = 180.0    # 작업 완료 대기 상한(무한 대기 방지)
    log_timeout_seconds: float = 30.0      # 응답 회수 상한
    log_tail: int = 1                      # 가져올 마지막 텍스트 블록 수
    keep_agents: int = 5                   # 누리 에이전트를 최신 몇 개까지 남길지(0 이면 정리 안 함)
    prune_timeout_seconds: float = 20.0    # 목록 조회/삭제 상한
    # 음성 안내 길이: 결과가 길어도 이 시간 분량으로 함축해서 읽는다
    # (뉴스·브리핑처럼 전문을 들려줘야 하는 요청은 아래 full_read_* 설정을 쓴다)
    speech_seconds: float = 10.0           # 음성으로 안내할 목표 길이(초)
    speech_chars_per_second: float = 7.0   # edge-tts 한국어 실측 속도(글자/초, 공백 포함)
    request_summary: bool = True           # 에이전트에게 음성용 요약 줄을 함께 요청
    summary_instruction: str = (
        "[필수] 답변을 마치기 전에, 음성 비서가 읽을 요약을 답변 맨 마지막 줄에 "
        "`요약: ` 로 시작하는 한국어 1~2문장(공백 포함 {chars}자 이내)으로 반드시 추가하세요. "
        "핵심 결과와 사용자가 할 일만 담고, 목록·표·코드 없이 평서문으로 씁니다. "
        "그 위에 나중에 다시 쓸 사실(이름·경로·날짜·설정값·결정 사항)이 있으면 "
        "`기억: ` 로 시작하는 한 줄을 사실마다 하나씩(최대 {notes}줄) 적고, 없으면 생략하세요."
    )
    max_notes: int = 3                     # 한 작업에서 기억으로 남길 최대 사실 수
    note_max_chars: int = 120              # 기억 한 건의 최대 길이(음성으로 읽기 좋은 분량)
    # 전문 낭독: 뉴스·브리핑처럼 끝까지 들려줘야 하는 요청은 10초로 줄이지 않는다.
    # 10초 요약만 읽으면 첫 항목만 듣고 끝나므로, 이런 요청만 길게 읽는다.
    full_read_seconds: float = 120.0       # 전문 낭독 목표 길이(초)
    full_read_instruction: str = (
        "[필수] 이 답변은 음성 비서가 처음부터 끝까지 그대로 읽습니다. "
        "마크다운 기호·목록 번호·표·링크·코드 없이, 사람이 말하듯 자연스러운 완결된 문장으로 "
        "요청한 항목을 빠뜨리지 말고 차례대로 모두 담아 {chars}자 이내로 작성하세요. "
        "답변 맨 마지막 줄에는 음성용 짧은 요약을 `요약: ` 로 시작하는 한국어 1~2문장으로 덧붙이고, "
        "나중에 다시 쓸 사실이 있으면 `기억: ` 로 시작하는 한 줄을 사실마다 하나씩(최대 {notes}줄) 적으세요."
    )

    @property
    def max_reply_chars(self) -> int:
        """음성으로 읽을 최대 글자 수(기본 10초 × 7자 ≈ 70자)."""
        return max(20, int(self.speech_seconds * self.speech_chars_per_second))

    @property
    def max_full_chars(self) -> int:
        """전문 낭독으로 읽을 최대 글자 수(기본 2분 × 7자 ≈ 840자)."""
        return max(self.max_reply_chars, int(self.full_read_seconds * self.speech_chars_per_second))


@dataclass
class UiConfig:
    """화면 표시 관련 설정."""

    tray: bool = True                  # 실행 중 시스템 트레이에 표시등 표시


@dataclass
class RagConfig:
    """누리 전용 기억 저장소(검색 증강) 설정."""

    enabled: bool = True
    path: str | None = None            # None 이면 저장소 루트의 rag 폴더
    max_items: int = 2000              # 보관할 최대 기억 수(넘으면 오래된 것부터 정리)
    answer_min_score: float = 0.30     # 질문에 답할 때 요구하는 유사도 하한
    inject_min_score: float = 0.45     # 작업 지시에 기억을 붙일 때의 하한(오주입 방지)
    max_inject: int = 3                # 작업 지시에 붙일 최대 기억 수
    max_text_chars: int = 200          # 기억 한 건의 최대 길이

    # 최근 대화 이력(기억과 별도 파일). 주고받은 말을 그대로 남겨 맥락을 되짚는다.
    log_enabled: bool = True
    log_days: int = 3                  # 원문 보관 기간(일), 날짜 전체 만료 후 처리기 요약
    log_max_items: int = 3000          # 호환용 설정. 보관 기간 안의 원문은 줄 수로 자르지 않음

    @property
    def directory(self) -> Path:
        return Path(self.path) if self.path else default_rag_dir()


@dataclass
class ChatConfig:
    """일상 대화 흥응 설정."""

    enabled: bool = True
    reflect_statements: bool = True    # 지시도 질문도 아닌 진술에 짧게 반응


@dataclass
class MemoConfig:
    """메모 모드(호출어 없이 받아쓰고 요약) 설정."""

    enabled: bool = True
    summary_every: int = 5             # 몇 발화마다 요약할지(요약 단위)
    max_buffer_chars: int = 1200       # 한 번에 요약할 최대 글자 수
    idle_timeout_ms: int = 1500        # 이만큼 조용하면 밀린 요약을 안내한다
    summary_prompt: str = (
        "다음은 음성 메모로 받아쓴 내용입니다. 도구 사용 없이 핵심만 한국어 두세 문장으로 정리하세요."
    )


@dataclass
class SelfImproveConfig:
    """대화 중 요청받은 개선을 스스로 적용할지."""

    enabled: bool = True
    restart_enabled: bool = True       # 적용 후 모듈 재기동 여부
    wait_timeout_seconds: float = 900.0    # 개선 작업(에이전트) 대기 상한
    verify_timeout_seconds: float = 180.0  # 검증(compileall/unittest) 상한
    keep_snapshots: int = 5            # backup/selfimprove_* 보관 개수


@dataclass
class ScheduleConfig:
    """예약(알람/주기 작업) 설정.

    "한 시간 후에 알람", "매일 오전 8시에 뉴스 조사해서 알려" 처럼 시각이 걸린
    요청은 예약으로 남겨 두고, 시간이 되면 누리가 먼저 말을 건다.
    """

    enabled: bool = True
    path: str | None = None            # None 이면 기억 저장소 폴더의 schedules.json
    poll_seconds: float = 1.0          # 예약 시각 확인 간격(초)
    misfire_grace_minutes: float = 60.0  # 이 시간 안에 지난 예약만 안내한다(놓친 예약 방지)
    max_items: int = 20                # 보관할 최대 예약 수

    @property
    def file(self) -> Path:
        base = Path(self.path) if self.path else default_rag_dir()
        return base / "schedules.json" if base.suffix != ".json" else base


@dataclass
class AecConfig:
    """참조 신호 기반 에코 제거(AEC) 설정.

    유튜브 등 다른 소리가 재생되는 환경에서 마이크로 되돌아온 소리를 지운다.
    참조 신호(출력 루프백)를 얻지 못하면 자동으로 원음 통과로 동작한다.
    """

    enabled: bool = True
    reference_device: int | str | None = None   # None 이면 이름으로 스테레오 믹스 탐색
    filter_ms: int = 270                        # 적응 필터 길이(반향 커버 범위)
    mu: float = 0.1                             # 적응 스텝 크기(작을수록 안정·느림)
    align_seconds: float = 3.0                  # 시작 후 첫 지연 측정까지 기다리는 시간
    realign_interval_s: float = 30.0            # 지연 재측정 주기
    erle_log_s: float = 5.0                     # 상태(ERLE) 로그 주기
    res_enabled: bool = False                   # 실환경 검증 전에는 음성 손상 방지
    res_strength: float = 0.5
    res_floor: float = 0.5


@dataclass
class DuckingConfig:
    enabled: bool = False
    level: float = 0.2


@dataclass
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    aec: AecConfig = field(default_factory=AecConfig)
    ducking: DuckingConfig = field(default_factory=DuckingConfig)
    loop: LoopConfig = field(default_factory=LoopConfig)
    stt: STTConfig = field(default_factory=STTConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    paseo: PaseoConfig = field(default_factory=PaseoConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    rag: RagConfig = field(default_factory=RagConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)
    memo: MemoConfig = field(default_factory=MemoConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    improve: SelfImproveConfig = field(default_factory=SelfImproveConfig)
    use_stt: bool = False              # 무거운 STT 엔진 사용 여부
    use_tts: bool = False              # TTS 사용 여부


DEFAULT_CONFIG = Config()
