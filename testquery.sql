-- ==============================================================================
-- PostgreSQL 성능 진단 룰(Total 35 Rules) 순수 SQL 검증 스크립트
-- ==============================================================================
-- 본 스크립트는 pg-ast-tuner의 EXPLAIN 분석 엔진에 직접 입력하여 테스트할 수 있도록
-- EXPLAIN 키워드 및 GUC 제어문(SET/RESET)을 제거하고 순수 SQL 구문으로만 구성되었습니다.
-- ==============================================================================

--------------------------------------------------------------------------------
-- 0. 환경 초기화 및 기본 테이블 / 인덱스 / 확장기능 세팅
--------------------------------------------------------------------------------
DROP TABLE IF EXISTS test_lineitems CASCADE;
DROP TABLE IF EXISTS test_orders CASCADE;
DROP TABLE IF EXISTS test_users CASCADE;
DROP FOREIGN TABLE IF EXISTS test_foreign_orders CASCADE;
DROP SERVER IF EXISTS mock_server CASCADE;

-- [0-1] postgres_fdw 설정 (ForeignTableScanRule 테스트용)
CREATE EXTENSION IF NOT EXISTS postgres_fdw;
CREATE SERVER IF NOT EXISTS mock_server FOREIGN DATA WRAPPER postgres_fdw OPTIONS (dbname 'postgres');
CREATE USER MAPPING IF NOT EXISTS FOR CURRENT_USER SERVER mock_server OPTIONS (user 'postgres');

-- [0-2] 1. 사용자 테이블 (test_users - 소/중형)
CREATE TABLE test_users (
    user_id SERIAL PRIMARY KEY,
    username VARCHAR(50),
    user_category VARCHAR(20),
    created_at TIMESTAMP DEFAULT NOW()
);

-- [0-3] 2. 주문 테이블 (test_orders - 대용량)
CREATE TABLE test_orders (
    order_id SERIAL PRIMARY KEY,
    user_id INT,
    order_amount NUMERIC(10, 2),
    order_status VARCHAR(20),
    order_date TIMESTAMP,
    padding TEXT
);

-- [0-4] 3. 주문 상세 테이블 (test_lineitems - 대용량)
CREATE TABLE test_lineitems (
    lineitem_id SERIAL PRIMARY KEY,
    order_id INT,
    item_name VARCHAR(100),
    price NUMERIC(10, 2),
    quantity INT,
    padding TEXT
);

-- [0-5] Foreign Table 생성 (ForeignTableScanRule 테스트용)
CREATE FOREIGN TABLE test_foreign_orders (
    order_id INT,
    user_id INT,
    order_amount NUMERIC(10, 2)
) SERVER mock_server OPTIONS (table_name 'test_orders');

-- [0-6] 더미 데이터 생성 (test_users: 1,000건 / test_orders: 200,000건 / test_lineitems: 300,000건)
INSERT INTO test_users (username, user_category, created_at)
SELECT
    'user_' || g,
    CASE WHEN g % 5 = 0 THEN 'VIP' ELSE 'NORMAL' END,
    NOW() - (g || ' days')::INTERVAL
FROM generate_series(1, 1000) g;

INSERT INTO test_orders (user_id, order_amount, order_status, order_date, padding)
SELECT
    floor(random() * 1000 + 1)::int,
    (random() * 500 + 10)::numeric(10,2),
    CASE
        WHEN random() < 0.1 THEN 'PENDING'    -- 약 10%
        WHEN random() < 0.3 THEN 'CANCELLED'  -- 약 20%
        ELSE 'COMPLETED'                      -- 나머지 약 70%
    END,
    NOW() - (random() * 365 || ' days')::INTERVAL,
    repeat('A', 150)
FROM generate_series(1, 200000);

INSERT INTO test_lineitems (order_id, item_name, price, quantity, padding)
SELECT
    floor(random() * 200000 + 1)::int,
    'Item_' || (g % 50),
    (random() * 100 + 1)::numeric(10,2),
    floor(random() * 5 + 1)::int,
    repeat('B', 150)
