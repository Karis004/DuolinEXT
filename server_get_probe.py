import json
import os
import subprocess
import sys
import time
import urllib.parse


USER_ID = "1148050773"


def main():
    jwt_token = os.environ.get("DUOLINGO_JWT", "").strip()
    if not jwt_token or jwt_token == "PASTE_YOUR_JWT_TOKEN_HERE":
        print("Please set the DUOLINGO_JWT environment variable first.")
        sys.exit(1)

    fields = "currentCourse,currentCourseId,learningLanguage,fromLanguage"
    url = (
        f"https://www.duolingo.cn/2023-05-23/users/{USER_ID}"
        f"?fields={urllib.parse.quote(fields)}&_={int(time.time() * 1000)}"
    )

    command = [
        "curl",
        "--silent",
        "--show-error",
        "--location",
        "--compressed",
        "--url",
        url,
        "-H",
        "Accept: application/json; charset=UTF-8",
        "-H",
        "Accept-Language: zh-CN,zh;q=0.9",
        "-H",
        "Authorization: Bearer " + jwt_token,
        "-H",
        "X-Amzn-Trace-Id: User=" + USER_ID,
        "-H",
        "X-Requested-With: XMLHttpRequest",
        "-H",
        "Referer: https://www.duolingo.cn/practice-hub/words",
        "-H",
        "Sec-Fetch-Dest: empty",
        "-H",
        "Sec-Fetch-Mode: cors",
        "-H",
        "Sec-Fetch-Site: same-origin",
        "-H",
        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
        "-H",
        'sec-ch-ua: "Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
        "-H",
        "sec-ch-ua-mobile: ?0",
        "-H",
        'sec-ch-ua-platform: "Windows"',
        "--write-out",
        "\n__DUO_HTTP_STATUS__:%{http_code}",
    ]

    print("[probe] requesting:", url)
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if completed.returncode != 0:
        print("[probe] curl failed")
        print(completed.stderr)
        sys.exit(1)

    marker = "__DUO_HTTP_STATUS__:"
    if marker not in completed.stdout:
        print("[probe] missing HTTP status marker")
        print(completed.stdout[:1000])
        sys.exit(1)

    body, status_text = completed.stdout.rsplit(marker, 1)
    status = int(status_text.strip())
    print("[probe] HTTP status:", status)

    if status < 200 or status >= 300:
        print("[probe] request failed. Response preview:")
        print(body[:2000])
        sys.exit(1)

    data = json.loads(body)
    current_course = data.get("currentCourse") or {}

    print("[probe] success")
    print("[probe] currentCourseId:", data.get("currentCourseId"))
    print("[probe] account learningLanguage:", data.get("learningLanguage"))
    print("[probe] account fromLanguage:", data.get("fromLanguage"))
    print("[probe] course id:", current_course.get("id"))
    print("[probe] course learningLanguage:", current_course.get("learningLanguage"))
    print("[probe] course fromLanguage:", current_course.get("fromLanguage"))
    print("[probe] path sections:", len(current_course.get("pathSectioned") or []))


if __name__ == "__main__":
    main()
