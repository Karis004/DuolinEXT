import json
import os
import subprocess
import tempfile
import time
import urllib.parse
import re
from dataclasses import dataclass
from typing import Any

from .config import DUOLINGO_PAGE_SIZE, Settings


@dataclass
class DuolingoError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


class DuolingoClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _request_json(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = self.settings.duolingo_jwt.strip()
        if not token:
            raise DuolingoError("token_missing", "未配置 DUOLINGO_JWT。")
        user_id = self.settings.duolingo_user_id.strip()
        if not user_id:
            raise DuolingoError("user_id_missing", "未配置 DUOLINGO_USER_ID。")

        headers = {
            "Accept": "application/json; charset=UTF-8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Authorization": f"Bearer {token}",
            "X-Amzn-Trace-Id": f"User={user_id}",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.duolingo.cn/practice-hub/words",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            ),
        }

        temp_body_path: str | None = None
        if body is not None:
            headers["Content-Type"] = "application/json; charset=UTF-8"
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", delete=False, suffix=".json"
            ) as temp_body:
                json.dump(body, temp_body, ensure_ascii=False)
                temp_body_path = temp_body.name

        marker = "\n__DUOLINEXT_HTTP_STATUS__:%{http_code}"
        command = [
            "curl",
            "--silent",
            "--show-error",
            "--location",
            "--compressed",
            "--max-time",
            "45",
        ]
        if os.name == "nt":
            command.append("--ssl-no-revoke")
        command.extend(["--request", method, "--url", url, "--write-out", marker])

        for name, value in headers.items():
            command.extend(["-H", f"{name}: {value}"])
        if temp_body_path:
            command.extend(["--data-binary", f"@{temp_body_path}"])

        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        finally:
            if temp_body_path:
                try:
                    os.remove(temp_body_path)
                except OSError:
                    pass

        if completed.returncode != 0:
            detail = completed.stderr.strip() or "curl 未返回错误详情"
            raise DuolingoError("request_failed", f"多邻国网络请求失败：{detail}")

        status_marker = "__DUOLINEXT_HTTP_STATUS__:"
        if status_marker not in completed.stdout:
            raise DuolingoError("invalid_response", "多邻国响应中缺少 HTTP 状态码。")

        response_text, status_text = completed.stdout.rsplit(status_marker, 1)
        try:
            status = int(status_text.strip())
        except ValueError as exc:
            raise DuolingoError("invalid_response", "多邻国返回了无效状态码。") from exc

        if status in (401, 403):
            raise DuolingoError(
                "auth_failed",
                f"多邻国鉴权失败（HTTP {status}），请更新 DUOLINGO_JWT。",
            )
        if not 200 <= status < 300:
            raise DuolingoError(
                "request_failed",
                f"多邻国请求失败（HTTP {status}）。",
            )

        try:
            return json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise DuolingoError("invalid_response", "多邻国返回的内容不是有效 JSON。") from exc

    def fetch_current_course(self) -> dict[str, Any]:
        fields = "currentCourse,currentCourseId,learningLanguage,fromLanguage"
        url = (
            "https://www.duolingo.cn/2023-05-23/users/"
            f"{self.settings.duolingo_user_id}"
            f"?fields={urllib.parse.quote(fields)}&_={int(time.time() * 1000)}"
        )
        return self._request_json("GET", url)

    @staticmethod
    def extract_course_progress(course_data: dict[str, Any]) -> list[dict[str, Any]]:
        current_course = course_data.get("currentCourse")
        if not isinstance(current_course, dict):
            raise DuolingoError(
                "invalid_response",
                "多邻国响应缺少 currentCourse，Token 可能已失效。",
            )

        by_skill: dict[str, list[dict[str, Any]]] = {}
        for section_index, section in enumerate(current_course.get("pathSectioned") or []):
            for unit_index, unit in enumerate(section.get("units") or []):
                for path_level_index, level in enumerate(unit.get("levels") or []):
                    metadata = level.get("pathLevelMetadata") or {}
                    client_data = level.get("pathLevelClientData") or {}
                    skill_id = metadata.get("skillId") or client_data.get("skillId")
                    if (
                        level.get("type") != "skill"
                        or level.get("subtype") != "regular"
                        or level.get("state") not in ("passed", "active")
                        or not skill_id
                    ):
                        continue
                    row = {
                        "skillId": str(skill_id),
                        "debugName": str(level.get("debugName") or ""),
                        "sectionIndex": section_index,
                        "unitIndex": unit_index,
                        "pathLevelIndex": path_level_index,
                        "levelIndex": int(metadata.get("crownLevelIndex") or 0),
                        "finishedSessions": int(level.get("finishedSessions") or 0),
                        "totalSessions": int(level.get("totalSessions") or 0),
                        "state": str(level.get("state") or ""),
                    }
                    by_skill.setdefault(str(skill_id), []).append(row)

        skills = []
        for skill_id, rows in by_skill.items():
            rows.sort(
                key=lambda row: (
                    row["sectionIndex"],
                    row["unitIndex"],
                    row["pathLevelIndex"],
                )
            )
            # A repeated crown level is practice of the same Duolingo skill, not
            # another layer in this app's curriculum. The first crown introduces
            # the vocabulary and is the only row that defines our Sessions.
            canonical = next(
                (row for row in rows if row["levelIndex"] == 0),
                rows[0],
            )
            checkpoints = []
            for session_index in range(1, canonical["totalSessions"] + 1):
                checkpoints.append(
                    {
                        "levelIndex": 0,
                        "sessionIndex": session_index,
                        "totalSessions": canonical["totalSessions"],
                        "isCompleted": session_index <= canonical["finishedSessions"],
                        "sectionIndex": canonical["sectionIndex"],
                        "unitIndex": canonical["unitIndex"],
                        "pathLevelIndex": canonical["pathLevelIndex"],
                        "pathOrder": (
                            canonical["sectionIndex"] * 1_000_000
                            + canonical["unitIndex"] * 10_000
                            + canonical["pathLevelIndex"] * 100
                            + session_index
                        ),
                    }
                )

            debug_name = canonical["debugName"] or skill_id
            title = re.sub(r",?\s*Level\s+\d+\s*$", "", debug_name, flags=re.IGNORECASE)
            skills.append(
                {
                    "skillId": skill_id,
                    "title": title or debug_name,
                    "debugName": debug_name,
                    "sectionIndex": canonical["sectionIndex"],
                    "unitIndex": canonical["unitIndex"],
                    "pathLevelIndex": canonical["pathLevelIndex"],
                    "pathOrder": (
                        canonical["sectionIndex"] * 1_000_000
                        + canonical["unitIndex"] * 10_000
                        + canonical["pathLevelIndex"] * 100
                    ),
                    "currentLevel": 0,
                    "currentSessions": canonical["finishedSessions"],
                    "totalSessions": canonical["totalSessions"],
                    "state": canonical["state"],
                    "checkpoints": checkpoints,
                }
            )

        skills.sort(key=lambda skill: skill["pathOrder"])
        return skills

    @staticmethod
    def single_skill_payload(
        skill_id: str,
        finished_levels: int,
        finished_sessions: int,
    ) -> dict[str, Any]:
        return {
            "lastTotalLexemeCount": 0,
            "progressedSkills": [
                {
                    "finishedLevels": finished_levels,
                    "finishedSessions": finished_sessions,
                    "skillId": {"id": skill_id},
                }
            ],
        }

    def fetch_words_for_skill(
        self,
        skill_id: str,
        finished_levels: int,
        finished_sessions: int,
    ) -> list[dict[str, Any]]:
        return self._fetch_words_with_payload(
            self.single_skill_payload(skill_id, finished_levels, finished_sessions)
        )

    @staticmethod
    def build_progress_payload(course_data: dict[str, Any]) -> dict[str, Any]:
        current_course = course_data.get("currentCourse")
        if not isinstance(current_course, dict):
            raise DuolingoError(
                "invalid_response",
                "多邻国响应缺少 currentCourse，Token 可能已失效。",
            )

        skill_levels: list[dict[str, Any]] = []
        for section_index, section in enumerate(current_course.get("pathSectioned") or []):
            for unit_index, unit in enumerate(section.get("units") or []):
                for level_index, level in enumerate(unit.get("levels") or []):
                    metadata = level.get("pathLevelMetadata") or {}
                    client_data = level.get("pathLevelClientData") or {}
                    skill_id = metadata.get("skillId") or client_data.get("skillId")
                    if (
                        level.get("type") != "skill"
                        or level.get("subtype") != "regular"
                        or level.get("state") not in ("passed", "active")
                        or not skill_id
                    ):
                        continue
                    skill_levels.append(
                        {
                            "section": section_index,
                            "unit": unit_index,
                            "level": level_index,
                            "skill_id": skill_id,
                            "crown": metadata.get("crownLevelIndex") or 0,
                            "sessions": level.get("finishedSessions") or 0,
                        }
                    )

        by_skill: dict[str, list[dict[str, Any]]] = {}
        for row in skill_levels:
            by_skill.setdefault(row["skill_id"], []).append(row)

        progressed_skills = []
        for skill_id, rows in by_skill.items():
            rows.sort(key=lambda row: (row["section"], row["unit"], row["crown"], row["level"]))
            current = rows[-1]
            progressed_skills.append(
                {
                    "finishedLevels": current["crown"],
                    "finishedSessions": current["sessions"],
                    "skillId": {"id": skill_id},
                }
            )

        return {"lastTotalLexemeCount": 0, "progressedSkills": progressed_skills}

    def fetch_all_words(self) -> list[dict[str, Any]]:
        payload = self.build_progress_payload(self.fetch_current_course())
        return self._fetch_words_with_payload(payload)

    def _fetch_words_with_payload(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        words: list[dict[str, Any]] = []
        start_index: int | None = 0
        seen_indexes: set[int] = set()

        while start_index is not None:
            if start_index in seen_indexes:
                raise DuolingoError("invalid_response", "多邻国分页游标发生重复。")
            seen_indexes.add(start_index)
            url = (
                "https://www.duolingo.cn/2017-06-30/users/"
                f"{self.settings.duolingo_user_id}/courses/"
                f"{self.settings.duolingo_course_id}/{self.settings.duolingo_from_language}"
                "/learned-lexemes"
                f"?limit={DUOLINGO_PAGE_SIZE}"
                f"&sortBy=LEARNED_DATE&startIndex={start_index}"
            )
            page = self._request_json("POST", url, payload)
            page_words = page.get("learnedLexemes")
            if not isinstance(page_words, list):
                raise DuolingoError("invalid_response", "多邻国响应缺少 learnedLexemes。")
            words.extend(page_words)
            start_index = (page.get("pagination") or {}).get("nextStartIndex")

        return words
