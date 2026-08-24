"""Probe Duolingo learned lexemes with exactly one progressed skill.

The script is intentionally separate from the website sync implementation. It
reads DUOLINGO_JWT from the process environment, or falls back to website/.env,
then writes a non-sensitive JSON result for inspection.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from fetch_duolingo_words import (
    collect_path_levels,
    fetch_all_words,
    fetch_current_course,
)


DEFAULT_OUTPUT = Path("single_skill_probe_result.json")


def read_env_value(path: Path, name: str) -> str:
    if not path.exists():
        return ""
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    return ""


def get_jwt_token() -> str:
    token = os.environ.get("DUOLINGO_JWT", "").strip()
    if token:
        return token
    return read_env_value(Path("website/.env"), "DUOLINGO_JWT")


def collect_progressed_skills(course_data: dict) -> list[dict]:
    current_course = course_data.get("currentCourse")
    if not isinstance(current_course, dict):
        raise RuntimeError("currentCourse is missing. Is the JWT valid?")

    rows = []
    for entry in collect_path_levels(current_course):
        level = entry["level"]
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
        rows.append(
            {
                "sectionIndex": entry["sectionIndex"],
                "unitIndex": entry["unitIndex"],
                "levelIndex": entry["levelIndex"],
                "debugName": level.get("debugName") or "",
                "skillId": skill_id,
                "finishedLevels": metadata.get("crownLevelIndex") or 0,
                "finishedSessions": level.get("finishedSessions") or 0,
                "totalSessions": level.get("totalSessions") or 0,
                "state": level.get("state"),
            }
        )

    by_skill_id: dict[str, list[dict]] = {}
    for row in rows:
        by_skill_id.setdefault(row["skillId"], []).append(row)

    progressed = []
    for skill_rows in by_skill_id.values():
        skill_rows.sort(
            key=lambda row: (
                row["sectionIndex"],
                row["unitIndex"],
                row["finishedLevels"],
                row["levelIndex"],
            )
        )
        progressed.append(skill_rows[-1])

    progressed.sort(
        key=lambda row: (
            row["sectionIndex"],
            row["unitIndex"],
            row["levelIndex"],
        )
    )
    return progressed


def print_skills(skills: list[dict]) -> None:
    print("[skills] progressed skill count:", len(skills))
    for index, skill in enumerate(skills, start=1):
        print(
            f"  {index:>2}. {skill['debugName'] or '(unnamed)'}"
            f" | skillId={skill['skillId']}"
            f" | level={skill['finishedLevels']}"
            f" | sessions={skill['finishedSessions']}/{skill['totalSessions']}"
            f" | path={skill['sectionIndex']}/{skill['unitIndex']}/{skill['levelIndex']}"
            f" | state={skill['state']}"
        )


def select_skill(skills: list[dict], skill_index: int, skill_id: str) -> dict:
    if not skills:
        raise RuntimeError("No passed or active regular skills were found.")
    if skill_id:
        for skill in skills:
            if skill["skillId"] == skill_id:
                return skill
        raise RuntimeError(f"Skill ID was not found in current progress: {skill_id}")

    resolved_index = skill_index - 1 if skill_index > 0 else len(skills) + skill_index
    if resolved_index < 0 or resolved_index >= len(skills):
        raise RuntimeError(
            f"Skill index {skill_index} is outside the available range 1..{len(skills)}."
        )
    return skills[resolved_index]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send learned-lexemes a payload containing exactly one skill."
    )
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument(
        "--skill-index",
        type=int,
        default=-1,
        help="1-based path index. -1 selects the latest progressed skill (default).",
    )
    selector.add_argument("--skill-id", default="", help="Select an exact Duolingo skill ID.")
    parser.add_argument(
        "--sessions",
        type=int,
        help="Override finishedSessions for session-boundary comparison.",
    )
    parser.add_argument(
        "--finished-levels",
        type=int,
        help="Override finishedLevels. Defaults to current course progress.",
    )
    parser.add_argument("--list", action="store_true", help="List skills without requesting words.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    token = get_jwt_token()
    if not token:
        print("DUOLINGO_JWT is missing from the environment and website/.env.")
        sys.exit(1)

    course_data = fetch_current_course(token)
    skills = collect_progressed_skills(course_data)
    print_skills(skills)
    if args.list:
        return

    selected = select_skill(skills, args.skill_index, args.skill_id)
    sessions = (
        args.sessions if args.sessions is not None else selected["finishedSessions"]
    )
    finished_levels = (
        args.finished_levels
        if args.finished_levels is not None
        else selected["finishedLevels"]
    )
    if sessions < 0 or finished_levels < 0:
        raise RuntimeError("Session and level values must not be negative.")

    payload = {
        "lastTotalLexemeCount": 0,
        "progressedSkills": [
            {
                "finishedLevels": finished_levels,
                "finishedSessions": sessions,
                "skillId": {"id": selected["skillId"]},
            }
        ],
    }
    print(
        "[probe] requesting one skill:",
        selected["debugName"] or selected["skillId"],
        f"level={finished_levels}",
        f"sessions={sessions}",
    )

    result = fetch_all_words(token, payload)
    result["probe"] = {
        "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "selectedSkill": selected,
        "requestedFinishedLevels": finished_levels,
        "requestedFinishedSessions": sessions,
        "progressedSkillCount": len(payload["progressedSkills"]),
    }
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("[done] fetched:", result["fetchedCount"])
    print("[done] words:", " | ".join(word["text"] for word in result["learnedLexemes"]))
    print("[done] output:", args.output.resolve())


if __name__ == "__main__":
    main()
