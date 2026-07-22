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
import sqlglot
from psycopg import sql
from psycopg.rows import dict_row
from pydantic import BaseModel, Field
from sqlglot import exp

# ==========================================
# 1. 구조화된 데이터 모델 (Pydantic v2)
# ==========================================


class IndexMetadata(BaseModel):
    index_name: str
    columns: List[str]
    is_unique: bool


class TableMetadata(BaseModel):
    table_name: str
    total_rows: int
    indices: List[IndexMetadata]

    @property
    def indexed_columns(self) -> List[str]:
        cols = []
        for idx in self.indices:
            cols.extend(idx.columns)
        return list(set(cols))

    def find_usable_index_for_cols(self, query_cols: List[str]) -> Optional[IndexMetadata]:
        """
        제공된 질의 컬럼 중 하나라도 복합 인덱스의 첫 번째 선행 컬럼(Prefix)에
        매칭되는지 검사하여 복합/단일 인덱스 활용 가능 여부를 엄격하게 판별합니다.
        """
        if not query_cols:
            return None
        query_cols_set = {c.lower().strip() for c in query_cols}
        for idx in self.indices:
            if idx.columns:
                first_col = idx.columns[0].lower().strip()
                if first_col in query_cols_set:
                    return idx
        return None


class TuningRecommendation(BaseModel):
    node_type: str = Field(..., description="문제가 발생한 실행 계획 노드")
    severity: str = Field(..., description="위험도 (CRITICAL / WARNING / INFO)")
    issue: str = Field(..., description="성능 이슈 상세 설명")
    solution: str = Field(..., description="구체적인 해결 방안")
    recommended_sql: Optional[str] = Field(default=None, description="즉시 실행 가능한 권장 SQL")


# ==========================================
# 2. 메타데이터 제공자 (System Catalog Reader)
# ==========================================


class PGMetadataProvider:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def get_table_metadata(self, table_name: str) -> TableMetadata:
        row_count_query = """
            SELECT c.reltuples::bigint AS row_count
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = %s
              AND c.relkind = 'r'
              AND n.nspname = ANY(current_schemas(false));
        """
        index_query = """
            SELECT
                i.relname AS index_name,
                ix.indisunique AS is_unique,
                array_to_json(array_agg(a.attname ORDER BY k.n)) AS index_keys
            FROM pg_index ix
            JOIN pg_class t ON t.oid = ix.indrelid
            JOIN pg_class i ON i.oid = ix.indexrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            CROSS JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, n)
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
            WHERE t.relname = %s
              AND n.nspname = ANY(current_schemas(false))
            GROUP BY i.relname, ix.indisunique;
        """
        total_rows = 0
        indices: List[IndexMetadata] = []
        target_table = table_name.lower().strip()

        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(row_count_query, (target_table,))
            row_res = cur.fetchone()
            if row_res:
                total_rows = max(0, row_res["row_count"])

            cur.execute(index_query, (target_table,))
            for row in cur.fetchall():
                raw_keys = row["index_keys"]
                columns = json.loads(raw_keys) if isinstance(raw_keys, str) else raw_keys
                columns = [c.lower() for c in columns if c] if columns else []
                indices.append(
                    IndexMetadata(
                        index_name=row["index_name"], columns=columns, is_unique=row["is_unique"]
                    )
                )

        return TableMetadata(table_name=target_table, total_rows=total_rows, indices=indices)


# ==========================================
# 3. SQL AST Parser & Explain Analyzer
# ==========================================


