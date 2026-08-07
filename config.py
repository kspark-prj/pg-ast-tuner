import json
import os
from datetime import datetime


class ConfigManager:
    CONFIG_FILE = "db_config.json"

    @classmethod
    def load_config(cls) -> dict[str, str]:
        """로컬 설정 파일(db_config.json)에서 데이터베이스 연결 정보를 읽어옵니다."""
        if os.path.exists(cls.CONFIG_FILE):
            try:
                with open(cls.CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "host": "localhost",
            "port": "5432",
            "dbname": "my-app-db",
            "user": "postgres",
            "password": "",
        }

    @classmethod
    def save_config(cls, config_data: dict[str, str]):
        """로컬 설정 파일(db_config.json)에 데이터베이스 연결 정보를 안전하게 기록합니다."""
        with open(cls.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4, ensure_ascii=False)


class HistoryManager:
    HISTORY_FILE = "query_history.json"
    MAX_HISTORY = 50

    @classmethod
    def load_history(cls) -> list[dict]:
        """로컬 파일(query_history.json)에서 쿼리 이력을 역순(최신순)으로 읽어옵니다."""
        if os.path.exists(cls.HISTORY_FILE):
            try:
                with open(cls.HISTORY_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    @classmethod
    def save_query(cls, query: str):
        """새로운 쿼리를 이력 파일에 최신 순으로 저장하고 개수 제한을 유지합니다."""
        query = query.strip()
        if not query:
            return

        history = cls.load_history()

        # 중복 쿼리 제거 (최신 순서 갱신을 위해 기존 것 삭제)
        history = [item for item in history if item.get("query", "").strip() != query]

        new_item = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "query": query,
        }
        history.insert(0, new_item)

        # 최대 저장 개수 유지
        history = history[:cls.MAX_HISTORY]

        try:
            with open(cls.HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=4, ensure_ascii=False)
        except Exception:
            pass

