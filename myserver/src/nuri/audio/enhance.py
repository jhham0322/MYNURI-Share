"""마이크 프레임에 참조 신호 기반 에코 제거를 적용하는 파이프라인.

`MicCapture.frames()` 가 주는 30ms 프레임을 받아
1) 참조(재생) 신호를 같은 시점으로 맞춰 읽고
2) AEC 로 에코를 빼고
3) 가공한 프레임을 그대로 흘려보낸다.

참조를 얻지 못하면(스테레오 믹스 없음 등) 원음을 그대로 통과시킨다. 즉 AEC 가
없을 때의 동작과 완전히 같고, 앱 루프는 이 파이프라인의 존재를 몰라도 된다.

지연 정렬: 시작 직후와 이후 주기적으로 마이크/참조 신호의 상호상관으로 지연을
재고, 값이 바뀌면 필터를 초기화한다. 정렬이 어긋난 채로 오래 두면 오히려
에코가 커지기 때문이다.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Iterator

import numpy as np

from nuri.audio.aec import EchoCanceller, estimate_delay
from nuri.audio.reference import ReferenceCapture
from nuri.config import AecConfig

logger = logging.getLogger("nuri.audio.enhance")

# 지연 추정에 쓰는 구간(초)과 최대 지연(ms)
ALIGN_WINDOW_S = 1.0
MAX_DELAY_MS = 500
# 상관 피크가 이보다 낮으면 재생 중이 아니라고 보고 지연을 갱신하지 않는다
ALIGN_MIN_PEAK = 0.35
# 지연이 이만큼(ms) 이상 달라지면 필터를 다시 학습시킨다
ALIGN_TOLERANCE_MS = 5
# 아직 지연을 재지 못했을 때(재생 없음 등) 다시 시도하는 간격(초).
# 30초를 기다리면 짧게 재생된 소리를 놓치고, 그동안 정렬이 어긋난 채로
# 학습해 에코가 오히려 커진다.
ALIGN_RETRY_S = 0.5


def _db(energy: float) -> int:
    """평균 전력을 dBFS(정수)로 바꾼다. 로그 가독성용."""
    return int(round(10.0 * float(np.log10(max(energy, 1e-12)))))


def suppress_residual(frame: np.ndarray, echo: np.ndarray, strength: float, floor: float) -> np.ndarray:
    """추정 에코가 남은 대역을 제한적으로 억제한다."""
    spectrum = np.fft.rfft(frame)
    echo_power = np.abs(np.fft.rfft(echo)) ** 2
    power = np.abs(spectrum) ** 2
    gain = np.sqrt(np.clip(1.0 - max(0.0, strength) * echo_power / (power + 1e-12),
                           np.clip(floor, 0.0, 1.0) ** 2, 1.0))
    return np.fft.irfft(spectrum * gain, n=frame.size).astype(np.float32)


class EnhancePipeline:
    """참조 신호 + AEC 를 마이크 프레임 스트림에 적용한다."""

    def __init__(self, cfg: AecConfig, samplerate: int, block_ms: int) -> None:
        self.cfg = cfg
        self.samplerate = samplerate
        self.block_ms = block_ms
        self.reference: ReferenceCapture | None = None
        self._aec: EchoCanceller | None = None
        self._delay = 0
        self._next_align = 0.0
        self._next_log = 0.0
        self._mic_hist: deque[np.ndarray] = deque(
            maxlen=max(1, int(ALIGN_WINDOW_S * 1000 / block_ms))
        )
        self._passthrough = not cfg.enabled
        # 로그용 레벨 누적(마이크/참조) — 참조가 실제로 소리를 받고 있는지 확인한다
        self._sum_mic = 0.0
        self._sum_ref = 0.0
        self._log_blocks = 0

    # --- 준비 ---
    def __enter__(self) -> "EnhancePipeline":
        if not self.cfg.enabled:
            logger.info("AEC 비활성(설정)")
            return self
        block = max(64, int(self.samplerate * self.block_ms / 1000))
        partitions = max(1, int(round(self.cfg.filter_ms / self.block_ms)))
        self._aec = EchoCanceller(
            block=block, partitions=partitions, mu=self.cfg.mu
        )
        self.reference = ReferenceCapture(
            self.samplerate, device=self.cfg.reference_device, block_ms=self.block_ms
        )
        self.reference.__enter__()
        if not self.reference.available:
            self._passthrough = True
            logger.warning("참조 신호를 얻지 못해 AEC 없이 동작합니다: %s", self.reference.error)
        else:
            logger.info(
                "AEC 시작: 블록=%d, 파티션=%d, mu=%.2f", block, partitions, self.cfg.mu
            )
        now = time.monotonic()
        self._next_align = now + self.cfg.align_seconds
        self._next_log = now + self.cfg.erle_log_s
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        if self.reference is not None:
            self.reference.__exit__(*exc)
            self.reference = None

    @property
    def active(self) -> bool:
        """에코 제거가 실제로 동작 중인지(참조 확보 + AEC 준비)."""
        return not self._passthrough and self._aec is not None

    # --- 처리 ---
    def frames(self, mic_frames: Iterator[np.ndarray]) -> Iterator[np.ndarray]:
        for frame in mic_frames:
            yield self.process(frame)

    def process(self, frame: np.ndarray) -> np.ndarray:
        """프레임 하나를 처리한다. AEC 가 없으면 원음을 그대로 돌려준다."""
        if not self.active:
            return frame
        assert self.reference is not None and self._aec is not None
        body = np.asarray(frame, dtype=np.float32).reshape(-1)
        self._mic_hist.append(body)
        ref = self.reference.read(body.size, delay=self._delay)
        if not np.any(ref):
            return body  # 참조가 비어 있으면(무음) 그대로 통과
        out = self._aec.process(body, ref)
        if self.cfg.res_enabled and not self._aec.double_talk and out.size == body.size:
            out = suppress_residual(out, self._aec.last_echo, self.cfg.res_strength, self.cfg.res_floor)
        self._sum_mic += float(np.mean(body.astype(np.float64) ** 2))
        self._sum_ref += float(np.mean(ref.astype(np.float64) ** 2))
        self._log_blocks += 1
        self._maybe_align()
        self._maybe_log()
        return out

    # --- 정렬·로그 ---
    def _maybe_align(self) -> None:
        assert self.reference is not None
        now = time.monotonic()
        if now < self._next_align or len(self._mic_hist) < self._mic_hist.maxlen:
            return
        window = int(self.samplerate * ALIGN_WINDOW_S)
        mic = np.concatenate(self._mic_hist)[-window:]
        ref = self.reference.read(mic.size, delay=0)
        if not np.any(ref):
            # 재생이 없으면 지연을 잴 수 없다. 간격을 짧게 잡아 두고 곧 다시 본다.
            self._next_align = now + ALIGN_RETRY_S
            return
        delay, peak = estimate_delay(mic, ref, int(self.samplerate * MAX_DELAY_MS / 1000))
        if peak < ALIGN_MIN_PEAK:
            # 상관이 약하면(아직 재생이 작음) 지연을 믿을 수 없다
            self._next_align = now + ALIGN_RETRY_S
            return
        self._next_align = now + self.cfg.realign_interval_s
        tolerance = int(self.samplerate * ALIGN_TOLERANCE_MS / 1000)
        if abs(delay - self._delay) > tolerance:
            logger.info("AEC 지연 재정렬: %dms → %dms (피크 %.2f)",
                        int(self._delay * 1000 / self.samplerate),
                        int(delay * 1000 / self.samplerate), peak)
            self._delay = delay
            if self._aec is not None:
                self._aec.reset()

    def _maybe_log(self) -> None:
        if self._aec is None:
            return
        now = time.monotonic()
        if now < self._next_log:
            return
        self._next_log = now + self.cfg.erle_log_s
        erle, blocks, frozen = self._aec.take_erle()
        if blocks == 0:
            return
        mic_db = _db(self._sum_mic / max(self._log_blocks, 1))
        ref_db = _db(self._sum_ref / max(self._log_blocks, 1))
        self._sum_mic = 0.0
        self._sum_ref = 0.0
        self._log_blocks = 0
        logger.info(
            "AEC 상태: ERLE=%.1fdB, 지연=%dms, 마이크=%ddB, 참조=%ddB, 블록=%d, 적응정지=%d",
            erle, int(self._delay * 1000 / self.samplerate), mic_db, ref_db, blocks, frozen,
        )