class PGPlanAnalyzer:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    @staticmethod
    def clean_query_comments(query: str) -> str:
        query = re.sub(r"/\*.*?\*/", "", query, flags=re.DOTALL)
        lines = query.split("\n")
        clean_lines = []
        for line in lines:
            line_without_comment = re.sub(r"--.*$", "", line)
            if line_without_comment.strip():
                clean_lines.append(line_without_comment)
        return "\n".join(clean_lines).strip()

    def execute_explain_json(self, query: str) -> List[Dict[str, Any]]:
        clean_sql = self.clean_query_comments(query)
        # 안전성 검토 반영: 실제 데이터를 변조하거나 비정상 부하를 거는 DML/DDL 사전에 완전 차단
        if re.match(
            r"^\s*(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE)\b", clean_sql, re.IGNORECASE
        ):
            raise ValueError(
                "안전 제한: DML 또는 DDL 구문은 EXPLAIN ANALYZE 성능 분석을 임의로 수행할 수 없습니다."
            )

        explain_query = sql.SQL("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {}").format(
            sql.SQL(clean_sql)
        )
        with self.conn.cursor() as cur:
            cur.execute("SET statement_timeout = 10000;")
            cur.execute(explain_query)
            plan_data = cur.fetchone()[0]  # type:ignore
            if isinstance(plan_data, str):
                return json.loads(plan_data)
            return plan_data

    def execute_explain_text(self, query: str) -> str:
        clean_sql = self.clean_query_comments(query)
        explain_query = sql.SQL("EXPLAIN {}").format(sql.SQL(clean_sql))
        with self.conn.cursor() as cur:
            cur.execute("SET statement_timeout = 10000;")
            cur.execute(explain_query)
            rows = cur.fetchall()
            return "\n".join([row[0] for row in rows])

    @staticmethod
    def extract_right_value_from_ast(sql_query: str, target_col: str) -> str:
        clean_sql = PGPlanAnalyzer.clean_query_comments(sql_query)
        try:
            parsed_tree = sqlglot.parse_one(clean_sql, read="postgres")
            for eq_node in parsed_tree.find_all(exp.EQ):
                left = eq_node.left
                right = eq_node.right
                if any(
                    col.this.name.lower().strip() == target_col.lower()
                    for col in left.find_all(exp.Column)
                ):
                    if isinstance(right, exp.Literal):
                        return f"'{right.this}'" if right.is_string else str(right.this)
                    return str(right)
        except Exception:
            pass
        return "'ACTIVE'"

    @staticmethod
    def extract_date_literal_from_ast(sql_query: str, target_col: str) -> str:
        clean_sql = PGPlanAnalyzer.clean_query_comments(sql_query)
        try:
            parsed_tree = sqlglot.parse_one(clean_sql, read="postgres")
            for eq_node in parsed_tree.find_all(exp.EQ):
                left = eq_node.left
                right = eq_node.right
                if any(
                    col.this.name.lower().strip() == target_col.lower()
                    for col in left.find_all(exp.Column)
                ):
                    if isinstance(right, exp.Literal):
                        date_match = re.search(r"\d{4}-\d{2}-\d{2}", str(right.this))
                        if date_match:
                            return date_match.group(0)
        except Exception:
            pass

        escaped_col = re.escape(target_col)
        pattern = rf"{escaped_col}\s*\)?\s*=\s*['\"](\d{{4}}-\d{{2}}-\d{{2}})['\"]"
        match = re.search(pattern, clean_sql, re.IGNORECASE)
        if match:
            return match.group(1)
        return "2026-05-15"

    @staticmethod
    def check_front_wildcard_like(sql_query: str, target_col: str) -> bool:
        """LIKE 조건 절에서 % 문자가 맨 앞에 배치되었는지 검사합니다."""
        clean_sql = PGPlanAnalyzer.clean_query_comments(sql_query)
        try:
            parsed_tree = sqlglot.parse_one(clean_sql, read="postgres")
            for like_node in parsed_tree.find_all(exp.Like):
                left = like_node.left
                right = like_node.expression
                if any(
                    col.this.name.lower().strip() == target_col.lower()
                    for col in left.find_all(exp.Column)
                ):
                    val = str(right.this) if isinstance(right, exp.Literal) else str(right)
                    if val.startswith("%") or val.startswith("'%"):
                        return True
        except Exception:
            pass

        # SQLGlot 파싱 실패 대비 Fallback 정규식 탐지 필터
        pattern = rf"{re.escape(target_col)}\s+like\s+['\"]%"
        if re.search(pattern, clean_sql, re.IGNORECASE):
            return True
        return False

    @staticmethod
    def extract_columns_via_ast_ordered(
        sql_query: str, target_table: str
    ) -> Tuple[List[str], List[str], bool, bool]:
        """
        WHERE 조건절 컬럼과 JOIN/GROUP BY 절의 매핑 컬럼을 정밀하게 분리 추출합니다.
        실패할 경우, 정규식 기반의 Fallback 루틴으로 복원합니다.
        """
        clean_sql = PGPlanAnalyzer.clean_query_comments(sql_query)
        target_table_lower = target_table.lower().strip()

        try:
            parsed_tree = sqlglot.parse_one(clean_sql, read="postgres")
            alias_map = {}
            all_tables = []
            for table_node in parsed_tree.find_all(exp.Table):
                table_real_name = table_node.name.lower()
                table_alias = table_node.alias.lower()
                all_tables.append(table_real_name)
                if table_alias:
                    alias_map[table_alias] = table_real_name
                else:
                    alias_map[table_real_name] = table_real_name

            has_multiple_tables = len(set(all_tables)) > 1

            def is_target_column(col_node) -> bool:
                col_table_alias = col_node.table.lower()
                if col_table_alias:
                    resolved_table = alias_map.get(col_table_alias, col_table_alias)
                    return resolved_table == target_table_lower
                else:
                    return not has_multiple_tables

            has_or_condition = any(parsed_tree.find_all(exp.Or))
            where_columns = set()
            join_group_columns = set()

            # 1. WHERE 절 내부 필터 컬럼 추적
            for where_clause in parsed_tree.find_all(exp.Where):
                for col in where_clause.find_all(exp.Column):
                    col_name = col.this.name.lower().strip()
                    if col_name != "*" and is_target_column(col):
                        where_columns.add(col_name)

            # 2. JOIN / GROUP / ORDER 절 내부 매핑 컬럼 추적
            for clause in parsed_tree.find_all((exp.Join, exp.Group, exp.Order)):
                for col in clause.find_all(exp.Column):
                    col_name = col.this.name.lower().strip()
                    if col_name != "*" and is_target_column(col):
                        if col_name not in where_columns:
                            join_group_columns.add(col_name)

            return list(where_columns), list(join_group_columns), True, has_or_condition

        except Exception:
            # Fallback 정규식 복원 루틴
            where_cols = []
            join_cols = []
            where_match = re.search(
                r"where\s+(.*?)(?:group\s+by|order\s+by|limit|$)",
                clean_sql,
                re.IGNORECASE | re.DOTALL,
            )
            if where_match:
                where_clause = where_match.group(1)
                candidates = re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b", where_clause)
                keywords = {
                    "and",
                    "or",
                    "in",
                    "is",
                    "null",
                    "not",
                    "between",
                    "like",
                    "true",
                    "false",
                }
                where_cols = list({c.lower() for c in candidates if c.lower() not in keywords})

            has_or = bool(re.search(r"\bor\b", clean_sql, re.IGNORECASE))
            return where_cols, join_cols, True, has_or

    def find_problematic_nodes(self, plan_node: Dict[str, Any]) -> List[Dict[str, Any]]:
        nodes = []
        node_type = plan_node.get("Node Type")
        if node_type in ["Seq Scan", "Sort", "Hash Join", "Nested Loop", "Index Scan"]:
            nodes.append(plan_node)
        if "Plans" in plan_node:
            for sub_plan in plan_node["Plans"]:
                nodes.extend(self.find_problematic_nodes(sub_plan))
        return nodes


