-- 기존 테이블이 있다면 정리
DROP TABLE IF EXISTS test_orders CASCADE;

-- 1. 테스트용 주문 테이블 생성
CREATE TABLE test_orders (
    order_id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    status VARCHAR(20) NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    remarks TEXT
);

-- 2. 테스트 데이터 생성 (약 150,000건 삽입하여 통계 분석이 정상 작동하도록 유도)
INSERT INTO test_orders (user_id, status, amount, created_at, remarks)
SELECT
    (random() * 10000)::bigint + 1,
    (ARRAY['ACTIVE', 'PENDING', 'COMPLETED', 'CANCELLED'])[(random() * 3)::int + 1],
    (random() * 1000)::numeric(12,2),
    NOW() - (random() * 365 || ' days')::interval,
    repeat('A', (random() * 100)::int)
FROM generate_series(1, 150000);

-- 3. 인덱스 생성 (일부러 불완전하게 구성하여 시나리오 테스트)
-- 시나리오용 복합 인덱스 (선행 컬럼: status)
CREATE INDEX idx_orders_status_created ON test_orders (status, created_at);

-- 4. 통계 정보 수집 (PostgreSQL 옵티마이저가 정확한 비용을 계산하도록 갱신)
ANALYZE test_orders;


--인덱스 미존재 (CRITICAL 탐색)
SELECT * FROM test_orders WHERE amount > 900.00;

--복합 인덱스 선행 컬럼 누락 (CRITICAL 탐색 - Prefix Violation)
SELECT * FROM test_orders WHERE created_at >= '2026-01-01 00:00:00';

--인덱스 컬럼 좌변 가공 (CRITICAL 탐색 - Index Suppress)
-- status 컬럼은 인덱스가 있지만, 함수로 가공하여 인덱스가 비활성화됨
SELECT * FROM test_orders WHERE UPPER(status) = 'ACTIVE';

--OR 조건식 검출 (WARNING 탐색)
SELECT * FROM test_orders
WHERE status = 'PENDING' OR amount < 50.00;


--디스크 정렬(External Sort) 및 임시 파일 유발 (WARNING 탐색)
-- 대용량 데이터를 메모리 제한 내에서 가공 없이 전체 정렬 시도
SELECT * FROM test_orders ORDER BY remarks DESC;


----------------------- 추가 룰 검증 쿼리 -----------------------------------
-- 기존 테스트 객체 정리
DROP TABLE IF EXISTS test_orders CASCADE;
DROP TABLE IF EXISTS test_users CASCADE;

-- 1. 사용자 테이블 (소형/중형)
CREATE TABLE test_users (
    user_id SERIAL PRIMARY KEY,
    username VARCHAR(50),
    user_category VARCHAR(20),
    created_at TIMESTAMP DEFAULT NOW()
);

-- 2. 주문 테이블 (대용량 - 약 20만 건)
CREATE TABLE test_orders (
    order_id SERIAL PRIMARY KEY,
    user_id INT,
    order_amount NUMERIC(10, 2),
    order_status VARCHAR(20),
    order_date TIMESTAMP,
    padding TEXT
);

-- 더미 데이터 삽입 (test_users: 1,000건 / test_orders: 200,000건)
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
    CASE WHEN random() < 0.1 THEN 'PENDING' ELSE 'COMPLETED' END,
    NOW() - (random() * 365 || ' days')::INTERVAL,
    repeat('A', 100) -- 디스크 쓰기 유도를 위한 패딩 데이터
FROM generate_series(1, 200000);

-- 통계 정보 최신화
ANALYZE test_users;
ANALYZE test_orders;


--1. RULE_JOIN_001 (Hash Join Disk Spill) 테스트
--목적: work_mem을 의도적으로 매우 낮게 설정하여 해시 테이블이 디스크로 스필(Hash Batches > 1)되도록 유도합니다.

--SQL
-- [1-1] 세션 work_mem 최소화 (64kB)
SET work_mem = '64kB';

-- [1-2] 실행계획 추출 테스트 쿼리 (EXPLAIN ANALYZE)
EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT o.order_id, o.order_amount, u.username
FROM test_orders o
JOIN test_users u ON o.user_id = u.user_id;

-- [원복]
RESET work_mem;
--기대 결과: Node Type: Hash Join 노드에 Hash Batches 수치가 2 이상으로 출력되어 HashJoinRule 진단에 걸림.

--2. RULE_SCAN_001 (Seq Scan / 함수 가공으로 인한 인덱스 무력화) 테스트
--목적: 인덱스가 존재하는 컬럼의 좌변을 가공(DATE(), UPPER())하여 인덱스가 무력화되고 Seq Scan이 발생하는 상황을 테스트합니다.

--SQL
-- [2-1] 테스트용 인덱스 생성
CREATE INDEX idx_orders_order_date ON test_orders (order_date);

-- [2-2] 좌변 가공 쿼리 (인덱스 무력화 발생)
EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT *
FROM test_orders
WHERE DATE(order_date) = '2025-01-01';
--기대 결과: Node Type: Seq Scan 발생 및 Filter 내 DATE() 가공식이 감지되어 SeqScanRule 진단에 걸림.

---3. RULE_STAT_001 / RULE_STAT_003 (Sort Disk Spill) 테스트
--목적: 대용량 데이터를 정렬할 때 work_mem을 낮춰 External Sort (디스크 정렬) 및 임시 파일 쓰기(Temp Written Blocks)를 유도합니다.

--SQL
-- [3-1] 세션 work_mem 최소화
SET work_mem = '64kB';

-- [3-2] 실행계획 추출 테스트 쿼리
EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT *
FROM test_orders
ORDER BY padding DESC;

-- [원복]
RESET work_mem;
--기대 결과: Node Type: Sort 노드의 Sort Method에 external sort 기재 및 Temp Written Blocks > 0이 발생하여 SortRule 및 TempFileRule 진단에 걸림.

--4-. RULE_SCAN_005 (High Filter Removal Ratio) 테스트
--목적: 스캔 노드에서 읽어들인 전체 행 수에 비해 후속 Filter 단계에서 버려지는 데이터가 90% 이상인 케이스를 유도합니다.

--SQL
-- [4-1] 비인덱스 컬럼 조건으로 대량 읽기 후 극소수 추출
EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT *
FROM test_orders
WHERE order_status = 'PENDING'
  AND order_amount > 500.00;
--기대 결과: Rows Removed by Filter가 대량 발생하여 전체 스캔 수 대비 탈락률이 90%를 초과함으로써 HighFilterRemovalRatioRule 진단에 걸림.

--5. RULE_STAT_006 (JIT Compilation Overhead) 테스트
--목적: JIT 강제 활성화 및 비용 임계값을 0으로 설정하여 무조건 JIT 컴파일이 수행되도록 유도합니다.

--SQL
-- [5-1] JIT 강제 활성화
SET jit = on;
SET jit_above_cost = 0;
SET jit_inline_above_cost = 0;
SET jit_optimize_above_cost = 0;

-- [5-2] 복잡한 집계 연산 쿼리 실행
EXPLAIN (ANALYZE, COSTS, BUFFERS, FORMAT JSON)
SELECT user_id,
       SUM(order_amount),
       AVG(order_amount),
       COUNT(*)
FROM test_orders
GROUP BY user_id;

-- [원복]
RESET jit;
RESET jit_above_cost;
RESET jit_inline_above_cost;
RESET jit_optimize_above_cost;
-- 기대 결과: 실행계획 JSON 내 JIT 속성이 생성되고 Generation, Optimization 등의 시간이 기록되어 JITOverheadRule 진단 조건에 부합함.
