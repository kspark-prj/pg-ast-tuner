import json
import os
from typing import Dict


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
