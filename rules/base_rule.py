from abc import ABC, abstractmethod
from typing import List, Union, Any
from models.recommendation import RecommendationModel

class RuleContext:
    def __init__(
        self,
        raw_query: str,
        clean_query: str,
        metadata_provider: Any,
        plan_data: Any = None
    ):
        self.raw_query = raw_query
        self.clean_query = clean_query
        self.metadata_provider = metadata_provider
        self.plan_data = plan_data

class BaseRule(ABC):
    RULE_ID: str
    NAME: str
    DESCRIPTION: str
    CATEGORY: str
    TARGET_NODE_TYPES: List[str]
    SUPPORTED_PG_VERSION: str = "all"
    DEFAULT_PRIORITY: int = 3
    DEFAULT_SEVERITY: str = "INFO"

    @abstractmethod
    def match(self, context: RuleContext, node: dict) -> bool:
        """
        이 룰이 현재 노드에 부합하는지 여부를 판단합니다.
        """
        pass

    @abstractmethod
    def analyze(self, context: RuleContext, node: dict) -> Union[RecommendationModel, List[RecommendationModel]]:
        """
        노드를 정밀 진단하고 RecommendationModel 또는 그 리스트를 반환합니다.
        """
        pass
