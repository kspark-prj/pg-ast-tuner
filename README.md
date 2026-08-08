# PostgreSQL Production-Grade Performance Tuner (AST Core)

본 프로젝트는 **PostgreSQL 데이터베이스**의 실제 실행 계획(Explain Plan)과 SQL AST(Abstract Syntax Tree) 분석을 결합하여, 성능 병목 구간을 진단하고 지식 기반의 맞춤형 인덱스 및 설정 튜닝 가이드를 자동으로 제공하는 데스크톱 애플리케이션입니다.

기존 단일 파일(Monolithic) 구조에서 확장성과 유지보수성이 극대화된 **Production 수준의 모듈형 아키텍처**로 새롭게 재구축되었습니다.

---

## 📌 주요 핵심 기능 (Key Features)

- **실제 실행계획(Raw Plan) 최상단 탑재**: DB 옵티마이저가 수립한 표준 텍스트 기반 트리 구조의 실행 계획을 리포트 최상단에 직관적으로 시각화하여 튜닝 신뢰성을 높입니다.
- **SQL AST 분석 기반 지식 매핑**: `sqlglot` 파서를 통해 SQL의 논리적 구조를 완전히 분해(AST)하여, 테이블 별칭(Alias) 및 조건절에 사용된 컬럼 정보를 정확히 타겟팅합니다.
- **시스템 카탈로그 교차 검증**: 단순히 쿼리문만 파싱하는 것에 그치지 않고, `pg_class`, `pg_index` 등 데이터베이스 시스템 카탈로그를 실시간 조회하여 인덱스 구성 상태 및 실제 데이터 테이블 크기(Row Count)를 고려한 정밀 휴리스틱 진단을 수행합니다.
- **규칙 자동 검색(Auto-Discovery) 엔진**: 새 규칙 추가 시 `rules/` 하위에 파일만 생성하면 엔진 코드나 GUI 코드 수정 없이 동적으로 탐색되어 즉시 반영됩니다.
- **안전한 온디맨드(On-Demand) 트랜잭션 및 이중 락다운**: 분석 버튼을 누르는 즉시 연결을 맺고 완료 즉시 차단하며, `Explain (Analyze)` 등으로 인한 데이터 변경 가능성을 방지하기 위해 강제 롤백(Rollback) 세션 구조를 채택했습니다. 또한, 기존의 정적 문자열 매칭 한계를 극복하고 **SQL AST 분석을 활용하여 CTE(WITH 절) 내의 DML/DDL 우회 시도까지 이중으로 완전 차단**합니다.
- **스마트 주석 처리(Comment Stripper)**: 한 줄 주석(`--`) 및 인라인 블록 주석(`/* ... */`)이 섞여 있는 대용량 실무 쿼리도 에러 없이 완벽하게 정제하여 처리합니다.

---

## 📂 프로젝트 모듈 구조 (Modular Architecture)

본 프로젝트는 엄격한 책임 분리 원칙에 따라 모듈화되었습니다:

```
project/
├── main.py                     # CustomTkinter GUI 진입점 및 뷰 컨트롤러
├── config.py                   # DB 접속 및 환경 설정 관리자 (ConfigManager)
├── core/
│   ├── __init__.py
│   ├── engine.py               # RuleEngine 및 자동 규칙 탐색(Auto-Discovery) 코어
│   ├── catalog.py              # 시스템 카탈로그 조회 및 메타데이터 리더 (PGMetadataProvider)
│   └── parser.py               # SQL AST 파서 및 Explain 분석기 (PGPlanAnalyzer)
├── models/
│   ├── __init__.py
│   └── recommendation.py       # Pydantic 기반 표준 권장 리포트 데이터 모델
├── rules/
│   ├── __init__.py
│   ├── base_rule.py            # 모든 규칙이 상속받는 Abstract Base Rule & RuleContext
│   ├── scan/                   # SeqScanRule, IndexScanRule 등 스캔 관련 룰
│   ├── join/                   # HashJoinRule, NestedLoopRule 등 조인 관련 룰
│   └── statistics/             # TempFileRule, ParallelWorkersRule, SortRule 및 메모리/구조적 진단 룰
└── tests/
    └── test_rules.py           # Pytest 기반 단위 및 탐색 통합 테스트
```

---

## 🔍 진단 규칙(Rules) 현황 (총 30종)

분석 엔진은 총 30가지의 정적 및 동적 분석 휴리스틱 규칙을 탑재하고 있으며, 실행 계획 노드별 적합성을 자동 판별하여 튜닝 처방을 발행합니다.

