-- ==============================================================================
-- PostgreSQL 성능 진단 룰(Join & Scan Package) 검증용 통합 SQL 스크립트
-- ==============================================================================
-- 본 스크립트는 이미지에 명시된 join 및 scan 패키지 내 각 룰(Rule)들의
-- 진단 조건(Rule Condition)을 정확하게 유도하여 테스트하기 위한 DDL/DML/DQL 세트입니다.
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

================================================================================
-- PART 1. JOIN PACKAGE RULES TEST (join/)
================================================================================

--------------------------------------------------------------------------------
-- 1-1. CrossJoinRule.py
-- 목적: 조인 조건이 누락되거나 카테시안 곱(Cartesian Product)이 발생하는 Nested Loop / Cross Join 감지
--------------------------------------------------------------------------------
EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT u.username, o.order_id
FROM test_users u
CROSS JOIN test_orders o
LIMIT 100;


--------------------------------------------------------------------------------
-- 1-2. hash_join_rule.py & HashJoinLargeBuildTableRule.py
-- 목적: Hash Join 발생 검증 및 Build Side(오른쪽 자식)에 대용량 테이블이 배치되는 현상 탐지
--------------------------------------------------------------------------------
-- Enable Hash Join explicitly
SET enable_nestloop = off;
SET enable_mergejoin = off;

-- u(작은 테이블)와 o(대형 테이블) 조인 시 o가 Build Table로 선택되도록 유도 (혹은 Large Table 간 Hash Join)
EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT o.order_id, o.order_amount, u.username
FROM test_orders o
JOIN test_users u ON o.user_id = u.user_id;

RESET enable_nestloop;
RESET enable_mergejoin;


--------------------------------------------------------------------------------
-- 1-3. HashJoinBatchInflationRule.py
-- 목적: work_mem 부족으로 인해 Hash Join 시 디스크 스필(Batches > 1) 발생하는 케이스
--------------------------------------------------------------------------------
SET work_mem = '64kB';

EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT o.order_id, o.order_amount, l.item_name
FROM test_orders o
JOIN test_lineitems l ON o.order_id = l.order_id;

RESET work_mem;


--------------------------------------------------------------------------------
-- 1-4. JoinCardinalityMisestimationRule.py
-- 목적: 옵티마이저의 예상 조인 행 수(Plan Rows)와 실제 조인 행 수(Actual Rows) 간의 큰 오차 검증
--------------------------------------------------------------------------------
-- UPPER 가공 등으로 조인 조건의 카디널리티 추정을 무력화
EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT o.order_id, u.username
FROM test_orders o
JOIN test_users u ON UPPER(o.order_status) = UPPER(u.user_category);


--------------------------------------------------------------------------------
-- 1-5. MergeJoinSortRule.py
-- 목적: Merge Join 수행 전 정렬 단계(Explicit Sort)가 발생하여 CPU/메모리 부하가 가중되는 케이스
--------------------------------------------------------------------------------
SET enable_hashjoin = off;
SET enable_nestloop = off;

-- order_amount 컬럼은 인덱스가 없으므로 Merge Join을 위해 Explicit Sort 필요
EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT o.order_id, u.user_id
FROM test_orders o
JOIN test_users u ON o.order_amount = u.user_id;

RESET enable_hashjoin;
RESET enable_nestloop;


--------------------------------------------------------------------------------
-- 1-6. nested_loop_rule.py & NestedLoopHighLoopsRule.py
-- 목적: Nested Loop 발생 탐지 및 Outer Loop/Inner Loop 반복 횟수(Loops)가 과도하게 높은 케이스 탐지
--------------------------------------------------------------------------------
SET enable_hashjoin = off;
SET enable_mergejoin = off;

EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT o.order_id, u.username
FROM test_orders o
JOIN test_users u ON o.user_id = u.user_id
WHERE o.order_amount > 100;

RESET enable_hashjoin;
RESET enable_mergejoin;


--------------------------------------------------------------------------------
-- 1-7. ParallelJoinWorkerLossRule.py
-- 목적: 병렬 조인(Parallel Join) 실행 시 계획된 Worker 수보다 실제 작동한 Worker 수가 적거나 0인 케이스
--------------------------------------------------------------------------------
SET max_parallel_workers_per_gather = 4;
SET force_parallel_mode = on;

EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT count(*), avg(o.order_amount)
FROM test_orders o
JOIN test_lineitems l ON o.order_id = l.order_id;

RESET max_parallel_workers_per_gather;
RESET force_parallel_mode;


