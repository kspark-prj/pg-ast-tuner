from typing import Optional

from models.recommendation import RecommendationModel
from rules.base_rule import BaseRule, RuleContext


class NestedLoopRule(BaseRule):
    RULE_ID = "RULE_JOIN_002"
    NAME = "NestedLoopRule"
    DESCRIPTION = (
        "Nested Loop 조인 중 내부 드라이븐 테이블(Driven Table)에 조인 키 인덱스가 없어 "
        "반복적인 풀 스캔이 수행되는지 진단하며, TableMetadata를 통해 구조적 원인을 감지합니다."
    )
    CATEGORY = "JOIN"
    TARGET_NODE_TYPES = ["Nested Loop"]
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 1
    DEFAULT_SEVERITY = "CRITICAL"

    def match(self, context: RuleContext, node: dict) -> bool:
        return node.get("Node Type") == "Nested Loop"

    def analyze(self, context: RuleContext, node: dict) -> list[RecommendationModel]:
        recommendations = []
        node_type = node.get("Node Type", "Nested Loop")
        plans = node.get("Plans", [])

        if len(plans) >= 2:
            inner_node = plans[1]
            inner_node_type = inner_node.get("Node Type", "")

            if "Seq Scan" in inner_node_type:
                inner_table = inner_node.get("Relation Name", "알 수 없는 테이블")

                # [메타데이터 연동] 조인 컬럼에 대한 인덱스 적합성 평가
                from core.parser import PGPlanAnalyzer
                _, join_group_cols, _, _ = PGPlanAnalyzer.extract_columns_via_ast_ordered(
                    context.raw_query, inner_table
                )
                join_cols = [
                    c for c in join_group_cols
                    if isinstance(c, str) and not c.startswith("'") and not c.startswith('"')
                ]

                meta_diag = self._evaluate_inner_index_metadata(context, inner_table, join_cols)

                reason, recommendation, recommended_sql = self._build_action_guide(inner_table, inner_node_type, join_cols, meta_diag)

                recommendations.append(
                    RecommendationModel(
                        title="Nested Loop 내부 테이블 조인 인덱스 부재/부적합",
                        description=f"Nested Loop 조인 중 내부 드라이븐 테이블('{inner_table}')을 반복 검색하기 위한 조인 키 인덱스가 활용되지 못하고 있습니다.",
                        severity="CRITICAL",
                        priority=1,
                        reason=reason,
                        recommendation=recommendation,
                        recommended_sql=recommended_sql,
                        plan_node=node_type,
                        estimated_gain="Extreme (O(N * M) 반복 풀 스캔을 Random Access로 전환)",
                        false_positive_risk="Low",
                    )
                )

        return recommendations

    def _evaluate_inner_index_metadata(self, context: RuleContext, table_name: str, join_cols: list[str]) -> dict:
        result = {
            "has_metadata": False,
            "best_index_name": None,
            "has_skipped_prefix": False,
            "consecutive_matches": 0,
        }

        metadata_provider = getattr(context, "metadata_provider", None)
        if not metadata_provider or not join_cols:
            return result

        try:
            table_meta = metadata_provider.get_table_metadata(table_name)
            if not table_meta:
                return result

            result["has_metadata"] = True
            best_idx, best_eval = table_meta.find_best_index_with_score(join_cols)

            if best_idx:
                result["best_index_name"] = best_idx.index_name
                result["has_skipped_prefix"] = best_eval["has_skipped_prefix"]
                result["consecutive_matches"] = best_eval["consecutive_prefix_matches"]
        except Exception:
            pass

        return result

    def _build_action_guide(self, inner_table: str, node_type: str, join_cols: list[str], meta_diag: dict) -> tuple[str, str, str]:
        base_reason = f"외부 테이블에서 추출된 매 행마다 내부 테이블 전체를 풀 스캔({node_type})하고 있습니다."
        cols_str = ", ".join(join_cols) if join_cols else "조인_키_컬럼"
        idx_cols_name = "_".join(join_cols) if join_cols else "join"

        # 케이스 1: 인덱스는 있지만 선두 컬럼이 누락되어 사용할 수 없는 경우
        if meta_diag.get("has_skipped_prefix"):
            best_idx = meta_diag["best_index_name"]
            return (
                f"{base_reason} 테이블에 인덱스 '{best_idx}'가 존재하나, 조인 조건 컬럼이 인덱스의 첫 번째 선두 컬럼(Prefix)에 매칭되지 않아 옵티마이저가 인덱스 스캔을 포기했습니다.",
                "조인 ON 절 컬럼이 인덱스의 첫 선행 컬럼에 오도록 인덱스 구성을 수정하거나 복합 인덱스를 새로 설계하십시오.",
                f"-- 선두 매칭 복합 인덱스 생성\nCREATE INDEX CONCURRENTLY idx_{inner_table}_{idx_cols_name} ON {inner_table} ({cols_str});",
            )

        # 케이스 2: 완벽한 인덱스가 존재함에도 옵티마이저가 Seq Scan을 선택한 경우 (통계/타입 불일치)
        if meta_diag.get("best_index_name") and meta_diag["consecutive_matches"] > 0:
            best_idx = meta_diag["best_index_name"]
            return (
                f"{base_reason} 테이블에 조인 조건과 부합하는 인덱스 '{best_idx}'가 이미 존재하나 실행 계획에서 무시되었습니다.",
                "조인 양쪽 컬럼의 데이터 타입 불일치(묵시적 형변환 발생 여부)를 확인하고, 테이블 통계 정보를 최신화하여 옵티마이저가 인덱스를 선택하도록 조치하십시오.",
                f"-- 통계 정보 최신화\nANALYZE VERBOSE {inner_table};",
            )

        # 케이스 3: 인덱스가 아예 없는 경우 (기존 진단)
        return (
            f"{base_reason} 조인 ON 절의 매핑 키 컬럼에 대응하는 인덱스가 테이블에 존재하지 않습니다.",
            "조인 매핑 키 컬럼을 기준으로 내부 테이블에 인덱스를 생성하여 Random Access 효율을 극대화하십시오.\n※ 주의: CONCURRENTLY 인덱스 생성은 트랜잭션 외부에서 수행되어야 합니다.",
            f"-- 신규 인덱스 생성\nCREATE INDEX CONCURRENTLY idx_{inner_table}_{idx_cols_name} ON {inner_table} ({cols_str});",
        )