FROM generate_series(1, 300000) g;

-- [0-7] 테스트용 보조 인덱스 생성
CREATE INDEX idx_orders_user_id ON test_orders(user_id);
CREATE INDEX idx_orders_order_date ON test_orders(order_date);
CREATE INDEX idx_orders_status_amount ON test_orders(order_status, order_amount);

-- [0-8] 통계 정보 최신화
ANALYZE test_users;
ANALYZE test_orders;
ANALYZE test_lineitems;


-- ==============================================================================
-- PART 1. JOIN PACKAGE RULES TEST (rules/join/)
-- ==============================================================================

--------------------------------------------------------------------------------
-- 1-1. HashJoinRule.py (RULE_JOIN_001) / SeqScanRule.py (RULE_SCAN_001)
-- 진단 룰 매핑 분석:
--  - 기본 세션 상태(work_mem >= 4MB): 해시 조인이 메모리 내(Batches=1)에서 완료되어
--    테이블 전체 스캔에 대한 RULE_SCAN_001 가이드가 출력됩니다.
--  - work_mem 부족 상태(SET work_mem = '64kB';): 해시 테이블 빌드 시 디스크 스필(Batches > 1)이
--    발생하여 RULE_JOIN_001(해시 조인 디스크 스필) 룰이 함께 검출됩니다.
--------------------------------------------------------------------------------
SELECT o.order_id, o.order_amount, l.item_name
FROM test_orders o
JOIN test_lineitems l ON o.order_id = l.order_id;


--------------------------------------------------------------------------------
-- 1-2. nested_loop_rule.py (RULE_JOIN_002)
-- 진단 룰 매핑 분석:
--  - 옵티마이저가 Nested Loop 결합 방식을 유도하도록 단일 행 서치(WHERE order_id = 100) 및
--    비인덱스 컬럼(order_amount = user_id) 조인을 수행합니다.
--  - 내부 드라이븐 테이블(test_users)에 대해 반복 풀 스캔이 발생하여 RULE_JOIN_002 룰이 진단됩니다.
--------------------------------------------------------------------------------
SELECT o.order_id, u.username
FROM test_orders o
JOIN test_users u ON o.order_amount = u.user_id
WHERE o.order_id = 100;


--------------------------------------------------------------------------------
-- 1-3. MergeJoinSortRule.py (RULE_JOIN_003)
-- 목적: Merge Join 수행 전 인덱스 부재로 인한 Explicit Sort 발생 케이스
--------------------------------------------------------------------------------
SELECT o.order_id, u.user_id
FROM test_orders o
JOIN test_users u ON o.order_amount = u.user_id;


--------------------------------------------------------------------------------
-- 1-4. NestedLoopHighLoopsRule.py (RULE_JOIN_004)
-- 목적: Nested Loop 발생 시 Outer/Inner Loop 반복 횟수(Loops)가 과도하게 높은 케이스 탐지
--------------------------------------------------------------------------------
SELECT l.lineitem_id, u.username
FROM test_lineitems l
JOIN test_orders o ON l.order_id = o.order_id
JOIN test_users u ON o.user_id = u.user_id
WHERE l.price > 95.00;


--------------------------------------------------------------------------------
-- 1-5. HashJoinLargeBuildTableRule.py (RULE_JOIN_005)
-- 목적: Hash Join 시 Build Side(오른쪽 자식)에 대용량 테이블이 배치되는 현상 탐지
--------------------------------------------------------------------------------
SELECT o.order_id, u.username
FROM test_users u
JOIN test_orders o ON u.user_id = o.user_id;


--------------------------------------------------------------------------------
-- 1-6. JoinCardinalityMisestimationRule.py (RULE_JOIN_006)
-- 목적: 조인 조건 함수 가공(UPPER 등)으로 옵티마이저의 예상 행 수와 실제 행 수 오차 발생
--------------------------------------------------------------------------------
SELECT o.order_id, u.username
FROM test_orders o
JOIN test_users u ON UPPER(o.order_status) = UPPER(u.user_category);


