"""다른 프로세스의 오디오 세션을 일시적으로 낮추고 복원한다."""

from __future__ import annotations

import logging
import os

from nuri.config import DuckingConfig

logger = logging.getLogger(__name__)


class Ducking:
    def __init__(self, cfg: DuckingConfig) -> None:
        self.cfg = cfg
        self._saved: list[tuple[object, float, float]] = []
        self._com = False

    def __enter__(self) -> Ducking:  # noqa: PYI034 - Python 3.10 지원
        if not self.cfg.enabled:
            return self
        try:
            import comtypes
            from pycaw.pycaw import AudioUtilities

            comtypes.CoInitialize()
            self._com = True
            for session in AudioUtilities.GetAllSessions():
                try:
                    if session.ProcessId in (0, os.getpid()):
                        continue
                    volume = session.SimpleAudioVolume
                    original = float(volume.GetMasterVolume())
                    target = original * min(1.0, max(0.0, self.cfg.level))
                    volume.SetMasterVolume(target, None)
                    self._saved.append((volume, original, target))
                except Exception as exc:  # noqa: BLE001 - 한 세션 오류는 나머지를 막지 않는다
                    logger.warning("오디오 세션 볼륨 조정 실패: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("덕킹 사용 불가: %s", exc)
        return self

    def __exit__(self, *exc: object) -> None:
        for volume, original, target in self._saved:
            try:
                # 사용자가 덕킹 도중 직접 바꾼 음량은 덮어쓰지 않는다.
                if abs(volume.GetMasterVolume() - target) < 0.001:
                    volume.SetMasterVolume(original, None)
            except Exception as error:  # noqa: BLE001
                logger.warning("오디오 세션 볼륨 복원 실패: %s", error)
        self._saved.clear()
        if self._com:
            import comtypes

            comtypes.CoUninitialize()
            self._com = False
