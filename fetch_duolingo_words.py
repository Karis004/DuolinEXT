import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse


USER_ID = "1148050773"
COURSE_ID = "fr"
FROM_LANGUAGE = "zh"
PAGE_SIZE = 50
OUTPUT_FILE = "duolingo_words.json"


def request_json(method, url, jwt_token, body=None):
    headers = {
        "Accept": "application/json; charset=UTF-8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Authorization": "Bearer " + jwt_token,
        "X-Amzn-Trace-Id": "User=" + USER_ID,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.duolingo.cn/practice-hub/words",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    }

    temp_body_path = None
    if body is not None:
        headers["Content-Type"] = "application/json; charset=UTF-8"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json") as temp_body:
            json.dump(body, temp_body, ensure_ascii=False)
            temp_body_path = temp_body.name

    marker = "\n__DUO_HTTP_STATUS__:%{http_code}"
    command = [
        "curl",
        "--ssl-no-revoke",
        "--silent",
        "--show-error",
        "--location",
        "--request",
        method,
        "--url",
        url,
        "--write-out",
        marker,
    ]

    for name, value in headers.items():
        command.extend(["-H", f"{name}: {value}"])

    if temp_body_path:
        command.extend(["--data-binary", "@" + temp_body_path])

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
        raise RuntimeError("curl failed:\n" + completed.stderr)

    status_marker = "__DUO_HTTP_STATUS__:"
    if status_marker not in completed.stdout:
        raise RuntimeError("Could not find HTTP status marker in curl output:\n" + completed.stdout[:500])

    text, status_text = completed.stdout.rsplit(status_marker, 1)
    status = int(status_text.strip())
    if status < 200 or status >= 300:
        raise RuntimeError(f"HTTP {status} from {url}\n{text}")

    return status, json.loads(text)


def fetch_current_course(jwt_token):
    fields = "currentCourse,currentCourseId,learningLanguage,fromLanguage"
    url = (
        f"https://www.duolingo.cn/2023-05-23/users/{USER_ID}"
        f"?fields={urllib.parse.quote(fields)}&_={int(time.time() * 1000)}"
    )
    status, data = request_json("GET", url, jwt_token)
    print("[course] status:", status)
    print("[course] currentCourseId:", data.get("currentCourseId"))
    return data


def collect_path_levels(current_course):
    levels = []
    for section_index, section in enumerate(current_course.get("pathSectioned") or []):
        for unit_index, unit in enumerate(section.get("units") or []):
            for level_index, level in enumerate(unit.get("levels") or []):
                levels.append(
                    {
                        "sectionIndex": section_index,
                        "unitIndex": unit_index,
                        "levelIndex": level_index,
                        "level": level,
                    }
                )
    return levels


def build_payload(course_data):
    current_course = course_data.get("currentCourse")
    if not current_course:
        raise RuntimeError("currentCourse is missing. Is the JWT valid?")

    print("[course] id:", current_course.get("id"))
    print("[course] learningLanguage:", current_course.get("learningLanguage"))
    print("[course] fromLanguage:", current_course.get("fromLanguage"))

    skill_levels = []
    for entry in collect_path_levels(current_course):
        level = entry["level"]
        metadata = level.get("pathLevelMetadata") or {}
        client_data = level.get("pathLevelClientData") or {}
        skill_id = metadata.get("skillId") or client_data.get("skillId")

        if level.get("type") != "skill":
            continue
        if level.get("subtype") != "regular":
            continue
        if not skill_id:
            continue
        if level.get("state") not in ("passed", "active"):
            continue

        skill_levels.append(
            {
                "sectionIndex": entry["sectionIndex"],
                "unitIndex": entry["unitIndex"],
                "levelIndex": entry["levelIndex"],
                "debugName": level.get("debugName"),
                "skillId": skill_id,
                "crownLevelIndex": metadata.get("crownLevelIndex") or 0,
                "finishedSessions": level.get("finishedSessions") or 0,
                "state": level.get("state"),
            }
        )

    by_skill_id = {}
    for item in skill_levels:
        by_skill_id.setdefault(item["skillId"], []).append(item)

    progressed_skills = []
    for skill_id, rows in by_skill_id.items():
        rows.sort(
            key=lambda item: (
                item["sectionIndex"],
                item["unitIndex"],
                item["crownLevelIndex"],
                item["levelIndex"],
            )
        )
        current = rows[-1]
        progressed_skills.append(
            {
                "finishedLevels": current["crownLevelIndex"],
                "finishedSessions": current["finishedSessions"],
                "skillId": {"id": skill_id},
            }
        )

    print("[payload] progressed skill count:", len(progressed_skills))
    for index, skill in enumerate(progressed_skills):
        print(
            "[payload]",
            index,
            skill["skillId"]["id"],
            "levels=" + str(skill["finishedLevels"]),
            "sessions=" + str(skill["finishedSessions"]),
        )

    return {
        "lastTotalLexemeCount": 0,
        "progressedSkills": progressed_skills,
    }


def fetch_words_page(jwt_token, payload, start_index):
    url = (
        f"https://www.duolingo.cn/2017-06-30/users/{USER_ID}"
        f"/courses/{COURSE_ID}/{FROM_LANGUAGE}/learned-lexemes"
        f"?limit={PAGE_SIZE}&sortBy=LEARNED_DATE&startIndex={start_index}"
    )
    status, data = request_json("POST", url, jwt_token, payload)
    pagination = data.get("pagination") or {}
    words = data.get("learnedLexemes") or []
    print(
        "[words] status:",
        status,
        "startIndex:",
        start_index,
        "received:",
        len(words),
        "total:",
        pagination.get("totalLexemes"),
        "next:",
        pagination.get("nextStartIndex"),
    )
    return data


def fetch_all_words(jwt_token, payload):
    pages = []
    words = []
    start_index = 0

    while start_index is not None:
        page = fetch_words_page(jwt_token, payload, start_index)
        pages.append(page)
        for word in page.get("learnedLexemes") or []:
            words.append({"index": len(words), **word})
        start_index = (page.get("pagination") or {}).get("nextStartIndex")

    return {
        "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "totalLexemes": (pages[0].get("pagination") or {}).get("totalLexemes") if pages else len(words),
        "fetchedCount": len(words),
        "payload": payload,
        "pages": pages,
        "learnedLexemes": words,
    }


def main():
    jwt_token = os.environ.get("DUOLINGO_JWT", "").strip()
    if not jwt_token or jwt_token == "PASTE_YOUR_JWT_TOKEN_HERE":
        print("Please set the DUOLINGO_JWT environment variable first.")
        sys.exit(1)

    course_data = fetch_current_course(jwt_token)
    payload = build_payload(course_data)
    result = fetch_all_words(jwt_token, payload)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)

    print("[done] fetched:", result["fetchedCount"])
    print("[done] output:", OUTPUT_FILE)


if __name__ == "__main__":
    main()