### 1. 스캔 진단 규칙 (SCAN Category)

| 규칙 ID         | 규칙 클래스명                 | 진단 대상 노드              | 진단 및 권장 내용                                                                                                    |
| :-------------- | :---------------------------- | :-------------------------- | :------------------------------------------------------------------------------------------------------------------- |
| `RULE_SCAN_001` | `SeqScanRule`                 | Seq Scan                    | 풀 스캔 시 인덱스 누락, 소형 테이블 여부, OR 조건, LIKE 전방 와일드카드, 함수 가공(Index Suppression) 여부 종합 진단 |
| `RULE_SCAN_002` | `IndexScanRule`               | Index Scan                  | 인덱스 스캔 사용 시 인덱스 적정성 진단 (과도한 인덱스 조회 등)                                                       |
| `RULE_SCAN_003` | `BitmapHeapScanLossyRule`     | Bitmap Heap Scan            | `work_mem` 부족으로 인한 비트맵 Lossy 블록 전환 및 Recheck 힙 페이지 접근 진단                                       |
| `RULE_SCAN_004` | `IndexOnlyScanHeapFetchRule`  | Index Only Scan             | Visibility Map 미갱신으로 인한 과도한 테이블 힙 접근(Heap Fetches) 진단                                              |
| `RULE_SCAN_005` | `HighFilterRemovalRatioRule`  | Seq Scan, Index Scan 등     | 스캔 후 Filter 조건으로 버려지는 행(Rows Removed) 비율이 높아 발생하는 I/O 낭비 진단 (90% 이상 버려질 시)            |
| `RULE_SCAN_006` | `SubqueryScanRepetitionRule`  | Subquery Scan               | 상관 서브쿼리나 미튜닝 스칼라 서브쿼리가 상위 루프만큼 반복 실행(N+1 스캔 병목)되는지 진단                           |
| `RULE_SCAN_007` | `IndexFilterInefficiencyRule` | Index Scan, Index Only Scan | Index Cond이 아닌 Index Filter로 과도한 행이 스캔되는 비효율 진단 (선행 컬럼 Prefix Match 평가 포함)                 |
| `RULE_SCAN_008` | `StaleVisibilityMapRule`      | Seq Scan, Bitmap Heap Scan  | 데드 튜플(Dead Tuples) 및 테이블 블로트(Bloat)로 인한 불필요한 I/O 대량 발생 진단                                    |

### 2. 조인 진단 규칙 (JOIN Category)

| 규칙 ID         | 규칙 클래스명                      | 진단 대상 노드         | 진단 및 권장 내용                                                                                               |
| :-------------- | :--------------------------------- | :--------------------- | :-------------------------------------------------------------------------------------------------------------- |
| `RULE_JOIN_001` | `HashJoinRule`                     | Hash Join              | 해시 테이블 빌드 크기가 `work_mem`을 초과하여 디스크로 임시 스필(Spill)되었는지 감지                            |
| `RULE_JOIN_002` | `NestedLoopRule`                   | Nested Loop            | 내부 드라이븐 테이블(Driven Table)에 조인 키 인덱스가 없어 반복적인 풀 스캔이 유발되는지 진단                   |
| `RULE_JOIN_003` | `MergeJoinSortRule`                | Merge Join             | 정렬된 입력이 필요한 Merge Join에서 하위 노드에 인덱스가 없어 명시적 Sort 연산이 발생하는지 진단                |
| `RULE_JOIN_004` | `NestedLoopHighLoopsRule`          | Nested Loop            | 내부 테이블 반복 탐색 횟수(Loops)가 과도하게 많아(10만회 이상) 발생하는 랜덤 I/O 및 CPU 부하 진단               |
| `RULE_JOIN_005` | `HashJoinLargeBuildTableRule`      | Hash Join              | 통계 정보 불일치 등으로 인해 더 작은 집합이 아닌 대량 데이터 테이블이 해시 빌드(Build Side)로 지정되었는지 진단 |
| `RULE_JOIN_006` | `JoinCardinalityMisestimationRule` | Hash/NL/Merge Join     | 옵티마이저 예측 행 수(Plan Rows)와 실제 처리 행 수(Actual Rows) 간 10배 이상의 큰 카디널리티 오차 진단          |
| `RULE_JOIN_007` | `CrossJoinRule`                    | Nested Loop, Hash Join | 조인 조건이 누락되거나 잘못 설정되어 발생하는 카티시안 곱(Cartesian Product, Cross Join) 진단                   |
| `RULE_JOIN_008` | `ParallelJoinWorkerLossRule`       | Gather, Gather Merge   | 병렬 조인 수행 시 계획된 워커 수보다 실제 실행 시 할당된 워커 수(Workers Launched)가 부족한 현상 진단           |
| `RULE_JOIN_009` | `HashJoinBatchInflationRule`       | Hash Join              | 빌드 데이터 예측 실패로 인해 실행 중 해시 배치 수가 최초 예상보다 동적으로 폭증(8배 이상)했는지 진단            |

