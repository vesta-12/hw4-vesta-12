"""HW4 B4/C1: reproducible S1/S2/S3 experiments and JSON logs.

Usage:
    python experiment.py --pilot                    # Two questions, all 3 strategies
    python experiment.py --full                     # Both complete runs (20 x 3 x 2)
    python experiment.py --full --run 1             # Just run 1 (resume-safe)
    python experiment.py --full --run 2             # Just run 2 (resume-safe)
    python experiment.py --vague                    # C1: five S3 questions, vague lookup
    python experiment.py --summary                  # Rebuild tables, no API calls

No gold answers are ever passed to the API; they are only read for local grading.
"""

import argparse
import csv
import json
import os
import re
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

import strategies

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "outputs"
MODEL = strategies.MODEL
# Prices in USD per 1 million tokens, as listed for gpt-5.6-luna on 2026-10-18.
# Estimated costs use non-cached input rates (actual invoices may be lower).
INPUT_PRICE_PER_M = 0.20
OUTPUT_PRICE_PER_M = 1.20
REASONING_EFFORT = "none"  # Required for tools via Chat Completions with this model.
S2_K = 5
S2_TEMPERATURE = 0.7
VAGUE_IDS = ["Q-01", "Q-03", "Q-09", "Q-13", "Q-18"]
S1_S2_SYSTEM = (
    "You answer quantitative questions using the Nine Rivers Region documents below. "
    "Use current 2025 profiles, not ARCHIVED profiles. Correction notices override "
    "old values. Keep units consistent and perform arithmetic carefully, without "
    "rounding intermediate values. Use only the provided documents. "
    "End with exactly one line: ANSWER: <name or yes/no> | <number>. "
    "Do not include any other ANSWER lines."
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def questions():
    values = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    assert len(values) == 20 and len(set(q["id"] for q in values)) == 20, "Expected 20 unique questions"
    return values


def corpus_text():
    """Full corpus for S1 and S2 ONLY. Never include ground-truth answers."""
    docs = [json.loads(line) for line in (DATA / "corpus.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(docs) == 17, "Expected 17 corpus documents"
    return "\n".join(f"[{d['id']}] {d['title']}\n{d['text']}" for d in docs)


def parse_answer(text):
    """Require final response line ANSWER: name | number."""
    lines = (text or "").strip().splitlines()
    if not lines:
        return None
    match = strategies.ANSWER_PATTERN.fullmatch(lines[-1].strip())
    if not match:
        return None
    label = match.group(1).strip()
    number_text = match.group(2).strip()
    if not label:
        return None
    try:
        numeric = float(number_text)
        if not (-1e100 < numeric < 1e100):
            return None
    except ValueError:
        return None
    return {"name": label, "number": numeric, "raw_number": number_text}


def cost_usd(input_tokens, output_tokens):
    """List-price estimate, ignoring any unknown input cache discounts."""
    return round((input_tokens * INPUT_PRICE_PER_M + output_tokens * OUTPUT_PRICE_PER_M) / 1_000_000, 8)


def grade(record, question):
    """Check name exactly, numeric answer within ONE unit in the last requested digit.

    Gold is used ONLY after the model has returned its final answer.
    """
    parsed = parse_answer("ANSWER: " + record["answer"]) if record.get("answer") else None
    if parsed is None or record.get("status") != "answered":
        correct = False
    else:
        allowed_difference = 10 ** (-int(question["decimals"]))
        correct = (
            parsed["name"] == question["answer"]
            and abs(parsed["number"] - float(question["value"])) <= allowed_difference + 1e-10
        )
    record["grading"] = {
        "correct": correct,
        "expected_name": question["answer"],
        "expected_value": question["value"],
        "decimals": question["decimals"],
        "allowed_absolute_difference": 10 ** (-int(question["decimals"])),
    }
    record["estimated_cost_usd"] = cost_usd(record.get("input_tokens", 0), record.get("output_tokens", 0))
    return record


def single_sample(client, prompt_messages, *, temperature=None):
    params = {
        "model": MODEL,
        "messages": prompt_messages,
        "reasoning_effort": REASONING_EFFORT,
    }
    if temperature is not None:
        params["temperature"] = temperature
    started = time.perf_counter()
    response = client.chat.completions.create(**params)
    seconds = round(time.perf_counter() - started, 6)
    usage = response.usage
    message = response.choices[0].message
    return {
        "response": message.content or "",
        "parsed": parse_answer(message.content or ""),
        "seconds": seconds,
        "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "cached_input_tokens": int(getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0),
        "finish_reason": response.choices[0].finish_reason,
    }


def run_s1_s2(client, question, corpus, strategy):
    assert strategy in ("S1", "S2")
    is_vote = strategy == "S2"
    messages = [
        {"role": "system", "content": S1_S2_SYSTEM},
        {"role": "user", "content": (
            f"DOCUMENTS:\n{corpus}\n\nQUESTION:\n{question['question']}\n"
            f"Round the final number to {question['decimals']} decimal place(s). "
            + ("Show your calculation steps before the answer. " if is_vote else "")
            + "End with: ANSWER: <name or yes/no> | <number>"
        )},
    ]
    record = {
        "strategy": strategy,
        "question_id": question["id"],
        "question": question["question"],
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "temperature": S2_TEMPERATURE if is_vote else "API default",
        "documents_in_prompt": True,
        "samples_requested": S2_K if is_vote else 1,
        "started_at_utc": utc_now(),
        "status": "unanswered",
        "answer": None,
        "api_calls": [],
        "input_tokens": 0,
        "output_tokens": 0,
        "total_seconds": 0,
        "prompt_messages": messages,
    }
    started = time.perf_counter()
    try:
        for i in range(S2_K if is_vote else 1):
            sample = single_sample(client, messages, temperature=S2_TEMPERATURE if is_vote else None)
            sample["sample_number"] = i + 1
            record["api_calls"].append(sample)
            record["input_tokens"] += sample["input_tokens"]
            record["output_tokens"] += sample["output_tokens"]
        if not is_vote:
            first = record["api_calls"][0]
            if first["parsed"]:
                record["answer"] = f"{first['parsed']['name']} | {first['parsed']['raw_number']}"
                record["status"] = "answered"
            else:
                record["status"] = "unanswered_invalid_format"
        else:
            # Canonicalize numbers to decimals for voting, but preserve each raw sample in the log.
            votes = []
            for sample in record["api_calls"]:
                parsed = sample["parsed"]
                if parsed:
                    rounded_number = f"{parsed['number']:.{int(question['decimals'])}f}"
                    votes.append(f"{parsed['name']} | {rounded_number}")
            if votes:
                counts = Counter(votes)
                top_count = max(counts.values())
                winners = [answer for answer in votes if counts[answer] == top_count]
                record["answer"] = winners[0]  # Deterministic tie break by first occurrence.
                record["status"] = "answered"
                record["vote_counts"] = dict(counts)
                record["vote_tie"] = len(set(winners)) > 1
            else:
                record["status"] = "unanswered_all_invalid"
    except Exception as exc:
        record["status"] = "api_or_loop_error"
        record["error_type"] = type(exc).__name__
        if getattr(exc, "status_code", None) is not None:
            record["error_status_code"] = exc.status_code
    finally:
        record["total_seconds"] = round(time.perf_counter() - started, 6)
    return record


def run_strategy(client, question, corpus, strategy, *, vague=False):
    if strategy in ("S1", "S2"):
        return run_s1_s2(client, question, corpus, strategy)
    if strategy != "S3":
        raise ValueError(f"Unknown strategy: {strategy}")

    # Keep the working B3 implementation intact; modify only the tool description for C1.
    original = strategies.TOOLS[0]["function"]["description"]
    if vague:
        strategies.TOOLS[0]["function"]["description"] = "Looks things up."
    try:
        record = strategies.run_s3(
            client, question=question["question"], decimals=question["decimals"],
            question_id=question["id"], max_rounds=strategies.MAX_ROUNDS,
        )
    finally:
        strategies.TOOLS[0]["function"]["description"] = original
    record["strategy"] = "S3"
    record["reasoning_effort"] = REASONING_EFFORT
    record["temperature"] = "API default"
    record["documents_in_prompt"] = False
    record["lookup_description"] = "vague" if vague else "original"
    return record


EVALUATION_RULE = """# Evaluation rule — recorded before the first full B4 run

A model answer must end with `ANSWER: <name or yes/no> | <number>`.
The name/yes/no must match `data/questions.json` **exactly** (case-sensitive).
The numeric value must differ by at most **one unit in the final requested decimal place**:
absolute difference <= 10 ** (-decimals). No partial credit. Missing or malformed
final answers and API/loop errors are marked unanswered (and incorrect).
The correct answer, gold number, and gold sources are NEVER included in API prompts.

S2: five independent calls, temperature=0.7 and the instruction to show working;
parse the final answer from each response, vote on name and value formatted to the
requested number of decimals; tie-break using the earliest valid sample.
All strategies use model gpt-5.6-luna with reasoning_effort='none' and Chat Completions.
S1/S3 use API default temperature; S2 explicitly uses 0.7 to promote variety.
S3 has maximum 8 API rounds; S1 and S2 get all 17 documents, S3 gets none.
Costs are *estimates* based on published standard (noncached) token rates:
$0.20/1M input tokens, $1.20/1M output tokens, using all calls' usage totals.
"""


def ensure_rule():
    OUT.mkdir(exist_ok=True)
    rule_path = OUT / "evaluation_rule.md"
    if not rule_path.exists():
        rule_path.write_text(EVALUATION_RULE, encoding="utf-8")
        print(f"Evaluation rule recorded: {rule_path}")
    elif rule_path.read_text(encoding="utf-8") != EVALUATION_RULE:
        print("NOTE: evaluation_rule.md already exists and differs; not overwriting it.")


def summarize_run(run_path, question_list, *, save=True):
    rows = []
    all_existing = []
    for q in question_list:
        statuses = {}
        for strategy in ("S1", "S2", "S3"):
            path = run_path / strategy / f"{q['id']}.json"
            if not path.exists():
                statuses[strategy] = "?"
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            all_existing.append(record)
            statuses[strategy] = "✓" if record["grading"]["correct"] else "✗"
        rows.append({"Question": q["id"], **statuses})

    results = {}
    for strategy in ("S1", "S2", "S3"):
        items = [r for r in all_existing if r["strategy"] == strategy]
        durations = [r["total_seconds"] for r in items]
        results[strategy] = {
            "n_questions": len(items),
            "correct": sum(int(x["grading"]["correct"]) for x in items),
            "unanswered": sum(x["status"] != "answered" for x in items),
            "input_tokens": sum(x["input_tokens"] for x in items),
            "output_tokens": sum(x["output_tokens"] for x in items),
            "median_seconds_per_question": round(statistics.median(durations), 4) if durations else 0,
            "total_seconds": round(sum(durations), 4),
            "estimated_cost_usd": round(sum(x["estimated_cost_usd"] for x in items), 8),
        }
    summary = {"run": run_path.name, "strategies": results, "per_question": rows}
    if save:
        save_json(run_path / "summary.json", summary)
        with (run_path / "per_question.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["Question", "S1", "S2", "S3"])
            writer.writeheader()
            writer.writerows(rows)
    return summary


def summarize_all(question_list):
    summaries = []
    for number in (1, 2):
        folder = OUT / f"run_{number}"
        summary = summarize_run(folder, question_list)
        summaries.append(summary)
    print("\nB4 summary (estimated token cost, USD):")
    print("Run  Strategy  N   Correct  Unanswered   Input tokens  Output tokens  Median sec  Total sec   Cost")
    for number, summary in enumerate(summaries, start=1):
        for strategy, item in summary["strategies"].items():
            print(f"{number:<4} {strategy:<8} {item['n_questions']:<3} {item['correct']:<8} "
                  f"{item['unanswered']:<12} {item['input_tokens']:<13} {item['output_tokens']:<14} "
                  f"{item['median_seconds_per_question']:<11} {item['total_seconds']:<11} "
                  f"${item['estimated_cost_usd']:.6f}")
    save_json(OUT / "b4_combined_summary.json", {"runs": summaries})

    # Candidate logs for the later written Part C: wrong answer, or most expensive if all correct.
    suggestions = {}
    for strategy in ("S1", "S2", "S3"):
        collected = []
        for number in (1, 2):
            for q in question_list:
                path = OUT / f"run_{number}" / strategy / f"{q['id']}.json"
                if path.exists():
                    r = json.loads(path.read_text(encoding="utf-8"))
                    collected.append({
                        "path": str(path.relative_to(ROOT)), "run": number,
                        "question_id": q["id"], "answer": r.get("answer"),
                        "correct": r["grading"]["correct"],
                        "cost_usd": r["estimated_cost_usd"],
                        "rounds": r.get("rounds_used"),
                        "tool_calls": r.get("tool_call_count"),
                    })
        wrong = [r for r in collected if not r["correct"]]
        pool = wrong if wrong else collected
        suggestions[strategy] = {
            "suggested_case_for_C2": max(pool, key=lambda x: x["cost_usd"]) if pool else None,
            "has_wrong_answer": bool(wrong),
        }
    save_json(OUT / "c2_case_suggestions.json", suggestions)
    print("C2 candidate logs: outputs/c2_case_suggestions.json")


def execute_batch(client, question_list, corpus, target, *, vague=False):
    target.mkdir(parents=True, exist_ok=True)
    for i, question in enumerate(question_list, start=1):
        for strategy in (("S3",) if vague else ("S1", "S2", "S3")):
            path = target / strategy / f"{question['id']}.json"
            if path.exists():
                old = json.loads(path.read_text(encoding="utf-8"))
                if old.get("status") != "api_or_loop_error":
                    print(f"[{i}/{len(question_list)}] {question['id']} {strategy}: already saved, skipping")
                    continue
            record = run_strategy(client, question, corpus, strategy, vague=vague)
            grade(record, question)
            save_json(path, record)  # Save IMMEDIATELY; Ctrl+C won't lose previous questions.
            sign = "✓" if record["grading"]["correct"] else "✗"
            print(f"[{i}/{len(question_list)}] {question['id']} {strategy}: {sign} "
                  f"{record.get('answer')}  {record['total_seconds']:.2f}s "
                  f"{record['input_tokens']}in/{record['output_tokens']}out; "
                  f"${record['estimated_cost_usd']:.5f}", flush=True)
            if record["status"] == "api_or_loop_error":
                raise SystemExit(
                    f"API error for {question['id']} {strategy}: {record.get('error_type')} "
                    f"(HTTP {record.get('error_status_code', 'unknown')}). "
                    "Stopped to protect credit; previous results are saved. "
                    "Fix the issue and rerun the same command to resume."
                )


def main():
    parser = argparse.ArgumentParser(description="HW4 B4 three strategies / C1 vague lookup test")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pilot", action="store_true", help="test all three strategies on Q-01 and Q-09")
    mode.add_argument("--full", action="store_true", help="full experiment, 2 runs (or one with --run)")
    mode.add_argument("--vague", action="store_true", help="C1: S3 with vague lookup on five questions")
    mode.add_argument("--summary", action="store_true", help="recalculate summaries without API calls")
    parser.add_argument("--run", choices=["1", "2", "both"], default="both")
    args = parser.parse_args()
    all_questions = questions()
    if args.summary:
        summarize_all(all_questions)
        return

    ensure_rule()  # Rule is written before first API run.
    load_dotenv(ROOT / ".env")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("Missing OPENAI_API_KEY in .env")
    client = OpenAI(timeout=90.0, max_retries=2)
    corpus = corpus_text()
    if args.pilot:
        selected = [q for q in all_questions if q["id"] in {"Q-01", "Q-09"}]
        execute_batch(client, selected, corpus, OUT / "pilot")
        print("\nPilot saved in outputs/pilot. If successful, run: python experiment.py --full")
    elif args.full:
        selected_runs = [1, 2] if args.run == "both" else [int(args.run)]
        for number in selected_runs:
            print(f"\n===== FULL RUN {number} =====", flush=True)
            folder = OUT / f"run_{number}"
            execute_batch(client, all_questions, corpus, folder)
            summarize_run(folder, all_questions)
        summarize_all(all_questions)
    elif args.vague:
        selected = [q for q in all_questions if q["id"] in VAGUE_IDS]
        execute_batch(client, selected, corpus, OUT / "c1_vague", vague=True)
        comparison = []
        print("\nC1 comparison: Q-ID | original calls -> vague calls | original answer -> vague answer")
        for q in selected:
            reference = OUT / "run_1" / "S3" / f"{q['id']}.json"
            variant = OUT / "c1_vague" / "S3" / f"{q['id']}.json"
            if not reference.exists() or not variant.exists():
                print(q["id"], "missing baseline/variant; run --full first")
                continue
            a = json.loads(reference.read_text(encoding="utf-8"))
            b = json.loads(variant.read_text(encoding="utf-8"))
            row = {
                "id": q["id"], "original_tool_calls": a["tool_call_count"],
                "vague_tool_calls": b["tool_call_count"],
                "original_api_rounds": a["rounds_used"], "vague_api_rounds": b["rounds_used"],
                "original_answer": a["answer"], "vague_answer": b["answer"],
                "original_tool_inputs": [x["parsed_arguments"] for x in a["tool_calls"]],
                "vague_tool_inputs": [x["parsed_arguments"] for x in b["tool_calls"]],
                "original_correct": a["grading"]["correct"],
                "vague_correct": b["grading"]["correct"],
            }
            comparison.append(row)
            print(f"{q['id']}: {a['tool_call_count']} -> {b['tool_call_count']} calls; "
                  f"{a['answer']} -> {b['answer']}")
        save_json(OUT / "c1_comparison.json", comparison)
        print("Saved C1 comparison: outputs/c1_comparison.json")


if __name__ == "__main__":
    main()
