"""주파수 영역 적응 필터(AEC)와 지연 정렬.

스피커로 나간 소리(참조)와 마이크로 되돌아온 소리는 **같은 내용 + 지연 + 감쇠**다.
그래서 참조에 적응 필터를 걸어 마이크 신호에서 빼면 에코가 줄어든다.

구현은 Shynk 방식의 파티션 블록 LMS(overlap-save)를 numpy 로 옮긴 것이다.
- 블록 N=256(16ms), 파티션 K=16 → 256ms 길이의 반향까지 모델링
- 스텝 크기 mu, 파티션별 전력 정규화, 이중 발화 시 적응 정지
- 출력은 "마이크 - 추정 에코" 이며, 길이는 입력과 같다

의존성 없는 순수 numpy 라 연산량이 적고(프레임당 수천 회 복소 곱), 합성 신호로
검증할 수 있다.
"""

from __future__ import annotations

from collections import deque

import numpy as np

EPS = 1e-8
# 에코 추정이 마이크 전력의 이 비율을 넘으면 "에코를 설명할 만큼 학습됐다"로 본다
ADAPT_RATIO = 0.5
# 마이크·참조가 모두 이보다 조용하면(RMS² 기준, 약 -70dBFS) 적응을 건너뛴다.
# 무음에서 정규화 전력이 0 에 가까워지면 스텝이 폭주하기 때문이다.
SILENCE_ENERGY = 1e-7


def estimate_delay(mic: np.ndarray, ref: np.ndarray, max_delay: int) -> tuple[int, float]:
    """마이크 신호 대비 참조 신호의 지연(샘플)과 상관 피크를 추정한다.

    두 신호는 같은 길이여야 한다. 반환하는 지연은 **참조가 마이크보다 얼마나
    먼저 나갔는지**이며, 참조 버퍼를 그만큼 뒤로 밀어 읽으면 마이크와 같은
    시점의 신호가 된다. 상관 피크가 낮으면 신뢰할 수 없다(재생 중이 아님).
    """
    if mic.size == 0 or ref.size != mic.size:
        return 0, 0.0
    a = mic.astype(np.float64, copy=False)
    b = ref.astype(np.float64, copy=False)
    a = a - a.mean()
    b = b - b.mean()
    # 미세한 장치 잡음도 정규화 상관값은 높을 수 있어 무음을 먼저 제외한다.
    if np.mean(a * a) < SILENCE_ENERGY or np.mean(b * b) < SILENCE_ENERGY:
        return 0, 0.0
    denom = np.sqrt(np.sum(a * a) * np.sum(b * b))
    if denom <= EPS:
        return 0, 0.0
    size = 1 << int(np.ceil(np.log2(a.size + max_delay + 1)))
    corr = np.fft.irfft(np.fft.rfft(b, size) * np.conj(np.fft.rfft(a, size)), size)
    # corr[k] = Σ b[n+k]·a[n] 이므로, 마이크가 D 만큼 늦으면 k=-D 에서 최대가 된다
    lags = np.arange(-max_delay, max_delay + 1)
    values = corr[lags % size]
    index = int(np.argmax(np.abs(values)))
    peak = float(np.abs(values[index]) / denom)
    return -int(lags[index]), peak


