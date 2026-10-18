"""HW4 B3: one-question S3 pilot; the full B4 runner will be added later."""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from strategies import MAX_ROUNDS, run_s3

ROOT = Path(__file__).resolve().parent
QUESTIONS_PATH = ROOT / "data" / "questions.json"
OUTPUT_DIR = ROOT / "outputs"


def main():
    parser = argparse.ArgumentParser(description="Pilot-test the HW4 S3 tool loop")
    parser.add_argument("--question", default="Q-09", help="Question ID to test, e.g. Q-09")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is missing. Add it to .env (do not commit that file).")

    with QUESTIONS_PATH.open(encoding="utf-8") as f:
        questions = json.load(f)
    question = next((item for item in questions if item["id"] == args.question), None)
    if question is None:
        raise SystemExit(f"No question with id {args.question!r} in {QUESTIONS_PATH}")

    client = OpenAI(timeout=90.0, max_retries=1)
    # Pass only the question and its rounding requirement, NEVER answer/value/sources.
    record = run_s3(
        client,
        question=question["question"],
        decimals=question["decimals"],
        question_id=question["id"],
        max_rounds=MAX_ROUNDS,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output_path = OUTPUT_DIR / f"b3_{timestamp}_{question['id']}.json"
    output_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Question: {record['question_id']}")
    print(f"Status: {record['status']}")
    print(f"Answer: {record['answer']}")
    print(f"API rounds: {record['rounds_used']}; tool calls: {record['tool_call_count']}")
    print(f"Tokens: input={record['input_tokens']}, output={record['output_tokens']}")
    print(f"Total seconds: {record['total_seconds']}")
    print(f"Log saved: {output_path}")
    if record["status"] == "api_or_loop_error":
        print(f"Error type: {record.get('error_type')}; HTTP status: {record.get('error_status_code', 'unknown')}; "
              f"API code: {record.get('error_code', 'unknown')}")
        raise SystemExit("Request failed. Verify model access, API credit and .env settings.")


if __name__ == "__main__":
    main()
