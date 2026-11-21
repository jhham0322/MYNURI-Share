"""재생 중인 소리(참조 신호) 캡처.

AEC 는 "스피커로 나간 소리"를 알고 있어야 그 소리가 마이크로 되돌아온 성분을
지울 수 있다. 여기서는 출력 장치의 루프백(스테레오 믹스 등)을 입력으로 열어
16kHz 모노로 맞춘 참조 신호를 링 버퍼에 쌓는다.

`sounddevice` 의 WASAPI 설정에는 루프백 옵션이 없으므로, Windows 에서 흔히
노출되는 "스테레오 믹스" 입력 장치를 이름으로 찾아 쓴다. 장치가 없거나 열 수
없으면 `available=False` 로 조용히 비활성화된다(AEC 는 원음 통과로 동작).
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger("nuri.audio.reference")

# 이름으로 루프백 장치를 찾을 때 쓰는 표기(한글/영문 드라이버 이름)
_LOOPBACK_HINTS = ("스테레오 믹스", "stereo mix", "what u hear", "루프백", "loopback")
# 참조 링 버퍼 길이(초). 지연 정렬 여유(기본 500ms)를 넉넉히 덮는다.
RING_SECONDS = 2.0


class RingBuffer:
    """최근 오디오를 담아 두고, 원하는 길이만큼 뒤에서 잘라 주는 원형 버퍼."""

    def __init__(self, capacity: int) -> None:
        self._buf = np.zeros(max(1, capacity), dtype=np.float32)
        self._write = 0
        self._filled = 0

    def write(self, samples: np.ndarray) -> None:
        if samples.size == 0:
            return
        body = samples.astype(np.float32, copy=False)
        size = self._buf.size
        if body.size >= size:
            self._buf[:] = body[-size:]
            self._write = 0
            self._filled = size
            return
        end = self._write + body.size
        if end <= size:
            self._buf[self._write:end] = body
        else:
            first = size - self._write
            self._buf[self._write:] = body[:first]
            self._buf[: end - size] = body[first:]
        self._write = end % size
        self._filled = min(size, self._filled + body.size)

    def read(self, count: int, delay: int = 0) -> np.ndarray:
        """가장 최근에서 `delay` 샘플 뒤로 `count` 샘플을 꺼낸다(부족하면 0 채움)."""
        count = max(0, int(count))
        if count == 0:
            return np.zeros(0, dtype=np.float32)
        delay = max(0, int(delay))
        size = self._buf.size
        available = min(count, self._filled - delay)
        out = np.zeros(count, dtype=np.float32)
        if available <= 0:
            return out
        end = (self._write - delay) % size
        start = (end - available) % size
        body = self._buf[start:end] if start < end else np.concatenate(
            (self._buf[start:], self._buf[:end])
        )
        out[count - available:] = body
        return out

    @property
    def filled(self) -> int:
        return self._filled


@dataclass(frozen=True)
class ReferenceInfo:
    """참조 스트림 상태(로그·진단용)."""

    device: int
    name: str
    samplerate: int


class ReferenceCapture:
    """출력 루프백 장치를 열어 16kHz 모노 참조 신호를 링 버퍼에 쌓는다."""

    def __init__(
        self,
        target_samplerate: int,
        device: int | str | None = None,
        block_ms: int = 30,
    ) -> None:
        self.target_samplerate = target_samplerate
        self.block_ms = block_ms
        self.device = device
        self.info: ReferenceInfo | None = None
        self._ring = RingBuffer(int(target_samplerate * RING_SECONDS))
        self._stream = None
        self._q: "queue.Queue[np.ndarray]" = queue.Queue()
        self._error: str | None = None

    # --- 장치 선택 ---
    @staticmethod
    def find_device(device: int | str | None = None) -> int | None:
        """참조로 쓸 입력 장치를 찾는다. 못 찾으면 None."""
        import sounddevice as sd

        if isinstance(device, int):
            return device
        devices = sd.query_devices()
        for index, entry in enumerate(devices):
            if int(entry.get("max_input_channels", 0)) < 1:
                continue
            name = str(entry.get("name", ""))
            if device is not None and str(device).lower() not in name.lower():
                continue
            if any(hint in name.lower() for hint in _LOOPBACK_HINTS):
                return index
        return None

    def _open(self) -> bool:
        import sounddevice as sd

        index = self.find_device(self.device)
        if index is None:
            self._error = "루프백(스테레오 믹스) 입력 장치를 찾지 못했습니다"
            return False
        try:
            entry = sd.query_devices(index)
            default_sr = int(entry.get("default_samplerate") or 48000)
            channels = min(2, max(1, int(entry.get("max_input_channels", 1))))
        except Exception as exc:  # noqa: BLE001 - 장치 질의 실패는 비활성화로 처리
            self._error = f"참조 장치 정보를 읽지 못했습니다: {exc}"
            return False

        attempts = [default_sr, 48000, 44100, self.target_samplerate]
        for sr in dict.fromkeys(attempts):
            try:
                stream = sd.InputStream(
                    device=index,
                    channels=channels,
                    samplerate=sr,
                    dtype="float32",
                    blocksize=max(1, int(sr * self.block_ms / 1000)),
                    callback=self._callback,
                )
                stream.start()
            except Exception as exc:  # noqa: BLE001 - 다음 후보로 넘어간다
                self._error = f"참조 스트림을 열지 못했습니다({sr}Hz): {exc}"
                continue
            self._stream = stream
            self.info = ReferenceInfo(index, str(entry.get("name", "")), sr)
            logger.info("참조 신호 캡처 시작: %s (%dHz → %dHz)", self.info.name, sr, self.target_samplerate)
            return True
        return False

    def _callback(self, indata, frames, time_info, status):  # noqa: ANN001, ARG002
        # 콜백에서는 복사·리샘플만 한다(블로킹 금지). 리샘플은 30ms 분량이라 가볍다.
        mono = indata.mean(axis=1) if indata.ndim > 1 else indata[:, 0]
        self._q.put(self._resample(mono.astype(np.float32, copy=True)))

    def _resample(self, mono: np.ndarray) -> np.ndarray:
        sr = self.info.samplerate if self.info is not None else self.target_samplerate
        if sr == self.target_samplerate or mono.size == 0:
            return mono
        count = max(1, int(round(mono.size * self.target_samplerate / sr)))
        src = np.linspace(0.0, mono.size - 1.0, num=mono.size, dtype=np.float64)
        dst = np.linspace(0.0, mono.size - 1.0, num=count, dtype=np.float64)
        return np.interp(dst, src, mono).astype(np.float32)

    def _drain(self) -> None:
        """콜백이 넣은 블록을 링 버퍼로 옮긴다(읽기 시점에 호출)."""
        while True:
            try:
                self._ring.write(self._q.get_nowait())
            except queue.Empty:
                return

    # --- 사용 ---
    @property
    def available(self) -> bool:
        return self._stream is not None

    @property
    def error(self) -> str | None:
        return self._error

    def read(self, count: int, delay: int = 0) -> np.ndarray:
        self._drain()
        return self._ring.read(count, delay)

    def __enter__(self) -> "ReferenceCapture":
        try:
            if not self._open():
                logger.warning("참조 신호 없이 동작합니다: %s", self._error)
        except Exception as exc:  # noqa: BLE001 - 참조 실패가 앱을 막지 않는다
            self._error = f"참조 캡처 초기화 실패: {exc}"
            logger.warning("참조 신호 없이 동작합니다: %s", self._error)
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
