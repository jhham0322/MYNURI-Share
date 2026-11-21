"""결정론적 E2E: 합성 음성 2개('누리야', 명령)를 앱 로직에 직접 투입해 검증.

마이크 타이밍/실시간성과 무관하게 STT + 상태머신 통합을 검증한다.

    python tools/e2e_voice.py --model small
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

from nuri.app import MSG_AWAIT_COMMAND, NuriApp
from nuri.config import Config
from nuri.tts.engine import EdgeTTS

WAKE = "누리야"
COMMAND = "오늘 날씨 알려줘"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="small")
    args = parser.parse_args()

    tts = EdgeTTS(Config().tts)
    print(f"[1] 합성: {WAKE!r}")
    wake_audio, sr1 = tts.synthesize(WAKE)
    print(f"[1] 합성: {COMMAND!r}")
    cmd_audio, sr2 = tts.synthesize(COMMAND)

    cfg = Config()
    cfg.use_stt = True
    cfg.use_tts = False  # 테스트 중 소리 재생 방지
    cfg.stt.model_size = args.model
    cfg.loop.max_turns = 1

    from nuri.stt.engine import _resample

    def run_scenario(
        name: str, clips: list[np.ndarray], srs: list[int], *, expect_prompt: bool
    ) -> bool:
        spoken: list[str] = []
        app = NuriApp(cfg, speak=spoken.append)
        prepared = [_resample(c, s, 16000) for c, s in zip(clips, srs)]
        print(f"[{name}] 앱 실행")
        t0 = time.monotonic()
        handled = app.run_utterances(prepared, 16000)
        print(f"    소요 {time.monotonic() - t0:.1f}s, 처리 턴 {handled}, 발화 {spoken}")
        expected_prompts = [MSG_AWAIT_COMMAND] if expect_prompt else []
        return handled == 1 and bool(spoken) and spoken[:-1] == expected_prompts and "날씨" in spoken[-1]

    # 시나리오 1: 호출어/명령을 따로
    ok1 = run_scenario("분리 발화", [wake_audio, cmd_audio], [sr1, sr2], expect_prompt=True)

    # 시나리오 2: 한 호흡("누리야, 오늘 날씨 알려줘")
    inline_audio, sr3 = tts.synthesize(f"{WAKE}, {COMMAND}")
    ok2 = run_scenario("인라인(한 호흡)", [inline_audio], [sr3], expect_prompt=False)

    print(f"[결과] 분리={'PASS' if ok1 else 'FAIL'} 인라인={'PASS' if ok2 else 'FAIL'}")
    return 0 if (ok1 and ok2) else 2


if __name__ == "__main__":
    raise SystemExit(main())
