"""시스템 트레이 표시등.

누리가 실행 중임을 **'누' 글자 배지** 트레이 아이콘으로 보여주고, 상태
(IDLE/LISTEN/ACT...)에 따라 배지 색을 바꾼다. 사용자가 지시해서 실제로 작업하는
동안(THINK/ACT)에는 빈 고리를 돌려 "작동 중"임을 알린다.

아이콘에 마이크를 쓰지 않는 이유: 윈도우는 마이크를 쓰는 앱이 있으면 작업
표시줄에 파란 마이크 표시기를 띄운다(끄는 설정이 없다). 누리 아이콘까지
마이크 모양이면 둘이 헷갈리므로 '누리'의 첫 글자를 쓴다.

아이콘은 **상태 색으로 채운 둥근 배지 + 흰 글자**로 그린다. 트레이는 16px 로
줄여 그리기 때문에, 투명 배경에 얇은 선만 그리면 밝은 작업 표시줄과 어두운
트레이 창 어디에서도 거의 보이지 않았다. 채운 배지는 줄여도 뭉치가 남아
어느 배경에서도 눈에 띄고, 흰 글자는 상태 색 위에서 항상 읽힌다.

오른쪽 클릭 메뉴에서 동작 모드(대기/메모)를 고르고, `대화 기록 보기` 로 지난 대화를
날짜별로 볼 수 있다. 메뉴의 체크 표시는 매번 열 때 현재 모드를 읽어 맞추므로,
음성으로 모드를 바꿔도 어긋나지 않는다.

아이콘은 윈도우 기본 알림 영역에 **항상 표시**되도록 구동할 때
레지스트리(`NotifyIconSettings` 의 `IsPromoted`)를 올려 둔다(`promote`).
그대로 두면 윈도우가 새 아이콘을 '숨겨진 아이콘' 안으로 넣어 보이지 않는다.

pystray/Pillow 가 없거나 트레이 생성이 실패하면 조용히 비활성화되고 음성 기능은
그대로 동작한다(표시 기능이 본 기능을 막지 않는다).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from nuri.core.mode import Mode
from nuri.core.state_machine import State

logger = logging.getLogger("nuri.ui.tray")

# 윈도우는 트레이 아이콘을 '항상 표시'할지 '숨겨진 아이콘'에 넣을지 사용자별로
# 레지스트리에 기억한다. 값이 없으면 숨김 쪽으로 들어가므로, 구동할 때 우리
# 실행 파일 항목을 찾아 항상 표시(1)로 올린다.
_NOTIFY_ROOT = r"Control Panel\NotifyIconSettings"
_PROMOTED = "IsPromoted"
_EXE_PATH = "ExecutablePath"

# 상태별 배지 색(RGB). 흰 마이크를 얹으므로 상태를 알리는 색은 어둡게 잡는다
# (밝은 색 위의 흰 글리프는 16px 에서 뭉개져 잘 안 보인다).
STATE_COLORS: dict[State, tuple[int, int, int]] = {
    State.IDLE: (108, 118, 132),   # 슬레이트: 밝은/어두운 배경 모두에서 보인다
    State.ACK: (0, 140, 215),
    State.LISTEN: (0, 158, 74),
    State.THINK: (255, 170, 0),
    State.ACT: (255, 110, 0),
    State.SPEAK: (135, 70, 235),
}

STATE_LABELS: dict[State, str] = {
    State.IDLE: "대기 중",
    State.ACK: "호출 응답",
    State.LISTEN: "듣는 중",
    State.THINK: "생각 중",
    State.ACT: "작업 중",
    State.SPEAK: "말하는 중",
}

# 처리 중(고리를 돌리는 표시)으로 볼 상태
BUSY_STATES = frozenset({State.THINK, State.ACT})

ICON_SIZE = 64
FRAME_INTERVAL = 0.12      # 회전 프레임 간격(초)
SPINNER_STEP = 24          # 프레임당 회전 각도
SPINNER_SPAN = 150         # 꼬리까지 포함한 호 전체 각도
SPINNER_SEGMENTS = 24      # 꼬리를 이루는 조각 수(많을수록 부드럽다)
SPINNER_WIDTH = 8          # 고리 두께(16px 트레이에서도 보이도록 두껍게)

# 배지 위에 얹는 표식. 마이크 대신 '누리'의 첫 글자를 쓴다.
# 윈도우가 마이크를 쓰는 앱에 보여주는 파란 마이크 표시기와 헷갈리지 않게 하기 위함이다.
_GLYPH_TEXT = "누"
_GLYPH_COLOR = (255, 255, 255, 255)
_GLYPH_RATIO = 0.74        # 배지 지름 대비 글자 크기
_FONT_CANDIDATES = ("malgunbd.ttf", "malgun.ttf", "gulim.ttc", "arialbd.ttf")

# 배지: 아이콘 가장자리를 조금 남기고 채운 둥근 사각형.
# 16px 로 줄여도 색 뭉치가 남아 작업 표시줄에서 눈에 띈다.
_BADGE_BOX = (3, 3, ICON_SIZE - 3, ICON_SIZE - 3)
_BADGE_RADIUS = 16
_RING_BOX = (8, 8, ICON_SIZE - 8, ICON_SIZE - 8)


@lru_cache(maxsize=4)
def _glyph_font(size: int) -> Any:
    """배지 글자용 굵은 글꼴. 없으면 Pillow 기본 글꼴로 떨어진다."""
    from PIL import ImageFont

    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()



def promote(exe_path: str | None = None) -> bool:
    """알림 영역(작업 표시줄 트레이)에 아이콘을 **항상 표시**로 올린다.

    윈도우 11 은 아이콘 표시 여부를
    `HKCU\\Control Panel\\NotifyIconSettings\\<키>` 의 `IsPromoted` 에 저장한다.
    값이 없거나 0 이면 새 아이콘이 '숨겨진 아이콘'(∧) 안으로 들어가므로,
    우리 실행 파일(`ExecutablePath`) 항목을 찾아 1 로 올린다.

    - 항목은 아이콘이 한 번 등록된 뒤에 생긴다. 처음 실행이라 항목이 없으면
      올릴 대상이 없으므로 False 를 돌려준다(다음 구동 때 올라간다).
    - 표시 설정은 부가 기능이므로 어떤 실패도 예외로 올리지 않는다.
    """
    if os.name != "nt":
        return False
    try:
        import winreg
    except ImportError:  # pragma: no cover - 윈도우에서는 항상 있다
        return False

    target = os.path.normcase(exe_path or sys.executable)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _NOTIFY_ROOT) as root:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(root, index)
                except OSError:  # 더 이상 하위 키가 없다
                    return False
                index += 1
                try:
                    with winreg.OpenKey(
                        root, name, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE
                    ) as sub:
                        path, _ = winreg.QueryValueEx(sub, _EXE_PATH)
                        if os.path.normcase(str(path)) != target:
                            continue
                        winreg.SetValueEx(sub, _PROMOTED, 0, winreg.REG_DWORD, 1)
                        logger.info("트레이 아이콘을 항상 표시로 올림: %s", target)
                        return True
                except OSError:
                    continue  # 읽거나 쓸 수 없는 항목은 건너뛴다
    except OSError as exc:
        logger.debug("트레이 표시 설정을 읽지 못함: %s", exc)
        return False


class TrayIndicator:
    """트레이 아이콘을 별도 스레드에서 관리하는 표시등."""

    def __init__(
        self,
        title: str = "누리",
        on_quit: Callable[[], None] | None = None,
        on_mode: Callable[[Mode], None] | None = None,
        mode_getter: Callable[[], Mode] | None = None,
        on_history: Callable[[], None] | None = None,
        on_settings: Callable[[], None] | None = None,
    ) -> None:
        self.title = title
        self._on_quit = on_quit
        self._on_mode = on_mode
        self._mode_getter = mode_getter
        self._on_history = on_history
        self._on_settings = on_settings
        self._icon: Any = None
        self._thread: threading.Thread | None = None
        self._state = State.IDLE
        self._spin_stop = threading.Event()
        self._spin_thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._icon is not None

    def start(self, state: State = State.IDLE) -> bool:
        """트레이 아이콘을 띄운다. 실패하면 False 를 돌려주고 조용히 넘어간다."""
        try:
            import pystray
        except ImportError as exc:
            logger.warning("트레이 표시 비활성화(pystray 없음): %s", exc)
            return False

        # 아이콘을 등록하기 전에 '항상 표시'로 올려 둔다.
        # 윈도우는 아이콘을 등록할 때 이 설정을 읽는다.
        promote()

        try:
            self._icon = pystray.Icon(
                "nuri", self._render(state), self._tooltip(state), menu=self._menu(pystray)
            )
        except Exception as exc:  # noqa: BLE001 - 표시 실패는 치명적이지 않다
            logger.warning("트레이 아이콘 생성 실패: %s", exc)
            self._icon = None
            return False

        self._thread = threading.Thread(target=self._loop, name="nuri-tray", daemon=True)
        self._thread.start()
        return True

    def set_state(self, state: State) -> None:
        """상태가 바뀔 때 아이콘을 갱신한다(상태 머신 리스너).

        처리 중(THINK/ACT)에는 마이크를 채우고 테두리를 돌려 작동 중임을 보여주고,
        그 외에는 상태 색 마이크(테두리)를 보여준다.
        """
        self._state = state
        if self._icon is None:
            return
        if state in BUSY_STATES:
            self._start_spin()
            return
        self._stop_spin()
        self._apply_icon(self._render(state), state)

    def stop(self) -> None:
        self._stop_spin()
        icon, self._icon = self._icon, None
        if icon is None:
            return
        try:
            icon.stop()
        except Exception as exc:  # noqa: BLE001
            logger.debug("트레이 종료 실패: %s", exc)

    # --- 회전 표시(처리 중) ---
    def _start_spin(self) -> None:
        if self._spin_thread is not None and self._spin_thread.is_alive():
            return
        self._spin_stop.clear()
        self._spin_thread = threading.Thread(
            target=self._spin, name="nuri-tray-spin", daemon=True
        )
        self._spin_thread.start()

    def _stop_spin(self) -> None:
        self._spin_stop.set()
        thread, self._spin_thread = self._spin_thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=FRAME_INTERVAL * 4)

    def _spin(self) -> None:
        angle = 0
        while not self._spin_stop.is_set():
            self._apply_icon(self._render_spinner(angle), self._state)
            angle = (angle + SPINNER_STEP) % 360
            if self._spin_stop.wait(FRAME_INTERVAL):
                break

    def _apply_icon(self, image: Any, state: State) -> None:
        icon = self._icon
        if icon is None:
            return
        try:
            icon.icon = image
            icon.title = self._tooltip(state)
        except Exception as exc:  # noqa: BLE001
            logger.debug("트레이 상태 갱신 실패: %s", exc)

    # --- 내부 ---
    def _loop(self) -> None:
        icon = self._icon
        if icon is None:
            return
        try:
            icon.run()  # 자체 메시지 루프를 돌며 블로킹되므로 별도 스레드에서 실행
        except Exception as exc:  # noqa: BLE001
            logger.warning("트레이 루프 종료: %s", exc)

    def _tooltip(self, state: State) -> str:
        label = STATE_LABELS.get(state, state.value)
        return f"{self.title} - {label} ({self._current_mode().label})"

    # --- 모드 선택 ---
    def _current_mode(self) -> Mode:
        """지금 동작 모드(메뉴 체크 표시·툴팁용). 조회 실패 시 대기 모드."""
        if self._mode_getter is None:
            return Mode.STANDBY
        try:
            return self._mode_getter()
        except Exception as exc:  # noqa: BLE001 - 표시 기능이 본 기능을 막지 않는다
            logger.debug("모드 조회 실패: %s", exc)
            return Mode.STANDBY

    def _select_mode(self, mode: Mode) -> None:
        """메뉴에서 고른 모드를 앱에 넘긴다(실제 전환은 앱이 처리한다)."""
        if self._on_mode is None:
            return
        try:
            self._on_mode(mode)
        except Exception as exc:  # noqa: BLE001
            logger.warning("트레이에서 모드 변경 실패: %s", exc)
        self._refresh()

    def _refresh(self) -> None:
        """메뉴 체크 표시와 툴팁을 현재 모드로 맞춘다."""
        icon = self._icon
        if icon is None:
            return
        try:
            icon.title = self._tooltip(self._state)
            icon.update_menu()
        except Exception as exc:  # noqa: BLE001
            logger.debug("트레이 메뉴 갱신 실패: %s", exc)

    def _render(self, state: State) -> Any:
        """상태 색 배지 위에 '누' 글자를 얹은 아이콘을 만든다.

        배지를 채우는 이유: 투명 배경의 얇은 선은 16px 로 줄면 사라져
        작업 표시줄에서 보이지 않았다. 채운 색은 줄여도 남는다.
        마이크 대신 글자를 쓰는 이유: 윈도우가 마이크를 쓰는 앱에 보여주는
        마이크 표시기와 헷갈리지 않게 하기 위함이다.
        """
        from PIL import Image, ImageDraw

        color = STATE_COLORS.get(state, STATE_COLORS[State.IDLE])
        image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(_BADGE_BOX, radius=_BADGE_RADIUS, fill=(*color, 255))
        self._draw_glyph(draw)
        return image

    def _render_spinner(self, angle: int) -> Any:
        """작동 중 표시: 빙글빙글 도는 고리 모양.

        색만 바꾸면 16px 트레이에서 구분이 어려우므로 **모양 자체**를 바꾼다.
        뒤로 갈수록 옅어지는 꼬리를 붙여 정지 화면에서도 도는 느낌이 나게 한다.
        """
        from PIL import Image, ImageDraw

        color = STATE_COLORS.get(self._state, STATE_COLORS[State.THINK])
        image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse(_RING_BOX, outline=(128, 128, 128, 90), width=SPINNER_WIDTH)

        # 꼬리 끝(옅음)에서 머리(밝음)로 알파를 올려 가며 조각을 이어 붙인다.
        # 정지 화면에서도 어느 쪽으로 도는지 읽히게 하는 장치다.
        step_deg = SPINNER_SPAN / SPINNER_SEGMENTS
        for index in range(SPINNER_SEGMENTS):
            ratio = index / (SPINNER_SEGMENTS - 1)
            alpha = int(30 + 225 * ratio**2)
            start = angle - SPINNER_SPAN + index * step_deg
            draw.arc(
                _RING_BOX,
                start=start,
                end=start + step_deg + 1,  # 조각 사이 틈 방지
                fill=(*color, alpha),
                width=SPINNER_WIDTH,
            )
        return image

    @staticmethod
    def _draw_glyph(draw: Any) -> None:
        """배지 가운데에 흰 '누' 글자를 그린다(16px 에서도 형태가 남게 크게)."""
        size = int(ICON_SIZE * _GLYPH_RATIO)
        font = _glyph_font(size)
        left, top, right, bottom = draw.textbbox((0, 0), _GLYPH_TEXT, font=font)
        offset_x = (ICON_SIZE - (right - left)) / 2 - left
        offset_y = (ICON_SIZE - (bottom - top)) / 2 - top
        draw.text((offset_x, offset_y), _GLYPH_TEXT, font=font, fill=_GLYPH_COLOR)

    def _menu(self, pystray_module: Any) -> Any:
        """오른쪽 클릭 메뉴: 동작 모드(라디오) · 대화 기록 보기 · 종료.

        `checked` 는 메뉴를 열 때마다 호출되므로 음성으로 모드를 바꿔도
        체크 표시가 현재 모드를 그대로 보여준다.
        """
        items: list[Any] = []
        if self._on_mode is not None:
            items.extend(
                [
                    pystray_module.MenuItem(
                        Mode.STANDBY.label,
                        lambda: self._select_mode(Mode.STANDBY),
                        checked=lambda item: self._current_mode() is Mode.STANDBY,
                        radio=True,
                    ),
                    pystray_module.MenuItem(
                        Mode.MEMO.label,
                        lambda: self._select_mode(Mode.MEMO),
                        checked=lambda item: self._current_mode() is Mode.MEMO,
                        radio=True,
                    ),
                    pystray_module.Menu.SEPARATOR,
                ]
            )
        if self._on_history is not None:
            items.extend(
                [
                    pystray_module.MenuItem("대화 기록 보기", self._open_history),
                    pystray_module.Menu.SEPARATOR,
                ]
            )
        if self._on_settings is not None:
            items.extend([pystray_module.MenuItem("설정", self._open_settings), pystray_module.Menu.SEPARATOR])
        if self._on_quit is not None:
            items.append(pystray_module.MenuItem("누리 종료", self._quit))
        return pystray_module.Menu(*items)

    def _open_history(self) -> None:
        """대화 기록 창을 연다(창은 앱이 별도 스레드에서 띄운다)."""
        if self._on_history is None:
            return
        try:
            self._on_history()
        except Exception as exc:  # noqa: BLE001 - 표시 기능이 본 기능을 막지 않는다
            logger.warning("대화 기록 창 열기 실패: %s", exc)

    def _open_settings(self) -> None:
        if self._on_settings is not None:
            try:
                self._on_settings()
            except Exception:
                logger.exception("설정 창 열기 실패")

    def _quit(self) -> None:
        self.stop()
        if self._on_quit is not None:
            self._on_quit()
