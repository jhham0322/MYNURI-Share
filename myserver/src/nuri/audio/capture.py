"""마이크 캡처: 블로킹 없이 프레임을 큐로 전달하고, 워치독으로 무한 대기를 막는다."""

from __future__ import annotations

import queue
import sys
import time
from collections.abc import Iterator

import numpy as np
import sounddevice as sd

from nuri.config import AudioConfig


class MicCapture:
    def __init__(self, cfg: AudioConfig, no_data_timeout: float = 2.0) -> None:
        self.cfg = cfg
        self.no_data_timeout = no_data_timeout
        self._q: "queue.Queue[np.ndarray]" = queue.Queue()
        self._stream: sd.InputStream | None = None
        self.samplerate = cfg.samplerate
        self._block = int(cfg.samplerate * cfg.block_ms / 1000)

    def _callback(self, indata, frames, time_info, status):  # noqa: ANN001, ARG002
        if status:
            print(f"[stream status] {status}", file=sys.stderr)
        mono = indata.mean(axis=1) if indata.ndim > 1 else indata[:, 0]
        self._q.put(mono.copy())

    def __enter__(self) -> "MicCapture":
        attempts = [self.cfg.samplerate]
        try:
            info = sd.query_devices(self.cfg.device, "input")
            default_sr = int(info["default_samplerate"])
            if default_sr not in attempts:
                attempts.append(default_sr)
        except Exception:  # noqa: BLE001
            pass

        last_err: Exception | None = None
        for sr in attempts:
            try:
                self._stream = sd.InputStream(
                    device=self.cfg.device,
                    channels=self.cfg.channels,
                    samplerate=sr,
                    blocksize=max(1, int(sr * self.cfg.block_ms / 1000)),
                    dtype="float32",
                    callback=self._callback,
                )
                self._stream.start()
                self.samplerate = sr
                self._block = int(sr * self.cfg.block_ms / 1000)
                self._flush_startup()
                return self
            except Exception as exc:  # noqa: BLE001
                last_err = exc
        raise RuntimeError(f"입력 스트림을 열 수 없습니다: {last_err}")

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        if self._stream is not None:
            try:
                self._stream.stop()
            finally:
                self._stream.close()
                self._stream = None

    def _flush_startup(self, warmup_s: float = 0.3) -> None:
        """스트림 시작 직후의 팝/과도현상 버퍼를 버린다."""
        deadline = time.monotonic() + warmup_s
        while time.monotonic() < deadline:
            try:
                self._q.get(timeout=0.05)
            except queue.Empty:
                pass
        # 남은 버퍼 비우기
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    def flush(self) -> None:
        """큐에 쌓인 프레임을 모두 버린다.

        TTS 재생 중에는 마이크 큐에 누리 자신의 목소리가 쌓인다. 재생이 끝난 뒤
        그 프레임을 그대로 읽으면 사용자 발화 대신 에코를 명령으로 오인하므로,
        후속 지시를 듣기 전에 비운다.
        """
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                return

    def frames(self) -> Iterator[np.ndarray]:
        """프레임을 무한히 생성. no_data_timeout 동안 프레임이 없으면 TimeoutError."""
        last_data = time.monotonic()
        while True:
            try:
                block = self._q.get(timeout=0.25)
            except queue.Empty:
                if time.monotonic() - last_data > self.no_data_timeout:
                    raise TimeoutError(
                        f"{self.no_data_timeout:.0f}초 동안 오디오 프레임이 없습니다."
                    )
                continue
            last_data = time.monotonic()
            yield block