--------------------------------------------------------------------------------
-- 1-7. CrossJoinRule.py (RULE_JOIN_007)
-- 목적: 조인 조건 누락 및 카테시안 곱(Cartesian Product) 발생 감지
--------------------------------------------------------------------------------
SELECT u.username, o.order_id
FROM test_users u
CROSS JOIN test_orders o
LIMIT 100;


--------------------------------------------------------------------------------
-- 1-8. ParallelJoinWorkerLossRule.py (RULE_JOIN_008)
-- 목적: 병렬 조인(Parallel Join) 실행 시 Worker 미활용 또는 손실 케이스
--------------------------------------------------------------------------------
SELECT count(*), avg(o.order_amount)
FROM test_orders o
JOIN test_lineitems l ON o.order_id = l.order_id;


--------------------------------------------------------------------------------
-- 1-9. HashJoinBatchInflationRule.py (RULE_JOIN_009)
-- 목적: Hash Join 시 메모리 부족으로 디스크 스필(Batches > 1) 및 동적 배치 확장 케이스
--------------------------------------------------------------------------------
SELECT o.order_id, o.order_amount, l.item_name
FROM test_orders o
JOIN test_lineitems l ON o.order_id = l.order_id;


--------------------------------------------------------------------------------
-- 1-10. MemoizeCacheInefficiencyRule.py (RULE_JOIN_010)
-- 목적: Memoize 노드의 Cache Misses 및 털어내기 비효율 탐지
--------------------------------------------------------------------------------
SELECT o.order_id,
       (SELECT u.username FROM test_users u WHERE u.user_id = o.user_id)
FROM test_orders o
LIMIT 1000;


-- ==============================================================================
-- PART 2. SCAN PACKAGE RULES TEST (rules/scan/)
-- ==============================================================================

--------------------------------------------------------------------------------
-- 2-1. seq_scan_rule.py (RULE_SCAN_001)
-- 목적: 전체 테이블 스캔(Seq Scan) 및 좌변 가공(UPPER)으로 인한 인덱스 무력화 감지
--------------------------------------------------------------------------------
SELECT *
FROM test_orders
WHERE UPPER(order_status) = 'PENDING';


--------------------------------------------------------------------------------
-- 2-2. index_scan_rule.py (RULE_SCAN_002)
-- 목적: Index Scan 발생 및 비효율적 데이터 추출 탐지
--------------------------------------------------------------------------------
SELECT *
FROM test_orders
WHERE order_date >= NOW() - INTERVAL '1 day';


--------------------------------------------------------------------------------
-- 2-3. BitmapHeapScanLossyRule.py (RULE_SCAN_003)
-- 목적: Bitmap Scan 시 Lossy Pages(손실 페이지) 및 Recheck 조건 발생 탐지
--------------------------------------------------------------------------------
SELECT *
FROM test_orders
WHERE user_id BETWEEN 1 AND 800;


--------------------------------------------------------------------------------
-- 2-4. IndexOnlyScanHeapFetchRule.py (RULE_SCAN_004)
-- 목적: Index Only Scan 수행 시 Visibility Map 미갱신으로 Heap Fetches가 다수 발생하는 케이스
--------------------------------------------------------------------------------
SELECT order_status, order_amount
FROM test_orders
WHERE order_status = 'COMPLETED';


--------------------------------------------------------------------------------
-- 2-5. HighFilterRemovalRatioRule.py (RULE_SCAN_005)
-- 목적: Filter 단계에서 버려지는 비율(Filter Removal Ratio)이 90% 이상인 케이스
--------------------------------------------------------------------------------
SELECT *
FROM test_orders
WHERE order_status = 'PENDING'
  AND order_amount > 490.00;


--------------------------------------------------------------------------------
-- 2-6. SubqueryScanRepetitionRule.py (RULE_SCAN_006)
-- 목적: 상관 서브쿼리로 인해 Subquery Scan / SubPlan 노드가 반복 실행되는 케이스
--------------------------------------------------------------------------------
SELECT u.user_id,
       (SELECT COUNT(*) FROM test_orders o WHERE o.user_id = u.user_id AND o.order_amount > 200) AS high_orders
