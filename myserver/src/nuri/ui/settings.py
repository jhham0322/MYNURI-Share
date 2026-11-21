"""누리의 자주 쓰는 설정 창."""
from __future__ import annotations

from typing import Any

from nuri.core.settings import SettingsStore, UserSettings


class SettingsViewer:
    def __init__(self, store: SettingsStore) -> None:
        self.store = store
        self.root: Any = None

    def show(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        self.root = root
        root.title("누리 설정")
        root.geometry("540x490")
        root.minsize(500, 450)
        root.configure(background="white")
        current = self.store.current()
        self.provider = tk.StringVar(value=current.provider)
        self.wait_seconds = tk.StringVar(value=str(current.wait_seconds))
        self.speech_seconds = tk.StringVar(value=str(current.speech_seconds))
        self.followup_enabled = tk.BooleanVar(value=current.followup_enabled)
        self.followup_seconds = tk.StringVar(value=str(current.followup_seconds))

        panel = ttk.Frame(root, padding=20)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="누리 설정", font=("맑은 고딕", 16, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Label(panel, text="작업 처리기", font=("맑은 고딕", 10, "bold")).grid(row=1, column=0, sticky="w")
        choices = ttk.Frame(panel)
        choices.grid(row=1, column=1, sticky="w")
        ttk.Radiobutton(choices, text="Codex", value="codex", variable=self.provider).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(choices, text="CodeBuddy", value="codebuddy-code", variable=self.provider).pack(side="left")
        ttk.Label(panel, text="선택한 처리기를 사용할 수 없고 재시도가 안전하면 다른 처리기로 전환합니다.", foreground="#6b7280", wraplength=465).grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 12))
        for row, label, variable, hint in (
            (3, "작업 대기 시간", self.wait_seconds, "초 (30~1800)"),
            (4, "일반 응답 길이", self.speech_seconds, "초 (3~120)"),
            (6, "후속 대화 대기", self.followup_seconds, "초 (1~60)"),
        ):
            ttk.Label(panel, text=label).grid(row=row, column=0, sticky="w", pady=8)
            field = ttk.Frame(panel)
            field.grid(row=row, column=1, sticky="w", pady=8)
            ttk.Entry(field, textvariable=variable, width=8).pack(side="left")
            ttk.Label(field, text=hint, foreground="#6b7280").pack(side="left", padx=8)
        ttk.Checkbutton(panel, text="호출어 없이 후속 대화 이어가기", variable=self.followup_enabled).grid(row=5, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Label(panel, text="저장한 설정은 다음 작업부터 적용되며 재시작 후에도 유지됩니다.", foreground="#6b7280", wraplength=465).grid(row=7, column=0, columnspan=2, sticky="w", pady=(15, 8))
        buttons = ttk.Frame(panel)
        buttons.grid(row=8, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="저장", command=self._save).pack(side="left", padx=5)
        ttk.Button(buttons, text="취소", command=root.destroy).pack(side="left")
        panel.columnconfigure(1, weight=1)
        root.lift()
        root.attributes("-topmost", True)
        root.after(250, lambda: root.attributes("-topmost", False))
        root.mainloop()
        self.root = None

    def _save(self) -> None:
        from tkinter import messagebox

        try:
            settings = UserSettings(
                self.provider.get(), int(self.wait_seconds.get()), int(self.speech_seconds.get()),
                self.followup_enabled.get(), int(self.followup_seconds.get()),
                # 말 속도는 이 창에서 바꾸지 않지만, 저장할 때 초기화되지 않도록 현재 값을 넘긴다
                self.store.current().tts_rate,
            )
            self.store.save(settings)
        except ValueError as exc:
            message = str(exc) if "invalid literal" not in str(exc) else "시간은 숫자로 입력해 주세요."
            messagebox.showerror("설정 저장", message, parent=self.root)
            return
        except OSError:
            messagebox.showerror("설정 저장", "설정을 저장하지 못했어요.", parent=self.root)
            return
        self.root.destroy()
