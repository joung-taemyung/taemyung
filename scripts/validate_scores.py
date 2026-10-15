"""뉴스 점수 JSONL 검사 — `uv run python scripts/validate_scores.py` 로 실행합니다.

검사: ① 줄 수 100 ② news_sent_0_10이 0–10 정수 ③ label이 점수 대역과 일치
      ④ evidence가 원문 headline · body_short 안에 글자 그대로 있음
원문은 d2_news.csv에서 news_id로 찾아 맞춰 봅니다.
인자로 다른 JSONL 경로를 줄 수 있습니다. 위반이 있으면 행별로 출력하고 종료 코드 1을 돌려줍니다. 파일은 고치지 않습니다.
"""
import csv
import json
import sys
from pathlib import Path

SOURCE = Path("data/checkpoints/d2_news.csv")
SCORED = Path("workbench/day2/outputs/news_scored.jsonl")
EXPECTED_LINES = 100


def band(score):
    """규칙 5: 0–2 severe, 3–4 high, 5 watch, 6–10 low."""
    if score <= 2:
        return "severe"
    if score <= 4:
        return "high"
    if score == 5:
        return "watch"
    return "low"


def main():
    # Windows 콘솔(cp949)에서도 한글이 깨지지 않게
    sys.stdout.reconfigure(encoding="utf-8")
    scored = Path(sys.argv[1]) if len(sys.argv) > 1 else SCORED

    with SOURCE.open(encoding="utf-8-sig", newline="") as f:
        source = {r["news_id"]: r for r in csv.DictReader(f)}

    lines = scored.read_text(encoding="utf-8").splitlines()
    problems = []

    if len(lines) != EXPECTED_LINES:
        problems.append(("-", f"줄 수 {len(lines)} (기대 {EXPECTED_LINES})"))

    for no, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            problems.append((f"{no}행", f"JSON 파싱 실패: {e}"))
            continue
        where = f"{no}행 {row.get('news_id', '?')}"

        score = row.get("news_sent_0_10")
        # bool은 int의 하위형이라 따로 막는다
        score_ok = isinstance(score, int) and not isinstance(score, bool) and 0 <= score <= 10
        if not score_ok:
            problems.append((where, f"점수가 0–10 정수가 아님: {score!r}"))
        elif row.get("label") != band(score):
            problems.append((where, f"label {row.get('label')!r} ≠ 점수 {score}의 대역 {band(score)!r}"))

        src = source.get(row.get("news_id"))
        if src is None:
            problems.append((where, "원문에 없는 news_id"))
            continue
        evidence = row.get("evidence") or ""
        if not evidence or (evidence not in src["headline"] and evidence not in src["body_short"]):
            problems.append((where, f"evidence가 원문에 그대로 없음: {evidence!r}"))

    print(f"검사 파일: {scored} · {len(lines)}줄")
    if not problems:
        print("위반 0건")
        return 0
    print(f"위반 {len(problems)}건")
    for where, msg in problems:
        print(f"  [{where}] {msg}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
