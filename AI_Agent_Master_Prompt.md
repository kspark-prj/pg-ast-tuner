# PostgreSQL Execution Plan Analyzer AI Agent Master Prompt

> Version: 3.0
> Target: Production-Grade Modular PostgreSQL Execution Plan Analyzer
> Stack: Python 3.11+, CustomTkinter, psycopg (v3), SQLGlot, Pydantic, Pytest

## 1. ROLE & OBJECTIVE
당신은 PostgreSQL Query Optimizer/Executor 전문가이자 Python Software Architect이다.
우리는 기존에 단일 파일(Monolithic)로 구현되어 있던 PostgreSQL 실행계획 분석기(SQLGlot 기반 AST 파싱, LIKE 전방 와일드카드 감지, 가공 컬럼 진단, 시스템 카탈로그 조회, DML/DDL 안전 차단, CustomTkinter GUI 포함)를 바탕으로, 확장성과 유지보수성이 극대화된 **Production 수준의 모듈형 아키텍처 신규 프로젝트**로 재구축하고자 한다.

기존에 검증된 핵심 분석 로직(AST 파싱, 카탈로그 리더 등)의 기능을 하나도 빠짐없이 살리면서, 아래의 설계 원칙에 맞추어 프로젝트 전체 소스 코드를 처음부터 완벽하게 작성해 달라.

## 2. CORE PRINCIPLES
- 기존 핵심 분석 알고리즘(SQLGlot AST 파싱, 와일드카드/함수 가공 감지, 안전 필터) 100% 보존 및 이관
- 엄격한 모듈형 디렉토리 및 패키지 책임 분리
- 규칙 자동 검색(Auto Discovery) 메커니즘 도입 (레지스트리 수동 등록 금지)
- 새로운 규칙 추가 시 rules/ 하위에 파일 하나만 추가하면 엔진 수정 없이 즉시 반영되는 구조
- PostgreSQL 14~17 호환성 지원

## 3. TARGET ARCHITECTURE (디렉토리 구조)
project/
    ├── main.py                     # CustomTkinter GUI 진입점 및 컨트롤러
    ├── config.py                   # DB 접속 및 환경 설정 관리
    ├── core/
    │   ├── __init__.py
    │   ├── engine.py               # RuleEngine 및 Auto Discovery 코어
    │   ├── catalog.py              # 시스템 카탈로그 및 메타데이터 리더 (PGMetadataProvider)
    │   └── parser.py               # SQL AST 파서 및 Explain 분석기 (PGPlanAnalyzer)
    ├── models/
    │   ├── __init__.py
    │   └── recommendation.py       # Pydantic 기반 표준 리포트 모델
    ├── rules/
    │   ├── __init__.py
    │   ├── base_rule.py            # 모든 Rule이 상속받는 Abstract Base Interface
    │   ├── scan/                   # SeqScanRule, IndexScanRule 등
    │   ├── join/                   # HashJoinRule, NestedLoopRule 등
    │   ├── statistics/             # Statistics/Cardinality 관련 룰
    │   └── ... 
    └── tests/
        └── test_rules.py           # Pytest 기반 단위 테스트

## 4. RULE DISCOVERY & ENGINE MECHANISM
- Rule는 Registry에 수동으로 등록하지 않는다. (`engine.add_rule()`, `RULES = []`, `registry.py` 사용 금지)
- `RuleEngine`은 파이썬 표준 라이브러리(`pkgutil`, `importlib`, `__subclasses__`)를 활용하여 `rules/` 패키지 하위의 모든 클래스를 자동 탐색한다.
- 새 Rule 추가 시 `rules/` 폴더 하위에 파일 하나만 생성하며, **RuleEngine 및 `main.py` 코드는 절대 수정하지 않는다.**

## 5. RULE TYPES & INTERFACE
- **NodeRule**: 특정 노드 전용 (예: `SeqScanRule`, `SortRule`, `HashJoinRule`)
- **GenericRule**: 여러 노드 공통 (예: `CardinalityRule`, `CostRule`)
- **PlanRule**: 전체 실행 계획 분석 (예: `JoinOrderRule`, `ParallelSkewRule`)

### Mandatory Rule Metadata
모든 Rule은 다음 속성을 가진다:
- `RULE_ID`, `NAME`, `DESCRIPTION`, `CATEGORY`, `TARGET_NODE_TYPES`, `SUPPORTED_PG_VERSION`, `DEFAULT_PRIORITY`, `DEFAULT_SEVERITY`

### Mandatory Rule Interface
- `match(context, node) -> bool`
- `analyze(context, node) -> RecommendationModel`

## 6. RECOMMENDATION MODEL (데이터 모델)
Pydantic 또는 Dataclass 기반으로 아래 구조를 통일한다:
- `title`, `description`, `severity` (CRITICAL / WARNING / INFO)
- `priority`, `reason`, `recommendation`, `recommended_sql`
- `references`, `plan_node`, `estimated_gain`, `false_positive_risk`

## 7. FALSE POSITIVE POLICY & SAFETY RULES
- 무조건 Index 추천 금지 (Seq Scan이 최선이거나 소형 테이블이면 예외 처리)
- DML/DDL 구문은 `EXPLAIN ANALYZE` 수행 전 사전에 차단
- 인덱스 생성 추천 SQL은 무중단 운영을 위해 `CONCURRENTLY` 옵션 적극 활용

## 8. PROHIBITED (엄격한 금지 사항)
- ❌ `engine.add_rule()` 또는 수동 레지스트리 작성
- ❌ `registry.py` 생성
- ❌ 신규 룰 추가를 위해 `RuleEngine`이나 `main.py`를 수정하는 행위
- ❌ 노드 타입별 하드코딩된 거대한 `if-else` 분기문 증가

## 9. OUTPUT REQUIREMENT
위 요구사항에 맞춰 즉시 복사하여 프로젝트를 구성할 수 있도록 **각 파일별 전체 소스 코드(main.py, config.py, core/*.py, models/*.py, rules/base_rule.py, rules/scan/seq_scan_rule.py 등)**를 누락 없이 구체적이고 완성도 있게 작성해 주세요.