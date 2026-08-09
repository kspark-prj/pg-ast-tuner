import json
import os
import re
import sys
import threading
from typing import Any

import customtkinter as ctk

# 핵심 라이브러리
import psycopg
from PIL import Image
from psycopg import sql
from psycopg.rows import dict_row

# 모듈화 패키지 임포트
from config import ConfigManager, HistoryManager
from core.catalog import PGMetadataProvider
from core.engine import RuleEngine
from core.parser import PGPlanAnalyzer
from models.recommendation import RecommendationModel
from rules.base_rule import RuleContext


# ==========================================
# 리소스 경로 헬퍼 (PyInstaller EXE 빌드 대응)
# ==========================================
def get_resource_path(relative_path: str) -> str:
    """PyInstaller 번들 내부 파일 또는 일반 실행 시의 상대 경로 반환"""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


# ==========================================
# 스플래시 윈도우 클래스 (CTkToplevel)
# ==========================================
class SplashScreen(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent

        self.width = 600
        self.height = 350

        # OS 타이틀바 제거
        self.overrideredirect(True)

        # 화면 중앙 배치 계산
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = (screen_width - self.width) // 2
        y = (screen_height - self.height) // 2
        self.geometry(f"{self.width}x{self.height}+{x}+{y}")

        # 최상단에 표시 & 배경색 지정
        self.attributes("-topmost", True)
        self.configure(fg_color="#1C1D1F")

        # 1. 이미지 영역 (상단 ~280px)
        image_path = get_resource_path("splash.png")
        if os.path.exists(image_path):
            pil_img = Image.open(image_path)
            self.splash_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(600, 280))
            self.lbl_image = ctk.CTkLabel(self, image=self.splash_img, text="")
            self.lbl_image.pack(side="top", fill="both", expand=True)
        else:
            self.lbl_image = ctk.CTkLabel(
                self,
                text="PostgreSQL Performance Tuner",
                font=ctk.CTkFont(size=22, weight="bold"),
                text_color="#D8DEE9",
            )
            self.lbl_image.pack(side="top", fill="both", expand=True, pady=80)

        # 2. 하단 로딩 바 영역 (하단 70px)
        self.bottom_frame = ctk.CTkFrame(self, fg_color="#18191A", height=70, corner_radius=0)
        self.bottom_frame.pack(side="bottom", fill="x")

        self.lbl_status = ctk.CTkLabel(
            self.bottom_frame,
            text="애플리케이션을 초기화하는 중입니다...",
            font=ctk.CTkFont(size=11),
            text_color="#9299A6",
        )
        self.lbl_status.pack(anchor="w", padx=20, pady=(8, 2))

        self.progress_bar = ctk.CTkProgressBar(
            self.bottom_frame,
            width=560,
            height=10,
            progress_color="#2F5C8F",
            fg_color="#2E3033",
        )
        self.progress_bar.pack(padx=20, pady=(2, 12))
        self.progress_bar.set(0.0)

        self.update_idletasks()

    def set_progress(self, value: float, status_text: str = ""):
        """진행 상황(0.0 ~ 1.0)과 텍스트 상태를 안전하게 갱신합니다."""
        self.progress_bar.set(value)
        if status_text:
            self.lbl_status.configure(text=status_text)
        self.update_idletasks()


# ==========================================
# 상용 GUI 애플리케이션 (CustomTkinter)
# ==========================================

