import re
import json
from datetime import datetime, timedelta
from typing import Any
import psycopg
from psycopg import sql
import sqlglot
from sqlglot import exp


class PGPlanAnalyzer:
    # 안전성 검사를 위한 위험 노드 타입 집합
    _UNSAFE_NODE_TYPES = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Drop,
        exp.Alter,
        exp.Create,
        exp.TruncateTable,
    )
    _UNSAFE_KEYWORD_PATTERN = re.compile(
        r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE)\b", re.IGNORECASE
    )

    # 분석 대상 노드 타입 집합 (모든 규칙의 TARGET_NODE_TYPES 합집합)
    _PROBLEMATIC_NODE_TYPES: frozenset[str] = frozenset({
        # Scan
        "Seq Scan",
        "Index Scan",
        "Index Only Scan",
        "Bitmap Heap Scan",
        "Bitmap Index Scan",
        "CTE Scan",
        "Subquery Scan",
        "Foreign Scan",
        "Function Scan",
        "Values Scan",
        "WorkTable Scan",
        "Sample Scan",
        # Join
        "Hash Join",
        "Nested Loop",
        "Merge Join",
        "Gather",
        "Gather Merge",
        "Memoize",
        # Sort / Aggregate / Hash / Memory / Window / Lock
        "Sort",
        "Hash",
        "Aggregate",
        "Incremental Sort",
        "WindowAgg",
        "Materialize",
        "LockRows",
        "Limit",
        "BitmapAnd",
        "BitmapOr",
        # DML (ConstraintTriggerOverheadRule, HotUpdateFailureRule 등)
        "ModifyTable",
        "Update",
        "Insert",
        "Delete",
    })

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    @staticmethod
    def clean_query_comments(query: str) -> str:
        query = re.sub(r"/\*.*?\*/", "", query, flags=re.DOTALL)
        clean_lines = [
            re.sub(r"--.*$", "", line)
            for line in query.split("\n")
        ]
        return "\n".join(line for line in clean_lines if line.strip()).strip()

    def _is_unsafe_query(self, clean_sql: str) -> bool:
        """SQL이 DML/DDL을 포함하는지 검사합니다 (AST 우선, 정규식 fallback)."""
        try:
            parsed = sqlglot.parse_one(clean_sql, read="postgres")
            for _ in parsed.find_all(self._UNSAFE_NODE_TYPES):
                return True
            return False
        except Exception:
            return bool(self._UNSAFE_KEYWORD_PATTERN.search(clean_sql))

    def execute_explain_json(self, query: str) -> list[dict[str, Any]]:
        clean_sql = self.clean_query_comments(query)
        # 안전성 검토: DML/DDL 구문 사전 차단
        if self._is_unsafe_query(clean_sql):
            raise ValueError(
                "안전 제한: DML 또는 DDL 구문(INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE 등)은 "
                "EXPLAIN ANALYZE 성능 분석을 임의로 수행할 수 없습니다."
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
        # 안전성 검토: DML/DDL 구문 사전 차단
        if self._is_unsafe_query(clean_sql):
            raise ValueError(
                "안전 제한: DML 또는 DDL 구문(INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE 등)은 "
                "EXPLAIN ANALYZE 성능 분석을 임의로 수행할 수 없습니다."
            )

        explain_query = sql.SQL("EXPLAIN (ANALYZE, BUFFERS) {}").format(sql.SQL(clean_sql))
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
    ) -> tuple[list[str], list[str], bool, bool]:
        """
        WHERE 조건절 컬럼과 JOIN/GROUP BY 절의 매핑 컬럼을 정밀하게 분리 추출합니다.
        실패할 경우, 정규식 기반의 Fallback 루틴으로 복원합니다.
        """
        clean_sql = PGPlanAnalyzer.clean_query_comments(sql_query)
        target_table_lower = target_table.lower().strip()

        try:
            parsed_tree = sqlglot.parse_one(clean_sql, read="postgres")
            alias_map: dict[str, str] = {}
            all_tables: list[str] = []
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
            where_columns: set[str] = set()
            join_group_columns: set[str] = set()

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
            where_cols: list[str] = []
            where_match = re.search(
                r"where\s+(.*?)(?:group\s+by|order\s+by|limit|$)",
                clean_sql,
                re.IGNORECASE | re.DOTALL,
            )
            if where_match:
                where_clause_text = where_match.group(1)
                candidates = re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b", where_clause_text)
                keywords = {
                    "and", "or", "in", "is", "null", "not", "between", "like", "true", "false",
                }
                where_cols = list({c.lower() for c in candidates if c.lower() not in keywords})

            has_or = bool(re.search(r"\bor\b", clean_sql, re.IGNORECASE))
            return where_cols, [], True, has_or

    def find_problematic_nodes(self, plan_node: dict[str, Any]) -> list[dict[str, Any]]:
        """실행계획 트리를 재귀적으로 순회하여 분석 대상 노드를 수집합니다."""
        nodes: list[dict[str, Any]] = []
        node_type = plan_node.get("Node Type")
        if node_type in self._PROBLEMATIC_NODE_TYPES:
            nodes.append(plan_node)
        for sub_plan in plan_node.get("Plans", []):
            nodes.extend(self.find_problematic_nodes(sub_plan))
        return nodes

    @staticmethod
    def assign_line_numbers(root_plan: dict[str, Any], raw_explain_text: str) -> None:
        """
        JSON 플랜 트리의 각 노드에 대응하는 실제 실행 순서(Post-Order Traversal, 1-indexed)를
        '_line_number' 키로 재귀 부여합니다. 또한 raw_explain_text 상의 대응 라인 인덱스(0-indexed)를
        '_raw_line_idx' 키로 기록합니다.
        """
        if not raw_explain_text or not root_plan:
            return

        lines = raw_explain_text.splitlines()
        if not lines:
            return

        def find_matching_line(node: dict[str, Any], start_idx: int) -> int:
            node_type = node.get("Node Type", "")
            rel_name = node.get("Relation Name", "")
            alias = node.get("Alias", "")
            idx_name = node.get("Index Name", "")
            subplan_name = node.get("Subplan Name", "")

            first_type_match = -1

            for i in range(start_idx, len(lines)):
                line = lines[i]
                if node_type and node_type in line:
                    match_rel = bool(rel_name and (rel_name in line or (alias and alias in line)))
                    match_idx = bool(idx_name and idx_name in line)
                    match_sub = bool(subplan_name and subplan_name in line)

                    if match_rel or match_idx or match_sub:
                        return i

                    if first_type_match == -1:
                        first_type_match = i

            if first_type_match != -1:
                return first_type_match

            return min(start_idx, len(lines) - 1)

        def traverse_match_lines(node: dict[str, Any], search_idx: int) -> int:
            found_idx = find_matching_line(node, search_idx)
            node["_raw_line_idx"] = found_idx

            curr_search = found_idx + 1
            for sub_plan in node.get("Plans", []):
                curr_search = traverse_match_lines(sub_plan, curr_search)
            return max(curr_search, found_idx + 1)

        traverse_match_lines(root_plan, 0)

        exec_counter = [1]

        def traverse_post_order(node: dict[str, Any]) -> None:
            for sub_plan in node.get("Plans", []):
                traverse_post_order(sub_plan)
            node["_line_number"] = exec_counter[0]
            exec_counter[0] += 1

        traverse_post_order(root_plan)

    @staticmethod
    def format_explain_with_line_numbers(
        raw_explain_text: str, root_plan: dict[str, Any] | None = None
    ) -> str:
        """실행계획 텍스트 각 라인의 왼쪽에 실제 실행 순서(Post-Order) 기준 SEQ 번호를 추가합니다."""
        if not raw_explain_text:
            return ""

        lines = raw_explain_text.splitlines()
        if not lines:
            return ""

        line_exec_map: dict[int, int] = {}

        if root_plan:
            def collect_node_lines(node: dict[str, Any]):
                raw_idx = node.get("_raw_line_idx")
                line_num = node.get("_line_number")
                if raw_idx is not None and line_num is not None:
                    line_exec_map[raw_idx] = line_num
                for sub in node.get("Plans", []):
                    collect_node_lines(sub)

            collect_node_lines(root_plan)

        if not line_exec_map:
            # Fallback: Parse indentation tree from raw_explain_text
            nodes_info = []
            for idx, line in enumerate(lines):
                stripped = line.lstrip()
                if idx == 0 or stripped.startswith("->"):
                    indent = len(line) - len(stripped)
                    nodes_info.append((idx, indent))

            if nodes_info:
                tree_nodes = []
                stack = []
                for idx, indent in nodes_info:
                    node_item = {"idx": idx, "children": []}
                    while stack and stack[-1]["indent"] >= indent:
                        stack.pop()
                    if stack:
                        stack[-1]["children"].append(node_item)
                    else:
                        tree_nodes.append(node_item)
                    stack.append({"indent": indent, "children": node_item["children"]})

                exec_seq = [1]

                def post_order_fallback(tnode):
                    for child in tnode["children"]:
                        post_order_fallback(child)
                    line_exec_map[tnode["idx"]] = exec_seq[0]
                    exec_seq[0] += 1

                for tnode in tree_nodes:
                    post_order_fallback(tnode)

        full_line_exec: list[int | None] = [None] * len(lines)
        curr_exec = None
        for idx in range(len(lines)):
            if idx in line_exec_map:
                curr_exec = line_exec_map[idx]
            full_line_exec[idx] = curr_exec

        valid_nums = [val for val in full_line_exec if val is not None]
        max_num = max(valid_nums) if valid_nums else len(lines)
        max_digits = max(2, len(str(max_num)))

        formatted_lines = []
        for idx, line in enumerate(lines):
            exec_num = full_line_exec[idx]
            if exec_num is not None:
                line_num_str = f"SEQ {exec_num:>{max_digits}}"
                formatted_lines.append(f"{line_num_str} | {line}")
            else:
                prefix_spaces = " " * (len(f"SEQ {1:>{max_digits}}"))
                formatted_lines.append(f"{prefix_spaces} | {line}")

        return "\n".join(formatted_lines)

    @staticmethod
    def _get_node_operation_desc(node: dict[str, Any]) -> str:
        node_type = node.get("Node Type", "")
        rel_name = node.get("Relation Name", "")
        idx_name = node.get("Index Name", "")
        filter_cond = node.get("Filter", "")
        index_cond = node.get("Index Cond", "")
        hash_cond = node.get("Hash Cond", "")
        merge_cond = node.get("Merge Cond", "")
        sort_keys = node.get("Sort Key", [])
        sort_method = node.get("Sort Method", "")
        group_keys = node.get("Group Key", [])
        strategy = node.get("Strategy", "")

        desc = ""
        if node_type == "Seq Scan":
            desc = f"{rel_name} 테이블 데이터 전체 순차 스캔 (Seq Scan)" if rel_name else "테이블 데이터 전체 순차 스캔"
            if filter_cond:
                desc += f" [필터 조건: {filter_cond}]"
        elif node_type == "Index Scan":
            desc = f"인덱스 '{idx_name}'을(를) 사용하여 {rel_name} 테이블 조건 검색" if rel_name and idx_name else f"인덱스 '{idx_name}' 검색"
            if index_cond:
                desc += f" [인덱스 조건: {index_cond}]"
            if filter_cond:
                desc += f" [추가 필터: {filter_cond}]"
        elif node_type == "Index Only Scan":
            desc = f"인덱스 '{idx_name}'만으로 필요 커버링 데이터 직접 추출 (힙 데이터 탐색 회피)" if idx_name else "인덱스 전용 커버링 스캔"
            if index_cond:
                desc += f" [인덱스 조건: {index_cond}]"
        elif node_type == "Bitmap Index Scan":
            desc = f"인덱스 '{idx_name}' 조건 기반 비트맵(Bitmap) 매핑 메모리 생성" if idx_name else "비트맵 인덱스 스캔 메모리 매핑"
            if index_cond:
                desc += f" [인덱스 조건: {index_cond}]"
        elif node_type == "Bitmap Heap Scan":
            desc = f"하위 비트맵 매핑을 참조하여 {rel_name} 테이블 힙 데이터 블록 효율적 스캔" if rel_name else "비트맵 힙 블록 스캔"
            if filter_cond:
                desc += f" [필터 조건: {filter_cond}]"
        elif node_type == "Hash":
            desc = "하위 노드 탐색 결과를 메모리 해시 테이블(Hash Table) 구조로 빌드"
        elif node_type == "Hash Join":
            desc = "하위 노드 탐색 결과와 해시 테이블 데이터를 키 값 비교하여 조인 수행"
            if hash_cond:
                desc += f" [해시 조인 조건: {hash_cond}]"
        elif node_type == "Nested Loop":
            desc = "외부(Outer) 테이블의 각 행에 대해 내부(Inner) 테이블 탐색을 루프 반복 수행"
            if index_cond or filter_cond:
                cond = index_cond or filter_cond
                desc += f" [조인/탐색 조건: {cond}]"
        elif node_type == "Merge Join":
            desc = "정렬된 두 하위 노드의 탐색 결과를 병합(Merge)하여 조인 수행"
            if merge_cond:
                desc += f" [병합 조인 조건: {merge_cond}]"
        elif node_type == "Sort":
            sort_str = f", 정렬 방식: {sort_method}" if sort_method else ""
            desc = f"결과 집합 정렬 수행 (Sort{sort_str})"
            if sort_keys:
                desc += f" [정렬 키: {', '.join(sort_keys)}]"
        elif node_type == "Incremental Sort":
            desc = "기정렬된 데이터 집합에 대해 추가 부분 정렬 수행 (Incremental Sort)"
            if sort_keys:
                desc += f" [정렬 키: {', '.join(sort_keys)}]"
        elif node_type == "Aggregate":
            strat_str = f" ({strategy})" if strategy else ""
            desc = f"그룹화 및 데이터 집계 연산(COUNT, SUM, AVG 등) 수행{strat_str}"
            if group_keys:
                desc += f" [그룹 키: {', '.join(group_keys)}]"
        elif node_type == "WindowAgg":
            desc = "윈도우 함수(OVER 절 분석 연산) 처리"
        elif node_type == "Gather":
            workers_planned = node.get("Workers Planned", 0)
            workers_launched = node.get("Workers Launched")
            worker_str = f" (계획: {workers_planned}개"
            if workers_launched is not None:
                worker_str += f" / 실제 할당: {workers_launched}개)"
            else:
                worker_str += ")"
            desc = f"병렬 워커(Parallel Worker) 프로세스 실행 결과 수집 및 병합{worker_str}"
        elif node_type == "Gather Merge":
            desc = "병렬 워커 프로세스가 정렬한 결과를 순서 보장하며 취합 병합"
        elif node_type == "Memoize":
            desc = "Nested Loop 반복 탐색 시 동일 매개변수 검색 결과를 메모리에 캐싱하여 재사용"
        elif node_type == "Materialize":
            desc = "하위 노드 탐색 결과를 메모리/디스크에 임시 캐싱하여 반복 재사용"
        elif node_type == "LockRows":
            desc = "동시성 행 잠금(FOR UPDATE / FOR SHARE) 점유 수행"
        elif node_type == "Limit":
            desc = "지정된 행 수(LIMIT/OFFSET)만큼 결과 추출 제한"
        elif node_type == "Subquery Scan":
            desc = "서브쿼리 실행 결과 집합 스캔"
        elif node_type == "CTE Scan":
            cte_name = node.get("CTE Name", "")
            desc = f"WITH 절(CTE: '{cte_name}') 임시 테이블 스캔" if cte_name else "WITH 절(CTE) 임시 테이블 스캔"
        elif node_type == "Foreign Scan":
            desc = "외부 데이터베이스(FDW) 테이블 탐색"
        elif node_type == "Function Scan":
            func_name = node.get("Function Name", "")
            desc = f"PostgreSQL 함수('{func_name}') 실행 및 결과 스캔" if func_name else "함수 실행 및 결과 스캔"
        else:
            desc = f"{node_type} 연산 수행"

        return desc

    @staticmethod
    def generate_execution_process_report(root_plan: dict[str, Any]) -> str:
        """
        JSON 실행계획 트리의 노드들을 실제 실행 순서(Post-Order Traversal, 1-indexed) 순서대로 정렬하여
        각 단계별 실행 과정, 연산 설명, 실측 성능 지표를 한글 리포트로 생성합니다.
        """
        if not root_plan:
            return ""

        nodes = []

        def collect_nodes(node: dict[str, Any]):
            nodes.append(node)
            for sub in node.get("Plans", []):
                collect_nodes(sub)

        collect_nodes(root_plan)
        nodes.sort(key=lambda n: n.get("_line_number", 0))

        lines = []
        lines.append("========================================================")
        lines.append("📊 [단계별 실행 과정 분석 리포트 (실행 순서 기준)]")
        lines.append("========================================================")

        for node in nodes:
            seq = node.get("_line_number", 0)
            node_type = node.get("Node Type", "Unknown Node")
            rel_name = node.get("Relation Name", "")
            alias = node.get("Alias", "")
            idx_name = node.get("Index Name", "")

            target_info = []
            if rel_name:
                target_info.append(f"테이블: {rel_name}")
                if alias and alias != rel_name:
                    target_info[-1] += f" (alias: {alias})"
            if idx_name:
                target_info.append(f"인덱스: {idx_name}")

            target_str = f" ({', '.join(target_info)})" if target_info else ""
            lines.append(f"• [SEQ {seq}] {node_type}{target_str}")

            op_desc = PGPlanAnalyzer._get_node_operation_desc(node)
            lines.append(f"  - 수행 연산: {op_desc}")

            act_time = node.get("Actual Total Time")
            act_startup = node.get("Actual Startup Time")
            act_rows = node.get("Actual Rows")
            act_loops = node.get("Actual Loops")

            if act_time is not None and act_rows is not None and act_loops is not None:
                startup_str = f"{act_startup:.3f}~" if act_startup is not None else ""
                lines.append(
                    f"  - 처리 실적: 소요시간 {startup_str}{act_time:.3f}ms | 출력행 {act_rows:,}행 | 반복횟수 {act_loops:,}회"
                )
            else:
                cost_start = node.get("Startup Cost")
                cost_total = node.get("Total Cost")
                plan_rows = node.get("Plan Rows")
                if cost_total is not None and plan_rows is not None:
                    lines.append(
                        f"  - 예상 비용: cost={cost_start:.2f}..{cost_total:.2f} | 예상행 {plan_rows:,}행"
                    )

            hit_blocks = node.get("Shared Hit Blocks", 0)
            read_blocks = node.get("Shared Read Blocks", 0)
            temp_read = node.get("Temp Read Blocks", 0)
            temp_written = node.get("Temp Written Blocks", 0)

            io_parts = []
            if hit_blocks > 0 or read_blocks > 0:
                io_parts.append(f"메모리 히트 {hit_blocks:,}블록")
                if read_blocks > 0:
                    io_parts.append(f"디스크 읽기 {read_blocks:,}블록")
            if temp_written > 0 or temp_read > 0:
                io_parts.append(f"임시 파일 I/O (읽기 {temp_read:,} / 쓰기 {temp_written:,} 블록)")

            if io_parts:
                lines.append(f"  - 버퍼/I/O : {', '.join(io_parts)}")

            lines.append("")

        return "\n".join(lines)