### 3. 통계 및 리소스 진단 규칙 (STATISTICS Category)

| 규칙 ID         | 규칙 클래스명              | 진단 대상 노드       | 진단 및 권장 내용                                                                                                                |
| :-------------- | :------------------------- | :------------------- | :------------------------------------------------------------------------------------------------------------------------------- |
| `RULE_STAT_001` | `TempFileRule`             | 전체 (\*)            | 정렬, 해시, 그룹화 연산 중 `work_mem` 부족으로 임시 파일 쓰기(Temp Written Blocks)가 발생한 디스크 I/O 병목 진단                 |
| `RULE_STAT_002` | `ParallelWorkersRule`      | 전체 (\*)            | 병렬 처리 및 Gather 노드 수행 시 너무 많은 워커(4개 이상)가 계획되어 가용 자원을 빠르게 소모하는 오버헤드 진단                   |
| `RULE_STAT_003` | `SortRule`                 | Sort                 | 정렬 연산 시 디스크 정렬(External Sort)이 유발되거나 LIMIT 조건 하에서 정렬 인덱스 미적용으로 대규모 Quicksort가 유발되는지 진단 |
| `RULE_STAT_004` | `DiskHashAggRule`          | Aggregate            | GROUP BY/집계 연산 처리 중 메모리가 부족하여 디스크 기반 해시 집계(Disk Used > 0)가 발생했는지 감지                              |
| `RULE_STAT_005` | `ParallelWorkerSkewRule`   | Gather, Gather Merge | 병렬 워커 간 데이터 처리량 차이가 5배 이상으로 한쪽 워커에 편중되어 병목이 발생하는지 감지                                       |
| `RULE_STAT_006` | `JITOverheadRule`          | 전체 (\*)            | JIT(Just-In-Time) 컴파일 작업에 총 100ms 이상의 과도한 시간이 소요되는 컴파일 오버헤드 진단                                      |
| `RULE_STAT_007` | `IncrementalSortSpillRule` | Incremental Sort     | 증분 정렬 수행 중 부분 정렬 메모리 한계를 초과하여 디스크 스필(Sort Space Used)이 일어나는지 진단                                |

### 4. 메모리 진단 규칙 (MEMORY Category)

| 규칙 ID        | 규칙 클래스명              | 진단 대상 노드        | 진단 및 권장 내용                                                               |
| :------------- | :------------------------- | :-------------------- | :------------------------------------------------------------------------------ |
| `RULE_MEM_001` | `ExcessiveWorkMemRule`     | Sort, Hash, Aggregate | 단일 연산 노드에서 지나치게 높은 `work_mem`을 할당하여 사용 중인지 진단         |
| `RULE_MEM_002` | `BufferCacheMissRatioRule` | 전체 (\*)             | Shared Buffers 메모리 히트율이 낮아 실제 디스크 Read I/O 병목이 발생하는지 진단 |

### 5. 구조적 진단 규칙 (STRUCTURAL Category)

| 규칙 ID        | 규칙 클래스명                   | 진단 대상 노드                      | 진단 및 권장 내용                                                                                     |
| :------------- | :------------------------------ | :---------------------------------- | :---------------------------------------------------------------------------------------------------- |
| `RULE_STR_001` | `CTEInliningFailureRule`        | CTE Scan                            | WITH 절(CTE) 사용 시 Materialize 되면서 인라이닝 최적화가 방해받고 있는지 진단                        |
| `RULE_STR_002` | `ForeignTableScanRule`          | Foreign Scan                        | FDW(Foreign Data Wrapper) 원격 테이블 스캔 시 푸시다운(Pushdown) 실패로 대량 데이터가 전송되는지 진단 |
| `RULE_STR_003` | `ConstraintTriggerOverheadRule` | ModifyTable, Insert, Update, Delete | DML(INSERT/UPDATE/DELETE) 수행 중 FK 검증 또는 트리거 실행 지연 요소 진단                             |
| `RULE_STR_004` | `HotUpdateFailureRule`          | Update                              | UPDATE 시 HOT(Heap-Only Tuple) 최적화가 적용되지 못해 인덱스 블록 수정 오버헤드가 발생하는지 진단     |

