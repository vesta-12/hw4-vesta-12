"""HW4 B3: S3 Chat Completions tool loop (no corpus in LLM prompt)."""

import json
import re
import time
from datetime import datetime, timezone

from tools import calculate, lookup

MODEL = "gpt-5.6-luna"
MAX_ROUNDS = 8  # Maximum API response rounds per question.

# Definitions copied from Part A in SUBMISSION.md; B3 must use these exact schemas.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": (
                "Retrieve one factual value from the Nine Rivers Region documents. "
                "Use an exact district name and field, or entity 'region' for the household water tariff. "
                "Prefer the current 2025 profiles, ignore archived profiles, and apply correction notices. "
                "Normalize household water use to megalitres. Return the numeric value, its unit, "
                "and source document IDs. Call this tool before calculating with facts from the documents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {
                        "type": "string",
                        "description": "Exact district name, or 'region' for regional information.",
                    },
                    "field": {
                        "type": "string",
                        "enum": [
                            "population_2020", "population_2025", "area_km2", "founded_year",
                            "budget_2025_million_tenge", "household_water_2025_megalitres",
                            "schools_2025_26", "pupils_2025_26", "paved_road_km",
                            "water_tariff_2025_tenge_per_m3",
                        ],
                        "description": (
                            "The one factual field to retrieve. The water tariff belongs to entity 'region'."
                        ),
                    },
                },
                "required": ["entity", "field"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": (
                "Safely evaluate arithmetic expressions containing numbers, parentheses, addition, "
                "subtraction, multiplication, and division. Use this tool for numerical calculations "
                "instead of mental arithmetic. Do not include units or Python code. Function calls, "
                "attribute access, exponentiation, and other unsupported operations are rejected."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": (
                            "Arithmetic expression, for example '37203 / 758.6 - 44675 / 921.4'."
                        ),
                    }
                },
                "required": ["expression"],
                "additionalProperties": False,
            },
        },
    },
]

SYSTEM_PROMPT = (
    "You answer factual questions about the Nine Rivers Region. You do not have the documents "
    "and must not invent facts. Retrieve facts using lookup, then use calculate for arithmetic. "
    "Check that compared quantities use compatible units; compare full-precision results and "
    "round only at the end. For yes/no comparisons, return the positive magnitude of the "
    "difference. If a tool returns an error, try correcting the request. "
    "Finish with exactly one final line: ANSWER: <name or yes/no> | <number>. "
    "If data cannot be obtained, explain the issue and do not produce an ANSWER line."
)

ANSWER_PATTERN = re.compile(
    r"^ANSWER:\s*([^|\n]+?)\s*\|\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*$",
    flags=re.MULTILINE,
)


def _reject_duplicate_keys(pairs):
    """Reject ambiguous JSON such as {\"field\":\"x\",\"field\":\"y\"}."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON argument key: {key}")
        result[key] = value
    return result


def execute_tool_call(tool_call):
    """Validate all arguments BEFORE calling any tool; always return a JSON-able dict."""
    started = time.perf_counter()
    name = tool_call.function.name
    raw = tool_call.function.arguments
    arguments = None
    try:
        if tool_call.type != "function":
            result = {"error": "unsupported tool call type"}
        elif name not in ("lookup", "calculate"):
            result = {"error": f"unknown tool: {name}"}
        elif not isinstance(raw, str) or len(raw) > 1000:
            result = {"error": "arguments must be a JSON string of at most 1000 characters"}
        else:
            arguments = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
            expected = {"entity", "field"} if name == "lookup" else {"expression"}
            if not isinstance(arguments, dict):
                result = {"error": "arguments must be a JSON object"}
            elif set(arguments) != expected:
                result = {"error": f"expected exactly these fields: {sorted(expected)}"}
            elif any(not isinstance(arguments[key], str) for key in expected):
                result = {"error": "all tool arguments must be strings"}
            else:
                result = lookup(**arguments) if name == "lookup" else calculate(**arguments)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        result = {"error": f"invalid tool arguments: {type(exc).__name__}: {exc}"}
    except Exception as exc:
        # No arbitrary exception string is sent into logs, which might contain sensitive data.
        result = {"error": f"tool execution failed: {type(exc).__name__}"}

    duration = round(time.perf_counter() - started, 6)
    return result, arguments, duration


def run_s3(client, question, decimals, question_id="Q-unknown", max_rounds=MAX_ROUNDS):
    """Run S3 for ONE question. No answer key and no documents are sent to the model."""
    user_question = (
        f"{question}\nRound only the final numerical answer to {int(decimals)} decimal "
        "place(s). End with: ANSWER: <name or yes/no> | <number>"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_question},
    ]
    record = {
        "strategy": "S3_tools",
        "question_id": question_id,
        "question": question,
        "model": MODEL,
        "max_rounds": max_rounds,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "unanswered",
        "answer": None,
        "api_calls": [],
        "tool_calls": [],
        "input_tokens": 0,
        "output_tokens": 0,
    }
    started = time.perf_counter()
    try:
        for round_no in range(1, max_rounds + 1):
            call_started = time.perf_counter()
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="required" if round_no == 1 else "auto",
                reasoning_effort="none",
            )
            api_seconds = round(time.perf_counter() - call_started, 6)
            usage = response.usage
            input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            record["input_tokens"] += input_tokens
            record["output_tokens"] += output_tokens

            message = response.choices[0].message
            record["api_calls"].append({
                "round": round_no,
                "seconds": api_seconds,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "finish_reason": response.choices[0].finish_reason,
                "assistant_message": message.model_dump(exclude_none=True),
            })
            # Preserve SDK model message unchanged, including tool_calls and their IDs.
            messages.append(message)
            calls = message.tool_calls or []
            if not calls:
                content = message.content or ""
                found = ANSWER_PATTERN.findall(content)
                if found and content.rstrip().splitlines()[-1].startswith("ANSWER:"):
                    record["answer"] = f"{found[-1][0].strip()} | {found[-1][1]}"
                    record["status"] = "answered"
                else:
                    record["status"] = "unanswered_invalid_final_format"
                break

            for call in calls:
                result, args, seconds = execute_tool_call(call)
                record["tool_calls"].append({
                    "round": round_no,
                    "tool_call_id": call.id,
                    "tool": call.function.name,
                    "raw_arguments": call.function.arguments,
                    "parsed_arguments": args,
                    "result": result,
                    "seconds": seconds,
                })
                # Every tool_call_id gets one response, even if validation failed.
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
        else:
            record["status"] = "unanswered_round_limit"
    except Exception as exc:
        record["status"] = "api_or_loop_error"
        record["error_type"] = type(exc).__name__  # Never write a key or raw exception to logs.
        if getattr(exc, "status_code", None) is not None:
            record["error_status_code"] = exc.status_code
        if getattr(exc, "code", None) is not None:
            record["error_code"] = str(exc.code)
    finally:
        record["total_seconds"] = round(time.perf_counter() - started, 6)
        record["rounds_used"] = len(record["api_calls"])
        record["tool_call_count"] = len(record["tool_calls"])
        record["messages"] = [
            msg.model_dump(exclude_none=True) if hasattr(msg, "model_dump") else msg
            for msg in messages
        ]
    return record