class EchoCanceller:
    """파티션 블록 LMS 기반 에코 제거기(16kHz, 고정 블록 길이)."""

    def __init__(
        self,
        block: int = 256,
        partitions: int = 16,
        mu: float = 0.3,
        power_decay: float = 0.9,
        double_talk_ratio: float = 4.0,
    ) -> None:
        self.block = int(block)
        self.partitions = int(partitions)
        self.mu = float(mu)
        self.power_decay = float(power_decay)
        self.double_talk_ratio = float(double_talk_ratio)

        bins = self.block + 1
        self._w = np.zeros((self.partitions, bins), dtype=np.complex128)
        self._power = np.zeros((self.partitions, bins), dtype=np.float64)
        self._history: deque[np.ndarray] = deque(maxlen=self.partitions)
        self._prev_ref = np.zeros(self.block, dtype=np.float32)
        self._zero = np.zeros(self.block, dtype=np.float64)
        # 에코를 설명할 만큼 학습됐는지(이중 발화 판정을 켜도 되는 시점)
        self._adapted = False
        self.last_echo = np.zeros(self.block, dtype=np.float32)
        self.double_talk = False

        # 진단용 누적값(ERLE 로그)
        self._sum_mic = 0.0
        self._sum_out = 0.0
        self._blocks = 0
        self._frozen = 0

    # --- 상태 ---
    def reset(self) -> None:
        self._w[:] = 0
        self._power[:] = 0
        self._history.clear()
        self._prev_ref[:] = 0
        self._adapted = False
        self.last_echo.fill(0)
        self.double_talk = False
        self._reset_stats()

    def _reset_stats(self) -> None:
        self._sum_mic = 0.0
        self._sum_out = 0.0
        self._blocks = 0
        self._frozen = 0

    def take_erle(self) -> tuple[float, int, int]:
        """누적 구간의 ERLE(dB), 처리 블록 수, 적응을 건너뛴 블록 수를 돌려주고 초기화한다."""
        if self._blocks == 0 or self._sum_out <= EPS:
            self._reset_stats()
            return 0.0, 0, 0
        ratio = (self._sum_mic + EPS) / (self._sum_out + EPS)
        erle = 10.0 * float(np.log10(max(ratio, EPS)))
        result = (erle, self._blocks, self._frozen)
        self._reset_stats()
        return result

    # --- 처리 ---
    def process(self, mic: np.ndarray, ref: np.ndarray) -> np.ndarray:
        """마이크 블록에서 추정 에코를 빼서 돌려준다. 길이는 입력과 같다."""
        block = self.block
        mic_block = self._fit(mic, block)
        ref_block = self._fit(ref, block)

        # overlap-save: 이전 블록 + 현재 블록을 FFT
        frame = np.concatenate((self._prev_ref, ref_block)).astype(np.float64)
        spectrum = np.fft.rfft(frame)
        self._prev_ref = ref_block.copy()

        if len(self._history) < self.partitions:
            self._history.appendleft(spectrum)
            # 필터가 아직 충분히 쌓이지 않았으면 원음을 그대로 내보낸다
            return mic_block.copy()
        self._history.appendleft(spectrum)

        echo_spectrum = np.zeros_like(spectrum)
        for index in range(self.partitions):
            echo_spectrum += self._w[index] * self._history[index]
        echo = np.fft.irfft(echo_spectrum, 2 * block)[block:]
        out = mic_block.astype(np.float64) - echo

        mic_energy = float(np.mean(mic_block.astype(np.float64) ** 2))
        ref_energy = float(np.mean(ref_block.astype(np.float64) ** 2))
        echo_energy = float(np.mean(echo ** 2))
        out_energy = float(np.mean(out ** 2))

        # 학습 여부 판정: 에코 추정이 마이크 전력의 절반을 넘게 설명하면
        # 필터가 어느 정도 수렴한 것으로 본다(이때부터 이중 발화 판정을 켠다).
        if not self._adapted and echo_energy > ADAPT_RATIO * mic_energy:
            self._adapted = True
        # 이중 발화 보호: 학습된 뒤 마이크가 예상 에코보다 훨씬 크면 사용자가
        # 동시에 말하는 중이므로 적응을 멈춘다. 학습 **전**에는 에코 추정이
        # 0 에 가까워 "마이크 ≫ 에코" 가 항상 참이 되므로, 그대로 두면 영원히
        # 한 블록도 배우지 못한다(실측: 정지 블록 = 전체 블록).
        double_talk = self._adapted and mic_energy > self.double_talk_ratio * echo_energy
        self.last_echo = echo.astype(np.float32)
        self.double_talk = double_talk
        # 참조가 사실상 무음이면(재생 중이 아님) 배울 것이 없고, 무음에서는
        # 정규화 전력이 0 에 가까워 스텝이 폭주한다. 두 경우 모두 적응을 건너뛴다.
        no_reference = ref_energy < SILENCE_ENERGY
        silent = mic_energy < SILENCE_ENERGY
        freeze = no_reference or silent or double_talk

        if not freeze:
            error_spectrum = np.fft.rfft(np.concatenate((self._zero, out)))
            for index in range(self.partitions):
                history = self._history[index]
                self._power[index] = (
                    self.power_decay * self._power[index]
                    + (1.0 - self.power_decay) * np.abs(history) ** 2
                )
            # 파티션 전체 전력으로 한 번에 정규화한다. 파티션별로 따로 나누면
            # 겹치는 구간에서 스텝이 과대해져 발산한다.
            total_power = np.sum(self._power, axis=0)
            step = self.mu / (total_power + EPS)
            for index in range(self.partitions):
                history = self._history[index]
                # 경사 제약(gradient constraint): 필터 탭은 2N 창의 **앞 절반**에만
                # 존재하므로 뒤 절반을 0 으로 잘라 순환 합성곱 성분을 없앤다.
                # (반대로 자르면 에너지가 비인과 구간으로 흘러 발산한다)
                gradient = np.fft.irfft(np.conj(history) * error_spectrum, 2 * block)
                gradient[block:] = 0.0
                self._w[index] += step * np.fft.rfft(gradient)
        else:
            self._frozen += 1

        if not np.all(np.isfinite(out)) or float(np.max(np.abs(out))) > 8.0:
            # 발산 방지: 필터를 초기화하고 이번 블록은 원음을 내보낸다
            self.reset()
            return mic_block.copy()

        # 안전장치: 에코 제거가 오히려 소리를 키우면(참조와 마이크가 실제로
        # 상관되지 않는 경우) 원음을 그대로 내보낸다. AEC 는 절대 손해를 보게
        # 해서는 안 된다.
        if out_energy > mic_energy * 1.05:
            out = mic_block.astype(np.float64)

        self._sum_mic += mic_energy
        self._sum_out += float(np.mean(out ** 2))
        self._blocks += 1
        return out.astype(np.float32)

    @staticmethod
    def _fit(samples: np.ndarray, size: int) -> np.ndarray:
        """블록 길이에 맞춘다(짧으면 0 채움, 길면 뒤에서 자름)."""
        body = np.asarray(samples, dtype=np.float32).reshape(-1)
        if body.size == size:
            return body
        if body.size > size:
            return body[-size:]
        out = np.zeros(size, dtype=np.float32)
        out[size - body.size:] = body
        return out