---

## 🎨 UI/UX 디자인 스키마 & 안구 보호 가이드

모니터를 장시간 응시하는 개발자와 DBA의 눈부심 및 안구 피로를 경감시키기 위해 일반 다크 모드의 순수 화이트(`#FFFFFF`) 배색을 지양하고, 부드러운 저명도 파스텔 컬러 팔레트를 전면 도입하였습니다.

| 요소명                       | 색상 코드 (HEX) | 의미 및 역할                                                     |
| ---------------------------- | --------------- | ---------------------------------------------------------------- |
| **Deep Charcoal Background** | `#1C1D1F`       | 에디터 및 리포트 창의 핵심 배경색으로 눈의 긴장 완화             |
| **Cream White Normal Text**  | `#D8DEE9`       | 가독성을 확보하면서 명도 대비를 최적화한 본문 텍스트             |
| **Muted Blue SQL Accent**    | `#8FC7FF`       | 권장 생성 인덱스(DDL) 등 SQL 스크립트 전용 하이라이트            |
| **Pastel Mint Success**      | `#A1EF9B`       | 최적화 성공 상태(`INFO`) 알림                                    |
| **Soft Gold Warning**        | `#F4D35E`       | 통계 최신화 권장, 메모리 정렬 버틀넥(`WARNING`) 경고             |
| **Rose Pink Critical**       | `#F97B7D`       | 인덱스 누락으로 인한 Seq Scan 탐지(`CRITICAL`) 및 구문 오류 알림 |

---

## 🔄 3-Way 하이브리드 교차 검증 아키텍처

이 도구는 작성하신 쿼리가 성능 문제를 일으키는지 진단하기 위해 아래 **3가지 데이터 원천**을 유기적으로 대조합니다.

```
                  ┌──────────────────────┐
                  │   1. SQL AST Tree    │
                  │ (필터링 대상 컬럼 추출)  │
                  └──────────┬───────────┘
                             │ (교차 검증)
   ┌─────────────────────────┼─────────────────────────┐
   │                                                   │
 ┌──┴─────────────────────┐                           ┌─┴──────────────────────┐
 │  2. 시스템 카탈로그     │                           │  3. EXPLAIN 실행 계획  │
 │ (실제 인덱스 & 테이블 크기)│                           │ (옵티마이저의 선택 노드)│
 └────────────────────────┘                           └────────────────────────┘
```

#### 1단계. SQL AST 문법 트리 분석 (`sqlglot` 엔진)

- **목적**: 쿼리에서 "필터링(`WHERE`)이나 정렬(`ORDER BY`)에 실제로 사용된 테이블과 컬럼"이 무엇인지 식별합니다.
- **이유**: 단순 정규식이나 문자열 검색은 테이블 별칭(Alias, 예: `orders o` -> `o.user_id`)이나 복잡한 서브쿼리 내의 컬럼을 제대로 짚어내지 못합니다. AST 파서는 이를 트리 구조로 완벽히 쪼개어 `orders` 테이블의 `user_id` 컬럼이 조건절에 쓰였음을 명확히 알아냅니다.

#### 2단계. PostgreSQL 시스템 카탈로그 조회 (`PGMetadataProvider`)

- **목적**: 해당 테이블에 **"실제 어떤 인덱스들이 만들어져 있는지"**, 그리고 **"테이블 크기(Row Count)가 얼마나 큰지"** 확인합니다.
- **이유**: 소량의 데이터(예: 10건)가 들어있는 테이블은 인덱스가 있어도 옵티마이저가 풀 스캔(`Seq Scan`)을 해버립니다. 따라서 카탈로그를 조회해 실제 테이블 규모와 인덱스 컬럼 목록(`user_id`가 인덱스 첫 열로 지정되어 있는지 등)을 파악합니다.

#### 3단계. EXPLAIN 실행 계획 추적 (`PGPlanAnalyzer`)

- **목적**: PostgreSQL 옵티마이저가 실제로 수립한 "물리적 실행 계획"을 받아옵니다.
- **이유**: 아무리 쿼리를 잘 짜고 인덱스가 있어도, 옵티마이저가 엉뚱한 길을 선택할 수 있기 때문입니다. 실행 계획상에 `Seq Scan` 노드가 찍혔는지를 최종 확인합니다.

