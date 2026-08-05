import re
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple
import psycopg
from psycopg import sql
import sqlglot
from sqlglot import exp

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
