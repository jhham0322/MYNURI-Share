"""TTS 엔진.

- EdgeTTS: 마이크로소프트 edge-tts(온라인, 한국어 고품질). mp3 를 soundfile 로 디코딩해 재생.
- Pyttsx3TTS: Windows SAPI5(오프라인). 즉시 재생.
- FallbackTTS: 주 엔진 실패 시 보조 엔진으로 재생.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import threading

import numpy as np

from nuri.config import TTSConfig

logger = logging.getLogger("nuri.tts")

# TTS 는 기호와 천 단위 쉼표를 엉뚱하게 읽는다("150,000" → "일백오십 쉼표 영영영",
# "×" → "엑스"). 말로 읽히는 형태로 바꿔서 넘긴다.
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("×", " 곱하기 "),
    ("÷", " 나누기 "),
    ("＋", " 더하기 "),
    ("−", " 빼기 "),
    ("%", " 퍼센트 "),
    ("=", " "),
)
_SPACES = re.compile(r"\s+")


def speech_text(text: str) -> str:
    """TTS 로 읽기 전에 기호·쉼표를 음성으로 읽히는 형태로 바꾼다."""
    body = _THOUSANDS.sub("", text)
    for symbol, word in _SYMBOLS:
        body = body.replace(symbol, word)
    return _SPACES.sub(" ", body).strip()


class TTSEngine:
    def speak(self, text: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def prepare(self, text: str) -> None:
        """자주 쓰는 고정 안내를 미리 합성해 둔다(기본은 아무것도 하지 않는다).

        합성에 시간이 걸리는 엔진은 여기서 미리 만들어 두면 실제 재생이 빨라진다.
        실패해도 재생 시 다시 시도하므로 예외를 올리지 않는다.
        """
        del text  # 기본 구현은 할 일이 없다(하위 엔진이 재정의한다)


class EdgeTTS(TTSEngine):
    # 짧은 고정 안내 문구만 담는다(긴 작업 결과는 매번 달라 캐시 효과가 없다).
    CACHE_LIMIT = 16

    def __init__(self, cfg: TTSConfig) -> None:
        self.cfg = cfg
        self._cache: dict[tuple[str, str, str], tuple[np.ndarray, int]] = {}
        # 미리 합성(백그라운드)과 실제 재생이 동시에 캐시를 만질 수 있다.
        # 합성 자체는 잠그지 않고(네트워크 대기) 사전 접근만 보호한다.
        self._lock = threading.Lock()

    def _cache_key(self, text: str) -> tuple[str, str, str]:
        # 목소리·속도가 바뀌면 같은 문장도 다른 소리이므로 함께 키에 넣는다.
        return (speech_text(text), self.cfg.voice, self.cfg.rate)

    async def _collect_mp3(self, text: str) -> bytes:
        import edge_tts

        communicate = edge_tts.Communicate(text, self.cfg.voice, rate=self.cfg.rate)
        buf = bytearray()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        """텍스트 → (모노 float32 파형, 샘플레이트). 짧은 안내는 캐시를 쓴다."""
        import soundfile as sf

        key = self._cache_key(text)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        mp3 = asyncio.run(self._collect_mp3(speech_text(text)))
        if not mp3:
            raise RuntimeError("edge-tts 가 오디오를 반환하지 않았습니다.")
        data, sr = sf.read(io.BytesIO(mp3), dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        audio = data.astype(np.float32)
        with self._lock:
            if len(self._cache) >= self.CACHE_LIMIT:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (audio, int(sr))
        return audio, int(sr)

    def prepare(self, text: str) -> None:
        try:
            self.synthesize(text)
        except Exception as exc:  # noqa: BLE001 - 미리 합성 실패는 재생 때 다시 시도한다
            logger.debug("TTS 미리 합성 실패(%r): %s", text, exc)

    def speak(self, text: str) -> None:
        import sounddevice as sd

        data, sr = self.synthesize(text)
        sd.play(data, sr)
        sd.wait()


class Pyttsx3TTS(TTSEngine):
    def __init__(self, cfg: TTSConfig) -> None:
        self.cfg = cfg
        self._engine = None

    def _ensure_engine(self):
        if self._engine is None:
            import pyttsx3

            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", self.cfg.pyttsx3_rate)
        return self._engine

    def speak(self, text: str) -> None:
        engine = self._ensure_engine()
        engine.say(speech_text(text))
        engine.runAndWait()


class FallbackTTS(TTSEngine):
    def __init__(self, primary: TTSEngine, secondary: TTSEngine) -> None:
        self.primary = primary
        self.secondary = secondary

    def prepare(self, text: str) -> None:
        # 미리 합성은 주 엔진에만 한다(보조 엔진은 즉시 재생이라 필요 없다).
        self.primary.prepare(text)

    def speak(self, text: str) -> None:
        try:
            self.primary.speak(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("주 TTS 실패(%s) → 보조 TTS 사용: %s", type(self.primary).__name__, exc)
            self.secondary.speak(text)


def build_tts(cfg: TTSConfig) -> TTSEngine:
    if cfg.engine == "pyttsx3":
        return Pyttsx3TTS(cfg)
    # 기본: edge-tts, 실패 시 pyttsx3 로 폴백
    return FallbackTTS(EdgeTTS(cfg), Pyttsx3TTS(cfg))
