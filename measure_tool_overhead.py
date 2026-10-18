"""Measure S3 two-tool prompt overhead through the required model's usage counters.

Run from project root AFTER replacing SUBMISSION.md with the completed file:
    python measure_tool_overhead.py

Performs two small API requests, saves outputs/tool_definition_tokens.json and
fills the one remaining measurement in SUBMISSION.md automatically.
The model is not asked to execute tools; only the input-token overhead is needed.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from strategies import MODEL, SYSTEM_PROMPT, TOOLS

HERE = Path(__file__).resolve().parent


def main():
    load_dotenv(HERE / '.env')
    client = OpenAI()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            "Which district has higher population density, Brightwater or Millbrook? "
            "Only reply OK for this tool-overhead measurement."
        )},
    ]
    params = {"model": MODEL, "messages": messages, "reasoning_effort": "none"}
    try:
        # Identical model, messages and reasoning. Only the presence of tools varies.
        no_tools = client.chat.completions.create(**params)
        with_tools = client.chat.completions.create(**params, tools=TOOLS, tool_choice="auto")
    except Exception as exc:
        print(f"Measurement API error: {type(exc).__name__}. Please check model/API access.")
        return

    without = int(no_tools.usage.prompt_tokens)
    with_ = int(with_tools.usage.prompt_tokens)
    added = with_ - without
    summary = {
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "reasoning_effort": "none",
        "without_tools_prompt_tokens": without,
        "with_two_tools_prompt_tokens": with_,
        "two_tools_added_input_tokens_per_request": added,
        "measurement_method": "Two live Chat Completions calls with otherwise identical messages; one has no tools, the other has the two S3 schemas and tool_choice=auto.",
    }
    out = HERE / 'outputs' / 'tool_definition_tokens.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(f"Without tool definitions: {without} input tokens")
    print(f"With both tool definitions: {with_} input tokens")
    print(f"Added by both definitions: {added} input tokens per request")
    print(f"Saved: {out}")

    submission = HERE / 'SUBMISSION.md'
    if submission.exists() and '@@TOOL_TOKEN_OVERHEAD@@' in submission.read_text(encoding='utf-8'):
        content = submission.read_text(encoding='utf-8').replace('@@TOOL_TOKEN_OVERHEAD@@', str(added))
        submission.write_text(content, encoding='utf-8')
        print('Updated SUBMISSION.md final question #3.')
    else:
        print('SUBMISSION.md measurement placeholder not found; enter the observed number manually if needed.')


if __name__ == '__main__':
    main()
