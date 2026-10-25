"""HTML Report Generator for PostgreSQL EXPLAIN Analysis.

Generates a standalone, beautiful, dark-themed HTML report for educational
and professional query plan analysis.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any

from models.recommendation import RecommendationModel


class HTMLReportGenerator:
    """PostgreSQL 실행계획 분석 결과를 가독성 높은 다크테마 HTML로 렌더링하는 제너레이터."""

    @classmethod
    def generate(
        cls,
        raw_explain: str,
        recs: list[RecommendationModel],
        root_plan: dict[str, Any] | None = None,
        explain_data: list[dict[str, Any]] | None = None,
        query: str = "",
        formatted_tree: str = "",
    ) -> str:
        """독립 실행 가능한 단일 HTML 문서 문자열을 생성합니다."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        exec_time = None
        plan_time = None
        if explain_data and isinstance(explain_data, list) and len(explain_data) > 0:
            exec_time = explain_data[0].get("Execution Time")
            plan_time = explain_data[0].get("Planning Time")
        if exec_time is None and root_plan:
            exec_time = root_plan.get("Actual Total Time")

        exec_time_str = f"{exec_time:.3f} ms" if exec_time is not None else "N/A"
        plan_time_str = f"{plan_time:.3f} ms" if plan_time is not None else "N/A"

        # 1. Plan Summary 정보 수집
        nodes: list[dict[str, Any]] = []

        def collect_nodes(n: dict[str, Any]):
            nodes.append(n)
            for sub in n.get("Plans", []):
                collect_nodes(sub)

        if root_plan:
            collect_nodes(root_plan)

        nodes.sort(key=lambda n: n.get("_line_number", 0))
        total_nodes = len(nodes)
        node_types = sorted(list({n.get("Node Type", "") for n in nodes if n.get("Node Type")}))

        # 2. 종합 판단 수집
        has_critical = any(getattr(r, "severity", "") == "CRITICAL" for r in recs)
        has_high = any(getattr(r, "severity", "") == "HIGH" for r in recs)

        if exec_time is not None and exec_time <= 5.0 and not has_critical:
            status_class = "status-optimal"
            status_title = "최적 상태 / 튜닝 불필요"
            status_desc = (
                f"현재 총 실행시간이 <strong>{exec_time:.3f}ms</strong>로 매우 우수하며, 서비스에 지장을 주는 병목이 감지되지 않았습니다. "
                "일부 경미한 오차나 인덱스 부재가 있더라도 현 데이터 규모에서는 성급한 인덱스 추가나 SQL 수정을 권장하지 않습니다."
            )
        elif has_critical:
            status_class = "status-critical"
            status_title = "긴급 튜닝 필요 / CRITICAL"
            status_desc = "디스크 Spill 또는 대규모 풀스캔/조인 병목이 발생하고 있습니다. 하단 튜닝 가이드의 CRITICAL 항목을 최우선으로 검토하십시오."
        elif has_high:
            status_class = "status-high"
            status_title = "성능 개선 권장 / HIGH"
            status_desc = "일부 노드에서 비효율적인 스캔이나 통계 정보 불일치가 확인되었습니다. 가이드의 오진 가능성과 예상 효과를 고려하여 단계적으로 개선을 적용하십시오."
        else:
            status_class = "status-good"
            status_title = "양호 / 모니터링"
            status_desc = "현재 옵티마이저가 수립한 실행 계획이 전반적으로 합리적입니다. 데이터 증가에 따른 추세 모니터링을 권장합니다."

        # 3. HTML 생성
        html_parts = []
        html_parts.append(cls._get_html_header(now_str))
        html_parts.append(cls._get_top_summary_bar(exec_time_str, plan_time_str, total_nodes, recs))
        html_parts.append(cls._get_overall_judgment_card(status_class, status_title, status_desc))

        if query:
            html_parts.append(cls._get_query_card(query))

        if formatted_tree:
            html_parts.append(cls._get_tree_card(formatted_tree))

        if nodes:
            html_parts.append(cls._get_steps_card(nodes))
            html_parts.append(cls._get_cardinality_card(nodes))
            html_parts.append(cls._get_buffer_card(root_plan, nodes))
            html_parts.append(cls._get_bottleneck_card(root_plan, nodes, exec_time))
            html_parts.append(cls._get_join_sort_partition_cards(nodes))

        html_parts.append(cls._get_recommendations_card(recs))
        html_parts.append(cls._get_html_footer())

        return "\n".join(html_parts)

    @classmethod
    def _get_html_header(cls, generated_time: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PostgreSQL 쿼리 실행계획 & 튜닝 분석 리포트</title>
    <style>
        :root {{
            --bg-body: #121314;
            --bg-card: #1c1d20;
            --bg-card-sub: #24262a;
            --bg-code: #161719;
            --border-color: #2f3238;
            --border-hover: #454952;
            --text-main: #e2e4e9;
            --text-muted: #959ba6;
            --text-dim: #6c727d;
            --accent-blue: #61afef;
            --accent-green: #98c379;
            --accent-orange: #ff9644;
            --accent-pink: #ff4b6e;
            --accent-gold: #e5c07b;
            --accent-purple: #c678dd;
        }}
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            background-color: var(--bg-body);
            color: var(--text-main);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Pretendard", sans-serif;
            line-height: 1.6;
            padding: 24px;
        }}
        .container {{
            max-width: 1240px;
            margin: 0 auto;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border-color);
            margin-bottom: 24px;
            flex-wrap: wrap;
            gap: 12px;
        }}
        .header-title {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .header-title h1 {{
            font-size: 20px;
            font-weight: 700;
            color: #ffffff;
            letter-spacing: -0.5px;
        }}
        .header-badge {{
            background: rgba(97, 175, 239, 0.15);
            color: var(--accent-blue);
            font-size: 11px;
            font-weight: 600;
            padding: 3px 8px;
            border-radius: 4px;
            border: 1px solid rgba(97, 175, 239, 0.3);
        }}
        .header-meta {{
            font-size: 12px;
            color: var(--text-muted);
        }}
        .top-stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .stat-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }}
        .stat-label {{
            font-size: 12px;
            color: var(--text-muted);
            font-weight: 500;
        }}
        .stat-value {{
            font-size: 22px;
            font-weight: 700;
            color: #ffffff;
            font-family: "Consolas", monospace;
        }}
        .card {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            margin-bottom: 24px;
            overflow: hidden;
            box-shadow: 0 4px 16px rgba(0,0,0,0.25);
        }}
        .card-header {{
            background: var(--bg-card-sub);
            padding: 14px 20px;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .card-header h2 {{
            font-size: 14px;
            font-weight: 700;
            color: var(--accent-blue);
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .card-body {{
            padding: 20px;
        }}
        pre.code-block {{
            background: var(--bg-code);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            padding: 16px;
            font-family: "Consolas", "Fira Code", monospace;
            font-size: 12px;
            color: #abb2bf;
            overflow-x: auto;
            white-space: pre-wrap;
            word-break: break-all;
        }}
        /* 배너 상태 */
        .status-optimal {{
            border-left: 4px solid var(--accent-green);
            background: rgba(152, 195, 121, 0.08);
        }}
        .status-high {{
            border-left: 4px solid var(--accent-orange);
            background: rgba(255, 150, 68, 0.08);
        }}
        .status-critical {{
            border-left: 4px solid var(--accent-pink);
            background: rgba(255, 75, 110, 0.08);
        }}
        .status-good {{
            border-left: 4px solid var(--accent-blue);
            background: rgba(97, 175, 239, 0.08);
        }}
        .status-title {{
            font-size: 16px;
            font-weight: 700;
            margin-bottom: 6px;
            color: #ffffff;
        }}
        .status-desc {{
            font-size: 13px;
            color: var(--text-main);
        }}
        /* 단계별 스텝 테이블 */
        .step-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}
        .step-table th, .step-table td {{
            padding: 12px 14px;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
        }}
        .step-table th {{
            background: var(--bg-card-sub);
            color: var(--text-muted);
            font-weight: 600;
            font-size: 12px;
        }}
        .step-table tr:hover td {{
            background: rgba(255, 255, 255, 0.02);
        }}
        .badge {{
            display: inline-block;
            font-size: 11px;
            font-weight: 700;
            padding: 2px 7px;
            border-radius: 4px;
            font-family: "Consolas", monospace;
        }}
        .badge-seq {{
            background: #2a2e35;
            color: #8fc7ff;
            border: 1px solid #3c4452;
        }}
        .badge-critical {{
            background: rgba(255, 75, 110, 0.15);
            color: var(--accent-pink);
            border: 1px solid rgba(255, 75, 110, 0.35);
        }}
        .badge-high {{
            background: rgba(255, 150, 68, 0.15);
            color: var(--accent-orange);
            border: 1px solid rgba(255, 150, 68, 0.35);
        }}
        .badge-warning {{
            background: rgba(229, 192, 123, 0.15);
            color: var(--accent-gold);
            border: 1px solid rgba(229, 192, 123, 0.35);
        }}
        .badge-info {{
            background: rgba(152, 195, 121, 0.15);
            color: var(--accent-green);
            border: 1px solid rgba(152, 195, 121, 0.35);
        }}
        .badge-conf {{
            background: #23272e;
            color: var(--accent-gold);
            border: 1px solid #3e4451;
            font-size: 10px;
        }}
        /* 튜닝 추천 카드 */
        .rec-item {{
            background: var(--bg-card-sub);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 16px;
        }}
        .rec-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 12px;
            flex-wrap: wrap;
            gap: 8px;
        }}
        .rec-title-group {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .rec-title {{
            font-size: 14px;
            font-weight: 700;
            color: #ffffff;
        }}
        .rec-grid {{
            display: grid;
            grid-template-columns: 140px 1fr;
            gap: 8px 12px;
            font-size: 13px;
        }}
        .rec-key {{
            color: var(--text-muted);
            font-weight: 600;
        }}
        .rec-val {{
            color: var(--text-main);
        }}
        .sql-box {{
            position: relative;
            background: var(--bg-code);
            border: 1px solid var(--border-color);
            border-radius: 6px;
            padding: 12px 14px;
            margin-top: 8px;
            font-family: "Consolas", monospace;
            font-size: 12px;
            color: #8fc7ff;
            word-break: break-all;
        }}
        .btn-copy {{
            position: absolute;
            top: 8px;
            right: 8px;
            background: #2c313a;
            border: 1px solid #3e4451;
            color: var(--text-muted);
            font-size: 11px;
            padding: 3px 8px;
            border-radius: 4px;
            cursor: pointer;
            transition: all 0.2s;
        }}
        .btn-copy:hover {{
            background: #3e4451;
            color: #ffffff;
        }}
        /* 프로그레스 바 */
        .bar-container {{
            width: 100%;
            height: 10px;
            background: #2c313a;
            border-radius: 5px;
            overflow: hidden;
            margin-top: 6px;
        }}
        .bar-fill {{
            height: 100%;
            border-radius: 5px;
            transition: width 0.3s;
        }}
        .btn-action {{
            background: #2f5c8f;
            color: #ffffff;
            border: none;
            padding: 6px 14px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
        }}
        .btn-action:hover {{
            background: #4173aa;
        }}
        @media print {{
            body {{
                background: #ffffff;
                color: #000000;
                padding: 0;
            }}
            .card {{
                box-shadow: none;
                border: 1px solid #cccccc;
                break-inside: avoid;
            }}
            .btn-action, .btn-copy {{
                display: none;
            }}
        }}
    </style>
</head>
<body>
<div class="container">
    <header class="header">
        <div class="header-title">
            <h1>🐘 PostgreSQL 쿼리 실행계획 & 튜닝 분석 리포트</h1>
            <span class="header-badge">PostgreSQL Analyzer</span>
        </div>
        <div style="display: flex; gap: 10px; align-items: center;">
            <span class="header-meta">생성 시각: {html.escape(generated_time)}</span>
            <button class="btn-action" onclick="window.print()">🖨️ 인쇄 / PDF 저장</button>
        </div>
    </header>
"""

    @classmethod
    def _get_top_summary_bar(
        cls,
        exec_time: str,
        plan_time: str,
        total_nodes: int,
        recs: list[RecommendationModel],
    ) -> str:
        crit_count = sum(1 for r in recs if r.severity == "CRITICAL")
        high_count = sum(1 for r in recs if r.severity == "HIGH")
        warn_count = sum(1 for r in recs if r.severity == "WARNING")
        rec_summary_str = f"총 {len(recs)}건"
        if crit_count > 0 or high_count > 0:
            rec_summary_str += f" (CRIT {crit_count}, HIGH {high_count})"

        return f"""
    <div class="top-stats">
        <div class="stat-card">
            <span class="stat-label">⚡ 총 실행 시간 (Execution Time)</span>
            <span class="stat-value" style="color: var(--accent-green);">{html.escape(exec_time)}</span>
        </div>
        <div class="stat-card">
            <span class="stat-label">⏱️ 계획 수립 시간 (Planning Time)</span>
            <span class="stat-value" style="color: var(--accent-blue);">{html.escape(plan_time)}</span>
        </div>
        <div class="stat-card">
            <span class="stat-label">🌳 플랜 트리 노드 수</span>
            <span class="stat-value">{total_nodes:,}개</span>
        </div>
        <div class="stat-card">
            <span class="stat-label">💡 튜닝 권장 가이드</span>
            <span class="stat-value" style="color: {"var(--accent-orange)" if (crit_count + high_count > 0) else "var(--accent-green)"};">{rec_summary_str}</span>
        </div>
    </div>
"""

    @classmethod
    def _get_overall_judgment_card(cls, status_class: str, title: str, desc: str) -> str:
        return f"""
    <div class="card {status_class}" style="margin-bottom: 24px;">
        <div class="card-body">
            <div class="status-title">📊 [종합 진단 평의] {html.escape(title)}</div>
            <div class="status-desc">{desc}</div>
        </div>
    </div>
"""

    @classmethod
    def _get_query_card(cls, query: str) -> str:
        return f"""
    <div class="card">
        <div class="card-header">
            <h2>✍️ 분석 대상 SQL 질의문</h2>
        </div>
        <div class="card-body">
            <pre class="code-block">{html.escape(query.strip())}</pre>
        </div>
    </div>
"""

    @classmethod
    def _get_tree_card(cls, formatted_tree: str) -> str:
        return f"""
    <div class="card">
        <div class="card-header">
            <h2>🌳 [2] PLAN DEPENDENCY TREE (실제 수립 계획 & 순서)</h2>
        </div>
        <div class="card-body">
            <pre class="code-block">{html.escape(formatted_tree.strip())}</pre>
        </div>
    </div>
"""

    @classmethod
    def _get_steps_card(cls, nodes: list[dict[str, Any]]) -> str:
        rows = []
        for n in nodes:
            seq = n.get("_line_number", 0)
            ntype = html.escape(n.get("Node Type", "Unknown"))
            rel = html.escape(n.get("Relation Name", ""))
            alias = html.escape(n.get("Alias", ""))
            target = rel
            if alias and alias != rel:
                target += f" ({alias})"

            act_time = n.get("Actual Total Time")
            act_startup = n.get("Actual Startup Time")
            act_rows = n.get("Actual Rows")
            act_loops = n.get("Actual Loops")

            if act_time is not None:
                startup_str = f"{act_startup:.3f}~" if act_startup is not None else ""
                time_str = f"{startup_str}{act_time:.3f} ms"
                if act_rows is not None:
                    loop_str = f" (x{act_loops})" if act_loops is not None else ""
                    rows_str = f"{act_rows:,}행{loop_str}"
                else:
                    plan_rows = n.get("Plan Rows")
                    rows_str = f"예상 {plan_rows:,}행" if plan_rows is not None else "-"
            else:
                cost_tot = n.get("Total Cost", 0.0)
                time_str = f"cost={cost_tot:.2f}" if cost_tot is not None else "-"
                plan_rows = n.get("Plan Rows")
                rows_str = f"예상 {plan_rows:,}행" if plan_rows is not None else "-"

            hit = n.get("Shared Hit Blocks", 0)
            read = n.get("Shared Read Blocks", 0)
            io_str = f"Hit {hit:,}" if hit > 0 else "-"
            if read > 0:
                io_str += f" / Read {read:,}"

            rows.append(f"""
                <tr>
                    <td><span class="badge badge-seq">[{seq:02d}]</span></td>
                    <td><strong>{ntype}</strong></td>
                    <td style="color: var(--accent-blue);">{target if target else "-"}</td>
                    <td style="font-family: monospace;">{time_str}</td>
                    <td style="font-family: monospace;">{rows_str}</td>
                    <td style="font-family: monospace; font-size: 11px;">{io_str}</td>
                </tr>
            """)

        return f"""
    <div class="card">
        <div class="card-header">
            <h2>📊 [3] EXECUTION DEPENDENCY STEPS (파이프라인 의존 및 준비 순서)</h2>
        </div>
        <div class="card-body" style="padding: 14px 20px 10px 20px; border-bottom: 1px solid var(--border-color); background: rgba(97, 175, 239, 0.03);">
            <p style="font-size: 12px; color: var(--text-muted); line-height: 1.5;">
                💡 <strong>안내</strong>: 이 순서는 상위 연산 처리에 필요한 <strong>선행 준비 순서(Dependency Order)</strong>이며, 실제 CPU 실행 순서가 아닙니다. 실제 엔진은 상위 노드의 요구(Pull)에 따라 노드 간에 스트리밍 방식으로 맞물려 동작합니다.
            </p>
        </div>
        <div class="card-body" style="padding: 0; overflow-x: auto;">
            <table class="step-table">
                <thead>
                    <tr>
                        <th style="width: 70px;">순서</th>
                        <th>연산자 타입 (Node Type)</th>
                        <th>대상 테이블 / 인덱스</th>
                        <th>소요 시간</th>
                        <th>처리 행수 (반복)</th>
                        <th>버퍼 I/O</th>
                    </tr>
                </thead>
                <tbody>
                    {"".join(rows)}
                </tbody>
            </table>
        </div>
    </div>
"""

    @classmethod
    def _get_cardinality_card(cls, nodes: list[dict[str, Any]]) -> str:
        mismatches = []
        for n in nodes:
            est = n.get("Plan Rows")
            act_r = n.get("Actual Rows")
            act_l = n.get("Actual Loops", 1)
            if est is not None and act_r is not None:
                tot_act = act_r * act_l
                ratio = max(est, tot_act) / max(min(est, tot_act), 1)
                if ratio >= 2.0:
                    seq = n.get("_line_number", 0)
                    ntype = html.escape(n.get("Node Type", ""))
                    rel = html.escape(n.get("Relation Name", ""))
                    badge_cls = (
                        "badge-critical"
                        if ratio >= 100
                        else ("badge-high" if ratio >= 10 else "badge-warning")
                    )
                    status_lbl = (
                        "추정 오차 관찰 (필요 시 ANALYZE 검토)" if ratio >= 10 else "경미한 오차"
                    )
                    target_str = f" ({rel})" if rel else ""
                    mismatches.append(f"""
                        <li style="margin-bottom: 8px;">
                            <span class="badge badge-seq">[{seq:02d}]</span>
                            <strong>{ntype}{target_str}</strong>: 추정 <code>{est:,}</code>행 vs 실측 <code>{tot_act:,}</code>행
                            &nbsp;<span class="badge {badge_cls}">오차 {ratio:.1f}배 ({status_lbl})</span>
                        </li>
                    """)

        body_content = (
            f"<ul style='list-style: none; padding-left: 0;'>{''.join(mismatches)}</ul>"
            if mismatches
            else "<p style='color: var(--accent-green);'>✅ 전체 노드에서 유의미한 카디널리티 추정 오차(2배 이상)가 감지되지 않았습니다.</p>"
        )

        return f"""
    <div class="card">
        <div class="card-header">
            <h2>⚖️ [4] CARDINALITY ANALYSIS (추정행 vs 실측행 오차)</h2>
        </div>
        <div class="card-body">
            {body_content}
        </div>
    </div>
"""

    @classmethod
    def _get_buffer_card(cls, root_plan: dict[str, Any] | None, nodes: list[dict[str, Any]]) -> str:
        hit = root_plan.get("Shared Hit Blocks", 0) if root_plan else 0
        read = root_plan.get("Shared Read Blocks", 0) if root_plan else 0
        tot = hit + read
        hit_rate = (hit / tot * 100) if tot > 0 else 100.0

        temp_read = sum(n.get("Temp Read Blocks", 0) for n in nodes)
        temp_written = sum(n.get("Temp Written Blocks", 0) for n in nodes)

        temp_str = (
            f"<span class='badge badge-critical'>⚠️ 임시 디스크 Spill: 쓰기 {temp_written:,} / 읽기 {temp_read:,} 블록</span>"
            if (temp_read > 0 or temp_written > 0)
            else "<span style='color: var(--accent-green);'>✅ 임시 파일 디스크 Spill 없음</span>"
        )

        return f"""
    <div class="card">
        <div class="card-header">
            <h2>💾 [5] BUFFER / I/O ANALYSIS</h2>
        </div>
        <div class="card-body">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                <span style="font-size: 13px; font-weight: 600;">Shared Buffer Hit Rate</span>
                <span style="font-size: 14px; font-weight: 700; color: var(--accent-green);">{hit_rate:.2f}% (Hit {hit:,} / Read {read:,} 블록)</span>
            </div>
            <div class="bar-container">
                <div class="bar-fill" style="width: {hit_rate}%; background: {"var(--accent-green)" if hit_rate >= 95 else "var(--accent-orange)"};"></div>
            </div>
            <div style="margin-top: 14px; font-size: 13px;">
                {temp_str}
            </div>
        </div>
    </div>
"""

    @classmethod
    def _get_bottleneck_card(
        cls,
        root_plan: dict[str, Any] | None,
        nodes: list[dict[str, Any]],
        exec_time: float | None,
    ) -> str:
        if not root_plan or exec_time is None or exec_time <= 0:
            return ""

        node_exclusives = []
        for n in nodes:
            act_tot = n.get("Actual Total Time")
            if act_tot is not None:
                child_sum = sum(
                    c.get("Actual Total Time", 0.0)
                    for c in n.get("Plans", [])
                    if c.get("Actual Total Time") is not None
                )
                exclusive = max(0.0, act_tot - child_sum)
                pct = (exclusive / exec_time) * 100 if exec_time > 0 else 0
                node_exclusives.append((exclusive, pct, n))

        node_exclusives.sort(key=lambda x: x[0], reverse=True)

        rows = []
        for exc, pct, n in node_exclusives[:4]:
            seq = n.get("_line_number", 0)
            ntype = html.escape(n.get("Node Type", ""))
            rel = html.escape(n.get("Relation Name", ""))
            target = f" ({rel})" if rel else ""
            badge = (
                "<span class='badge badge-critical'>주요 병목 (≥30%)</span>" if pct >= 30.0 else ""
            )

            rows.append(f"""
                <div style="margin-bottom: 12px;">
                    <div style="display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 4px;">
                        <span><span class="badge badge-seq">[{seq:02d}]</span> <strong>{ntype}{target}</strong> {badge}</span>
                        <span><strong>{exc:.3f} ms</strong> ({pct:.1f}%)</span>
                    </div>
                    <div class="bar-container">
                        <div class="bar-fill" style="width: {min(100.0, pct)}%; background: {"var(--accent-pink)" if pct >= 30 else "var(--accent-blue)"};"></div>
                    </div>
                </div>
            """)

        return f"""
    <div class="card">
        <div class="card-header">
            <h2>⏱️ [6] PERFORMANCE & BOTTLENECK ANALYSIS (독점 실행 시간 점유율)</h2>
        </div>
        <div class="card-body">
            {"".join(rows)}
        </div>
    </div>
"""

    @classmethod
    def _get_join_sort_partition_cards(cls, nodes: list[dict[str, Any]]) -> str:
        # Join
        joins = [n for n in nodes if "Join" in n.get("Node Type", "")]
        join_html = []
        for j in joins:
            seq = j.get("_line_number", 0)
            ntype = html.escape(j.get("Node Type", ""))
            jtype = html.escape(j.get("Join Type", "Inner"))
            cond = html.escape(
                j.get("Hash Cond") or j.get("Join Filter") or j.get("Merge Cond") or "조건 없음"
            )
            join_html.append(
                f"<li><span class='badge badge-seq'>[{seq:02d}]</span> <strong>{ntype}</strong>: <code>{cond}</code></li>"
            )

        # Sort
        sorts = [n for n in nodes if "Sort" in n.get("Node Type", "")]
        sort_html = []
        for s in sorts:
            seq = s.get("_line_number", 0)
            method = html.escape(s.get("Sort Method", "quicksort"))
            space = s.get("Sort Space Used", 0)
            stype = html.escape(s.get("Sort Space Type", "Memory"))
            sort_html.append(
                f"<li><span class='badge badge-seq'>[{seq:02d}]</span> <strong>{html.escape(s.get('Node Type', 'Sort'))}</strong>: {method} ({space}kB {stype})</li>"
            )

        return f"""
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 16px; margin-bottom: 24px;">
        <div class="card" style="margin-bottom: 0;">
            <div class="card-header">
                <h2>🤝 [7] JOIN ANALYSIS</h2>
            </div>
            <div class="card-body" style="font-size: 13px;">
                {'<ul style="list-style: none; padding-left: 0;">' + "".join(join_html) + "</ul>" if join_html else '<p style="color: var(--text-muted);">조인 연산이 없습니다.</p>'}
            </div>
        </div>
        <div class="card-card" style="background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 10px; overflow: hidden;">
            <div class="card-header">
                <h2>🔄 [8] SORT & WINDOW ANALYSIS</h2>
            </div>
            <div class="card-body" style="font-size: 13px;">
                {'<ul style="list-style: none; padding-left: 0;">' + "".join(sort_html) + "</ul>" if sort_html else '<p style="color: var(--text-muted);">정렬 연산이 없습니다.</p>'}
            </div>
        </div>
    </div>
"""

    @classmethod
    def _get_recommendations_card(cls, recs: list[RecommendationModel]) -> str:
        if not recs:
            return """
    <div class="card">
        <div class="card-header">
            <h2>💡 [10] KNOWLEDGE-BASED TUNING RECOMMENDATIONS</h2>
        </div>
        <div class="card-body" style="color: var(--accent-green); font-size: 14px;">
            ✅ 현재 옵티마이저가 수립한 실행 계획상 병목 구간이나 인덱스 누락이 감지되지 않는 최상의 플랜입니다.
        </div>
    </div>
"""

        items = []
        for idx, rec in enumerate(recs, 1):
            sev = rec.severity
            badge_cls = (
                "badge-critical"
                if sev == "CRITICAL"
                else (
                    "badge-high"
                    if sev == "HIGH"
                    else ("badge-warning" if sev == "WARNING" else "badge-info")
                )
            )

            lines_str = ""
            if rec.plan_lines:
                lines_str = f"[{', '.join(map(str, rec.plan_lines))}]"
            elif rec.plan_line is not None:
                lines_str = f"[{rec.plan_line}]"
            loc_str = f"{lines_str} [{rec.plan_node}]" if lines_str else (rec.plan_node or "-")

            sql_block = ""
            if rec.recommended_sql:
                safe_sql = html.escape(rec.recommended_sql.strip())
                sql_id = f"sql_box_{idx}"
                sql_block = f"""
                    <div class="sql-box">
                        <button class="btn-copy" onclick="copySql('{sql_id}')">📋 복사</button>
                        <div id="{sql_id}">{safe_sql}</div>
                    </div>
                """

            items.append(f"""
                <div class="rec-item">
                    <div class="rec-header">
                        <div class="rec-title-group">
                            <span class="badge {badge_cls}">[{sev}]</span>
                            <span class="badge badge-conf">신뢰도: {html.escape(rec.confidence)}</span>
                            <span class="rec-title">#{idx} {html.escape(rec.title)}</span>
                        </div>
                        <span style="font-size: 11px; color: var(--text-dim); font-family: monospace;">{html.escape(rec.rule_id or "")}</span>
                    </div>
                    <div class="rec-grid">
                        <span class="rec-key">📍 위치:</span>
                        <span class="rec-val"><code>{html.escape(loc_str)}</code></span>
                        <span class="rec-key">🔍 현상 및 원인:</span>
                        <span class="rec-val">{html.escape(rec.reason)}</span>
                        <span class="rec-key">🛠️ 조치 가이드:</span>
                        <span class="rec-val">{html.escape(rec.recommendation)}</span>
                        {f'<span class="rec-key">📈 예상 효과:</span><span class="rec-val">{html.escape(rec.estimated_gain)}</span>' if rec.estimated_gain else ""}
                        {f'<span class="rec-key">⚠️ 오진 가능성:</span><span class="rec-val">{html.escape(rec.false_positive_risk)}</span>' if rec.false_positive_risk else ""}
                    </div>
                    {sql_block}
                </div>
            """)

        return f"""
    <div class="card">
        <div class="card-header">
            <h2>💡 [10] KNOWLEDGE-BASED TUNING RECOMMENDATIONS (지식 기반 권장 가이드)</h2>
        </div>
        <div class="card-body">
            {"".join(items)}
        </div>
    </div>
"""

    @classmethod
    def _get_html_footer(cls) -> str:
        return """
    <footer style="text-align: center; font-size: 12px; color: var(--text-dim); padding: 24px 0 12px;">
        PostgreSQL Performance Tuner • Educational Query Plan & Tuning Report
    </footer>
</div>
<script>
function copySql(elementId) {
    var el = document.getElementById(elementId);
    if (!el) return;
    var text = el.innerText || el.textContent;
    navigator.clipboard.writeText(text).then(function() {
        alert('추천 SQL이 클립보드에 복사되었습니다:\\n' + text);
    }).catch(function(err) {
        prompt('Ctrl+C를 눌러 복사하세요:', text);
    });
}
</script>
</body>
</html>
"""