FROM test_users u;


--------------------------------------------------------------------------------
-- 2-7. IndexFilterInefficiencyRule.py (RULE_SCAN_007)
-- 목적: Index Scan 내에서 Index Filter로 인해 대량 행이 제거되는 비효율 탐지
--------------------------------------------------------------------------------
SELECT *
FROM test_orders
WHERE order_status = 'COMPLETED'
  AND padding LIKE 'A%';


--------------------------------------------------------------------------------
-- 2-8. StaleVisibilityMapRule.py (RULE_SCAN_008)
-- 목적: 대량 UPDATE/INSERT 후 VACUUM 미수행으로 블로팅(Bloat) 발생 탐지
--------------------------------------------------------------------------------
SELECT user_id
FROM test_orders
WHERE user_id BETWEEN 1 AND 100;


--------------------------------------------------------------------------------
-- 2-9. BitmapMultiIndexInefficiencyRule.py (RULE_SCAN_009)
-- 목적: 단일 복합 인덱스 대신 다수의 인덱스 비트맵(BitmapAnd / BitmapOr)을 결합하는 케이스
--------------------------------------------------------------------------------
SELECT *
FROM test_orders
WHERE user_id = 500
   OR order_date = NOW() - INTERVAL '10 days';


-- ==============================================================================
-- PART 3. MEMORY PACKAGE RULES TEST (rules/memory/ & rules/statistics/)
-- ==============================================================================

--------------------------------------------------------------------------------
-- 3-1. ExcessiveWorkMemRule.py (RULE_MEM_001)
-- 목적: 과도하게 높은 work_mem 설정으로 OOM 위험을 유발하는 케이스
--------------------------------------------------------------------------------
SELECT order_status, COUNT(*)
FROM test_orders
GROUP BY order_status;


--------------------------------------------------------------------------------
-- 3-2. BufferCacheMissRatioRule.py (RULE_MEM_002)
-- 목적: Shared Buffers 메모리 히트율이 낮고 디스크 Read I/O가 대량 발생하는 케이스
--------------------------------------------------------------------------------
SELECT *
FROM test_lineitems
WHERE padding LIKE 'XYZ%';


--------------------------------------------------------------------------------
-- 3-3. MaterializeSpillRule.py (RULE_MEM_003)
-- 목적: Materialize 노드의 디스크 스필(Storage: disk) 또는 메모리 낭비 케이스
--------------------------------------------------------------------------------
SELECT *
FROM test_users u
JOIN test_lineitems l ON u.user_id = l.order_id;


-- ==============================================================================
-- PART 4. STATISTICS PACKAGE RULES TEST (rules/statistics/)
-- ==============================================================================

--------------------------------------------------------------------------------
-- 4-1. temp_file_rule.py (RULE_STAT_001)
-- 목적: 쿼리 실행 중 임시 파일(Temp Written Blocks) 생성을 유발하는 정렬/집계 쿼리
--------------------------------------------------------------------------------
SELECT *
FROM test_orders
ORDER BY padding;


--------------------------------------------------------------------------------
-- 4-2. parallel_workers_rule.py (RULE_STAT_002)
-- 목적: 병렬 쿼리 Gather 노드의 Worker 할당 및 실행 효율성 검증
--------------------------------------------------------------------------------
SELECT COUNT(*), AVG(order_amount)
FROM test_orders;


--------------------------------------------------------------------------------
-- 4-3. sort_rule.py (RULE_STAT_003)
-- 목적: 인덱스 부재로 인한 Explicit Sort 및 External Sort 탐지
--------------------------------------------------------------------------------
SELECT *
FROM test_orders
ORDER BY order_amount DESC;


--------------------------------------------------------------------------------
-- 4-4. DiskHashAggRule.py (RULE_STAT_004)
-- 목적: HashAggregate 수행 중 디스크 기반 해시 집계(Disk-based HashAgg) 스필 케이스
--------------------------------------------------------------------------------
SELECT order_id, COUNT(*), AVG(price)
FROM test_lineitems
GROUP BY order_id;


