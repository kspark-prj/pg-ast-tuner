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
