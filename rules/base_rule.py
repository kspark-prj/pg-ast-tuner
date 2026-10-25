from abc import ABC, abstractmethod
from typing import Any

import sqlglot
from sqlglot import exp

from models.recommendation import RecommendationModel


class RuleContext:
    def __init__(
        self,
        raw_query: str,
        clean_query: str,
        metadata_provider: Any,
        plan_data: Any = None,
    ):
        self.raw_query = raw_query
        self.clean_query = clean_query
        self.metadata_provider = metadata_provider
        self.plan_data = plan_data
        self._is_agg_only = None

    def is_aggregate_only(self) -> bool:
        """
        [중앙 집중형 문맥 분석]
        WHERE 절 필터 조건이 없거나, 전체 집계(COUNT, SUM, AVG 등) 중심인
        쿼리 성격을 AST 기반으로 판별하고 결과를 캐싱합니다.
        """
        if self._is_agg_only is not None:
            return self._is_agg_only

        try:
            target_query = self.clean_query or self.raw_query
            parsed_tree = sqlglot.parse_one(target_query, read="postgres")
            has_where = any(parsed_tree.find_all(exp.Where))
            has_agg = any(
                isinstance(node, (exp.Count, exp.Sum, exp.Avg, exp.Max, exp.Min))
                for node in parsed_tree.find_all(exp.Func)
            )
            self._is_agg_only = not has_where or has_agg
        except Exception:
            self._is_agg_only = False

        return self._is_agg_only


class BaseRule(ABC):
    RULE_ID: str
    NAME: str
    DESCRIPTION: str
    CATEGORY: str
    TARGET_NODE_TYPES: list[str]
    SUPPORTED_PG_VERSION: str = "all"
    DEFAULT_PRIORITY: int = 3
    DEFAULT_SEVERITY: str = "INFO"

    @abstractmethod
    def match(self, context: RuleContext, node: dict) -> bool:
        """이 룰이 현재 노드에 부합하는지 여부를 판단합니다."""

    @abstractmethod
    def analyze(
        self, context: RuleContext, node: dict
    ) -> RecommendationModel | list[RecommendationModel]:
        """노드를 정밀 진단하고 RecommendationModel 또는 그 리스트를 반환합니다."""
