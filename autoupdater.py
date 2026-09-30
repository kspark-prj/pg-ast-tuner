"""
autoupdater.py - 모노레포 환경 지원 파이썬 윈도우 GUI 무소음 공통 자동 업데이트 모듈
"""

import ctypes
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


class AutoUpdater:
    """
    GitHub Pages 기반 모노레포 파이썬 윈도우 GUI 프로그램 자동 업데이트 클래스
    """

    def __init__(
        self,
        app_name: str,
        current_version: str,
        github_repo: str,
        timeout: int = 3,
        version_url: str | None = None,
        show_progress: bool = True,
    ):
        """
        :param app_name: 모노레포 내 앱 식별자 (예: 'app-a')
        :param current_version: 현재 실행 중인 앱 버전 (예: '1.0.0' 또는 'v1.0.0')
        :param github_repo: GitHub 저장소 경로 ('owner/repo')
        :param timeout: 네트워크 요청 타임아웃 (초, 기본값: 3)
        :param version_url: GitHub Pages 등의 버전 정보 JSON URL (기본값: https://{owner}.github.io/{repo}/version.json)
        :param show_progress: 다운로드 진행 상황 GUI 프로그래스 바 표시 여부 (기본값: True)
        """
        self.app_name = app_name
        self.current_version = current_version.lstrip("v")
        self.github_repo = github_repo
        self.timeout = timeout
        self.show_progress = show_progress

        if version_url:
            self.version_url = version_url
        else:
            owner_repo = github_repo.strip("/")
            if "/" in owner_repo:
                owner, repo = owner_repo.split("/", 1)
                self.version_url = f"https://{owner}.github.io/{repo}/version.json"
            else:
                self.version_url = f"https://{owner_repo}.github.io/version.json"

    def _check_internet(self) -> bool:
        """
        업데이트 검사 전 인터넷 연결 상태를 소켓으로 빠르게 확인 (무소음)
        """
        try:
            # DNS/소켓 통신 테스트 (8.8.8.8:53)
            socket.create_connection(("8.8.8.8", 53), timeout=self.timeout).close()
            return True
        except Exception:
            return False

    def _parse_version(self, ver_str: str) -> tuple:
        """
        버전 문자열을 비교 가능한 튜플 형태로 변환 (예: 'v1.0.2' -> (1, 0, 2))
        """
        clean = re.sub(r"[^0-9.]", "", ver_str.lstrip("v"))
        parts = [int(p) for p in clean.split(".") if p.isdigit()]
        return tuple(parts) if parts else (0,)

    def _show_ask_dialog(self, title: str, message: str) -> bool:
        """
        업데이트 여부를 묻는 GUI Yes/No 메시지 박스
        PyQt / PySide / Tkinter / Windows Native API (ctypes) 순서로 자동 감지
        """
        # 1. Active Qt Application (PyQt5, PyQt6, PySide2, PySide6) 자동 감지
        for qt_mod in [
            "PyQt5.QtWidgets",
            "PySide6.QtWidgets",
            "PyQt6.QtWidgets",
            "PySide2.QtWidgets",
        ]:
            if qt_mod in sys.modules:
                try:
                    mod = sys.modules[qt_mod]
                    QMessageBox = mod.QMessageBox
                    reply = QMessageBox.question(
                        None,
                        title,
                        message,
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.Yes,
                    )
                    return reply == QMessageBox.Yes
                except Exception:
                    pass

        # 2. Tkinter 감지
        if "tkinter" in sys.modules or "tkinter.messagebox" in sys.modules:
            try:
                import tkinter.messagebox

                return bool(tkinter.messagebox.askyesno(title, message))
            except Exception:
                pass

        # 3. Windows Native API (ctypes) Fallback - 추가 설치 모듈 없을 때
        if sys.platform == "win32":
            try:
                # MB_YESNO(0x04) | MB_ICONQUESTION(0x20) | MB_TOPMOST(0x40000) = 0x40024
                # IDYES = 6
                res = ctypes.windll.user32.MessageBoxW(0, message, title, 0x40024)
                return res == 6
            except Exception:
                pass

        return False

    def _show_info_dialog(self, title: str, message: str) -> None:
        """
        사용자에게 정보 안내를 제공하는 GUI OK 메시지 박스
        PyQt / PySide / Tkinter / Windows Native API (ctypes) 순서로 자동 감지
        """
        # 1. Active Qt Application (PyQt5, PyQt6, PySide2, PySide6) 자동 감지
        for qt_mod in [
            "PyQt5.QtWidgets",
            "PySide6.QtWidgets",
            "PyQt6.QtWidgets",
            "PySide2.QtWidgets",
        ]:
            if qt_mod in sys.modules:
                try:
                    mod = sys.modules[qt_mod]
                    QMessageBox = mod.QMessageBox
                    QMessageBox.information(
                        None,
                        title,
                        message,
                        QMessageBox.Ok,
                    )
                    return
                except Exception:
                    pass

        # 2. Tkinter 감지
        if "tkinter" in sys.modules or "tkinter.messagebox" in sys.modules:
            try:
                import tkinter.messagebox

                tkinter.messagebox.showinfo(title, message)
                return
            except Exception:
                pass

        # 3. Windows Native API (ctypes) Fallback - 추가 설치 모듈 없을 때
        if sys.platform == "win32":
            try:
                # MB_OK(0x00) | MB_ICONINFORMATION(0x40) | MB_TOPMOST(0x40000) = 0x40040
                ctypes.windll.user32.MessageBoxW(0, message, title, 0x40040)
                return
            except Exception:
                pass

    def check_for_update(self, show_progress: bool | None = None) -> bool:
        """
        GitHub Pages에서 최신 버전 정보(version.json)를 검사하고, 업데이트 존재 시 사용자 확인 후 진행.
        api.github.com 대신 정적 GitHub Pages URL을 사용하여 GitHub API Rate Limit(시간당 60회 제한)을 회피함.
        최신 버전과 현재 버전이 같거나 예외 발생 시 "최신 버전입니다." 알림을 표시함.
        :param show_progress: 다운로드 프로그래스 바 GUI 창 표시 여부 (미입력 시 생성자 기본값 사용)
        """
        try:
            # 1. 오프라인 사전 확인 (소켓 통신)
            if not self._check_internet():
                self._show_info_dialog("자동 업데이트 알림", "최신 버전입니다.")
                return False

            # 2. GitHub Pages 버전 정보(JSON) 호출 (CDN 캐시 방지 타임스탬프 파라미터 및 헤더 적용)
            url = self.version_url
            sep = "&" if "?" in url else "?"
            cache_bust_url = f"{url}{sep}_t={int(time.time())}"

            req = urllib.request.Request(
                cache_bust_url,
                headers={
                    "User-Agent": "Python-AutoUpdater",
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                },
            )

            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    self._show_info_dialog("자동 업데이트 알림", "최신 버전입니다.")
                    return False
                data = json.loads(resp.read().decode("utf-8"))

            # 3. version.json 규격에서 해당 앱(app_name) 정보 추출
            if not isinstance(data, dict) or self.app_name not in data:
                self._show_info_dialog("자동 업데이트 알림", "최신 버전입니다.")
                return False

            app_info = data[self.app_name]
            if not isinstance(app_info, dict):
                self._show_info_dialog("자동 업데이트 알림", "최신 버전입니다.")
                return False

            # 4. 규격 필수/선택 항목(version, download_url, file_name, sha256/sha512) 추출
            latest_version = app_info.get("version")
            download_url = app_info.get("download_url")
            file_name = app_info.get("file_name")

            # 해시 무결성 검증 필드 추출 (sha256, sha512, hash, checksum 순서로 지원)
            expected_hash = None
            hash_algorithm = "sha256"

            if app_info.get("sha256"):
                expected_hash = str(app_info["sha256"]).strip()
                hash_algorithm = "sha256"
            elif app_info.get("sha512"):
                expected_hash = str(app_info["sha512"]).strip()
                hash_algorithm = "sha512"
            elif app_info.get("hash"):
                expected_hash = str(app_info["hash"]).strip()
                hash_algorithm = "sha512" if len(expected_hash) == 128 else "sha256"
            elif app_info.get("checksum"):
                expected_hash = str(app_info["checksum"]).strip()
                hash_algorithm = "sha512" if len(expected_hash) == 128 else "sha256"

            if not latest_version or not download_url:
                self._show_info_dialog("자동 업데이트 알림", "최신 버전입니다.")
                return False

            if not file_name:
                file_name = download_url.split("/")[-1].split("?")[0]
            if not file_name or not file_name.lower().endswith(".exe"):
                file_name = f"{self.app_name}-setup.exe"

            # 5. 버전 비교
            latest_ver_tuple = self._parse_version(latest_version)
            curr_ver_tuple = self._parse_version(self.current_version)

            if latest_ver_tuple <= curr_ver_tuple:
                self._show_info_dialog("자동 업데이트 알림", "최신 버전입니다.")
                return False

            # 6. 사용자 승인 대화상자 표시
            clean_ver_str = latest_version.lstrip("v")
            msg = f"새로운 버전({clean_ver_str})이 출시되었습니다.\n\n"
            msg += "지금 업데이트를 다운로드하고 설치하시겠습니까?"

            if self._show_ask_dialog("자동 업데이트 알림", msg):
                self.run_update(
                    download_url=download_url,
                    file_name=file_name,
                    expected_hash=expected_hash,
                    hash_algorithm=hash_algorithm,
                    show_progress=show_progress,
                )
                return True

        except Exception:
            # 네트워크 오류, 타임아웃, JSON 파싱 실패 등 모든 예외 발생 시 "최신 버전입니다." 알림 표시
            self._show_info_dialog("자동 업데이트 알림", "최신 버전입니다.")
            return False

        return False

    def _verify_hash(self, file_path: str, expected_hash: str, algorithm: str = "sha256") -> bool:
        """
        다운로드된 파일의 SHA-256 또는 SHA-512 해시값을 계산하여 기대 해시값과 비교 검증
        """
        try:
            algo_map = {
                "sha256": hashlib.sha256,
                "sha512": hashlib.sha512,
            }
            hasher_factory = algo_map.get(algorithm.lower(), hashlib.sha256)
            hasher = hasher_factory()

            with open(file_path, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)

            calculated_hash = hasher.hexdigest().lower()
            return calculated_hash == expected_hash.strip().lower()
        except Exception:
            return False

    def _download_silent(
        self, req: urllib.request.Request, setup_path: str, timeout: int = 30
    ) -> bool:
        """
        UI 없이 무소음 파일 다운로드
        """
        try:
            with (
                urllib.request.urlopen(req, timeout=timeout) as resp,
                open(setup_path, "wb") as out_file,
            ):
                while chunk := resp.read(65536):
                    out_file.write(chunk)
            return True
        except Exception:
            if os.path.exists(setup_path):
                try:
                    os.remove(setup_path)
                except Exception:
                    pass
            return False

    def _download_with_qt_progress(
        self, req: urllib.request.Request, setup_path: str, timeout: int = 30
    ) -> tuple[bool, bool]:
        """
        PyQt / PySide 기반 QProgressDialog 프로그래스 바 다운로드
        :return: (성공 여부, 취소 클릭 여부)
        """
        for qt_widgets, qt_core in [
            ("PyQt5.QtWidgets", "PyQt5.QtCore"),
            ("PySide6.QtWidgets", "PySide6.QtCore"),
            ("PyQt6.QtWidgets", "PyQt6.QtCore"),
            ("PySide2.QtWidgets", "PySide2.QtCore"),
        ]:
            if qt_widgets in sys.modules:
                try:
                    mod_w = sys.modules[qt_widgets]
                    mod_c = sys.modules.get(qt_core, mod_w)
                    QApplication = getattr(mod_w, "QApplication", None)
                    QProgressDialog = getattr(mod_w, "QProgressDialog", None)
                    Qt = getattr(mod_c, "Qt", None)

                    if not QApplication or not QProgressDialog:
                        continue

                    app = QApplication.instance()
                    if not app:
                        continue

                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        total_size_header = resp.headers.get("Content-Length")
                        total_size = (
                            int(total_size_header)
                            if total_size_header and total_size_header.isdigit()
                            else 0
                        )

                        progress = QProgressDialog(
                            "최신 버전을 다운로드 중입니다...",
                            "취소",
                            0,
                            max(0, total_size),
                        )
                        progress.setWindowTitle(f"{self.app_name} - 자동 업데이트")
                        if Qt and hasattr(Qt, "WindowModal"):
                            progress.setWindowModality(Qt.WindowModal)
                        progress.show()

                        if total_size == 0:
                            progress.setRange(0, 0)

                        downloaded = 0
                        chunk_size = 65536
                        cancelled = False

                        with open(setup_path, "wb") as out_file:
                            while True:
                                if progress.wasCanceled():
                                    cancelled = True
                                    break

                                chunk = resp.read(chunk_size)
                                if not chunk:
                                    break

                                out_file.write(chunk)
                                downloaded += len(chunk)

                                if total_size > 0:
                                    progress.setValue(downloaded)
                                    dl_mb = downloaded / (1024 * 1024)
                                    total_mb = total_size / (1024 * 1024)
                                    progress.setLabelText(
                                        f"다운로드 중... {dl_mb:.2f} MB / {total_mb:.2f} MB"
                                    )
                                else:
                                    dl_mb = downloaded / (1024 * 1024)
                                    progress.setLabelText(f"다운로드 중... {dl_mb:.2f} MB")

                                list_events = getattr(QApplication, "processEvents", None)
                                if list_events:
                                    list_events()

                        progress.close()

                        if cancelled:
                            if os.path.exists(setup_path):
                                try:
                                    os.remove(setup_path)
                                except Exception:
                                    pass
                            return False, True

                        return True, False
                except Exception:
                    pass

        return False, False

    def _download_with_tkinter_progress(
        self, req: urllib.request.Request, setup_path: str, timeout: int = 30
    ) -> tuple[bool, bool]:
        """
        Tkinter 기반 프로그래스 바 창 다운로드 (Toplevel 호환 수정)
        :return: (성공 여부, 취소 클릭 여부)
        """
        try:
            import tkinter as tk
            from tkinter import ttk

            # ---------------------------------------------------------
            # 1. 기존 Tk 루트 존재 여부 확인 후 Toplevel 생성
            # ---------------------------------------------------------
            if tk._default_root is not None:
                dialog = tk.Toplevel(tk._default_root)
            else:
                dialog = tk.Tk()

            dialog.title(f"{self.app_name} - 자동 업데이트")

            window_width = 420
            window_height = 160
            screen_w = dialog.winfo_screenwidth()
            screen_h = dialog.winfo_screenheight()
            x = (screen_w // 2) - (window_width // 2)
            y = (screen_h // 2) - (window_height // 2)
            dialog.geometry(f"{window_width}x{window_height}+{x}+{y}")
            dialog.resizable(False, False)

            try:
                dialog.attributes("-topmost", True)
            except Exception:
                pass

            cancelled = [False]

            def on_close():
                cancelled[0] = True

            dialog.protocol("WM_DELETE_WINDOW", on_close)

            title_label = ttk.Label(
                dialog,
                text="최신 버전 설치 파일을 다운로드 중입니다...",
                font=("Malgun Gothic", 10, "bold"),
            )
            title_label.pack(anchor="w", padx=20, pady=(15, 5))

            # 프로그래스 바 (fill="x" 확장)
            progress_var = tk.DoubleVar(value=0)
            progressbar = ttk.Progressbar(
                dialog,
                variable=progress_var,
                maximum=100,
                orient="horizontal",
                mode="determinate",
            )
            progressbar.pack(fill="x", padx=20, pady=5)

            bottom_frame = ttk.Frame(dialog)
            bottom_frame.pack(fill="x", padx=20, pady=(5, 15))

            status_label = ttk.Label(
                bottom_frame, text="다운로드 준비 중...", font=("Malgun Gothic", 9)
            )
            status_label.pack(side="left")

            cancel_btn = ttk.Button(bottom_frame, text="취소", width=8, command=on_close)
            cancel_btn.pack(side="right")

            dialog.update_idletasks()
            dialog.update()

            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    total_size_header = resp.headers.get("Content-Length")
                    total_size = (
                        int(total_size_header)
                        if total_size_header and total_size_header.isdigit()
                        else None
                    )

                    if not total_size:
                        progressbar.config(mode="indeterminate")
                        progressbar.start(10)

                    downloaded = 0
                    chunk_size = 65536
                    last_update_time = time.time()

                    with open(setup_path, "wb") as out_file:
                        while True:
                            if cancelled[0]:
                                raise InterruptedError("Download cancelled by user")

                            chunk = resp.read(chunk_size)
                            if not chunk:
                                break

                            out_file.write(chunk)
                            downloaded += len(chunk)

                            curr_time = time.time()
                            if curr_time - last_update_time > 0.03 or not chunk:
                                last_update_time = curr_time
                                if total_size:
                                    percent = (downloaded / total_size) * 100
                                    progress_var.set(percent)
                                    dl_mb = downloaded / (1024 * 1024)
                                    total_mb = total_size / (1024 * 1024)
                                    status_label.config(
                                        text=f"{dl_mb:.2f} MB / {total_mb:.2f} MB ({int(percent)}%)"
                                    )
                                else:
                                    dl_mb = downloaded / (1024 * 1024)
                                    status_label.config(text=f"{dl_mb:.2f} MB 다운로드 중...")

                                # 화면 강제 갱신
                                dialog.update_idletasks()
                                dialog.update()

                    if downloaded > 0:
                        progress_var.set(100)
                        if total_size:
                            total_mb = total_size / (1024 * 1024)
                            status_label.config(
                                text=f"{total_mb:.2f} MB / {total_mb:.2f} MB (100%) - 다운로드 완료!"
                            )
                        else:
                            status_label.config(text="다운로드 완료!")
                        dialog.update_idletasks()
                        dialog.update()
                        time.sleep(0.3)

                return True, False
            except InterruptedError:
                if os.path.exists(setup_path):
                    try:
                        os.remove(setup_path)
                    except Exception:
                        pass
                return False, True
            except Exception:
                if os.path.exists(setup_path):
                    try:
                        os.remove(setup_path)
                    except Exception:
                        pass
                return False, False
            finally:
                try:
                    dialog.destroy()
                except Exception:
                    pass
        except Exception:
            return False, False

    def run_update(
        self,
        download_url: str,
        file_name: str,
        expected_hash: str | None = None,
        hash_algorithm: str = "sha256",
        show_progress: bool | None = None,
    ):
        """
        설치 파일(%TEMP%) 다운로드(프로그래스 바 UI 지원) 및 해시 무결성 검증, 배치 파일 생성 후 메인 프로세스 종료 & 설치 프로그램 실행
        """
        use_progress = self.show_progress if show_progress is None else show_progress
        try:
            temp_dir = tempfile.gettempdir()
            setup_path = os.path.join(temp_dir, file_name)

            # 1. NSIS setup executable 다운로드 (프로그래스 바 UI 또는 무소음)
            req = urllib.request.Request(download_url, headers={"User-Agent": "Python-AutoUpdater"})

            download_success = False
            user_cancelled = False

            if use_progress:
                # 1-1. Qt 환경 감지 및 Qt 다운로드 프로그래스 바 시도
                download_success, user_cancelled = self._download_with_qt_progress(
                    req, setup_path, timeout=30
                )

                # 1-2. Qt 미사용 또는 UI 생성 실패 시 Tkinter 프로그래스 바 시도
                if not download_success and not user_cancelled:
                    download_success, user_cancelled = self._download_with_tkinter_progress(
                        req, setup_path, timeout=30
                    )

            # 1-3. 사용자가 취소하지 않았으나 UI 프로그래스 창 사용 불가한 경우 무소음 다운로드 Fallback
            if not download_success and not user_cancelled:
                download_success = self._download_silent(req, setup_path, timeout=30)

            if not download_success or not os.path.exists(setup_path):
                return

            # 2. 해시 무결성 검증 (해시값이 지정되어 있는 경우)
            if expected_hash and not self._verify_hash(setup_path, expected_hash, hash_algorithm):
                # 파일 손상 또는 위변조 감지 시 임시 파일 삭제 후 업데이트 중단 (무소음 안전 처리)
                if os.path.exists(setup_path):
                    try:
                        os.remove(setup_path)
                    except Exception:
                        pass
                return

            # 3. 메인 프로세스 파일 잠금(Lock) 해제 및 비동기 설치 실행용 배치 파일 생성
            bat_path = os.path.join(temp_dir, f"run_setup_{os.getpid()}.bat")
            bat_content = (
                f"@echo off\n"
                f"timeout /t 1 /nobreak > NUL\n"
                f'start "" /WAIT "{setup_path}"\n'
                f":cleanup\n"
                f'if exist "{setup_path}" (\n'
                f'    del /f /q "{setup_path}" >NUL 2>&1\n'
                f'    if exist "{setup_path}" (\n'
                f"        timeout /t 1 /nobreak > NUL\n"
                f"        goto cleanup\n"
                f"    )\n"
                f")\n"
                f'del "%~f0"\n'
            )
            with open(bat_path, "w", encoding="cp949") as f:
                f.write(bat_content)

            # 4. 배치 파일 비동기 실행
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            subprocess.Popen(["cmd.exe", "/c", bat_path], creationflags=creationflags)

            # 5. 현재 메인 파이썬 프로세스 즉시 종료
            sys.exit(0)

        except Exception:
            # 다운로드/실행 실패 시 조용히 넘어가서 기존 프로그램 수행 보장
            pass