# ==========================================
# 4. 정밀 튜닝 휴리스틱 엔진
# ==========================================


class PGHeuristicEngine:
    def __init__(self, metadata_provider: PGMetadataProvider):
        self.metadata_provider = metadata_provider

    def generate_recommendations(
        self, node: Dict[str, Any], raw_sql: str
    ) -> List[TuningRecommendation]:
        recommendations = []
        node_type = node.get("Node Type")

        # --- [기존 룰 1] 디스크 I/O 병목 감지 ---
        temp_write = node.get("Temp Written Blocks", 0)
        if temp_write > 0:
            recommendations.append(
                TuningRecommendation(
                    node_type=node_type,
                    severity="WARNING",
                    issue=f"임시 파일 쓰기 발생 ({temp_write * 8} KB). 메모리 공간 부족으로 디스크 I/O 병목이 발생하고 있습니다.",
                    solution="작업 메모리(work_mem) 설정을 상향 조정하거나 대용량 조인 범위를 최적화하십시오.",
                    recommended_sql="SET work_mem = '64MB';",
                )
            )

        # --- [룰 A] Gather 포함 모든 병렬 스캔 메모리 병목 감지 ---
        if "Parallel" in str(node_type) or "Gather" in str(node_type):
            workers = node.get("Workers Planned", 0)
            if workers >= 4:
                recommendations.append(
                    TuningRecommendation(
                        node_type=node_type,
                        severity="WARNING",
                        issue=f"많은 수의 병렬 워커({workers}명)가 할당되었습니다. 세션당 중복 메모리가 할당되어 전체 시스템 OOM 위험이 가중됩니다.",
                        solution="복잡한 집계 분석이 아니라면 병렬 워커 개수를 기본값(2개)으로 조율해 리소스 소모를 제어하십시오.",
                        recommended_sql="SET max_parallel_workers_per_gather = 2;",
                    )
                )

        # --- [기존 룰 2] Seq Scan 정밀 진단 ---
        if node_type == "Seq Scan":
            table_name = node.get("Relation Name")
            actual_rows = node.get("Actual Rows", 0)

            if table_name:
                meta = self.metadata_provider.get_table_metadata(table_name)
                where_cols, join_group_cols, parse_success, has_or = (
                    PGPlanAnalyzer.extract_columns_via_ast_ordered(raw_sql, table_name)
                )

                if not parse_success:
                    recommendations.append(
                        TuningRecommendation(
                            node_type=node_type,
                            severity="INFO",
                            issue=f"'{table_name}' 테이블에 Seq Scan이 감지되었으나, 정적 AST 파싱 분석에 한계가 발생했습니다.",
                            solution="조건절 컬럼의 인덱스 상태를 수동 검증해주십시오.",
                        )
                    )
                    return recommendations

                # 소규모 테이블 필터 최우선 적용
                if meta.total_rows < 1000:
                    recommendations.append(
                        TuningRecommendation(
                            node_type=node_type,
                            severity="INFO",
                            issue=f"'{table_name}' 테이블은 통계 데이터 건수({meta.total_rows}건)가 적은 소형 테이블입니다.",
                            solution="PostgreSQL 옵티마이저는 오버헤드를 막기 위해 의도적으로 풀 스캔을 선택한 것이므로 정상적인 상태입니다.",
                        )
                    )
                    return recommendations

                # --- 2-A. WHERE 필터 조건절 컬럼이 존재하는 경우 ---
                if where_cols:
                    if has_or:
                        recommendations.append(
                            TuningRecommendation(
                                node_type=node_type,
                                severity="WARNING",
                                issue=f"'{table_name}' 테이블의 조건절 내부에서 'OR' 연산자가 감지되었습니다.",
                                solution="OR 조건 양측의 각 필터 컬럼에 개별 단일 인덱스를 구축하여 옵티마이저가 Bitmap Or Scan을 타도록 유도하십시오.\n※ 주의: CONCURRENTLY 인덱스 생성은 트랜잭션 블록 외부(autocommit=True 상태)에서 수행되어야 합니다.",
                                recommended_sql="\n".join(
                                    [
                                        f"CREATE INDEX CONCURRENTLY idx_{table_name}_{col} ON {table_name} ({col});"
                                        for col in where_cols[:3]
                                    ]
                                ),
                            )
                        )

                    # [룰 B] LIKE 전방 와일드카드 전용 매칭 진단
                    front_wildcard_detected = False
                    for col in where_cols:
                        if PGPlanAnalyzer.check_front_wildcard_like(raw_sql, col):
                            front_wildcard_detected = True
                            recommendations.append(
                                TuningRecommendation(
                                    node_type=node_type,
                                    severity="CRITICAL",
                                    issue=f"'{table_name}' 테이블의 조건절 컬럼({col})에 전방 와일드카드(예: LIKE '%키워드') 매칭이 선언되어 일반 B-Tree 인덱스가 완전히 무력화되었습니다.",
                                    solution="전방/부분 일치 검색 성능 향상을 위해 'pg_trgm' 확장 모듈을 활성화하고 GIN 트라이그램 인덱스를 생성하십시오.\n※ 주의: CONCURRENTLY 인덱스 생성은 트랜잭션 블록 외부에서 수행해야 합니다.",
                                    recommended_sql=f"CREATE EXTENSION IF NOT EXISTS pg_trgm;\nCREATE INDEX CONCURRENTLY idx_{table_name}_{col}_trgm ON {table_name} USING gin ({col} gin_trgm_ops);",
                                )
                            )
                            break

                    suppressed_detected = False
                    if not front_wildcard_detected:
                        for col in where_cols:
                            pattern = (
                                r"\b(\w+)\(\s*(?:\w+\.)?" + re.escape(col) + r"\s*(?:,\s*.*?)?\)"
                            )
                            match = re.search(pattern, raw_sql, re.IGNORECASE)

                            if match:
                                suppressed_detected = True
                                func_name = match.group(1).upper()
                                rec_sql = ""
                                if func_name in ["UPPER", "LOWER"]:
                                    real_val = PGPlanAnalyzer.extract_right_value_from_ast(
                                        raw_sql, col
                                    )
                                    raw_val_stripped = real_val.strip("'\"")
                                    processed_val = real_val
                                    if func_name == "UPPER":
                                        processed_val = f"'{raw_val_stripped.upper()}'"
                                    elif func_name == "LOWER":
                                        processed_val = f"'{raw_val_stripped.lower()}'"

                                    rec_sql = (
                                        f"-- 방법 1: 조건식 우변 가공을 통한 인덱스 활용 (가장 권장)\n"
                                        f"-- WHERE {col} = {processed_val};\n\n"
                                        f"-- 방법 2: 함수 기반 인덱스(Functional Index) 생성 (트랜잭션 외부 수행 권장)\n"
                                        f"CREATE INDEX CONCURRENTLY idx_{table_name}_{col}_{func_name.lower()} "
                                        f"ON {table_name} ({func_name}({col}));"
                                    )
                                elif func_name in ["DATE", "TRUNC"]:
                                    extracted_date = PGPlanAnalyzer.extract_date_literal_from_ast(
                                        raw_sql, col
                                    )
                                    try:
                                        start_dt = datetime.strptime(extracted_date, "%Y-%m-%d")
                                        end_dt = start_dt + timedelta(days=1)
                                        start_str = start_dt.strftime("%Y-%m-%d 00:00:00")
                                        end_str = end_dt.strftime("%Y-%m-%d 00:00:00")
                                    except Exception:
                                        start_str = "2026-05-15 00:00:00"
                                        end_str = "2026-05-16 00:00:00"

                                    rec_sql = (
                                        f"-- 방법 1: 범위 조건식(Between)으로 우변 변경 (가장 권장)\n"
                                        f"-- WHERE {col} >= '{start_str}' AND {col} < '{end_str}'\n\n"
                                        f"-- 방법 2: 함수 기반 인덱스(Functional Index) 생성 (트랜잭션 외부 수행 권장)\n"
                                        f"CREATE INDEX CONCURRENTLY idx_{table_name}_{col}_date "
                                        f"ON {table_name} (CAST({col} AS date));"
                                    )
                                else:
                                    rec_sql = (
                                        f"-- 방법 1: 좌변 가공 회피하도록 쿼리 수정\n\n"
                                        f"-- 방법 2: 함수 기반 인덱스(Functional Index) 생성 (트랜잭션 외부 수행 권장)\n"
                                        f"CREATE INDEX CONCURRENTLY idx_{table_name}_{col}_func "
                                        f"ON {table_name} ({func_name}({col}));"
                                    )

                                recommendations.append(
                                    TuningRecommendation(
                                        node_type=node_type,
                                        severity="CRITICAL",
                                        issue=f"'{table_name}' 테이블의 인덱스 컬럼({col})이 WHERE 조건절 내부에서 {func_name}() 함수로 감싸져 좌변이 가공(Index Suppress)되었습니다.",
                                        solution=f"인덱스 컬럼 원본이 가공 없이 노출되도록 조건식 우변을 변경하거나, 해당 {func_name}() 함수가 그대로 들어간 '함수 기반 인덱스(Functional Index)'를 설계하십시오.",
                                        recommended_sql=rec_sql,
                                    )
                                )
                                break

                    if not suppressed_detected and not front_wildcard_detected:
                        unindexed_cols = [
                            col for col in where_cols if col not in meta.indexed_columns
                        ]
                        usable_index = meta.find_usable_index_for_cols(where_cols)

                        if unindexed_cols:
                            recommendations.append(
                                TuningRecommendation(
                                    node_type=node_type,
                                    severity="CRITICAL",
                                    issue=f"'{table_name}' 테이블에 전체 스캔 발생. 조건절 필수 필터 컬럼 {unindexed_cols}에 인덱스가 전혀 구성되어 있지 않습니다.",
                                    solution="테이블 스캔 비용을 줄이기 위해 등가(=) 조건 컬럼을 선두로 구성한 복합 인덱스를 무중단(CONCURRENTLY) 방식으로 생성하십시오.\n※ 주의: CONCURRENTLY 인덱스 생성은 트랜잭션 블록 외부에서 수행해야 합니다.",
                                    recommended_sql=f"CREATE INDEX CONCURRENTLY idx_{table_name}_{'_'.join(unindexed_cols)} ON {table_name} ({', '.join(unindexed_cols)});",
                                )
                            )
                        elif not usable_index:
                            recommendations.append(
                                TuningRecommendation(
                                    node_type=node_type,
                                    severity="CRITICAL",
                                    issue=f"'{table_name}' 테이블의 조건절 컬럼 {where_cols}은 기존 인덱스에 존재하지만, 복합 인덱스의 선두(첫 번째) 컬럼이 조건절에 빠져있어 인덱스 스캔을 활용하지 못하고 있습니다.",
                                    solution="현재 쿼리의 필터 조건 컬럼을 맨 앞 순서로 배치하는 최적화된 신규 인덱스를 설계하여 인덱스 풀 스캔 비용을 상쇄하십시오.\n※ 주의: CONCURRENTLY 인덱스 생성은 트랜잭션 블록 외부에서 수행해야 합니다.",
                                    recommended_sql=f"CREATE INDEX CONCURRENTLY idx_{table_name}_{'_'.join(where_cols)} ON {table_name} ({', '.join(where_cols)});",
                                )
                            )
                        elif meta.total_rows > 10000:
                            selectivity = (
                                (actual_rows / meta.total_rows) if meta.total_rows > 0 else 1.0
                            )
                            if selectivity < 0.1:
                                recommendations.append(
                                    TuningRecommendation(
                                        node_type=node_type,
                                        severity="WARNING",
                                        issue=f"대용량 테이블에서 낮은 반환율({selectivity:.1%})임에도 풀 스캔이 선택되었습니다.",
                                        solution="인덱스는 존재하나 옵티마이저가 수집 정보를 잘못 파악해 회피 중입니다. 테이블 통계 수집 데이터(Statistics)를 갱신하십시오.",
                                        recommended_sql=f"ANALYZE VERBOSE {table_name};",
                                    )
                                )

                # --- 2-B. WHERE 절은 없으나 JOIN ON / GROUP BY 등으로 인해 풀 스캔된 경우 ---
                elif join_group_cols:
                    usable_index = meta.find_usable_index_for_cols(join_group_cols)

                    if usable_index:
                        recommendations.append(
                            TuningRecommendation(
                                node_type=node_type,
                                severity="INFO",
                                issue=f"'{table_name}' 테이블의 그룹화/조인 연산 대상 컬럼 중 선행 키가 포함된 인덱스 '{usable_index.index_name}'(구성: {usable_index.columns})가 이미 테이블에 존재합니다.",
                                solution="해시 조인(Hash Join) 처리를 위해 전체 메모리에 테이블 데이터를 올리거나, 소규모 그룹화(GROUP BY)를 위해 옵티마이저가 비용 기반 모델에 따라 의도적으로 풀 스캔을 선택한 정상 상태입니다. 통계 정보 왜곡 가능성을 배제하기 위해 ANALYZE를 적용해볼 수 있습니다.",
                                recommended_sql=f"ANALYZE VERBOSE {table_name};",
                            )
                        )
                    else:
                        recommendations.append(
                            TuningRecommendation(
                                node_type=node_type,
                                severity="WARNING",
                                issue=f"'{table_name}' 테이블이 조인 결합(JOIN) 또는 정렬 집계(GROUP BY) 연산을 위해 풀 스캔되었습니다. 현재 매핑 컬럼을 지원하는 적절한 인덱스가 감지되지 않습니다.",
                                solution="대량의 데이터를 해시 결합하거나 중복을 제거할 때 인덱스가 유용하므로, 조인 키 또는 그룹화 선두 컬럼에 인덱스 생성을 고려하십시오.\n※ 주의: CONCURRENTLY 인덱스 생성은 트랜잭션 외부에서 수행해야 합니다.",
                                recommended_sql=f"CREATE INDEX CONCURRENTLY idx_{table_name}_{join_group_cols[0]} ON {table_name} ({join_group_cols[0]});",
                            )
                        )

        # --- [룰 C] Index Scan 비효율성 검증 룰 ---
        elif node_type == "Index Scan":
            table_name = node.get("Relation Name")
            actual_rows = node.get("Actual Rows", 0)
            if table_name:
                meta = self.metadata_provider.get_table_metadata(table_name)
                if meta.total_rows > 50000 and actual_rows > (meta.total_rows * 0.25):
                    recommendations.append(
                        TuningRecommendation(
                            node_type=node_type,
                            severity="WARNING",
                            issue=f"'{table_name}' 테이블에서 인덱스 스캔을 수행 중이나, 반환 데이터량({actual_rows}건)이 전체 행의 25%를 초과하여 대규모 랜덤 I/O 병목을 유발하고 있습니다.",
                            solution="테이블 페이지 랜덤 접근 오버헤드를 줄이기 위해 Bitmap Index Scan으로 강제 전환되도록 유도하거나, 커버링 인덱스(INCLUDE 절) 구성 또는 클러스터링을 검토하십시오.",
                            recommended_sql=f"ANALYZE VERBOSE {table_name};",
                        )
                    )

        # --- [기존 룰 3] 정렬(Sort) 최적화 ---
        elif node_type == "Sort":
            sort_method = node.get("Sort Method", "")
            if "external" in sort_method.lower():
                actual_kb = node.get("Sort Space Used", 0)
                recommendations.append(
                    TuningRecommendation(
                        node_type=node_type,
                        severity="WARNING",
                        issue=f"정렬 버퍼 용량 한계로 디스크 정렬(External Sort) 진행 중. (사용 공간: {actual_kb} KB)",
                        solution="정렬 작업 영역 메모리(work_mem)를 세션 단위로 임시 상향하십시오.",
                        recommended_sql="SET work_mem = '64MB';",
                    )
                )
            elif "top-n" not in sort_method.lower() and "quicksort" in sort_method.lower():
                if re.search(r"\bLIMIT\b", raw_sql, re.IGNORECASE):
                    recommendations.append(
                        TuningRecommendation(
                            node_type=node_type,
                            severity="WARNING",
                            issue="LIMIT 조건이 존재하나 인덱스 순서 스캔 정렬이 지원되지 않아 대형 정렬 연산(Quicksort)이 과도하게 유발되고 있습니다.",
                            solution="ORDER BY 대상 정렬 조건 컬럼에 순서 정합성을 살린 최적의 인덱스를 추가하여 정렬 비용 자체를 회피(Index Scan Ordering)시키십시오.",
                        )
                    )

        # --- [룰 D] Hash Join 디스크 스필(Spill) 감지 룰 ---
        elif node_type == "Hash Join":
            hash_batches = node.get("Hash Batches", 1)
            if hash_batches > 1:
                recommendations.append(
                    TuningRecommendation(
                        node_type=node_type,
                        severity="CRITICAL",
                        issue=f"해시 조인 처리 중 빌드 테이블 크기가 work_mem을 초과하여 {hash_batches}개의 배치 블록으로 쪼개져 디스크 임시 공간으로 강제 스필(Spill)되었습니다.",
                        solution="해시 테이블이 한 번에 메모리에 로드될 수 있도록 세션 혹은 글로벌 단위의 작업 메모리 영역(work_mem)을 대폭 증가시켜 주십시오.",
                        recommended_sql="SET work_mem = '128MB';",
                    )
                )

        # --- Nested Loop 조인 키 인덱스 누락 검증 ---
        elif node_type == "Nested Loop":
            plans = node.get("Plans", [])
            if len(plans) >= 2:
                inner_node = plans[1]
                inner_node_type = inner_node.get("Node Type", "")

                if "Seq Scan" in inner_node_type:
                    inner_table = inner_node.get("Relation Name", "알 수 없는 테이블")
                    recommendations.append(
                        TuningRecommendation(
                            node_type=node_type,
                            severity="CRITICAL",
                            issue=f"Nested Loop 조인 중 내부 드라이븐 테이블('{inner_table}')을 반복 검색하기 위한 조인 키 인덱스가 없습니다. 이로 인해 심각한 반복 풀 스캔({inner_node_type})이 발생 중입니다.",
                            solution="조인 ON 절의 매핑 키 컬럼을 기준으로 내부 테이블에 인덱스를 생성하여 Random Access 효율을 극대화하십시오.",
                        )
                    )
            else:
                pass

        return recommendations


