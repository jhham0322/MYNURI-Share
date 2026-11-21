"""STT 엔진 (faster-whisper, 로컬)."""

from __future__ import annotations

import logging
import re

import numpy as np

from nuri.config import STTConfig

logger = logging.getLogger("nuri.stt")


def _resample(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr or audio.size == 0:
        return audio
    duration = audio.size / src_sr
    n_dst = int(round(duration * dst_sr))
    if n_dst <= 0:
        return np.zeros(0, dtype=np.float32)
    x_old = np.linspace(0.0, duration, num=audio.size, endpoint=False)
    x_new = np.linspace(0.0, duration, num=n_dst, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def _normalize(audio: np.ndarray) -> np.ndarray:
    """너무 작은 입력을 0.9 피크로 정규화. 무음(피크<0.01)은 건드리지 않는다."""
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if 0.01 <= peak < 0.9:
        audio = audio * (0.9 / peak)
    return audio.astype(np.float32)


class STTEngine:
    def warmup(self) -> None:  # 기본: 아무 것도 안 함
        pass

    def transcribe(self, audio: np.ndarray, samplerate: int) -> str:  # pragma: no cover
        raise NotImplementedError

    def transcribe_quick(self, audio: np.ndarray, samplerate: int) -> str:
        """호출어 판정용 빠른 전사. 기본은 정확한 모델과 같다.

        호출어만 들렸는지 먼저 알아야 "네, 말씀하세요." 안내를 곧바로 시작할 수
        있다. 작은 모델을 쓸 수 있는 엔진은 이 메서드를 재정의한다.
        """
        return self.transcribe(audio, samplerate)


class FasterWhisperSTT(STTEngine):
    """faster-whisper 로컬 모델. 최초 사용 시 모델을 내려받아 로드한다."""

    def __init__(self, cfg: STTConfig) -> None:
        self.cfg = cfg
        self._model = None
        self._quick_model = None

    def warmup(self) -> None:
        """녹음 시작 전에 모델을 미리 로드해 첫 전사 지연을 없앤다.

        호출어 판정용 모델도 함께 올린다. 첫 호출에서 모델 로딩(1~2초)을
        기다리면 빠른 판정의 의미가 없어진다.
        """
        self._ensure_model()
        self._ensure_quick_model()

    def _load(self, size: str):
        from faster_whisper import WhisperModel

        logger.info("STT 모델 로딩: %s (device=%s, compute=%s)",
                    size, self.cfg.device, self.cfg.compute_type)
        return WhisperModel(
            size,
            device=self.cfg.device,
            compute_type=self.cfg.compute_type,
        )

    def _ensure_model(self):
        if self._model is None:
            self._model = self._load(self.cfg.model_size)
        return self._model

    def _ensure_quick_model(self):
        """호출어 판정용 모델. 주 모델과 같으면 같은 인스턴스를 쓴다."""
        size = self.cfg.quick_model_size or self.cfg.model_size
        if size == self.cfg.model_size:
            return self._ensure_model()
        if self._quick_model is None:
            self._quick_model = self._load(size)
        return self._quick_model

    def transcribe(self, audio: np.ndarray, samplerate: int) -> str:
        return self._run(self._ensure_model(), audio, samplerate)

    def transcribe_quick(self, audio: np.ndarray, samplerate: int) -> str:
        return self._run(self._ensure_quick_model(), audio, samplerate)

    def _run(self, model, audio: np.ndarray, samplerate: int) -> str:
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        if samplerate != 16000:
            audio = _resample(audio, samplerate, 16000)
        if audio.size == 0:
            return ""
        audio = _normalize(audio)
        segments, _info = model.transcribe(
            audio,
            language=self.cfg.language,
            beam_size=self.cfg.beam_size,
            # 상위 단계에서 이미 VAD 로 발화를 잘라냈으므로 내부 VAD 는 끈다.
            # (짧은 발화를 통째로 제거하는 문제 방지)
            vad_filter=False,
            condition_on_previous_text=False,
            without_timestamps=True,
            temperature=0.0,
            # 조용한 잡음에 대한 환각(예: "MBC 뉴스...") 억제
            no_speech_threshold=self.cfg.no_speech_threshold,
            log_prob_threshold=self.cfg.log_prob_threshold,
            # 짧은 호출어가 누락되는 것을 줄이기 위한 편향
            initial_prompt=self.cfg.initial_prompt or None,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        # 프롬프트에 나열한 연산자만 그대로 되풀이한 결과는 잡음 환각일 수 있다.
        # 실제 문장이나 숫자가 섞인 계산식은 그대로 전달한다.
        compact = re.sub(r"[\s,.!?~·]+", "", text)
        prompt_ops = re.sub(r"[\s,.!?~·]+", "", self.cfg.initial_prompt).replace("누리야", "")
        if len(compact) >= len("곱하기더하기") and compact in prompt_ops:
            logger.info("STT 힌트만 반복한 결과 무시")
            return ""
        return text


def build_stt(cfg: STTConfig) -> STTEngine:
    return FasterWhisperSTT(cfg)
