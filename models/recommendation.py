from pydantic import BaseModel, Field
from typing import Optional, List

class RecommendationModel(BaseModel):
    title: str = Field(..., description="튜닝 권장 제목 (Title of tuning recommendation)")
    description: str = Field(..., description="튜닝 권장 상세 설명 (Detailed description)")
    severity: str = Field(..., description="위험도 (CRITICAL / WARNING / INFO)")
    priority: int = Field(default=3, description="우선순위 (1: Highest, 5: Lowest)")
    reason: str = Field(..., description="성능 저하 원인 및 현상 (Why this is an issue)")
    recommendation: str = Field(..., description="구체적이고 액션 가능한 해결 방안 (Actionable recommendation)")
    recommended_sql: Optional[str] = Field(default=None, description="즉시 실행 가능한 권장 SQL 스크립트")
    references: Optional[List[str]] = Field(default=None, description="참고 문서 및 링크")
    plan_node: Optional[str] = Field(default=None, description="영향을 받는 실행 계획 노드 타입 (e.g. Seq Scan)")
    estimated_gain: Optional[str] = Field(default=None, description="예상 성능 향상 효과")
    false_positive_risk: Optional[str] = Field(default=None, description="오진 위험도 또는 제약 사항")
