
import re

from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class CrossJoinRule(BaseRule):
    RULE_ID = "RULE_JOIN_007"
    NAME = "CrossJoinRule"
    DESCRIPTION = "조인 조건이 누락되거나 잘못 설정되어 카티시안 곱(Cartesian Product)이 발생하는지 진단합니다."
    CATEGORY = "JOIN"
    TARGET_NODE_TYPES = ["Nested Loop", "Hash Join"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 1
    DEFAULT_SEVERITY = "CRITICAL"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") in self.TARGET_NODE_TYPES

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Join")

        # Hash Cond, Join Filter, Filter 속성이 전혀 없는 경우 조인 조건 누락 가능성 진단
        has_hash_cond = "Hash Cond" in node
        has_join_filter = "Join Filter" in node
        has_filter = "Filter" in node

        is_cross_join = not (has_hash_cond or has_join_filter or has_filter)

        # Nested Loop의 경우, 내부 구동 테이블(inner plan)의 인덱스 조건이나 필터 등에서
        # 외부 테이블의 컬럼/별칭을 참조하여 Parameterized 된 조인을 수행하는지 체크합니다.
        # Parameterized Nested Loop는 카티시안 곱(Cross Join)이 아니므로 제외합니다.
        # Nested Loop의 경우, 내부 구동 테이블(inner plan)의 인덱스 조건이나 필터 등에서
        # 외부 테이블의 컬럼/별칭을 참조하여 Parameterized 된 조인을 수행하거나,
        # Index Cond / Recheck Cond 및 행 수 조합 상쇄가 존재하는지 체크합니다.
        # 이 경우 카티시안 곱(Cross Join) 오진을 방지하기 위해 진단 대상에서 제외합니다.
        if is_cross_join and node_type == "Nested Loop":
            plans = node.get("Plans", [])
            if len(plans) >= 2:
                outer_plan = plans[0]
                inner_plan = plans[1]

                outer_aliases = self._collect_aliases(outer_plan)

                # 예외 규칙 1: Inner side 노드(Index Scan, Bitmap Index Scan 등)에
                # Index Cond 또는 Recheck Cond 형태로 Outer 테이블 키 조건/매개변수가 전달된 경우
                if self._is_parameterized_scan(inner_plan, outer_aliases):
                    is_cross_join = False
                # 예외 규칙 2: Outer 출력 행 수 × Inner 출력 행 수의 조합이 전체 테이블 Cross Join과 다르고
                # Index Cond / Recheck Cond 및 특정 조건으로 상쇄된 경우
                elif self._is_constrained_row_count(node, outer_plan, inner_plan):
                    is_cross_join = False

        if is_cross_join:
            recommendations.append(
                RecommendationModel(
                    title="카티시안 곱(Cross Join) 발생 감지",
                    description="조인 연산 노드에 명시적인 조인 조건(Join Cond / Filter)이 존재하지 않습니다.",
                    severity="CRITICAL",
                    priority=1,
                    reason="조인 조건절이 누락되어 두 테이블 간의 모든 조합을 탐색하는 Cartesian Product가 수행되고 있습니다. 이는 극심한 메모리 및 CPU 소모를 야기합니다.",
                    recommendation="ON 절 또는 WHERE 절에 두 테이블 간 결합을 위한 올바른 조인 조건이 누락되었는지 SQL 쿼리를 점검하십시오.",
                    plan_node=node_type,
                    estimated_gain="Extreme",
                    false_positive_risk="Medium",
                )
            )

        return recommendations

    def _collect_aliases(self, node: dict) -> set[str]:
        aliases = set()
        for key in ("Relation Name", "Alias", "CTE Name"):
            val = node.get(key)
            if val and isinstance(val, str):
                aliases.add(val)
                clean_val = val.replace('"', '').strip()
                if clean_val:
                    aliases.add(clean_val)

        for sub_plan in node.get("Plans", []):
            aliases.update(self._collect_aliases(sub_plan))
        return aliases

    def _is_parameterized_scan(self, node: dict, outer_aliases: set[str]) -> bool:
        """
        Inner side 노드(Index Scan, Bitmap Index Scan 등)에 Index Cond 또는 Recheck Cond
        형태로 Outer 테이블 키 조건이나 매개변수($N)가 전달되었는지 확인합니다.
        """
        index_cond = node.get("Index Cond", "")
        recheck_cond = node.get("Recheck Cond", "")
        filter_cond = node.get("Filter", "")

        cond_texts = [str(index_cond), str(recheck_cond), str(filter_cond)]
        combined_text = " ".join(cond_texts)

        # 1. Parameterized parameter reference e.g., $0, $1
        if re.search(r"\$\d+", combined_text):
            return True

        # 2. Reference to outer table aliases/columns
        for alias in outer_aliases:
            if not alias:
                continue
            escaped = re.escape(alias)
            pattern = rf'(?<![a-zA-Z0-9_])"?{escaped}"?\.'
            if re.search(pattern, combined_text, re.IGNORECASE):
                return True
            if (index_cond or recheck_cond) and re.search(rf'\b{escaped}\b', combined_text, re.IGNORECASE):
                return True

        # 3. Check any string field in current node
        for key, val in node.items():
            if key in ("Node Type", "Parent Relationship", "Relation Name", "Alias", "Index Name", "CTE Name"):
                continue
            if isinstance(val, str):
                if re.search(r"\$\d+", val):
                    return True
                for alias in outer_aliases:
                    if alias and re.search(rf'(?<![a-zA-Z0-9_])"?{re.escape(alias)}"?\.', val, re.IGNORECASE):
                        return True

        # 4. Recursively check child sub-plans
        for sub_plan in node.get("Plans", []):
            if self._is_parameterized_scan(sub_plan, outer_aliases):
                return True

        return False

    def _is_constrained_row_count(self, node: dict, outer_plan: dict, inner_plan: dict) -> bool:
        """
        Outer 출력 행 수 × Inner 출력 행 수의 조합이 전체 테이블 Cross Join 결과와 다르고
        Index Cond / Recheck Cond 또는 특정 조건으로 상쇄된 경우 Cartesian Product 오진을 방지합니다.
        """
        # Inner plan 서브트리에 Index Cond 또는 Recheck Cond가 명시된 경우 (인덱스 탐색 조건)
        if self._has_index_or_recheck_cond(inner_plan):
            return True

        outer_actual_rows = outer_plan.get("Actual Rows")
        inner_actual_rows = inner_plan.get("Actual Rows")
        inner_loops = inner_plan.get("Actual Loops", 1)
        node_actual_rows = node.get("Actual Rows")

        # Inner side가 반복 실행(Actual Loops > 1)되면서 루프당 출력 행이 제한된 소량인 경우
        if inner_loops is not None and inner_loops > 1:
            if inner_actual_rows is not None and inner_actual_rows <= 10:
                return True

        # Outer 행 수 * Inner 행 수의 결과가 전체 카티시안 곱 수치와 다르고 상쇄된 경우
        if outer_actual_rows is not None and inner_actual_rows is not None and node_actual_rows is not None:
            cartesian_product_estimate = outer_actual_rows * inner_actual_rows
            if node_actual_rows < cartesian_product_estimate or inner_actual_rows == 0:
                return True

        return False

    def _has_index_or_recheck_cond(self, node: dict) -> bool:
        if "Index Cond" in node or "Recheck Cond" in node:
            return True
        for sub in node.get("Plans", []):
            if self._has_index_or_recheck_cond(sub):
                return True
        return False

