"""에너지 기반 VAD 및 발화 구간(utterance) 분리.

마이크 프레임을 하나씩 넣으면, 발화가 끝났을 때 완성된 발화 오디오를 돌려준다.
외부 모델 없이 RMS 에너지와 적응형 노이즈 플로어로 동작한다.
"""

from __future__ import annotations

import logging
from collections import deque
from pathlib import Path

import numpy as np

from nuri.config import VadConfig

logger = logging.getLogger(__name__)


class SileroVad:
    """16kHz 스트림을 512샘플 단위로 추론하고 이전 문맥을 유지한다."""

    def __init__(self, path: str | None, samplerate: int) -> None:
        import onnxruntime as ort

        if samplerate != 16000:
            raise ValueError("Silero VAD는 16kHz 입력이 필요합니다")
        model = (
            Path(path)
            if path
            else Path(__file__).resolve().parents[1] / "assets/vad/silero_vad.onnx"
        )
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(model), options, providers=["CPUExecutionProvider"]
        )
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(64, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)
        self._probability = 0.0

    def probability(self, frame: np.ndarray) -> float:
        self._pending = np.concatenate((self._pending, np.asarray(frame, dtype=np.float32)))
        while self._pending.size >= 512:
            chunk, self._pending = self._pending[:512], self._pending[512:]
            data = np.concatenate((self._context, chunk))[None, :]
            output, self._state = self._session.run(
                None,
                {
                    "input": data,
                    "state": self._state,
                    "sr": np.array(16000, dtype=np.int64),
                },
            )
            self._context = chunk[-64:].copy()
            self._probability = float(output[0, 0])
        return self._probability


def frame_rms(frame: np.ndarray) -> float:
    if frame.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))


class UtteranceSegmenter:
    """프레임 스트림 → 발화(np.ndarray) 분리기."""

    def __init__(self, cfg: VadConfig, samplerate: int, block_ms: int) -> None:
        self.cfg = cfg
        self.samplerate = samplerate
        self.block_ms = block_ms
        self._frames_per_ms = 1.0 / block_ms

        self._noise_floor = cfg.abs_threshold
        self._silence_needed = max(1, int(cfg.end_silence_ms * self._frames_per_ms))
        self._preroll = deque(maxlen=max(1, int(cfg.preroll_ms * self._frames_per_ms)))
        self._min_frames = max(1, int(cfg.min_speech_ms * self._frames_per_ms))
        self._max_frames = max(1, int(cfg.max_speech_ms * self._frames_per_ms))

        self._in_speech = False
        self._speech_run = 0
        self._silence_run = 0
        self._buffer: list[np.ndarray] = []
        self._silero: SileroVad | None = None
        if cfg.engine == "silero":
            try:
                self._silero = SileroVad(cfg.silero_path, samplerate)
                logger.info("Silero VAD 사용")
            except Exception as exc:  # noqa: BLE001 - 기존 청취 경로로 복귀
                logger.warning("Silero VAD 사용 불가, 에너지 VAD로 전환: %s", exc)

    def _threshold(self) -> float:
        return max(self.cfg.abs_threshold, self._noise_floor * self.cfg.noise_multiplier)

    def _update_noise(self, rms: float) -> None:
        # 조용한 프레임만 천천히 반영
        if rms < self._threshold():
            self._noise_floor = 0.95 * self._noise_floor + 0.05 * rms

    def feed(self, frame: np.ndarray) -> np.ndarray | None:
        rms = frame_rms(frame)
        self._update_noise(rms)
        loud = rms > self._threshold()
        if self._silero is not None:
            try:
                threshold = (
                    self.cfg.continue_threshold if self._in_speech else self.cfg.speech_threshold
                )
                loud = self._silero.probability(frame) >= threshold
            except Exception as exc:  # noqa: BLE001 - 추론 실패가 청취를 막지 않는다
                logger.warning("Silero VAD 추론 실패, 에너지 VAD로 전환: %s", exc)
                self._silero = None

        if not self._in_speech:
            self._preroll.append(frame)
            if loud:
                self._speech_run += 1
            else:
                self._speech_run = 0
            if self._speech_run >= self.cfg.start_frames:
                self._in_speech = True
                self._silence_run = 0
                self._buffer = list(self._preroll)
            return None

        # 발화 중
        self._buffer.append(frame)
        if loud:
            self._silence_run = 0
        else:
            self._silence_run += 1

        ended = self._silence_run >= self._silence_needed
        too_long = len(self._buffer) >= self._max_frames
        if ended or too_long:
            utterance = np.concatenate(self._buffer) if self._buffer else np.zeros(0, np.float32)
            self._in_speech = False
            self._speech_run = 0
            self._silence_run = 0
            self._buffer = []
            self._preroll.clear()
            if len(utterance) >= self._min_frames * self._samples_per_frame():
                return utterance.astype(np.float32)
        return None

    def reset(self) -> None:
        """진행 중인 발화 판정과 프리롤을 버리고 조용한 상태로 되돌린다.

        TTS 재생 중 마이크에 들어온 자기 목소리(에코)가 다음 발화의 시작으로
        이어지지 않도록, 후속 지시를 듣기 전에 한 번 비운다.
        """
        self._in_speech = False
        self._speech_run = 0
        self._silence_run = 0
        self._buffer = []
        self._preroll.clear()
        if self._silero is not None:
            self._silero.reset()

    @property
    def in_speech(self) -> bool:
        """발화가 진행 중인지.

        대기 시간이 끝났더라도 말이 이미 시작됐다면 문장이 끝날 때까지 들어야
        "청주 날씨 알려줘" 처럼 문장 중간이 잘려 사라지는 일이 없다.
        """
        return self._in_speech

    def _samples_per_frame(self) -> int:
        return int(self.samplerate * self.block_ms / 1000)