---

## 🛠️ 자동 규칙 추가 및 탐색 방식 (Adding New Rules)

본 프로젝트는 OCP(Open-Closed Principle)를 지향하여 설계되었습니다. 새로운 분석 룰(Rule)을 추가할 때 **엔진 코드(`engine.py`)나 UI 코드(`main.py`)를 전혀 수정할 필요가 없습니다.**

### 규칙 추가 단계:

1. `rules/` 하위의 적절한 카테고리 폴더(예: `rules/scan/`)에 새 파이썬 파일 생성
2. `BaseRule` 클래스를 상속하는 규칙 클래스 정의 및 필수 메타데이터/메서드 구현:

```python
from rules.base_rule import BaseRule, RuleContext
from models.recommendation import RecommendationModel

class MyCustomScanRule(BaseRule):
    RULE_ID = "RULE_SCAN_999"
    NAME = "MyCustomScanRule"
    DESCRIPTION = "나만의 커스텀 스캔 검증 규칙"
    CATEGORY = "SCAN"
    TARGET_NODE_TYPES = ["Seq Scan"] # 검사 대상 실행 계획 노드 타입 설정 (* 지정 시 전체 대상)
    SUPPORTED_PG_VERSION = ">=14"
    DEFAULT_PRIORITY = 3
    DEFAULT_SEVERITY = "WARNING"

    def match(self, context: RuleContext, node: dict) -> bool:
        # 이 노드가 분석 대상인지 여부를 판단하는 boolean 반환
        return "Relation Name" in node

    def analyze(self, context: RuleContext, node: dict) -> RecommendationModel:
        # Pydantic 모델 형태의 처방전 생성 및 반환
        return RecommendationModel(
            title="나만의 튜닝 경고",
            description="상세 설명 내용...",
            severity=self.DEFAULT_SEVERITY,
            priority=self.DEFAULT_PRIORITY,
            reason="이러한 이유로 성능 저하가 발생했습니다.",
            recommendation="이렇게 인덱스를 설계하여 해결하십시오.",
            recommended_sql="CREATE INDEX CONCURRENTLY ...",
            plan_node=node.get("Node Type")
        )
```

---

## 🚀 요구 사항 및 실행 방법 (Quick Start)

### ⚙️ Prerequisites

실행을 위해 아래 라이브러리 설치가 필요합니다.

```bash
pip install customtkinter psycopg sqlglot pydantic pytest
```

### 🏃 GUI 실행 방법

```bash
python main.py
```

### 🧪 단위 테스트 실행 방법

작성된 규칙들의 기능 및 동적 디스커버리 엔진 작동 상태를 검증합니다.

```bash
python -m pytest
```

---

## 📦 패키징 가이드 (Executable Build)

Windows 환경 등에서 단일 실행 파일(`.exe`)로 배포하고 싶은 경우, 파이썬 환경 불일치를 방지하고 `psycopg` 등의 의존성을 올바르게 포함하기 위해 아래와 같이 **현재 파이썬 환경의 모듈 방식으로 실행**하는 것을 권장합니다.

### 1. Spec 파일 기반 빌드 (권장)

이미 프로젝트 루트에 구성되어 있는 [`main.spec`](file:///C:/Users/kspar/Tools/github/pg-ast-tuner/main.spec) 파일에는 `psycopg` 모듈 수집(`collect_all`) 및 아이콘 설정 등이 모두 정의되어 있습니다.

```bash
# PyInstaller가 설치되어 있지 않다면 먼저 설치
pip install pyinstaller

# Spec 파일을 사용하여 빌드 실행
python -m PyInstaller main.spec
```

### 2. 커맨드라인 명령어로 직접 빌드할 경우

Spec 파일 없이 명령어로 직접 빌드하는 경우, `psycopg` 모듈의 동적 바인딩 파일들을 수집하도록 `--collect-all` 옵션을 반드시 포함해야 합니다.

```bash
python -m PyInstaller --clean --noconfirm -w -D --icon=main.ico --collect-all psycopg --collect-all sqlglot --collect-all rules --exclude-module PIL --exclude-module Pillow --exclude-module pytest --exclude-module matplotlib --exclude-module tkinter.test --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module PySide2 --exclude-module PySide6 --exclude-module scipy --exclude-module pandas --exclude-module IPython --exclude-module notebook --exclude-module tornado main.py

```
