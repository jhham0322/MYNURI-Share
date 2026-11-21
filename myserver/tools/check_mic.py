"""마이크 입력 테스트 도구.

PC의 기본 입력 장치에서 음성을 녹음하고,
- 실시간 레벨 미터
- 적응형 음성 감지(에너지 기반 VAD)
- WAV 저장
- 요약 리포트(PASS/FAIL)

를 수행한다. STT/TTS 같은 무거운 의존성 없이 sounddevice + numpy 만 사용한다.

사용 예:
    python tools/check_mic.py                 # 기본 5초 녹음
    python tools/check_mic.py --seconds 8     # 8초
    python tools/check_mic.py --list          # 장치 목록만
    python tools/check_mic.py --device 12     # 특정 장치
"""

from __future__ import annotations

import argparse
import queue
import sys
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

# 콘솔/파일 리다이렉트 환경에서도 한글이 깨지지 않도록 UTF-8 로 고정
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

# 음성 감지 기본 파라미터
DEFAULT_SECONDS = 5.0
ABS_MIN_RMS = 0.006  # 절대 최소 임계값(무음 대비)


def list_devices() -> None:
    print("=== 오디오 장치 목록 ===")
    print(sd.query_devices())
    try:
        print("\n기본 입력 장치:", sd.query_devices(kind="input"))
    except Exception as exc:  # noqa: BLE001
        print("기본 입력 장치 조회 실패:", exc)


def _rms(block: np.ndarray) -> float:
    if block.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))


def _open_stream(device, samplerate: int, callback) -> tuple[sd.InputStream, int]:
    """요청 샘플레이트로 열고, 실패하면 장치 기본값으로 재시도.

    콜백은 반드시 생성 시점에 전달한다(나중에 stream.callback 으로 지정하면 무시됨).
    blocksize 를 강제하지 않는다. MME 드라이버에서 작은 blocksize 를 지정하면
    콜백이 전혀 오지 않는 경우가 있어, PortAudio 가 정하도록 둔다.
    """
    attempts = [samplerate]
    try:
        info = sd.query_devices(device, "input")
        default_sr = int(info["default_samplerate"])
        if default_sr not in attempts:
            attempts.append(default_sr)
    except Exception:  # noqa: BLE001
        pass

    last_err: Exception | None = None
    for sr in attempts:
        try:
            stream = sd.InputStream(
                device=device,
                channels=1,
                samplerate=sr,
                dtype="float32",
                callback=callback,
            )
            return stream, sr
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    raise RuntimeError(f"입력 스트림을 열 수 없습니다: {last_err}")