--------------------------------------------------------------------------------
-- 4-5. ParallelWorkerSkewRule.py (RULE_STAT_005)
-- 목적: 병렬 Worker 간 데이터 처리 편향(Worker Skew) 현상 탐지
--------------------------------------------------------------------------------
SELECT user_id, COUNT(*)
FROM test_orders
GROUP BY user_id;


--------------------------------------------------------------------------------
-- 4-6. JITOverheadRule.py (RULE_STAT_006)
-- 목적: JIT(Just-In-Time) 컴파일 시간 비중이 실행 시간 대비 과도한 비효율 탐지
--------------------------------------------------------------------------------
SELECT order_status, AVG(order_amount), COUNT(*)
FROM test_orders
GROUP BY order_status;


--------------------------------------------------------------------------------
-- 4-7. IncrementalSortSpillRule.py (RULE_STAT_007)
-- 목적: 부분 정렬(Incremental Sort) 수행 중 디스크 스필 발생하는 케이스
--------------------------------------------------------------------------------
SELECT user_id, order_date, order_amount
FROM test_orders
ORDER BY user_id, order_amount;


--------------------------------------------------------------------------------
-- 4-8. WindowAggSortOverheadRule.py (RULE_STAT_008)
-- 목적: WindowAgg(윈도우 함수) 사용 시 PARTITION BY / ORDER BY 정렬 오버헤드 탐지
--------------------------------------------------------------------------------
SELECT order_id, user_id, order_amount,
       ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY order_amount DESC) as rn
FROM test_orders;


-- ==============================================================================
-- PART 5. STRUCTURAL PACKAGE RULES TEST (rules/structural/ & rules/scan/ & rules/statistics/)
-- ==============================================================================

--------------------------------------------------------------------------------
-- 5-1. CTEInliningFailureRule.py (RULE_STR_001)
-- 목적: WITH절(CTE)에 AS MATERIALIZED 지정으로 Subquery Scan/Materialize 발생 케이스
--------------------------------------------------------------------------------
WITH cted_orders AS MATERIALIZED (
    SELECT user_id, order_amount
    FROM test_orders
    WHERE order_status = 'COMPLETED'
)
SELECT user_id, SUM(order_amount)
FROM cted_orders
GROUP BY user_id;


--------------------------------------------------------------------------------
-- 5-2. ForeignTableScanRule.py (RULE_STR_002)
-- 목적: FDW(Foreign Data Wrapper) 외부 테이블 스캔(Foreign Scan) 발생 탐지
--------------------------------------------------------------------------------
SELECT * FROM test_foreign_orders WHERE order_amount > 100;


--------------------------------------------------------------------------------
-- 5-3. ConstraintTriggerOverheadRule.py (RULE_STR_003)
-- 목적: DML(INSERT/UPDATE/DELETE) 실행 시 트리거 및 FK 검증 오버헤드 탐지
--------------------------------------------------------------------------------
INSERT INTO test_lineitems (order_id, item_name, price, quantity)
SELECT order_id, 'BulkItem', 10.00, 1
FROM test_orders
WHERE order_id <= 1000;


--------------------------------------------------------------------------------
-- 5-4. HotUpdateFailureRule.py (RULE_STR_004)
-- 목적: UPDATE 시 인덱스 컬럼 수정 등으로 HOT(Heap-Only Tuple) 최적화 실패 감지
--------------------------------------------------------------------------------
UPDATE test_orders
SET order_status = 'COMPLETED'
WHERE user_id BETWEEN 1 AND 500;


--------------------------------------------------------------------------------
-- 5-5. LockRowsOverheadRule.py (RULE_STR_005)
-- 목적: SELECT FOR UPDATE (LockRows) 수행 시 풀 스캔에 의한 잠금 오버헤드 탐지
--------------------------------------------------------------------------------
SELECT *
FROM test_orders
WHERE padding LIKE 'A%'
FOR UPDATE;
