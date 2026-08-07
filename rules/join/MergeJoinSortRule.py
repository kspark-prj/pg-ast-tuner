from typing import Optional

from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class MergeJoinSortRule(BaseRule):
    RULE_ID = "RULE_JOIN_003"
    NAME = "MergeJoinSortRule"
    DESCRIPTION = (
        "Merge Join 수행 시 인덱스 부재로 인해 하위 노드에서 불필요한 정렬(Sort) 연산이 발생하는지 진단하며, "
        "TableMetadata의 연속 매칭 평가를 활용해 정렬 대체 가능 여부를 분석합니다."
    )
    CATEGORY = "JOIN"
    TARGET_NODE_TYPES = ["Merge Join"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 2
    DEFAULT_SEVERITY = "WARNING"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") == "Merge Join"

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Merge Join")
        plans = node.get("Plans", [])

        # 하위 노드 중 Sort 노드가 포함되어 있는지 및 해당 테이블 식별
        sort_tables = []
        for child in plans:
            if child.get("Node Type") == "Sort":
                table_name = self._extract_table_from_plan(child)
                if table_name:
                    sort_tables.append(table_name)

        if sort_tables:
            target_table = sort_tables[0]
            # [메타데이터 연동] 조인 컬럼 파싱
            from core.parser import PGPlanAnalyzer
            _, join_group_cols, _, _ = PGPlanAnalyzer.extract_columns_via_ast_ordered(
                context.raw_query, target_table
            )
            join_cols = [
                c for c in join_group_cols
                if isinstance(c, str) and not c.startswith("'") and not c.startswith('"')
            ]

            meta_diag = self._evaluate_sort_index(context, target_table, join_cols)

            reason, recommendation, recommended_sql = self._build_action_guide(target_table, join_cols, meta_diag)

            recommendations.append(
                RecommendationModel(
                    title=f"'{target_table}' Merge Join을 위한 명시적 Sort 연산 발생",
                    description="Merge Join 조건절 집합을 정렬하기 위해 하위 연산에서 명시적 Sort 과정이 실행되고 있습니다.",
                    severity="WARNING",
                    priority=2,
                    reason=reason,
                    recommendation=recommendation,
                    recommended_sql=recommended_sql,
                    plan_node=node_type,
                    estimated_gain="Medium to High (In-Memory/Disk Sort 오버헤드 제거)",
                    false_positive_risk="Low",
                )
            )

        return recommendations

    def _extract_table_from_plan(self, plan: dict) -> str | None:
        if "Relation Name" in plan:
            return plan["Relation Name"]
        for child in plan.get("Plans", []):
            found = self._extract_table_from_plan(child)
            if found:
                return found
        return None

    def _evaluate_sort_index(self, context: RuleContext, table_name: str, join_cols: list[str]) -> dict:
        result = {"has_metadata": False, "best_index_name": None, "is_full_cover": False}
        metadata_provider = getattr(context, "metadata_provider", None)
        if not metadata_provider or not join_cols:
            return result

        try:
            table_meta = metadata_provider.get_table_metadata(table_name)
            if table_meta:
                result["has_metadata"] = True
                best_idx, best_eval = table_meta.find_best_index_with_score(join_cols)
                if best_idx:
                    result["best_index_name"] = best_idx.index_name
                    result["is_full_cover"] = best_eval["is_full_cover"]
        except Exception:
            pass

        return result

    def _build_action_guide(self, table_name: str, join_cols: list[str], meta_diag: dict) -> tuple[str, str, str]:
        cols_str = ", ".join(join_cols) if join_cols else "조인_키_컬럼"
        idx_cols_name = "_".join(join_cols) if join_cols else "sort"
        if meta_diag.get("is_full_cover"):
            idx_name = meta_diag["best_index_name"]
            return (
                f"테이블 '{table_name}'에 조인 키 순서대로 정렬을 지원하는 인덱스 '{idx_name}'가 존재하나, "
                "정렬 방식(ASC/DESC, NULLS FIRST/LAST) 또는 Collation 불일치로 인해 Sort 노드가 추가되었습니다.",
                "조인 조건의 정렬 옵션 및 Collation 설정을 인덱스 선언과 일치시키거나 최신 통계 정보를 반영하십시오.",
                f"-- 통계 갱신 및 실행 계획 재검토\nANALYZE {table_name};",
            )

        return (
            "Merge Join은 정렬된 입력을 필요로 합니다. 인덱스를 통한 정렬 순서 보장이 되지 않아 "
            "메모리/디스크 정렬(Sort) 노드가 추가되어 CPU 및 I/O 비용이 증가했습니다.",
            f"'{table_name}' 테이블의 조인 키 컬럼에 인덱스를 추가하여 정렬 작업 없이 Index Scan으로 "
            "정렬된 순서대로 조인을 수행하도록 개선하거나, 데이터량이 많을 경우 Hash Join 유도를 검토하십시오.",
            f"-- 정렬 대체용 인덱스 생성\nCREATE INDEX CONCURRENTLY idx_{table_name}_{idx_cols_name} ON {table_name} ({cols_str});",
        )