# ==========================================
# 5. DB 연결 정보 환경설정 관리자
# ==========================================


class ConfigManager:
    CONFIG_FILE = "db_config.json"

    @classmethod
    def load_config(cls) -> Dict[str, str]:
        if os.path.exists(cls.CONFIG_FILE):
            try:
                with open(cls.CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "host": "localhost",
            "port": "5432",
            "dbname": "postgres",
            "user": "postgres",
            "password": "",
        }

    @classmethod
    def save_config(cls, config_data: Dict[str, str]):
        with open(cls.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4, ensure_ascii=False)


# ==========================================
# 6. 상용 GUI 애플리케이션 (CustomTkinter)
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
            """SELECT
    d.dept_name,
    COALESCE(count(u.user_id), 0) AS user_count
FROM gb_departments d
LEFT JOIN gb_users u ON d.dept_id = u.dept_id
WHERE u.name LIKE '%admin'
GROUP BY d.dept_id, d.dept_name;""",
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

    def run_analysis(self, query: str, conn_params: Dict[str, str]):
        dsn = f"host={conn_params['host']} port={conn_params['port']} dbname={conn_params['dbname']} user={conn_params['user']} password={conn_params['password']}"

        try:
            with psycopg.connect(dsn, connect_timeout=5) as conn:
                conn.autocommit = False
                with conn.cursor() as sys_cur:
                    sys_cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;")

                try:
                    metadata_provider = PGMetadataProvider(conn)
                    plan_analyzer = PGPlanAnalyzer(conn)
                    heuristic_engine = PGHeuristicEngine(metadata_provider)

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

                    all_recs = []
                    for node in target_nodes:
                        recs = heuristic_engine.generate_recommendations(node, query)
                        all_recs.extend(recs)

                    self.after(0, lambda: self.render_recommendations(raw_explain_text, all_recs))

                finally:
                    conn.rollback()

        except ValueError as err:
            self.after(
                0,
                lambda error_val=err: self.update_result_box_custom(
                    f"❌ [안전 경고]\n\n{str(error_val)}",
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

    def render_recommendations(self, raw_explain: str, recs: List[TuningRecommendation]):
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
                self.txt_result.insert(
                    "end", f"{severity_symbol} [{rec.severity}] 튜닝 가이드 #{idx}\n"
                )
                self.txt_result.insert("end", f"  • 대상 노드  : {rec.node_type}\n")
                self.txt_result.insert("end", f"  • 현상 및 원인: {rec.issue}\n")
                self.txt_result.insert("end", f"  • 조치 가이드: {rec.solution}\n")
                if rec.recommended_sql:
                    self.txt_result.insert("end", "  • 추천 실행 스크립트:\n")
                    self.txt_result.insert("end", f"    {rec.recommended_sql}\n")
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
