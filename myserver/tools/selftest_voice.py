"""STT/TTS 왕복 셀프테스트 (사용자 발화 없이).

1) edge-tts 로 한국어 문장을 합성해 WAV 로 저장
2) faster-whisper 로 그 WAV 를 다시 텍스트로 전사
3) 호출어('누리') 포함 여부 확인

사용 예:
    python tools/selftest_voice.py --model base
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

from nuri.config import STTConfig, TTSConfig
from nuri.stt.engine import FasterWhisperSTT
from nuri.tts.engine import EdgeTTS

TEXT = "누리야, 오늘 날씨 알려줘."
WAKE = "누리"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="base", help="faster-whisper 모델")
    parser.add_argument("--out", default="tools/out/tts_test.wav")
    args = parser.parse_args()

    print(f"[1] TTS 합성: {TEXT!r}")
    t0 = time.monotonic()
    tts = EdgeTTS(TTSConfig())
    try:
        data, sr = tts.synthesize(TEXT)
    except Exception as exc:  # noqa: BLE001
        print("TTS FAIL:", exc)
        return 1
    dur = len(data) / sr
    print(f"    OK: {dur:.2f}s @ {sr}Hz, peak={abs(data).max():.3f}, "
          f"소요 {time.monotonic() - t0:.1f}s")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    import soundfile as sf

    sf.write(str(out), data, sr)
    print(f"    저장: {out}")

    print(f"[2] STT 전사 (model={args.model})")
    t0 = time.monotonic()
    stt = FasterWhisperSTT(STTConfig(model_size=args.model))
    try:
        transcript = stt.transcribe(data, sr)
    except Exception as exc:  # noqa: BLE001
        print("STT FAIL:", exc)
        return 1
    print(f"    전사: {transcript!r}")
    print(f"    소요 {time.monotonic() - t0:.1f}s")

    ok = WAKE in transcript
    print(f"[3] 호출어 '{WAKE}' 포함: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
