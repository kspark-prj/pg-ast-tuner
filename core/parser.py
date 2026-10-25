import json
import re
from typing import Any

import psycopg
import sqlglot
from psycopg import sql
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
    _PROBLEMATIC_NODE_TYPES: frozenset[str] = frozenset(
        {
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
        }
    )

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    @staticmethod
    def clean_query_comments(query: str) -> str:
        query = re.sub(r"/\*.*?\*/", "", query, flags=re.DOTALL)
        clean_lines = [re.sub(r"--.*$", "", line) for line in query.split("\n")]
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

        pattern = rf"{re.escape(target_col)}\s+like\s+['\"]%"
        if re.search(pattern, clean_sql, re.IGNORECASE):
            return True
        return False

    @staticmethod
    def extract_columns_via_ast_ordered(
        sql_query: str, target_table: str
    ) -> tuple[list[str], list[str], bool, bool]:
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

            for where_clause in parsed_tree.find_all(exp.Where):
                for col in where_clause.find_all(exp.Column):
                    col_name = col.this.name.lower().strip()
                    if col_name != "*" and is_target_column(col):
                        where_columns.add(col_name)

            for clause in parsed_tree.find_all((exp.Join, exp.Group, exp.Order)):
                for col in clause.find_all(exp.Column):
                    col_name = col.this.name.lower().strip()
                    if col_name != "*" and is_target_column(col):
                        if col_name not in where_columns:
                            join_group_columns.add(col_name)

            return list(where_columns), list(join_group_columns), True, has_or_condition

        except Exception:
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
            return where_cols, [], True, has_or

    def find_problematic_nodes(self, plan_node: dict[str, Any]) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        node_type = plan_node.get("Node Type")
        if node_type in self._PROBLEMATIC_NODE_TYPES:
            nodes.append(plan_node)
        for sub_plan in plan_node.get("Plans", []):
            nodes.extend(self.find_problematic_nodes(sub_plan))
        return nodes

    @staticmethod
    def assign_line_numbers(root_plan: dict[str, Any], raw_explain_text: str) -> None:
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

        def get_ordered_children(node: dict[str, Any]) -> list[dict[str, Any]]:
            plans = node.get("Plans", [])
            if not plans or len(plans) <= 1:
                return plans

            node_type = node.get("Node Type", "")
            child_info = []
            for i, child in enumerate(plans):
                default_rank = i
                if node_type == "Hash Join":
                    if child.get("Node Type") == "Hash" or i == 1:
                        default_rank = 0
                    else:
                        default_rank = 1

                sort_key = (default_rank, i)
                child_info.append((sort_key, child))

            child_info.sort(key=lambda x: x[0])
            return [x[1] for x in child_info]

        def traverse_post_order(node: dict[str, Any]) -> None:
            ordered_children = get_ordered_children(node)
            for sub_plan in ordered_children:
                traverse_post_order(sub_plan)
            node["_line_number"] = exec_counter[0]
            exec_counter[0] += 1

        traverse_post_order(root_plan)

    @staticmethod
    def render_json_plan_to_text(plan_node: dict[str, Any], indent_level: int = 0) -> str:
        if not plan_node:
            return ""

        lines: list[str] = []
        indent = "  " * indent_level
        prefix = "->  " if indent_level > 0 else ""

        node_type = plan_node.get("Node Type", "")
        rel_name = plan_node.get("Relation Name", "")
        alias = plan_node.get("Alias", "")
        idx_name = plan_node.get("Index Name", "")
        subplan_name = plan_node.get("Subplan Name", "")

        target_parts = []
        if rel_name:
            target_parts.append(f"on {rel_name}")
            if alias and alias != rel_name:
                target_parts.append(f"{alias}")
        if idx_name:
            target_parts.append(f"using {idx_name}")
        if subplan_name:
            target_parts.append(f"for {subplan_name}")

        target_str = (" " + " ".join(target_parts)) if target_parts else ""

        cost_start = plan_node.get("Startup Cost")
        cost_total = plan_node.get("Total Cost")
        plan_rows = plan_node.get("Plan Rows")
        plan_width = plan_node.get("Plan Width")

        cost_str = ""
        if cost_total is not None and plan_rows is not None:
            width_str = f" width={plan_width}" if plan_width is not None else ""
            cost_str = f" (cost={cost_start:.2f}..{cost_total:.2f} rows={plan_rows}{width_str})"

        act_startup = plan_node.get("Actual Startup Time")
        act_total = plan_node.get("Actual Total Time")
        act_rows = plan_node.get("Actual Rows")
        act_loops = plan_node.get("Actual Loops")

        actual_str = ""
        if act_total is not None and act_rows is not None and act_loops is not None:
            startup_val = f"{act_startup:.3f}.." if act_startup is not None else ""
            actual_str = (
                f" (actual time={startup_val}{act_total:.3f} rows={act_rows} loops={act_loops})"
            )

        main_line = f"{indent}{prefix}{node_type}{target_str}{cost_str}{actual_str}"
        lines.append(main_line)

        info_indent = "  " * (indent_level + (2 if indent_level > 0 else 1))

        if "Index Cond" in plan_node:
            lines.append(f"{info_indent}Index Cond: {plan_node['Index Cond']}")
        if "Filter" in plan_node:
            lines.append(f"{info_indent}Filter: {plan_node['Filter']}")
            if plan_node.get("Rows Removed by Filter"):
                lines.append(
                    f"{info_indent}Rows Removed by Filter: {plan_node['Rows Removed by Filter']}"
                )
        if "Hash Cond" in plan_node:
            lines.append(f"{info_indent}Hash Cond: {plan_node['Hash Cond']}")
        if "Merge Cond" in plan_node:
            lines.append(f"{info_indent}Merge Cond: {plan_node['Merge Cond']}")
        if "Sort Key" in plan_node:
            sort_keys = plan_node["Sort Key"]
            keys_str = ", ".join(sort_keys) if isinstance(sort_keys, list) else str(sort_keys)
            sort_method = plan_node.get("Sort Method", "")
            sort_type = plan_node.get("Sort Space Type", "")
            sort_space = plan_node.get("Sort Space Used", "")
            method_str = f" Sort Method: {sort_method}" if sort_method else ""
            space_str = (
                f"  Memory: {sort_space}kB"
                if sort_type == "Memory" and sort_space
                else (f"  Disk: {sort_space}kB" if sort_space else "")
            )
            lines.append(f"{info_indent}Sort Key: {keys_str}")
            if method_str or space_str:
                lines.append(f"{info_indent}{method_str.strip()}{space_str}")

        hit_blocks = plan_node.get("Shared Hit Blocks", 0)
        read_blocks = plan_node.get("Shared Read Blocks", 0)
        dirtied_blocks = plan_node.get("Shared Dirtied Blocks", 0)
        written_blocks = plan_node.get("Shared Written Blocks", 0)
        temp_read = plan_node.get("Temp Read Blocks", 0)
        temp_written = plan_node.get("Temp Written Blocks", 0)

        buf_parts = []
        if hit_blocks > 0 or read_blocks > 0 or dirtied_blocks > 0 or written_blocks > 0:
            shared_parts = []
            if hit_blocks > 0:
                shared_parts.append(f"hit={hit_blocks}")
            if read_blocks > 0:
                shared_parts.append(f"read={read_blocks}")
            if dirtied_blocks > 0:
                shared_parts.append(f"dirtied={dirtied_blocks}")
            if written_blocks > 0:
                shared_parts.append(f"written={written_blocks}")
            buf_parts.append(f"shared {' '.join(shared_parts)}")
        if temp_read > 0 or temp_written > 0:
            temp_parts = []
            if temp_read > 0:
                temp_parts.append(f"read={temp_read}")
            if temp_written > 0:
                temp_parts.append(f"written={temp_written}")
            buf_parts.append(f"temp {' '.join(temp_parts)}")

        if buf_parts:
            lines.append(f"{info_indent}Buffers: {', '.join(buf_parts)}")

        for child in plan_node.get("Plans", []):
            child_text = PGPlanAnalyzer.render_json_plan_to_text(child, indent_level + 1)
            if child_text:
                lines.append(child_text)

        return "\n".join(lines)

    @staticmethod
    def format_explain_with_line_numbers(
        raw_explain_text: str, root_plan: dict[str, Any] | None = None
    ) -> str:
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
            nodes_info = []
            for idx, line in enumerate(lines):
                stripped = line.lstrip()
                if idx == 0 or stripped.startswith("->"):
                    indent = len(line) - len(stripped)
                    act_match = re.search(r"actual time=(\d+\.\d+)\.\.", line)
                    act_start = float(act_match.group(1)) if act_match else None
                    nodes_info.append(
                        {
                            "idx": idx,
                            "indent": indent,
                            "line": line,
                            "act_start": act_start,
                            "children": [],
                        }
                    )

            if nodes_info:
                tree_nodes = []
                stack = []
                for item in nodes_info:
                    indent = item["indent"]
                    node_item = item
                    while stack and stack[-1]["indent"] >= indent:
                        stack.pop()
                    if stack:
                        stack[-1]["children"].append(node_item)
                    else:
                        tree_nodes.append(node_item)
                    stack.append(node_item)

                exec_seq = [1]

                def get_ordered_fallback_children(tnode):
                    children = tnode["children"]
                    if not children or len(children) <= 1:
                        return children

                    is_hash_join = "Hash Join" in tnode.get("line", "")

                    child_info = []
                    for i, child in enumerate(children):
                        default_rank = i
                        if is_hash_join:
                            child_line = child.get("line", "")
                            if "Hash" in child_line or i == 1:
                                default_rank = 0
                            else:
                                default_rank = 1

                        sort_key = (default_rank, i)
                        child_info.append((sort_key, child))

                    child_info.sort(key=lambda x: x[0])
                    return [x[1] for x in child_info]

                def post_order_fallback(tnode):
                    ordered_children = get_ordered_fallback_children(tnode)
                    for child in ordered_children:
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
                line_num_str = f"[{exec_num:0{max_digits}d}]"
                formatted_lines.append(f"{line_num_str} | {line}")
            else:
                prefix_spaces = " " * (max_digits + 2)
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

        desc = ""
        if node_type == "Seq Scan":
            desc = (
                f"{rel_name} 테이블 데이터 전체 순차 스캔 (Seq Scan)"
                if rel_name
                else "테이블 데이터 전체 순차 스캔"
            )
            if filter_cond:
                desc += f" [필터 조건: {filter_cond}]"
        elif node_type == "Index Scan":
            desc = (
                f"인덱스 '{idx_name}'을(를) 사용하여 {rel_name} 테이블 조건 검색"
                if rel_name and idx_name
                else f"인덱스 '{idx_name}' 검색"
            )
            if index_cond:
                desc += f" [인덱스 조건: {index_cond}]"
            if filter_cond:
                desc += f" [추가 필터: {filter_cond}]"
        elif node_type == "Index Only Scan":
            desc = (
                f"인덱스 '{idx_name}'만으로 필요 커버링 데이터 직접 추출 (힙 데이터 탐색 회피)"
                if idx_name
                else "인덱스 전용 커버링 스캔"
            )
            if index_cond:
                desc += f" [인덱스 조건: {index_cond}]"
        elif node_type == "Bitmap Index Scan":
            desc = (
                f"인덱스 '{idx_name}' 조건 기반 비트맵(Bitmap) 매핑 메모리 생성"
                if idx_name
                else "비트맵 인덱스 스캔 메모리 매핑"
            )
            if index_cond:
                desc += f" [인덱스 조건: {index_cond}]"
        elif node_type == "Bitmap Heap Scan":
            desc = (
                f"하위 비트맵 매핑을 참조하여 {rel_name} 테이블 힙 데이터 블록 효율적 스캔"
                if rel_name
                else "비트맵 힙 블록 스캔"
            )
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
            desc = "그룹화 및 데이터 집계 연산(COUNT, SUM, AVG 등) 수행"
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
            desc = (
                f"WITH 절(CTE: '{cte_name}') 임시 테이블 스캔"
                if cte_name
                else "WITH 절(CTE) 임시 테이블 스캔"
            )
        elif node_type == "Foreign Scan":
            desc = "외부 데이터베이스(FDW) 테이블 탐색"
        elif node_type == "Function Scan":
            func_name = node.get("Function Name", "")
            desc = (
                f"PostgreSQL 함수('{func_name}') 실행 및 결과 스캔"
                if func_name
                else "함수 실행 및 결과 스캔"
            )
        else:
            desc = f"{node_type} 연산 수행"

        return desc

    @staticmethod
    def generate_execution_process_report(root_plan: dict[str, Any]) -> str:
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
        lines.append("📊 [단계별 실행 의존성 분석 리포트 (파이프라인 준비 순서)]")
        lines.append("========================================================")
        lines.append(
            "※ 안내: 이 번호는 상위 연산 수행에 필요한 선행 준비 순서(Dependency Order)이며, 실제 CPU 실행 순서가 아닙니다."
        )
        lines.append(
            "         실제 엔진은 상위 노드의 Pull 요청에 따라 노드 간에 스트리밍 방식으로 맞물려 동작합니다.\n"
        )

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
            lines.append(f"• [{seq}] {node_type}{target_str}")

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

    @staticmethod
    def generate_plan_summary(
        root_plan: dict[str, Any], plan_data: list[dict[str, Any]] | None = None
    ) -> str:
        if not root_plan:
            return ""

        exec_time = None
        plan_time = None
        if plan_data and isinstance(plan_data, list) and len(plan_data) > 0:
            exec_time = plan_data[0].get("Execution Time")
            plan_time = plan_data[0].get("Planning Time")
        if exec_time is None:
            exec_time = root_plan.get("Actual Total Time")

        nodes = []

        def collect(n):
            nodes.append(n)
            for sub in n.get("Plans", []):
                collect(sub)

        collect(root_plan)

        node_types = sorted(list({n.get("Node Type", "") for n in nodes if n.get("Node Type")}))

        lines = [
            "=======================================================",
            "[1] PLAN SUMMARY",
            "=======================================================",
        ]
        if exec_time is not None:
            lines.append(f"• Total Execution Time : {exec_time:.3f} ms")
        if plan_time is not None:
            lines.append(f"• Planning Time        : {plan_time:.3f} ms")
        lines.append(f"• Root Node Type       : {root_plan.get('Node Type', 'Unknown')}")
        lines.append(f"• Total Plan Nodes     : {len(nodes)}개")
        lines.append(
            f"• Major Operations     : {', '.join(node_types[:8])}"
            + ("..." if len(node_types) > 8 else "")
        )
        return "\n".join(lines)

    @staticmethod
    def generate_cardinality_analysis(root_plan: dict[str, Any]) -> str:
        if not root_plan:
            return ""
        nodes = []

        def collect(n):
            nodes.append(n)
            for sub in n.get("Plans", []):
                collect(sub)

        collect(root_plan)

        lines = [
            "=======================================================",
            "[4] CARDINALITY ANALYSIS",
            "=======================================================",
        ]

        mismatches = []
        for n in nodes:
            est = n.get("Plan Rows")
            act_r = n.get("Actual Rows")
            act_l = n.get("Actual Loops", 1)
            if est is not None and act_r is not None:
                total_act = act_r * act_l
                ratio = max(est, total_act) / max(min(est, total_act), 1)
                seq = n.get("_line_number", 0)
                ntype = n.get("Node Type", "Node")
                rel = n.get("Relation Name", "")
                target_str = f" ({rel})" if rel else ""
                if ratio >= 2.0:
                    status = (
                        "추정 오차 관찰 (필요 시 ANALYZE 검토)" if ratio >= 10 else "경미한 오차"
                    )
                    mismatches.append(
                        f"• [{seq}] {ntype}{target_str}: 추정 {est:,}행 vs 실측 {total_act:,}행 (오차 {ratio:.1f}배 - {status})"
                    )

        if mismatches:
            lines.extend(mismatches)
            lines.append(
                "  (※ 주의: 카디널리티 오차가 크더라도 실제 실행시간이 극히 짧다면 즉각적인 성능 병목은 아닐 수 있습니다.)"
            )
        else:
            lines.append(
                "• 전체 노드에서 유의미한 카디널리티 추정 오차(2배 이상)가 감지되지 않았습니다."
            )

        return "\n".join(lines)

    @staticmethod
    def generate_buffer_io_analysis(root_plan: dict[str, Any]) -> str:
        if not root_plan:
            return ""
        nodes = []

        def collect(n):
            nodes.append(n)
            for sub in n.get("Plans", []):
                collect(sub)

        collect(root_plan)

        hit = root_plan.get("Shared Hit Blocks", 0)
        read = root_plan.get("Shared Read Blocks", 0)
        temp_read = 0
        temp_written = 0
        for n in nodes:
            temp_read += n.get("Temp Read Blocks", 0)
            temp_written += n.get("Temp Written Blocks", 0)

        lines = [
            "=======================================================",
            "[5] BUFFER / I/O ANALYSIS",
            "=======================================================",
        ]

        total_shared = hit + read
        if total_shared > 0:
            hit_ratio = (hit / total_shared) * 100
            lines.append(
                f"• Shared Buffer Hit Rate: {hit_ratio:.2f}% (Hit: {hit:,} 블록, Read: {read:,} 블록)"
            )
            if read > 0:
                lines.append(
                    f"• 디스크 I/O 발생: {read:,} 블록이 디스크/OS 캐시에서 물리적으로 읽혔습니다."
                )
            else:
                lines.append(
                    "• 100% 메모리 버퍼 캐시에서 처리되어 디스크 물리 I/O가 발생하지 않았습니다."
                )
        else:
            lines.append("• 수집된 버퍼 I/O 정보가 없습니다. (BUFFERS 옵션 수집 확인)")

        if temp_read > 0 or temp_written > 0:
            lines.append(
                f"⚠️ [임시 파일 I/O 감지] 쓰기 {temp_written:,} 블록 / 읽기 {temp_read:,} 블록 (work_mem 초과 디스크 Spill)"
            )
        else:
            lines.append("• 임시 파일 디스크 Spill(Temp File I/O)이 발생하지 않았습니다.")

        return "\n".join(lines)

    @staticmethod
    def generate_bottleneck_analysis(root_plan: dict[str, Any]) -> str:
        if not root_plan:
            return ""
        nodes = []

        def collect(n):
            nodes.append(n)
            for sub in n.get("Plans", []):
                collect(sub)

        collect(root_plan)

        total_time = root_plan.get("Actual Total Time")
        lines = [
            "=======================================================",
            "[6] PERFORMANCE & BOTTLENECK ANALYSIS",
            "=======================================================",
        ]

        if total_time is None or total_time <= 0:
            lines.append("• 실측 실행 시간 정보가 수집되지 않았습니다 (EXPLAIN ANALYZE 필요).")
            return "\n".join(lines)

        lines.append(f"• 전체 쿼리 실행시간: {total_time:.3f} ms")
        if total_time <= 5.0:
            lines.append(
                f"• 현재 실행시간({total_time:.3f}ms)은 극히 빠르므로 시스템 관점에서 실질적인 성능 병목은 존재하지 않습니다."
            )

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
                pct = (exclusive / total_time) * 100 if total_time > 0 else 0
                node_exclusives.append((exclusive, pct, n))

        node_exclusives.sort(key=lambda x: x[0], reverse=True)

        bottlenecks = [item for item in node_exclusives if item[1] >= 25.0]
        if bottlenecks:
            lines.append("• 주요 시간 점유 노드 (Exclusive Time 기준):")
            for exc, pct, n in node_exclusives[:3]:
                seq = n.get("_line_number", 0)
                ntype = n.get("Node Type", "Node")
                rel = n.get("Relation Name", "")
                target_str = f" ({rel})" if rel else ""
                is_primary = " 👈 [주요 병목 구간]" if pct >= 30.0 else ""
                lines.append(
                    f"  - [{seq}] {ntype}{target_str}: {exc:.3f} ms ({pct:.1f}%){is_primary}"
                )
        else:
            lines.append(
                "• 특정 노드에 편중된 심각한 독점 실행 시간(Exclusive Time 30% 이상)은 감지되지 않았습니다."
            )

        return "\n".join(lines)

    @staticmethod
    def generate_join_analysis(root_plan: dict[str, Any]) -> str:
        if not root_plan:
            return ""
        nodes = []

        def collect(n):
            nodes.append(n)
            for sub in n.get("Plans", []):
                collect(sub)

        collect(root_plan)

        join_types = {"Hash Join", "Nested Loop", "Merge Join"}
        join_nodes = [n for n in nodes if n.get("Node Type") in join_types]

        lines = [
            "=======================================================",
            "[7] JOIN ANALYSIS",
            "=======================================================",
        ]

        if not join_nodes:
            lines.append("• 실행 계획에 조인 연산(Join)이 존재하지 않습니다.")
            return "\n".join(lines)

        for n in join_nodes:
            seq = n.get("_line_number", 0)
            ntype = n.get("Node Type")
            cond = n.get("Hash Cond") or n.get("Join Filter") or n.get("Merge Cond") or "조건 없음"
            lines.append(f"• [{seq}] {ntype}")
            lines.append(f"  - 조인 조건: {cond}")

            sub_plans = n.get("Plans", [])
            if ntype == "Hash Join" and len(sub_plans) >= 2:
                probe_node = sub_plans[0]
                build_hash_node = sub_plans[1]
                p_type = probe_node.get("Node Type", "Unknown")
                p_rel = probe_node.get("Relation Name", "")
                b_plans = build_hash_node.get("Plans", [])
                b_rel = b_plans[0].get("Relation Name", "") if b_plans else ""
                lines.append(
                    f"  - 탐색 측 (Probe Side) : {p_type} ({p_rel})"
                    if p_rel
                    else f"  - 탐색 측 (Probe Side) : {p_type}"
                )
                lines.append(
                    f"  - 해시 생성 측 (Build Side) : {b_rel}"
                    if b_rel
                    else "  - 해시 생성 측 (Build Side) : Hash Table"
                )
                batches = build_hash_node.get("Hash Batches") or n.get("Hash Batches")
                buckets = build_hash_node.get("Hash Buckets") or n.get("Hash Buckets")
                if batches and buckets:
                    lines.append(
                        f"  - 해시 버킷: {buckets:,}개, 배치: {batches} (단일 배치 내 메모리 처리 확인)"
                        if batches == 1
                        else f"  ⚠️ 해시 배치: {batches} (work_mem 초과로 다중 배치 처리됨)"
                    )
            elif ntype == "Nested Loop":
                loops = n.get("Actual Loops", 1)
                lines.append(f"  - 반복 수행: {loops:,}회 반복 구동")

        return "\n".join(lines)

    @staticmethod
    def generate_sort_window_analysis(root_plan: dict[str, Any]) -> str:
        if not root_plan:
            return ""
        nodes = []

        def collect(n):
            nodes.append(n)
            for sub in n.get("Plans", []):
                collect(sub)

        collect(root_plan)

        sort_nodes = [n for n in nodes if n.get("Node Type") in ("Sort", "Incremental Sort")]
        window_nodes = [n for n in nodes if n.get("Node Type") == "WindowAgg"]

        lines = [
            "=======================================================",
            "[8] SORT / WINDOW ANALYSIS",
            "=======================================================",
        ]

        if not sort_nodes and not window_nodes:
            lines.append("• 정렬(Sort) 및 윈도우 함수(WindowAgg) 연산이 존재하지 않습니다.")
            return "\n".join(lines)

        for n in sort_nodes:
            seq = n.get("_line_number", 0)
            ntype = n.get("Node Type")
            method = n.get("Sort Method", "")
            space_used = n.get("Sort Space Used", 0)
            space_type = n.get("Sort Space Type", "Memory")
            key = n.get("Sort Key", [])
            key_str = ", ".join(key) if isinstance(key, list) else str(key)

            lines.append(f"• [{seq}] {ntype}")
            if key_str:
                lines.append(f"  - 정렬 키: {key_str}")
            if method:
                lines.append(f"  - 정렬 방식: {method} ({space_used}kB {space_type})")
                if space_type == "Disk" or "external" in method.lower():
                    lines.append(
                        "  ⚠️ [디스크 정렬 발생] work_mem 부족으로 임시 디스크 공간을 사용했습니다."
                    )
                else:
                    lines.append("  - 정상적인 인-메모리 정렬입니다.")

        for n in window_nodes:
            seq = n.get("_line_number", 0)
            lines.append(f"• [{seq}] WindowAgg (윈도우 함수 연산 수행)")

        return "\n".join(lines)

    @staticmethod
    def generate_partition_append_analysis(root_plan: dict[str, Any]) -> str:
        if not root_plan:
            return ""
        nodes = []

        def collect(n):
            nodes.append(n)
            for sub in n.get("Plans", []):
                collect(sub)

        collect(root_plan)

        append_nodes = [n for n in nodes if n.get("Node Type") in ("Append", "Merge Append")]
        lines = [
            "=======================================================",
            "[9] PARTITION / APPEND ANALYSIS",
            "=======================================================",
        ]

        if not append_nodes:
            lines.append("• 파티션 병합(Append / Merge Append) 연산이 존재하지 않습니다.")
            return "\n".join(lines)

        for n in append_nodes:
            seq = n.get("_line_number", 0)
            sub_plans = n.get("Plans", [])
            lines.append(
                f"• [{seq}] {n.get('Node Type')} (총 {len(sub_plans)}개 하위 파티션/테이블)"
            )
            zero_rows_count = 0
            for child in sub_plans:
                c_rows = child.get("Actual Rows")
                if c_rows is not None and c_rows == 0:
                    zero_rows_count += 1
            if zero_rows_count > 0:
                lines.append(
                    f"  - {len(sub_plans)}개 파티션 중 {zero_rows_count}개 파티션에서 반환 행이 0건이었습니다 (파티션 프루닝 여부 검토 가능)."
                )
            else:
                lines.append("  - 모든 파티션에서 정상적으로 데이터가 조회되었습니다.")

        return "\n".join(lines)

    @staticmethod
    def generate_overall_judgment(
        root_plan: dict[str, Any],
        recs: list[Any],
        plan_data: list[dict[str, Any]] | None = None,
    ) -> str:
        lines = [
            "=======================================================",
            "OVERALL TUNING JUDGMENT",
            "=======================================================",
        ]

        exec_time = None
        if plan_data and isinstance(plan_data, list) and len(plan_data) > 0:
            exec_time = plan_data[0].get("Execution Time")
        if exec_time is None and root_plan:
            exec_time = root_plan.get("Actual Total Time")

        has_critical = any(getattr(r, "severity", "") == "CRITICAL" for r in recs)
        has_high = any(getattr(r, "severity", "") == "HIGH" for r in recs)

        # [추가] MEM_002 (버퍼 히트율 저하 / 콜드 캐시 I/O 병목) 단독 CRITICAL 여부 파악
        is_only_buffer_miss_critical = has_critical and all(
            getattr(r, "rule_id", "") == "RULE_MEM_002"
            for r in recs
            if getattr(r, "severity", "") == "CRITICAL"
        )

        if exec_time is not None and exec_time <= 5.0 and not has_critical:
            lines.append("• 종합 진단 평의: [최적 상태 / 튜닝 불필요]")
            lines.append(
                f"  현재 총 실행시간이 {exec_time:.3f}ms로 매우 우수하며, 서비스에 지장을 주는 병목이 감지되지 않았습니다."
            )
            lines.append(
                "  일부 경미한 오차나 인덱스 부재가 있더라도 현 데이터 규모에서는 성급한 인덱스 추가나 SQL 수정을 권장하지 않습니다."
            )
        elif is_only_buffer_miss_critical:
            # [추가] 콜드 캐시 / I/O 병목 전용 평의 메시지 출력
            lines.append("• 종합 진단 평의: [I/O 웜업 필요 / MEMORY CACHE MISS]")
            lines.append(
                "  쿼리 구조(집계/스캔)는 정상이나, 데이터베이스 버퍼 캐시 미스로 인한 대규모 물리 디스크 I/O 병목이 발생하고 있습니다."
            )
            lines.append(
                "  상단의 권장 사항 중 시스템 캐시 적재(pg_prewarm) 및 shared_buffers 증설 조치를 검토하십시오."
            )
        elif has_critical:
            lines.append("• 종합 진단 평의: [긴급 튜닝 필요 / CRITICAL]")
            lines.append("  디스크 Spill 또는 대규모 조인/풀스캔 병목이 발생하고 있습니다.")
            lines.append("  상단의 권장 사항 중 CRITICAL 가이드를 최우선으로 검토하십시오.")
        elif has_high:
            lines.append("• 종합 진단 평의: [성능 개선 권장 / HIGH]")
            lines.append("  일부 노드에서 비효율적인 스캔이나 통계 정보 불일치가 확인되었습니다.")
            lines.append(
                "  가이드의 오진 가능성과 예상 효과를 고려하여 단계적으로 개선을 적용하십시오."
            )
        else:
            lines.append("• 종합 진단 평의: [양호 / 모니터링]")
            lines.append("  현재 옵티마이저가 수립한 실행 계획이 전반적으로 합리적입니다.")
            lines.append("  데이터 증가에 따른 추세 모니터링을 권장합니다.")

        return "\n".join(lines)
