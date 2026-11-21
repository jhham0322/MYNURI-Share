"""대화 기록 보기 창.

트레이 아이콘 오른쪽 클릭 메뉴의 `대화 기록 보기` 로 연다.
`rag/conversations.jsonl` 을 **읽기만** 해서 날짜별로 묶어 보여주고,
찾기 입력으로 내용·날짜·역할을 걸러 준다.

탭은 두 개다.
- `대화`: 주고받은 말 전체를 날짜별 목록으로 보여준다.
- `메모`: 메모 모드로 받아쓴 줄만 모아 **날짜별 카드**로 보여준다. 카드를 누르면
  그 메모 전체가 새 창으로 열린다.

Tkinter 는 창을 만든 스레드에서만 다뤄야 하므로, 트레이 콜백은 이 창을
별도 스레드에서 띄운다(`ConversationViewer.show` 가 그 스레드에서 mainloop 를 돈다).

화면 구성

    찾기 [________________]  [새로고침]
    최근 3일 · 대화 128줄 · 메모 14줄
    [대화 128][메모 4]
    11월 15일 (일) · 2개
      14:20 ~ 14:41 · 5줄
      오늘 회의는 세시로 옮기기로 함 / …
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from nuri.rag.conversation import (
    ROLE_MEMO,
    ROLE_NURI,
    ROLE_USER,
    Turn,
    read_daily_summaries,
    read_turns,
)

logger = logging.getLogger("nuri.ui.history")

ROLE_LABELS = {ROLE_USER: "나", ROLE_NURI: "누리", ROLE_MEMO: "메모"}
ROLE_TAGS = {ROLE_USER: "user", ROLE_NURI: "nuri", ROLE_MEMO: "memo"}
WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")

FONT = "Malgun Gothic"
REFRESH_MS = 5000  # 창을 열어 둔 동안 새 대화가 쌓이면 자동으로 다시 읽는다
MEMO_GAP_MINUTES = 10  # 메모를 이만큼 쉬었다가 다시 하면 다른 메모(카드)로 본다


@dataclass(frozen=True)
class DayGroup:
    """같은 날짜의 대화 묶음."""

    day: str
    turns: tuple[Turn, ...]


def time_of(at: str) -> str:
    """`2026-11-15T14:57:03` → `14:57`. 형식이 다르면 앞부분만 쓴다."""
    return at[11:16] if len(at) >= 16 else at


def day_of(at: str) -> str:
    """`2026-11-15T14:57:03` → `2026-11-15`."""
    return at[:10] if len(at) >= 10 else at


def day_label(day: str) -> str:
    """`2026-11-15` → `11월 15일 (토)`. 날짜가 아니면 원문 그대로."""
    try:
        moment = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return day
    return f"{moment.month}월 {moment.day}일 ({WEEKDAYS[moment.weekday()]})"


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def provider_label(turn: Turn) -> str:
    """처리 주체 표시.

    에이전트가 처리한 응답은 `Codex`/`CodeBuddy`(전환 시 `Codex → CodeBuddy`),
    누리가 로컬 규칙으로 바로 답한 응답은 `로컬` 로 보여준다. 로컬 응답에 아무
    표시도 하지 않으면 "처리기가 안 보인다" 로 읽히므로 빈칸 대신 `로컬` 을 쓴다.
    """
    names = {"codex": "Codex", "codebuddy-code": "CodeBuddy"}
    if not turn.provider:
        return "로컬" if turn.role == ROLE_NURI else ""
    current = names.get(turn.provider, turn.provider)
    if turn.fallback_from:
        return f"{names.get(turn.fallback_from, turn.fallback_from)} → {current}"
    return current


def group_by_day(turns: Sequence[Turn]) -> list[DayGroup]:
    """날짜별로 묶어 **최근 날짜부터** 돌려준다(묶음 안은 시간순)."""
    buckets: dict[str, list[Turn]] = {}
    for turn in turns:
        buckets.setdefault(day_of(turn.at), []).append(turn)
    return [
        DayGroup(day=day, turns=tuple(buckets[day])) for day in sorted(buckets, reverse=True)
    ]


def filter_turns(turns: Sequence[Turn], query: str) -> list[Turn]:
    """내용·날짜·역할 이름에 찾는 말이 들어간 줄만 남긴다(대소문자 무시).

    날짜는 `2026-11-15`, `11월 15일` 둘 다로 찾을 수 있다.
    """
    needle = query.strip().lower()
    if not needle:
        return list(turns)
    found: list[Turn] = []
    for turn in turns:
        day = day_of(turn.at)
        haystack = f"{turn.at} {turn.text} {role_label(turn.role)} {day_label(day)} {provider_label(turn)}"
        if needle in haystack.lower():
            found.append(turn)
    return found


@dataclass(frozen=True)
class MemoBlock:
    """메모 모드로 받아쓴 한 덩어리(카드 하나)."""

    day: str
    turns: tuple[Turn, ...]

    @property
    def start(self) -> str:
        return time_of(self.turns[0].at)

    @property
    def end(self) -> str:
        return time_of(self.turns[-1].at)

    @property
    def span(self) -> str:
        """`14:20 ~ 14:41` 처럼 보여준다(한 줄이면 시각 하나)."""
        return self.start if self.start == self.end else f"{self.start} ~ {self.end}"


def _moment(at: str) -> datetime | None:
    try:
        return datetime.fromisoformat(at)
    except ValueError:
        return None


def memo_blocks(turns: Sequence[Turn], gap_minutes: int = MEMO_GAP_MINUTES) -> list[MemoBlock]:
    """메모 모드로 받아쓴 줄만 모아 **날짜·시간 간격**으로 카드 단위로 묶는다.

    - `role=memo` 줄만 대상으로 한다.
    - 앞 줄과 `gap_minutes` 이상 벌어지거나 날짜가 바뀌면 새 카드로 나눈다.
      메모 모드를 잠깐 껐다 켠 경우가 자연스럽게 다른 카드가 된다.
    - 최근 카드가 위로 오도록 뒤집어 돌려준다.
    """
    blocks: list[list[Turn]] = []
    previous: datetime | None = None
    previous_day: str | None = None
    for turn in turns:
        if turn.role != ROLE_MEMO:
            continue
        moment = _moment(turn.at)
        day = day_of(turn.at)
        same_block = (
            bool(blocks)
            and moment is not None
            and previous is not None
            and day == previous_day
            and moment - previous <= timedelta(minutes=gap_minutes)
        )
        if same_block:
            blocks[-1].append(turn)
        else:
            blocks.append([turn])
        previous = moment
        previous_day = day
    blocks.reverse()
    return [MemoBlock(day=day_of(block[0].at), turns=tuple(block)) for block in blocks]


def memo_preview(block: MemoBlock, limit: int = 2, max_chars: int = 110) -> str:
    """카드에 보여줄 미리보기(앞 두 줄을 한 줄로)."""
    text = " / ".join(turn.text for turn in block.turns[:limit])
    if len(block.turns) > limit or len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


class ConversationViewer:
    """대화 기록을 날짜별로 보여주는 Tk 창."""

    def __init__(
        self,
        path: Path,
        keep_days: int = 3,
        title: str = "누리 대화 기록",
        memo_gap_minutes: int = MEMO_GAP_MINUTES,
        schedules: Any = None,
    ) -> None:
        self.path = Path(path)
        self.keep_days = keep_days
        self.title = title
        self.memo_gap_minutes = memo_gap_minutes
        self.schedules = schedules
        self._schedule_tree: Any = None
        self._turns: list[Turn] = []
        self._mtime: float | None = None
        self._root: Any = None
        self._text: Any = None
        self._status: Any = None
        self._search: Any = None
        self._notebook: Any = None
        self._memo_inner: Any = None
        self._memo_canvas: Any = None
        self._summary_text: Any = None
        self._summaries: dict[str, dict] = {}
        self._summary_error = False
        self._summary_mtime: float | None = None
        self._pending_summary: dict = {}
        self._pending_mtime: float | None = None

    # --- 창 ---
    def show(self) -> None:
        """창을 띄우고 닫힐 때까지 돈다(부른 스레드에서 실행해야 한다)."""
        try:
            import tkinter as tk
            from tkinter import ttk
        except ImportError as exc:  # pragma: no cover - Tk 가 없는 환경
            logger.warning("대화 기록 창을 열 수 없습니다(Tk 없음): %s", exc)
            return

        root = tk.Tk()
        self._root = root
        root.title(self.title)
        root.geometry("820x680")
        root.minsize(560, 420)
        root.configure(background="#ffffff")

        self._build_header(tk, root)
        self._build_body(tk, ttk, root)

        self._load_turns()
        self._render("")
        self._render_schedules()
        root.after(REFRESH_MS, self._auto_refresh)
        self._raise_window(root)
        logger.info("대화 기록 창 열림: %s줄", len(self._turns))
        root.mainloop()
        logger.info("대화 기록 창 닫힘")
        self._root = None

    @staticmethod
    def _raise_window(root: Any) -> None:
        """다른 창 뒤에 열리지 않도록 앞으로 올린다.

        트레이 메뉴(다른 프로세스의 콜백 스레드)에서 만들면 윈도우가
        포그라운드 권한을 받지 못해 뒤에 깔릴 수 있다. 잠깐 topmost 로
        올렸다가 풀어 앞으로 가져온다.
        """
        try:
            root.lift()
            root.attributes("-topmost", True)
            root.after(250, lambda: root.attributes("-topmost", False))
            root.focus_force()
        except Exception as exc:  # noqa: BLE001 - 창을 못 올려도 기능은 동작한다
            logger.debug("대화 기록 창 앞으로 올리기 실패: %s", exc)

    def _build_header(self, tk: Any, root: Any) -> None:
        header = tk.Frame(root, background="#ffffff", padx=14, pady=12)
        header.pack(fill="x")

        tk.Label(
            header, text="찾기", background="#ffffff", foreground="#374151", font=(FONT, 10)
        ).pack(side="left")

        self._search = tk.StringVar()
        entry = tk.Entry(
            header,
            textvariable=self._search,
            font=(FONT, 11),
            relief="flat",
            highlightthickness=1,
            highlightbackground="#d1d5db",
            highlightcolor="#2563eb",
            background="#ffffff",
        )
        entry.pack(side="left", fill="x", expand=True, padx=(8, 8), ipady=5)
        entry.bind("<Escape>", lambda _event: self._search.set(""))
        root.bind("<Control-f>", lambda _event: entry.focus_set())
        self._search.trace_add("write", lambda *_args: self._on_query())

        tk.Button(
            header,
            text="새로고침",
            command=self._reload,
            relief="flat",
            background="#eef2ff",
            activebackground="#e0e7ff",
            foreground="#1f2937",
            font=(FONT, 10),
            padx=12,
            pady=3,
        ).pack(side="left")

        self._status = tk.Label(
            root,
            text="",
            anchor="w",
            background="#ffffff",
            foreground="#6b7280",
            font=(FONT, 9),
            padx=14,
        )
        self._status.pack(fill="x")

    def _build_body(self, tk: Any, ttk: Any, root: Any) -> None:
        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=14, pady=(6, 12))
        self._notebook = notebook

        talk = tk.Frame(notebook, background="#ffffff")
        notebook.add(talk, text="대화")
        self._build_talk(tk, talk)

        memo = tk.Frame(notebook, background="#ffffff")
        notebook.add(memo, text="메모")
        self._build_memo(tk, memo)

        summary = tk.Frame(notebook, background="#ffffff")
        notebook.add(summary, text="지난 대화 요약")
        scrollbar = tk.Scrollbar(summary)
        scrollbar.pack(side="right", fill="y")
        self._summary_text = tk.Text(summary, wrap="word", font=(FONT, 10),
                                     yscrollcommand=scrollbar.set, padx=12, pady=10)
        self._summary_text.pack(fill="both", expand=True)
        scrollbar.config(command=self._summary_text.yview)
        self._summary_text.configure(state="disabled")

        schedule = tk.Frame(notebook, background="#ffffff")
        notebook.add(schedule, text="예약 작업")
        self._build_schedules(tk, ttk, schedule)

    def _build_schedules(self, tk: Any, ttk: Any, parent: Any) -> None:
        tk.Label(parent, text="예약 시각과 작업을 확인하고 상태를 관리하세요.",
                 background="#ffffff", foreground="#6b7280", font=(FONT, 10)).pack(anchor="w", pady=8)
        buttons = tk.Frame(parent, background="#ffffff")
        buttons.pack(side="bottom", fill="x", pady=8)
        for label, action in (("수정", "edit"), ("활성화", "enable"), ("비활성화", "disable"), ("삭제", "delete")):
            tk.Button(buttons, text=label, command=lambda a=action: self._schedule_action(a),
                      font=(FONT, 10), padx=12).pack(side="left", padx=(0, 6))
        tree = ttk.Treeview(parent, columns=("status", "when", "task"), show="headings", selectmode="browse")
        for name, title, width in (("status", "상태", 80), ("when", "예약 시각", 190), ("task", "작업", 360)):
            tree.heading(name, text=title)
            tree.column(name, width=width, minwidth=65, stretch=name == "task")
        tree.pack(fill="both", expand=True)
        tree.bind("<Double-1>", lambda _event: self._schedule_action("edit"))
        self._schedule_tree = tree

    def _render_schedules(self) -> None:
        tree = self._schedule_tree
        if tree is None:
            return
        selected = tree.selection()
        tree.delete(*tree.get_children())
        if self.schedules is None:
            return
        for item in self.schedules.ordered(datetime.now()):
            when = f"매일 {item.time}" if item.kind == "daily" else (item.at or "").replace("T", " ")
            tree.insert("", "end", iid=item.id, values=("활성" if item.enabled else "비활성", when, item.label or item.command or "알람"))
        if selected and tree.exists(selected[0]):
            tree.selection_set(selected[0])

    def _schedule_action(self, action: str) -> None:
        from tkinter import messagebox

        if self.schedules is None or self._schedule_tree is None:
            return
        selected = self._schedule_tree.selection()
        if not selected:
            messagebox.showinfo("예약 작업", "예약을 먼저 선택해 주세요.", parent=self._root)
            return
        item = next((entry for entry in self.schedules.items() if entry.id == selected[0]), None)
        if item is None:
            self._render_schedules()
            return
        if action == "edit":
            self._edit_schedule(item)
            return
        if action == "delete" and not messagebox.askyesno("예약 삭제", "선택한 예약을 삭제할까요?", parent=self._root):
            return
        try:
            if action == "delete":
                self.schedules.remove(item)
            else:
                self.schedules.set_enabled(item.id, action == "enable")
        except OSError:
            messagebox.showerror("예약 작업", "변경 사항을 저장하지 못했어요.", parent=self._root)
        self._render_schedules()

    def _edit_schedule(self, item: Any) -> None:
        """예약 편집 중에도 감시 스레드는 계속 실행되므로 저장 시 ID를 다시 확인한다."""
        import tkinter as tk
        from tkinter import messagebox, ttk

        if self._root is None or self.schedules is None:
            return
        window = tk.Toplevel(self._root)
        window.title("예약 작업 수정")
        window.transient(self._root)
        window.resizable(False, False)
        window.grab_set()
        body = tk.Frame(window, background="#ffffff", padx=18, pady=16)
        body.pack(fill="both", expand=True)
        kind = tk.StringVar(value=item.kind)
        once = tk.StringVar(value=(item.at or "").replace("T", " "))
        daily = tk.StringVar(value=item.time or "08:00")
        command = tk.StringVar(value=item.command)

        tk.Label(body, text="예약 종류", background="#ffffff", font=(FONT, 10, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        kinds = tk.Frame(body, background="#ffffff")
        kinds.grid(row=0, column=1, sticky="w", pady=(0, 8))
        tk.Radiobutton(kinds, text="한 번", variable=kind, value="once", background="#ffffff").pack(side="left")
        tk.Radiobutton(kinds, text="매일", variable=kind, value="daily", background="#ffffff").pack(side="left")
        tk.Label(body, text="날짜와 시각", background="#ffffff").grid(row=1, column=0, sticky="w", pady=5)
        once_entry = ttk.Entry(body, textvariable=once, width=32)
        once_entry.grid(row=1, column=1, sticky="ew", pady=5)
        tk.Label(body, text="YYYY-MM-DD HH:MM[:SS]", background="#ffffff", foreground="#6b7280").grid(row=2, column=1, sticky="w")
        tk.Label(body, text="매일 시각", background="#ffffff").grid(row=3, column=0, sticky="w", pady=5)
        daily_entry = ttk.Entry(body, textvariable=daily, width=32)
        daily_entry.grid(row=3, column=1, sticky="ew", pady=5)
        tk.Label(body, text="24시간 형식 HH:MM", background="#ffffff", foreground="#6b7280").grid(row=4, column=1, sticky="w")
        tk.Label(body, text="작업 내용", background="#ffffff").grid(row=5, column=0, sticky="w", pady=(12, 5))
        ttk.Entry(body, textvariable=command, width=40).grid(row=5, column=1, sticky="ew", pady=(12, 5))
        tk.Label(body, text="비워 두면 단순 알람으로 알려드려요.", background="#ffffff", foreground="#6b7280").grid(row=6, column=1, sticky="w")

        def update_fields(*_args: object) -> None:
            once_entry.configure(state="normal" if kind.get() == "once" else "disabled")
            daily_entry.configure(state="normal" if kind.get() == "daily" else "disabled")

        def save() -> None:
            try:
                updated = self.schedules.update(item.id, kind.get(), once.get() if kind.get() == "once" else daily.get(), command.get())
            except (ValueError, OSError) as exc:
                messagebox.showerror("예약 수정", str(exc) if isinstance(exc, ValueError) else "변경 사항을 저장하지 못했어요.", parent=window)
                return
            if not updated:
                messagebox.showinfo("예약 수정", "예약이 이미 실행되었거나 삭제되었어요.", parent=window)
            self._render_schedules()
            window.destroy()

        kind.trace_add("write", update_fields)
        update_fields()
        buttons = tk.Frame(body, background="#ffffff")
        buttons.grid(row=7, column=1, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="저장", command=save).pack(side="left", padx=4)
        ttk.Button(buttons, text="취소", command=window.destroy).pack(side="left")
        window.bind("<Escape>", lambda _event: window.destroy())

    def _build_talk(self, tk: Any, parent: Any) -> None:
        scrollbar = tk.Scrollbar(parent)
        scrollbar.pack(side="right", fill="y")

        text = tk.Text(
            parent,
            wrap="word",
            relief="flat",
            borderwidth=0,
            background="#ffffff",
            foreground="#111827",
            font=(FONT, 10),
            padx=6,
            pady=4,
            tabs=("52", "104"),
            yscrollcommand=scrollbar.set,
            cursor="arrow",
        )
        text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=text.yview)

        # 줄바꿈된 줄이 본문 시작 위치에 맞도록 두 번째 줄부터 들여쓴다.
        # (태그는 만든 순서대로 우선순위가 높아지므로 배경/색 태그를 나중에 만든다)
        text.tag_configure("say", lmargin2=104, spacing3=2)
        text.tag_configure("time", foreground="#9ca3af", font=("Consolas", 9))
        text.tag_configure("user", foreground="#1d4ed8", font=(FONT, 9, "bold"))
        text.tag_configure("nuri", foreground="#047857", font=(FONT, 9, "bold"))
        text.tag_configure("memo", foreground="#b45309", font=(FONT, 9, "bold"))
        text.tag_configure(
            "day",
            font=(FONT, 11, "bold"),
            foreground="#1f2937",
            background="#eef2f7",
            spacing1=14,
            spacing3=8,
            lmargin1=2,
            lmargin2=2,
        )
        text.tag_configure("hit", background="#fff3a3")
        text.tag_configure("empty", foreground="#9ca3af", font=(FONT, 10), spacing1=28)
        text.configure(state="disabled")
        self._text = text

    def _build_memo(self, tk: Any, parent: Any) -> None:
        """메모 카드 목록(스크롤되는 캔버스 안에 카드를 쌓는다)."""
        canvas = tk.Canvas(parent, background="#ffffff", highlightthickness=0)
        scrollbar = tk.Scrollbar(parent, command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)

        inner = tk.Frame(canvas, background="#ffffff", padx=2, pady=6)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind(
            "<Configure>", lambda event: canvas.itemconfigure(window, width=event.width)
        )
        canvas.bind("<MouseWheel>", self._on_wheel)
        inner.bind("<MouseWheel>", self._on_wheel)

        self._memo_canvas = canvas
        self._memo_inner = inner

    # --- 데이터 ---
    def _load_turns(self) -> None:
        try:
            self._mtime = self.path.stat().st_mtime
        except OSError:
            self._mtime = None
        self._turns = read_turns(self.path, self.keep_days)
        summary_path = self.path.with_name("daily_summaries.json")
        try:
            self._summary_mtime = summary_path.stat().st_mtime if summary_path.exists() else None
            self._summaries = read_daily_summaries(summary_path)
            pending_path = self.path.with_name("daily_summary_pending.json")
            self._pending_mtime = pending_path.stat().st_mtime if pending_path.exists() else None
            pending = json.loads(pending_path.read_text(encoding="utf-8")) if pending_path.exists() else {}
            if not isinstance(pending, dict):
                raise TypeError("요약 실행 상태 형식 오류")
            self._pending_summary = pending
            self._summary_error = False
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("대화 요약을 읽지 못했습니다: %s", exc)
            self._summaries = {}
            self._summary_error = True

    def _reload(self) -> None:
        """파일을 다시 읽어 화면을 갱신한다(찾기어와 스크롤 위치는 유지)."""
        if self._text is None:
            return
        query = self._search.get() if self._search is not None else ""
        top = self._text.yview()[0]
        self._load_turns()
        self._render(query)
        self._render_schedules()
        self._text.yview_moveto(top)

    def _auto_refresh(self) -> None:
        if self._root is None:
            return
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = None
        summary_path = self.path.with_name("daily_summaries.json")
        try:
            summary_mtime = summary_path.stat().st_mtime
        except OSError:
            summary_mtime = None
        try:
            pending_mtime = self.path.with_name("daily_summary_pending.json").stat().st_mtime
        except OSError:
            pending_mtime = None
        if (mtime != self._mtime or summary_mtime != self._summary_mtime
                or pending_mtime != self._pending_mtime):
            self._reload()
        self._render_schedules()
        self._root.after(REFRESH_MS, self._auto_refresh)

    def _on_query(self) -> None:
        self._render(self._search.get() if self._search is not None else "")

    # --- 그리기 ---
    def _render(self, query: str) -> None:
        if self._text is None:
            return
        turns = filter_turns(self._turns, query)
        blocks = memo_blocks(turns, self.memo_gap_minutes)
        self._render_talk(turns, query)
        self._render_memo(blocks, query)
        self._render_summaries(query)
        self._update_tabs(len(turns), len(blocks))
        self._update_status(query, len(turns))

    def _render_summaries(self, query: str) -> None:
        text = self._summary_text
        if text is None:
            return
        text.configure(state="normal")
        text.delete("1.0", "end")
        if self._summary_error:
            text.insert("end", "요약 파일을 읽지 못했어요. 로그를 확인해 주세요.")
        else:
            text.insert("end", "보관 기간이 지난 대화를 설정된 처리기로 요약합니다. 이전 발췌 기록은 별도로 표시합니다.\n\n")
            for item in self._pending_summary.values():
                if isinstance(item, dict):
                    text.insert("end", f"{item.get('day', '')}: 요약 진행 중이거나 실행 여부 확인이 필요합니다. 원문은 대화 탭에 보존됩니다.\n\n")
            shown = 0
            for day, entry in sorted(self._summaries.items(), reverse=True):
                lines = [f"{time_of(item.get('at', ''))} {role_label(item.get('role', ''))}: {item.get('text', '')}"
                         for item in entry.get("excerpts", []) if isinstance(item, dict)]
                summary = str(entry.get("summary") or "")
                labels = {"codex": "Codex", "codebuddy-code": "CodeBuddy"}
                provider = labels.get(entry.get("provider"), entry.get("provider") or "")
                fallback = labels.get(entry.get("fallback_from"), entry.get("fallback_from") or "")
                who = f"{fallback} → {provider}" if fallback else provider
                parts = ([f"[{who}] {summary}" if who else summary] if summary else [])
                if lines:
                    parts.append("이전 발췌 기록:\n" + "\n".join(lines))
                body = "\n".join(parts)
                if query.strip() and query.strip().lower() not in f"{day} {day_label(day)} {body}".lower():
                    continue
                text.insert("end", f"{day} · {entry.get('count', 0)}줄\n{body}\n\n")
                shown += 1
            if not shown:
                text.insert("end", "표시할 지난 대화 요약이 없어요.")
        text.configure(state="disabled")

    def _render_talk(self, turns: Sequence[Turn], query: str) -> None:
        text = self._text
        text.configure(state="normal")
        text.delete("1.0", "end")

        if not turns:
            text.insert(
                "end",
                "찾는 내용이 없어요." if query.strip() else "아직 기록된 대화가 없어요.",
                ("empty",),
            )
        else:
            for group in group_by_day(turns):
                text.insert("end", f"{day_label(group.day)} · {len(group.turns)}줄\n", ("day",))
                for turn in group.turns:
                    text.insert("end", f"{time_of(turn.at)}\t", ("say", "time"))
                    text.insert(
                        "end",
                        f"{role_label(turn.role)}\t",
                        ("say", ROLE_TAGS.get(turn.role, "say")),
                    )
                    start = text.index("end-1c")
                    label = provider_label(turn)
                    if label:
                        text.insert("end", f"[{label}] ", ("say", "time"))
                        start = text.index("end-1c")
                    text.insert("end", f"{turn.text}\n", ("say",))
                    self._highlight(start, turn.text, query)

        text.configure(state="disabled")
        text.yview_moveto(0)

    def _render_memo(self, blocks: Sequence[MemoBlock], query: str) -> None:
        inner = self._memo_inner
        if inner is None:
            return
        for child in inner.winfo_children():
            child.destroy()
        if self._memo_canvas is not None:
            self._memo_canvas.yview_moveto(0)

        if not blocks:
            message = (
                "찾는 메모가 없어요." if query.strip() else "메모 모드로 기록된 내용이 없어요."
            )
            self._memo_message(inner, message)
            return

        day: str | None = None
        for block in blocks:
            if block.day != day:
                day = block.day
                count = sum(1 for other in blocks if other.day == day)
                self._memo_day_header(inner, f"{day_label(day)} · {count}개")
            self._memo_card(inner, block)

    def _memo_message(self, parent: Any, message: str) -> None:
        import tkinter as tk

        tk.Label(
            parent,
            text=message,
            background="#ffffff",
            foreground="#9ca3af",
            font=(FONT, 10),
        ).pack(anchor="w", padx=6, pady=28)

    def _memo_day_header(self, parent: Any, text: str) -> None:
        import tkinter as tk

        strip = tk.Frame(parent, background="#eef2f7")
        strip.pack(fill="x", pady=(12, 8))
        tk.Label(
            strip,
            text=text,
            background="#eef2f7",
            foreground="#1f2937",
            font=(FONT, 11, "bold"),
            anchor="w",
            padx=8,
            pady=4,
        ).pack(fill="x")

    def _memo_card(self, parent: Any, block: MemoBlock) -> None:
        """카드 하나: 시각 범위·줄 수 + 앞부분 미리보기. 누르면 전체를 연다."""
        import tkinter as tk

        card = tk.Frame(
            parent,
            background="#ffffff",
            highlightthickness=1,
            highlightbackground="#e5e7eb",
            padx=12,
            pady=9,
        )
        card.pack(fill="x", pady=(0, 8))

        title = tk.Label(
            card,
            text=f"{block.span} · {len(block.turns)}줄",
            background="#ffffff",
            foreground="#6b7280",
            font=(FONT, 9),
            anchor="w",
        )
        title.pack(fill="x")
        preview = tk.Label(
            card,
            text=memo_preview(block),
            background="#ffffff",
            foreground="#111827",
            font=(FONT, 10),
            anchor="w",
            justify="left",
            wraplength=640,
        )
        preview.pack(fill="x", pady=(4, 0))

        widgets = (card, title, preview)

        def paint(color: str) -> None:
            for widget in widgets:
                widget.configure(background=color)

        for widget in widgets:
            widget.configure(cursor="hand2")
            widget.bind("<Button-1>", lambda _event, target=block: self._open_block(target))
            widget.bind("<Enter>", lambda _event: paint("#f5f8ff"))
            widget.bind("<Leave>", lambda _event: paint("#ffffff"))
            widget.bind("<MouseWheel>", self._on_wheel)

    def _on_wheel(self, event: Any) -> None:
        canvas = self._memo_canvas
        if canvas is None:
            return
        canvas.yview_scroll(int(-event.delta / 120), "units")

    def _open_block(self, block: MemoBlock) -> None:
        """카드 하나를 새 창에서 펼쳐 보여준다."""
        import tkinter as tk

        if self._root is None:
            return
        window = tk.Toplevel(self._root)
        window.title(f"메모 · {day_label(block.day)} {block.span}")
        window.geometry("560x460")
        window.minsize(360, 260)
        window.configure(background="#ffffff")
        window.transient(self._root)

        tk.Label(
            window,
            text=f"{day_label(block.day)}   {block.span} · {len(block.turns)}줄",
            background="#ffffff",
            foreground="#1f2937",
            font=(FONT, 11, "bold"),
            anchor="w",
        ).pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(
            window,
            text="메모 모드로 받아쓴 내용",
            background="#ffffff",
            foreground="#9ca3af",
            font=(FONT, 9),
            anchor="w",
        ).pack(fill="x", padx=16)

        # 닫기 버튼을 본문보다 먼저 pack 해야 잘리지 않는다.
        # (본문 Text 의 요청 높이가 커서 나중에 pack 하면 아래로 밀려 사라진다)
        close = tk.Button(
            window,
            text="닫기",
            command=window.destroy,
            relief="flat",
            background="#eef2ff",
            activebackground="#e0e7ff",
            foreground="#1f2937",
            font=(FONT, 10),
            padx=16,
            pady=3,
        )
        close.pack(side="bottom", pady=(0, 12))

        body = tk.Frame(window, background="#ffffff")
        body.pack(fill="both", expand=True, padx=16, pady=(8, 10))
        scrollbar = tk.Scrollbar(body)
        scrollbar.pack(side="right", fill="y")
        text = tk.Text(
            body,
            wrap="word",
            relief="flat",
            borderwidth=0,
            background="#fbfcfe",
            foreground="#111827",
            font=(FONT, 10),
            height=8,
            padx=10,
            pady=8,
            yscrollcommand=scrollbar.set,
        )
        text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=text.yview)
        text.tag_configure("time", foreground="#9ca3af", font=("Consolas", 9))
        for turn in block.turns:
            text.insert("end", f"{time_of(turn.at)}  ", ("time",))
            text.insert("end", f"{turn.text}\n\n")
        text.configure(state="disabled")

        window.lift()
        window.focus_force()
        logger.info("메모 카드 열기: %s %s (%d줄)", block.day, block.span, len(block.turns))

    def _update_tabs(self, talks: int, memos: int) -> None:
        notebook = self._notebook
        if notebook is None:
            return
        try:
            notebook.tab(0, text=f"대화 {talks}")
            notebook.tab(1, text=f"메모 {memos}")
        except Exception as exc:  # noqa: BLE001 - 탭 이름은 표시용이다
            logger.debug("탭 이름 갱신 실패: %s", exc)

    def _update_status(self, query: str, shown: int) -> None:
        if self._status is None:
            return
        total = len(self._turns)
        # 응답 앞 대괄호가 무엇인지 화면에서 바로 알 수 있게 함께 적는다
        hint = " · [Codex]/[CodeBuddy]/[로컬] = 처리 주체"
        if query.strip():
            self._status.configure(text=f"검색 결과 {shown}줄 / 전체 {total}줄{hint}")
            return
        memos = sum(1 for turn in self._turns if turn.role == ROLE_MEMO)
        self._status.configure(
            text=f"최근 {self.keep_days}일 및 요약 대기 · 대화 {total - memos}줄 · 메모 {memos}줄{hint}"
        )

    def _highlight(self, start: str, body: str, query: str) -> None:
        """찾는 말을 노란 배경으로 표시한다."""
        needle = query.strip()
        if not needle or not body:
            return
        stop = f"{start}+{len(body)}c"
        index = start
        while True:
            found = self._text.search(needle, index, stopindex=stop, nocase=True)
            if not found:
                return
            end = f"{found}+{len(needle)}c"
            self._text.tag_add("hit", found, end)
            index = end
