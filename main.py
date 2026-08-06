import json
import os
import re
import threading
import tkinter as tk
import traceback
from datetime import datetime, timedelta
from tkinter import messagebox
from typing import Any, Dict, List, Optional, Set, Tuple

import customtkinter as ctk

# 핵심 라이브러리
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

# 모듈화 패키지 임포트
from config import ConfigManager
from core.catalog import PGMetadataProvider
from core.engine import RuleEngine
from core.parser import PGPlanAnalyzer
from models.recommendation import RecommendationModel
from rules.base_rule import RuleContext

# ==========================================
# 상용 GUI 애플리케이션 (CustomTkinter)
# ==========================================


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("PostgreSQL Production-Grade Performance Tuner (AST Core)")
        self.geometry("1150x800")

        self.db_config = ConfigManager.load_config()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.color_bg_dark = "#1C1D1F"
        self.color_text_normal = "#D8DEE9"
        self.color_text_dim = "#9299A6"
        self.color_green = "#A1EF9B"
        self.color_gold = "#F4D35E"
        self.color_pink = "#F97B7D"

        self.create_header_frame()
        self.create_workspace()

    def create_header_frame(self):
        self.header_frame = ctk.CTkFrame(self, corner_radius=8, fg_color="#242629")
        self.header_frame.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="nsew")

        labels = ["Host:", "Port:", "DB Name:", "User:", "Password:"]
        keys = ["host", "port", "dbname", "user", "password"]
        self.entries = {}

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

        title_left = ctk.CTkLabel(
            left_panel,
            text="✍️ SQL Query (Ctrl + Enter로 즉시 실행)",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=self.color_text_normal,
        )
        title_left.grid(row=0, column=0, padx=15, pady=(10, 2), sticky="w")

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
            """SELECT *
    FROM test_orders
    WHERE order_status = 'PENDING'
    AND order_amount > 500.00;""",
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
        messagebox.showinfo("성공", "데이터베이스 접속 설정이 로컬 파일에 안전하게 기록되었습니다.")

    def start_analysis_thread(self):
        query = self.txt_query.get("1.0", "end").strip()
        if not query:
            messagebox.showwarning("입력 필요", "분석할 SQL 질의문을 입력해 주세요.")
            return

        conn_params = {key: entry.get().strip() for key, entry in self.entries.items()}
        self.btn_run.configure(state="disabled", text="⏳ 분석 진행 중...")
        self.update_result_box(
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
        dsn = f"host={conn_params['host']} port={conn_params['port']} dbname={conn_params['dbname']} user={conn_params['user']} password={conn_params['password']}"

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
                            lambda: self.update_result_box(
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

                    all_recs = []
                    for node in target_nodes:
                        recs = rule_engine.analyze_node(context, node)
                        all_recs.extend(recs)

                    # 우선순위 정렬 (우선순위 수치가 작을수록 시급하며, 동일 우선순위에서는 rule_id 및 title로 일관되게 정렬)
                    all_recs.sort(key=lambda r: (r.priority, r.rule_id or "", r.title or ""))

                    self.after(0, lambda: self.render_recommendations(raw_explain_text, all_recs))

                finally:
                    conn.rollback()

        except ValueError as err:
            self.after(
                0,
                lambda error_val=err: self.update_result_box_custom(
                    f"❌ [안전 경고]\n\n{error_val!s}",
                    self.color_pink,
                ),
            )
        except psycopg.errors.QueryCanceled as err:
            self.after(
                0,
                lambda error_val=err: self.update_result_box_custom(
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
                lambda error_val=err, preview_val=error_preview: self.update_result_box_custom(
                    f"❌ [SQL 문법 오류 감지]\n작성하신 SQL 구문에 표준 PostgreSQL 문법에 맞지 않는 부분이 있습니다.\n"
                    f"{preview_val}\n\n상세 메시지: {self.get_error_message(error_val)}",
                    self.color_pink,
                ),
            )
        except psycopg.errors.UndefinedTable as err:
            self.after(
                0,
                lambda error_val=err: self.update_result_box_custom(
                    f"❌ [테이블 없음 오류]\n\n상세 메시지: {self.get_error_message(error_val)}",
                    self.color_pink,
                ),
            )
        except psycopg.errors.UndefinedColumn as err:
            self.after(
                0,
                lambda error_val=err: self.update_result_box_custom(
                    f"❌ [컬럼 없음 오류]\n\n상세 메시지: {self.get_error_message(error_val)}",
                    self.color_pink,
                ),
            )
        except Exception as e:
            self.after(
                0,
                lambda error_val=e: self.update_result_box_custom(
                    f"❌ [연결 및 실행 에러]\n\n상세 메시지: {self.get_error_message(error_val)}",
                    self.color_pink,
                ),
            )
        finally:
            self.after(0, self.enable_run_button)

    def enable_run_button(self):
        self.btn_run.configure(state="normal", text="⚡ AST & Heuristics 분석 실행")

    def update_result_box(self, text: str):
        self.txt_result.configure(state="normal", text_color=self.color_text_normal)
        self.txt_result.delete("1.0", "end")
        self.txt_result.insert("1.0", text)
        self.txt_result.configure(state="disabled")

    def update_result_box_custom(self, text: str, text_color: str):
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
                severity_symbol = (
                    "🔴"
                    if rec.severity == "CRITICAL"
                    else "🟡"
                    if rec.severity == "WARNING"
                    else "🟢"
                    if rec.severity == "INFO"
                    else "🔷"
                )
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

        self.apply_comfort_tags()
        self.txt_result.configure(state="disabled")

    def apply_comfort_tags(self):
        self.txt_result.tag_config("critical_tag", foreground=self.color_pink)
        self.txt_result.tag_config("warning_tag", foreground=self.color_gold)
        self.txt_result.tag_config("info_tag", foreground=self.color_green)
        self.txt_result.tag_config("sql_tag", foreground="#8FC7FF")

        self.highlight_pattern(r"🔴 \[CRITICAL\]", "critical_tag")
        self.highlight_pattern(r"🟡 \[WARNING\]", "warning_tag")
        self.highlight_pattern(r"🟢 \[INFO\]", "info_tag")
        self.highlight_pattern(
            r"CREATE\s+EXTEN.*|CREATE\s+INDEX.*?|SET\s+work_mem.*?;|ANALYZE\s+VERBOSE.*?;|SET\s+max_parallel_workers_per_gather.*?;",
            "sql_tag",
        )

    def highlight_pattern(self, pattern, tag_name):
        start = "1.0"
        while True:
            pos = self.txt_result.search(pattern, start, stopindex="end", regexp=True)
            if not pos:
                break
            match_len = len(re.findall(pattern, self.txt_result.get(pos, "end"))[0])
            end = f"{pos}+{match_len}c"
            self.txt_result.tag_add(tag_name, pos, end)
            start = end


if __name__ == "__main__":
    app = App()
    app.mainloop()
