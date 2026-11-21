"""실제 마이크 발화 1개를 저장하고 분석한다.

- 파형 통계(피크/RMS/DC/ZCR/지배주파수)
- WAV 저장
- faster-whisper 로 여러 설정(언어 고정/자동) 전사 비교

    python tools/diag_utterance.py --seconds 15
"""

from __future__ import annotations

import argparse
import sys
import time
import wave
from pathlib import Path

import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

from nuri.audio.capture import MicCapture
from nuri.audio.vad import UtteranceSegmenter
from nuri.config import AudioConfig, VadConfig


def analyze(audio: np.ndarray, sr: int) -> None:
    dur = audio.size / sr
    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    dc = float(np.mean(audio))
    zcr = float(np.mean(np.abs(np.diff(np.sign(audio))) > 0)) if audio.size > 1 else 0.0
    # 지배 주파수
    spec = np.abs(np.fft.rfft(audio * np.hanning(audio.size)))
    freqs = np.fft.rfftfreq(audio.size, 1.0 / sr)
    dom = float(freqs[int(np.argmax(spec))]) if spec.size else 0.0
    print(f"  길이={dur:.2f}s peak={peak:.3f} rms={rms:.4f} dc={dc:+.4f} "
          f"zcr={zcr:.3f} 지배주파수={dom:.0f}Hz")


def save_wav(path: Path, audio: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--device", default=None)
    parser.add_argument("--out", default="tools/out/utt.wav")
    parser.add_argument("--model", default="small")
    args = parser.parse_args()

    acfg = AudioConfig()
    if args.device is not None:
        try:
            acfg.device = int(args.device)
        except ValueError:
            acfg.device = args.device

    deadline = time.monotonic() + args.seconds
    captured: np.ndarray | None = None
    with MicCapture(acfg) as cap:
        seg = UtteranceSegmenter(VadConfig(), cap.samplerate, acfg.block_ms)
        print(f"'{args.seconds:.0f}초' 안에 한 문장 말해주세요. (sr={cap.samplerate})", flush=True)
        for frame in cap.frames():
            if time.monotonic() > deadline:
                break
            utt = seg.feed(frame)
            if utt is not None:
                captured = utt
                break

    if captured is None:
        print("발화를 잡지 못했습니다(무음/임계값 미달).")
        return 1

    sr = cap.samplerate
    print("[분석]")
    analyze(captured, sr)
    save_wav(Path(args.out), captured, sr)
    print(f"  저장: {args.out}")

    # 전사 비교
    from faster_whisper import WhisperModel

    print(f"[전사] model={args.model}")
    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    audio = captured.astype(np.float32)
    audio = audio - float(np.mean(audio))
    peak = float(np.max(np.abs(audio))) or 1.0
    audio = audio / peak * 0.9

    for label, kwargs in [
        ("ko 고정", {"language": "ko"}),
        ("언어 자동", {"language": None}),
    ]:
        segs, info = model.transcribe(
            audio, beam_size=5, vad_filter=False, without_timestamps=True,
            temperature=0.0, **kwargs,
        )
        text = " ".join(s.text.strip() for s in segs).strip()
        print(f"  [{label}] lang={info.language}({info.language_probability:.2f}) → {text!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