================================================================================
-- PART 2. SCAN PACKAGE RULES TEST (scan/)
================================================================================

--------------------------------------------------------------------------------
-- 2-1. BitmapHeapScanLossyRule.py
-- 목적: work_mem 부족으로 Bitmap Scan 시 Lossy Pages(손실 페이지) 및 Recheck 조건이 발생하는 케이스
--------------------------------------------------------------------------------
SET work_mem = '64kB';

EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT *
FROM test_orders
WHERE user_id BETWEEN 1 AND 800;

RESET work_mem;


--------------------------------------------------------------------------------
-- 2-2. CTEInliningFailureRule.py
-- 목적: CTE(WITH절) 사용 시 AS MATERIALIZED 옵션 등으로 인라인화가 금지되어 Subquery Scan/Materialize 발생
--------------------------------------------------------------------------------
EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
WITH cted_orders AS MATERIALIZED (
    SELECT user_id, order_amount
    FROM test_orders
    WHERE order_status = 'COMPLETED'
)
SELECT user_id, SUM(order_amount)
FROM cted_orders
GROUP BY user_id;


--------------------------------------------------------------------------------
-- 2-3. ForeignTableScanRule.py
-- 목적: FDW(Foreign Data Wrapper) 외부 테이블 스캔(Foreign Scan) 발생 탐지
--------------------------------------------------------------------------------
EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT * FROM test_foreign_orders WHERE order_amount > 100;


--------------------------------------------------------------------------------
-- 2-4. HighFilterRemovalRatioRule.py
-- 목적: 스캔 노드에서 읽은 전체 행 대비 Filter 단계에서 버려지는 비율(Filter Removal Ratio)이 90% 이상인 케이스
--------------------------------------------------------------------------------
EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT *
FROM test_orders
WHERE order_status = 'PENDING'
  AND order_amount > 490.00;


--------------------------------------------------------------------------------
-- 2-5. index_scan_rule.py
-- 목적: 일반적인 Index Scan / Index Scan Backward 노드 탐지
--------------------------------------------------------------------------------
EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT *
FROM test_orders
WHERE order_date >= NOW() - INTERVAL '1 day';


--------------------------------------------------------------------------------
-- 2-6. IndexFilterInefficiencyRule.py
-- 목적: Index Scan 내부에서 Index Cond 이외에 추가적인 Filter(Index Filter)로 대량의 데이터가 제거되는 비효율 탐지
--------------------------------------------------------------------------------
-- 복합 인덱스 (order_status, order_amount) 활용
EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT *
FROM test_orders
WHERE order_status = 'COMPLETED'
  AND padding LIKE 'A%';


--------------------------------------------------------------------------------
-- 2-7. IndexOnlyScanHeapFetchRule.py
-- 목적: Index Only Scan으로 계획되었으나 Visibility Map 미갱신 등으로 실제 Heap Fetches가 다수 발생하는 케이스
--------------------------------------------------------------------------------
-- VACUUM이 실행되지 않은 상태에서 인덱스 컬럼만 조회
EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT order_status, order_amount
FROM test_orders
WHERE order_status = 'COMPLETED';


--------------------------------------------------------------------------------
-- 2-8. seq_scan_rule.py
-- 목적: 전체 테이블 스캔(Seq Scan) 발생 및 좌변 가공(UPPER, DATE 등)으로 인한 인덱스 무력화 감지
--------------------------------------------------------------------------------
EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT *
FROM test_orders
WHERE UPPER(order_status) = 'PENDING';


--------------------------------------------------------------------------------
-- 2-9. StaleVisibilityMapRule.py
-- 목적: 대량 UPDATE/INSERT 이후 VACUUM 미수행으로 Visibility Map이 최신화되지 않아 발생되는 문제 탐지
--------------------------------------------------------------------------------
-- 대량 업데이트 후 VACUUM 없이 Index Only Scan 수행 유도
UPDATE test_orders SET order_amount = order_amount + 1 WHERE order_id <= 50000;

EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT user_id
FROM test_orders
WHERE user_id BETWEEN 1 AND 100;


--------------------------------------------------------------------------------
-- 2-10. SubqueryScanRepetitionRule.py
-- 목적: Subquery Scan 노드가 반복 실행되거나 상관 서브쿼리(Correlated Subquery)로 인한 과도한 Scan 발생
--------------------------------------------------------------------------------
EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT u.user_id,
       (SELECT COUNT(*) FROM test_orders o WHERE o.user_id = u.user_id AND o.order_amount > 200) AS high_orders
FROM test_users u;