def record(device, seconds: float, samplerate: int, out_path: Path) -> dict:
    q: "queue.Queue[np.ndarray]" = queue.Queue()

    def callback(indata, frames, time_info, status):  # noqa: ANN001, ARG001
        if status:
            print(f"[stream status] {status}", file=sys.stderr)
        # 다채널이면 mono로 평균
        mono = indata.mean(axis=1) if indata.ndim > 1 else indata[:, 0]
        q.put(mono.copy())

    stream, actual_sr = _open_stream(device, samplerate, callback)

    chunks: list[np.ndarray] = []
    rms_history: list[float] = []
    start = time.monotonic()
    first_block_at: float | None = None
    no_data_timeout = 2.0  # 이 시간 안에 블록이 하나도 안 오면 중단(무한 대기 방지)

    print(f"녹음 시작: {seconds:.1f}초, 샘플레이트={actual_sr}Hz")
    print("지금부터 마이크에 대고 말해보세요...\n", flush=True)

    is_tty = sys.stdout.isatty()
    last_print = 0.0
    with stream:
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= seconds:
                break
            # 워치독: 첫 블록이 너무 늦으면 스트림 문제로 판단하고 중단
            if first_block_at is None and elapsed >= no_data_timeout:
                print(f"\n[워치독] {no_data_timeout:.0f}초 동안 오디오 블록이 오지 않아 중단합니다.",
                      file=sys.stderr, flush=True)
                break
            try:
                block = q.get(timeout=0.5)
            except queue.Empty:
                continue
            if first_block_at is None:
                first_block_at = elapsed
            chunks.append(block)
            level = _rms(block)
            rms_history.append(level)

            if is_tty:
                # 실시간 레벨 미터 (0~1 스케일을 0~40칸으로)
                bar_len = int(min(level * 20.0, 1.0) * 40)
                bar = "#" * bar_len
                remain = max(0.0, seconds - elapsed)
                sys.stdout.write(f"\r[{bar:<40}] rms={level:.4f}  남은 {remain:4.1f}s")
                sys.stdout.flush()
            elif elapsed - last_print >= 0.5:
                # 파이프/리다이렉트 환경: 0.5초마다 한 줄씩 출력
                last_print = elapsed
                print(f"  t={elapsed:4.1f}s  rms={level:.4f}", flush=True)

    if is_tty:
        sys.stdout.write("\r" + " " * 70 + "\r")

    if not chunks:
        return {"ok": False, "reason": "오디오 블록을 받지 못했습니다."}

    audio = np.concatenate(chunks).astype(np.float32)
    peak = float(np.max(np.abs(audio)))
    overall_rms = _rms(audio)

    # 노이즈 플로어 추정: 하위 20% 블록의 평균
    rms_arr = np.array(rms_history, dtype=np.float64)
    noise_floor = float(np.percentile(rms_arr, 20)) if rms_arr.size else 0.0
    threshold = max(ABS_MIN_RMS, noise_floor * 3.0)

    voiced = rms_arr > threshold
    voiced_ratio = float(np.mean(voiced)) if rms_arr.size else 0.0
    voiced_blocks = int(np.sum(voiced))

    # WAV 저장 (int16)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pcm16 = np.clip(audio, -1.0, 1.0)
    pcm16 = (pcm16 * 32767).astype(np.int16)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(actual_sr)
        wf.writeframes(pcm16.tobytes())

    voice_detected = voiced_ratio >= 0.05 and peak > 0.02

    return {
        "ok": voice_detected,
        "samplerate": actual_sr,
        "duration": len(audio) / actual_sr,
        "peak": peak,
        "rms": overall_rms,
        "noise_floor": noise_floor,
        "threshold": threshold,
        "voiced_blocks": voiced_blocks,
        "total_blocks": len(rms_history),
        "voiced_ratio": voiced_ratio,
        "wav": str(out_path),
    }


def print_report(result: dict) -> None:
    print("=== 결과 ===")
    if "reason" in result:
        print("FAIL:", result["reason"])
        return
    print(f"샘플레이트   : {result['samplerate']} Hz")
    print(f"녹음 길이    : {result['duration']:.2f} s")
    print(f"피크 레벨    : {result['peak']:.4f}")
    print(f"평균 RMS     : {result['rms']:.4f}")
    print(f"노이즈 플로어: {result['noise_floor']:.4f}")
    print(f"음성 임계값  : {result['threshold']:.4f}")
    print(f"음성 블록    : {result['voiced_blocks']}/{result['total_blocks']} "
          f"({result['voiced_ratio'] * 100:.1f}%)")
    print(f"저장 파일    : {result['wav']}")
    print()
    if result["ok"]:
        print("PASS: 음성 입력이 감지되었습니다.")
    else:
        print("FAIL: 음성이 감지되지 않았습니다(무음이거나 임계값 미달).")


def main() -> int:
    parser = argparse.ArgumentParser(description="마이크 입력 테스트")
    parser.add_argument("--list", action="store_true", help="장치 목록만 출력")
    parser.add_argument("--device", default=None, help="입력 장치 인덱스 또는 이름")
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS, help="녹음 길이(초)")
    parser.add_argument("--samplerate", type=int, default=16000, help="목표 샘플레이트")
    parser.add_argument("--out", default="tools/out/mic_test.wav", help="WAV 저장 경로")
    args = parser.parse_args()

    if args.list:
        list_devices()
        return 0

    device = args.device
    if device is not None:
        try:
            device = int(device)
        except ValueError:
            pass

    try:
        result = record(device, args.seconds, args.samplerate, Path(args.out))
    except Exception as exc:  # noqa: BLE001
        print("오류:", exc)
        return 1

    print_report(result)
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