_SEVERITY_SYMBOLS: dict[str, str] = {
    "CRITICAL": "🔴",
    "HIGH": "🟠",
    "WARNING": "🟡",
    "INFO": "🟢",
}


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        # 메인 창 숨김
        self.withdraw()

        # 단일 스플래시 창 생성
        self.splash = SplashScreen(self)

        # 비동기 로딩 시퀀스 시작 (time.sleep 대신 after 사용)
        self.after(100, self._load_step_1)

    def _load_step_1(self):
        self.splash.set_progress(0.25, "시스템 접속 환경 설정 로딩 중...")
        self.title("PostgreSQL Production-Grade Performance Tuner (AST Core) v1.0.0")
        self.geometry("1150x800")
        self.db_config = ConfigManager.load_config()

        self.color_bg_dark = "#1C1D1F"
        self.color_text_normal = "#D8DEE9"
        self.color_text_dim = "#9299A6"
        self.color_green = "#A1EF9B"
        self.color_gold = "#F4D35E"
        self.color_pink = "#F97B7D"

        self.after(300, self._load_step_2)

    def _load_step_2(self):
        self.splash.set_progress(0.60, "UI 컨트롤 및 모듈 생성 중...")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.create_header_frame()
        self.create_workspace()

        self.after(300, self._load_step_3)

    def _load_step_3(self):
        self.splash.set_progress(1.0, "초기화 완료! 메인 화면을 열고 있습니다...")
        self.after(200, self._finish_loading)

    def _finish_loading(self):
        # 스플래시 닫고 메인 화면 활성화
        self.splash.destroy()
        self.deiconify()
        self.focus_force()

    def create_header_frame(self):
        self.header_frame = ctk.CTkFrame(self, corner_radius=8, fg_color="#242629")
        self.header_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="nsew")

        labels = ["Host:", "Port:", "DB Name:", "User:", "Password:"]
        keys = ["host", "port", "dbname", "user", "password"]
        self.entries: dict[str, ctk.CTkEntry] = {}

        for i, (lbl_txt, key) in enumerate(zip(labels, keys)):
            lbl = ctk.CTkLabel(
                self.header_frame,
                text=lbl_txt,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=self.color_text_dim,
            )
            lbl.grid(row=0, column=i * 2, padx=(10, 2), pady=12, sticky="e")

            show_char = "*" if key == "password" else None
            entry = ctk.CTkEntry(
                self.header_frame,
                width=110,
                show=show_char,
                fg_color="#1E2022",
                text_color=self.color_text_normal,
                border_color="#333538",
            )
            entry.insert(0, self.db_config.get(key, ""))
            entry.grid(row=0, column=i * 2 + 1, padx=(0, 8), pady=12, sticky="w")
            self.entries[key] = entry

        self.btn_save = ctk.CTkButton(
            self.header_frame,
            text="정보 저장",
            width=80,
            fg_color="#34373C",
            hover_color="#454A52",
            text_color=self.color_text_normal,
            command=self.save_config,
        )
        self.btn_save.grid(row=0, column=10, padx=10, pady=12, sticky="e")

    def create_workspace(self):
        self.workspace = ctk.CTkFrame(self, fg_color="transparent")
        self.workspace.grid(row=1, column=0, padx=20, pady=10, sticky="nsew")
        self.workspace.grid_columnconfigure(0, weight=4)
        self.workspace.grid_columnconfigure(1, weight=6)
        self.workspace.grid_rowconfigure(0, weight=1)

        left_panel = ctk.CTkFrame(self.workspace, fg_color="#242629")
        left_panel.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="nsew")
        left_panel.grid_rowconfigure(1, weight=1)
        left_panel.grid_columnconfigure(0, weight=1)

        left_header = ctk.CTkFrame(left_panel, fg_color="transparent")
        left_header.grid(row=0, column=0, padx=15, pady=(10, 2), sticky="ew")
        left_header.grid_columnconfigure(0, weight=1)
        left_header.grid_columnconfigure(1, weight=0)

        title_left = ctk.CTkLabel(
            left_header,
            text="✍️ SQL Query (Ctrl + Enter로 즉시 실행)",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=self.color_text_normal,
        )
        title_left.grid(row=0, column=0, sticky="w")

        self.history_menu = ctk.CTkOptionMenu(
            left_header,
            width=180,
            values=["최근 쿼리 이력 없음"],
            command=self.on_history_select,
            fg_color="#34373C",
            button_color="#34373C",
            button_hover_color="#454A52",
            dropdown_fg_color="#242629",
            dropdown_hover_color="#454A52",
            text_color=self.color_text_normal,
            dropdown_text_color=self.color_text_normal,
        )
        self.history_menu.grid(row=0, column=1, sticky="e")
        self.load_history_menu()

        self.txt_query = ctk.CTkTextbox(
            left_panel,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=self.color_bg_dark,
            text_color=self.color_text_normal,
            border_color="#333538",
            border_width=1,
        )
        self.txt_query.grid(row=1, column=0, padx=15, pady=10, sticky="nsew")
        self.txt_query.insert(
            "1.0",
            """SELECT * FROM test_orders WHERE UPPER(status) = 'ACTIVE';""",
        )

        try:
            self.txt_query._textbox.configure(insertbackground=self.color_text_normal)
        except Exception:
            pass

        self.txt_query.bind("<Control-Return>", self.trigger_shortcut_run)
        self.txt_query.bind("<Command-Return>", self.trigger_shortcut_run)

        self.btn_run = ctk.CTkButton(
            left_panel,
            text="⚡ AST & Heuristics 분석 실행",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=40,
            fg_color="#2F5C8F",
            hover_color="#4173AA",
            text_color="#FFFFFF",
            command=self.start_analysis_thread,
        )
        self.btn_run.grid(row=2, column=0, padx=15, pady=15, sticky="ew")

        right_panel = ctk.CTkFrame(self.workspace, fg_color="#242629")
        right_panel.grid(row=0, column=1, padx=(10, 0), pady=0, sticky="nsew")
        right_panel.grid_rowconfigure(1, weight=1)
        right_panel.grid_columnconfigure(0, weight=1)

        title_right = ctk.CTkLabel(
            right_panel,
            text="📋 실제 실행계획 & 튜닝 가이드 리포트",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=self.color_text_normal,
        )
        title_right.grid(row=0, column=0, padx=15, pady=(10, 2), sticky="w")

        self.txt_result = ctk.CTkTextbox(
            right_panel,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=self.color_bg_dark,
            text_color=self.color_text_normal,
            border_color="#333538",
            border_width=1,
        )
        self.txt_result.grid(row=1, column=0, padx=15, pady=10, sticky="nsew")

        try:
            self.txt_result._textbox.configure(insertbackground=self.color_text_normal)
        except Exception:
            pass

    def trigger_shortcut_run(self, event):
        self.start_analysis_thread()
        return "break"

    def save_config(self):
        config_data = {key: entry.get().strip() for key, entry in self.entries.items()}
        ConfigManager.save_config(config_data)
        from tkinter import messagebox

        messagebox.showinfo("성공", "데이터베이스 접속 설정이 로컬 파일에 안전하게 기록되었습니다.")

    def load_history_menu(self):
        self.history_items = HistoryManager.load_history()
        options = []
        for item in self.history_items:
            query_lines = [line.strip() for line in item["query"].splitlines() if line.strip()]
            preview = " ".join(query_lines)[:25]
            if len(" ".join(query_lines)) > 25:
                preview += "..."

            try:
                parts = item["timestamp"].split(" ")
                date_part = parts[0]
                time_part = parts[1]
                month_day = "-".join(date_part.split("-")[1:])
                time_short = ":".join(time_part.split(":")[:2])
                time_str = f"{month_day} {time_short}"
            except Exception:
                time_str = item["timestamp"]

            label = f"[{time_str}] {preview}"
            options.append(label)

        if not options:
            self.history_menu.configure(values=["최근 쿼리 이력 없음"], state="disabled")
            self.history_menu.set("최근 쿼리 이력 없음")
        else:
            self.history_menu.configure(values=options, state="normal")
            self.history_menu.set("이력 불러오기...")

    def on_history_select(self, selected_label: str):
        if selected_label in ("이력 불러오기...", "최근 쿼리 이력 없음"):
            return

        try:
            options = []
            for item in self.history_items:
                query_lines = [line.strip() for line in item["query"].splitlines() if line.strip()]
                preview = " ".join(query_lines)[:25]
                if len(" ".join(query_lines)) > 25:
                    preview += "..."
                try:
                    parts = item["timestamp"].split(" ")
                    date_part = parts[0]
                    time_part = parts[1]
                    month_day = "-".join(date_part.split("-")[1:])
                    time_short = ":".join(time_part.split(":")[:2])
                    time_str = f"{month_day} {time_short}"
                except Exception:
                    time_str = item["timestamp"]
                label = f"[{time_str}] {preview}"
                options.append(label)

            idx = options.index(selected_label)
            full_query = self.history_items[idx]["query"]

            self.txt_query.delete("1.0", "end")
            self.txt_query.insert("1.0", full_query)
        except Exception:
            pass
        finally:
            self.history_menu.set("이력 불러오기...")

    def start_analysis_thread(self):
        query = self.txt_query.get("1.0", "end").strip()
        if not query:
            from tkinter import messagebox

            messagebox.showwarning("입력 필요", "분석할 SQL 질의문을 입력해 주세요.")
            return

        HistoryManager.save_query(query)
        self.load_history_menu()

        conn_params = {key: entry.get().strip() for key, entry in self.entries.items()}
        self.btn_run.configure(state="disabled", text="⏳ 분석 진행 중...")
        self._set_result_text(
            "...데이터베이스 시스템 카탈로그 조회 및 AST 트리를 병합 분석하는 중입니다..."
        )

        t = threading.Thread(target=self.run_analysis, args=(query, conn_params), daemon=True)
        t.start()

    @staticmethod
    def get_error_message(err: Any) -> str:
        if err is None:
            return "알 수 없는 에러가 발생했습니다."
        if hasattr(err, "diagnostics") and err.diagnostics:
            diag = err.diagnostics
            if hasattr(diag, "message_primary") and diag.message_primary:
                return str(diag.message_primary)
        if hasattr(err, "pgerror") and err.pgerror:
            return str(err.pgerror)
        return str(err).strip()

    def run_analysis(self, query: str, conn_params: dict[str, str]):
        dsn = (
            f"host={conn_params['host']} port={conn_params['port']} "
            f"dbname={conn_params['dbname']} user={conn_params['user']} "
            f"password={conn_params['password']}"
        )

        try:
            with psycopg.connect(dsn, connect_timeout=5) as conn:
                conn.autocommit = False
                with conn.cursor() as sys_cur:
                    sys_cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;")

                try:
                    metadata_provider = PGMetadataProvider(conn)
                    plan_analyzer = PGPlanAnalyzer(conn)
                    rule_engine = RuleEngine(metadata_provider)

                    raw_explain_text = plan_analyzer.execute_explain_text(query)
                    explain_data = plan_analyzer.execute_explain_json(query)

                    if not explain_data:
                        self.after(
                            0,
                            lambda: self._set_result_text(
                                "[안내] 수집된 실행계획 정보가 비어 있습니다."
                            ),
                        )
                        return

                    root_plan = explain_data[0].get("Plan", {})
                    target_nodes = plan_analyzer.find_problematic_nodes(root_plan)

                    clean_query = plan_analyzer.clean_query_comments(query)
                    context = RuleContext(
                        raw_query=query,
                        clean_query=clean_query,
                        metadata_provider=metadata_provider,
                        plan_data=explain_data,
                    )

                    all_recs: list[RecommendationModel] = []
                    for node in target_nodes:
                        all_recs.extend(rule_engine.analyze_node(context, node))

                    all_recs.sort(
                        key=lambda r: (
                            r.priority,
                            r.rule_id or "",
                            r.title or "",
                        )
                    )

                    self.after(
                        0,
                        lambda: self.render_recommendations(raw_explain_text, all_recs),
                    )

                finally:
                    conn.rollback()

        except ValueError as err:
            self.after(
                0,
                lambda error_val=err: self._set_result_text_colored(
                    f"❌ [안전 경고]\n\n{error_val!s}", self.color_pink
                ),
            )
        except psycopg.errors.QueryCanceled as err:
            self.after(
                0,
                lambda error_val=err: self._set_result_text_colored(
                    "❌ [타임아웃 발생]\n\n쿼리 수행 시간이 한계치(10초)를 초과하여 작업이 강제 취소되었습니다.\n"
                    f"상세 정보: {self.get_error_message(error_val)}",
                    self.color_pink,
                ),
            )
        except psycopg.errors.SyntaxError as err:
            diag = err.diagnostics  # type:ignore
            err_pos = diag.statement_position if diag else None
            error_preview = ""
            if err_pos and err_pos > 0:
                pos = err_pos - 1
                before = query[:pos]
                line_number = before.count("\n") + 1
                error_preview = f"\n[오류 예상 위치: {line_number}번째 줄]\n"
                error_preview += (
                    f"... {query[max(0, pos - 30) : pos]} 👉[여기]👈 {query[pos : pos + 30]} ..."
                )

            self.after(
                0,
                lambda error_val=err, preview_val=error_preview: self._set_result_text_colored(
                    f"❌ [SQL 문법 오류 감지]\n작성하신 SQL 구문에 표준 PostgreSQL 문법에 맞지 않는 부분이 있습니다.\n"
                    f"{preview_val}\n\n상세 메시지: {self.get_error_message(error_val)}",
                    self.color_pink,
                ),
            )
        except psycopg.errors.UndefinedTable as err:
            self.after(
                0,
                lambda error_val=err: self._set_result_text_colored(
                    f"❌ [테이블 없음 오류]\n\n상세 메시지: {self.get_error_message(error_val)}",
                    self.color_pink,
                ),
            )
        except psycopg.errors.UndefinedColumn as err:
            self.after(
                0,
                lambda error_val=err: self._set_result_text_colored(
                    f"❌ [컬럼 없음 오류]\n\n상세 메시지: {self.get_error_message(error_val)}",
                    self.color_pink,
                ),
            )
        except Exception as e:
            self.after(
                0,
                lambda error_val=e: self._set_result_text_colored(
                    f"❌ [연결 및 실행 에러]\n\n상세 메시지: {self.get_error_message(error_val)}",
                    self.color_pink,
                ),
            )
        finally:
            self.after(0, self.enable_run_button)

    def enable_run_button(self):
        self.btn_run.configure(state="normal", text="⚡ AST & Heuristics 분석 실행")

    def _set_result_text(self, text: str):
        self.txt_result.configure(state="normal", text_color=self.color_text_normal)
        self.txt_result.delete("1.0", "end")
        self.txt_result.insert("1.0", text)
        self.txt_result.configure(state="disabled")

    def _set_result_text_colored(self, text: str, text_color: str):
        self.txt_result.configure(state="normal", text_color=text_color)
        self.txt_result.delete("1.0", "end")
        self.txt_result.insert("1.0", text)
        self.txt_result.configure(state="disabled")

    def render_recommendations(self, raw_explain: str, recs: list[RecommendationModel]):
        self.txt_result.configure(state="normal")
        self.txt_result.delete("1.0", "end")
        self.txt_result.configure(text_color=self.color_text_normal)

        self.txt_result.insert("end", "========================================================\n")
        self.txt_result.insert("end", "🔍 [데이터베이스 실제 EXPLAIN 수립 결과]\n")
        self.txt_result.insert("end", "========================================================\n")
        self.txt_result.insert("end", f"{raw_explain}\n\n")

        self.txt_result.insert("end", "========================================================\n")
        self.txt_result.insert("end", "💡 [지식 기반 자동 튜닝 권장 리포트]\n")
        self.txt_result.insert("end", "========================================================\n")

        if not recs:
            self.txt_result.insert(
                "end",
                "✅ 현재 옵티마이저가 수립한 실행 계획상 병목 구간이나 인덱스 누락이 감지되지 않는 최상의 플랜입니다.\n",
            )
        else:
            for idx, rec in enumerate(recs, 1):
                severity_symbol = _SEVERITY_SYMBOLS.get(rec.severity, "🔷")
                rule_str = f" [{rec.rule_id}]" if getattr(rec, "rule_id", None) else ""
                self.txt_result.insert(
                    "end",
                    f"{severity_symbol} [{rec.severity}] 튜닝 가이드 #{idx}{rule_str}: {rec.title}\n",
                )
                self.txt_result.insert("end", f"  • 대상 노드  : {rec.plan_node or 'Unknown'}\n")
                self.txt_result.insert("end", f"  • 현상 및 원인: {rec.reason}\n")
                self.txt_result.insert("end", f"  • 조치 가이드: {rec.recommendation}\n")
                if rec.recommended_sql:
                    self.txt_result.insert("end", "  • 추천 실행 스크립트:\n")
                    self.txt_result.insert("end", f"    {rec.recommended_sql}\n")
                if rec.estimated_gain:
                    self.txt_result.insert("end", f"  • 예상 효과  : {rec.estimated_gain}\n")
                if rec.false_positive_risk:
                    self.txt_result.insert("end", f"  • 오진 가능성: {rec.false_positive_risk}\n")
                self.txt_result.insert("end", "\n" + "-" * 55 + "\n\n")

        self._apply_syntax_highlighting()
        self.txt_result.configure(state="disabled")

    def _apply_syntax_highlighting(self):
        textbox = self.txt_result

        textbox.tag_config("critical_tag", foreground=self.color_pink)
        textbox.tag_config("high_tag", foreground="#FF9644")
        textbox.tag_config("warning_tag", foreground=self.color_gold)
        textbox.tag_config("info_tag", foreground=self.color_green)
        textbox.tag_config("sql_tag", foreground="#8FC7FF")

        full_text = textbox.get("1.0", "end")

        highlight_rules: list[tuple[str, str]] = [
            (r"🔴 \[CRITICAL\]", "critical_tag"),
            (r"🟠 \[HIGH\]", "high_tag"),
            (r"🟡 \[WARNING\]", "warning_tag"),
            (r"🟢 \[INFO\]", "info_tag"),
            (
                r"CREATE\s+EXTEN.*|CREATE\s+INDEX.*?|SET\s+work_mem.*?;|ANALYZE\s+VERBOSE.*?;|SET\s+max_parallel_workers_per_gather.*?;",
                "sql_tag",
            ),
        ]

        for pattern, tag_name in highlight_rules:
            for m in re.finditer(pattern, full_text, re.MULTILINE):
                start_offset = m.start()
                end_offset = m.end()
                start_idx = f"1.0 + {start_offset} chars"
                end_idx = f"1.0 + {end_offset} chars"
                textbox.tag_add(tag_name, start_idx, end_idx)


if __name__ == "__main__":
    app = App()
    app.mainloop()
