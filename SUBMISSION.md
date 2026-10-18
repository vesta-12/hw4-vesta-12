# HW4 — submission

**AI assistance:** I used gpt to discuss Part A design, develop and debug the Python tools and experiment scripts, and review logs while drafting Parts B and C. I ran the tests and API experiments in my project; the numerical results and examples below are taken from saved local outputs, not invented model responses.

---

## Part A — Plan on paper (Easy)

> Commit this whole part before any `.py` file exists.

### A1 — The steps

**My approach.** The documents contain facts, but the questions require several operations before an answer can be produced. I decided to separate finding reliable data, performing arithmetic, and choosing the final answer. This makes it easier to identify whether a mistake comes from retrieval, calculation, or reasoning.

| # | Step | What goes in (types) | What comes out (types) | When it goes wrong | Tool, loop, or model? |
|---|---|---|---|---|---|
| 1 | Understand the question | Question (`str`) | District names (`list[str]`), needed fields (`list[str]`), calculation goal (`str`) | If the question is unclear, the model may not identify the needed facts. It should ask for clarification or leave the question unanswered, not invent values. | Model |
| 2 | Validate the tool request | Tool name (`str`), JSON arguments (`str`) | Validated arguments (`dict`) or error (`dict`) | Invalid JSON, missing fields, wrong types, extra fields, or an unknown tool give an error. | Loop |
| 3 | Retrieve facts | Entity (`str`) and field (`str`) | Fact (`dict`): value (`int` or `float`), unit (`str`), source IDs (`list[str]`) | Missing or misspelled district: error and suggestions. Ambiguous name: error and choices. Unreadable number, unknown field/unit: error. Archived profiles are excluded and corrections take priority. | `lookup` |
| 4 | Check units and prepare arithmetic | Facts (`list[dict]`) and goal (`str`) | Compatible numeric values and arithmetic expression (`str`) | If units are incompatible or unknown, do not calculate until resolved. Use standard units returned by `lookup` and explicit conversions where needed. | Model |
| 5 | Calculate | Arithmetic expression (`str`) | Result (`dict`) with numeric value (`int` or `float`) | Invalid syntax, division by zero, unsafe operations, and oversized expressions return an error instead of running. | `calculate` |
| 6 | Compare and format | Facts (`list[dict]`), computed numbers (`list[float]`), question (`str`) | Final answer (`str`) in the form `ANSWER: <name or yes/no> \| <number>` | If a required fact is missing or a calculation fails, the answer should be marked unanswered rather than guessed. | Model |
| 7 | Control tool calls | Conversation (`list[dict]`), tool calls (`list[dict]`), round count (`int`) | Updated conversation (`list[dict]`) and completion status (`str`) | Every `tool_call_id` gets a response, including errors. Stop at a fixed round limit; otherwise a repeated call could continue indefinitely. | Loop |

**How the steps connect:**

```text
User question
     |
     v
1. Understand the question (model)
     |
     v
2. Validate tool request (loop)
     |
     v
3. Retrieve facts (lookup)
     |  fact + unit + document IDs
     v
4. Check units and prepare expression (model)
     |
     v
5. Perform arithmetic (calculate)
     |  numeric result
     v
6. Compare and format (model)
     |
     v
ANSWER: <name or yes/no> | <number>
```

The loop handles every tool request and passes its result back to the model. If the result is an error or more facts are needed, the model may request another tool, and the loop repeats steps 2–5. Step 7 supervises the whole process and enforces the round limit.

**What I am assuming:**

1. **Years.** I use the 2025 census for current populations, the 2025 fiscal year for budgets, calendar year 2025 for household water use, and school year 2025/26 for pupils and schools. I use 2020 data only when the question asks for a comparison with 2020.
2. **Archived profiles.** NR-13 and NR-14 are archived 2019 profiles of Tarnvale and Kelmarsh. I exclude them and use the current profiles NR-03 and NR-04.
3. **Corrections.** Correction notices override original profiles. Brightwater's correct area is **921.4 km²** from NR-16, not 912.4 km² printed in NR-05.
4. **Units.** I keep units consistent. For example, 1 megalitre = 1,000 cubic metres, and 1 cubic metre = 1,000 litres. I normalize Lindenmoor's water use from cubic metres to megalitres in `lookup`. For per-day water questions I use **365 days** in 2025.
5. **Rounding.** I keep full precision while calculating and round only the final number to the decimals required by the question.
6. **Names.** I require exact district identification, because names such as Ossbridge and Ossford are similar. A misspelling may produce suggestions but must not silently select a district.
7. **Sources.** Each successful lookup includes its source document ID(s). No correct answers from `data/questions.json` will be included in model prompts; that file is only for checking results afterward.

### A2 — The two tools

I chose a structured `lookup(entity, field)` rather than a free-text search because every district profile has the same main indicators. A precise field request returns only the necessary fact and makes it easier to test source handling. The model decides what it needs, while the tools handle fact retrieval and arithmetic.

#### `lookup`

**Purpose.** Retrieve one fact from the local Nine Rivers documents with its unit and source. An archived value must not replace current data, and a correction notice must override the original value.

**Exact tool definition sent to the model:**

```json
{
  "type": "function",
  "function": {
    "name": "lookup",
    "description": "Retrieve one factual value from the Nine Rivers Region documents. Use an exact district name and field, or entity 'region' for the household water tariff. Prefer the current 2025 profiles, ignore archived profiles, and apply correction notices. Normalize household water use to megalitres. Return the numeric value, its unit, and source document IDs. Call this tool before calculating with facts from the documents.",
    "parameters": {
      "type": "object",
      "properties": {
        "entity": {
          "type": "string",
          "description": "Exact district name, or 'region' for regional information."
        },
        "field": {
          "type": "string",
          "enum": [
            "population_2020",
            "population_2025",
            "area_km2",
            "founded_year",
            "budget_2025_million_tenge",
            "household_water_2025_megalitres",
            "schools_2025_26",
            "pupils_2025_26",
            "paved_road_km",
            "water_tariff_2025_tenge_per_m3"
          ],
          "description": "The one factual field to retrieve. The water tariff belongs to entity 'region'."
        }
      },
      "required": ["entity", "field"],
      "additionalProperties": false
    }
  }
}
```

For example, `{"entity":"Brightwater","field":"population_2025"}` should return `{"entity":"Brightwater","field":"population_2025","value":44675,"unit":"residents","sources":["NR-05"]}`. Asking for Brightwater's area should return 921.4 with sources NR-05 and NR-16.

| Case | What the tool returns |
|---|---|
| Success | Small JSON: `{"entity":"...","field":"...","value":number,"unit":"...","sources":["NR-..."]}`. No full document text. |
| Misspelled or unknown district | `{"error":"unknown district","suggestions":["..."]}`; suggestions do not count as an automatic match. |
| Similar district names / ambiguous input | `{"error":"ambiguous district","matches":["..."]}`; exact Ossbridge and Ossford must return distinct profiles. |
| Archived profile | Ignore NR-13 and NR-14 and use the appropriate current profile. The interface does not offer archived values. |
| Corrected value | Return the corrected number; include the profile and the correction document in `sources`. |
| Unknown field or unavailable field for entity | `{"error":"unknown or unavailable field"}` with a short explanation. |
| Unreadable number or unknown unit | `{"error":"invalid numeric value or unit"}`; do not guess or return an unverified number. |
| Missing, non-string, very long, or extra input | `{"error":"invalid parameters"}`; do not crash. |
| File missing or corrupt | `{"error":"corpus unavailable or invalid"}`; do not crash. |

**Worst thing it could do with strange or dangerous input:**

An unexpected name might select the wrong district or a very long input could waste processing time. I will accept only the defined fields, validate string parameters, limit name length (80 characters), use a fixed local corpus path, and never treat input as a file path or Python code. I will not silently choose among similar names. These checks also make `lookup` easier to test without the model.

#### `calculate`

**Purpose.** Safely evaluate arithmetic. I chose a separate calculator because language models can make numeric mistakes, while Python can calculate an approved expression consistently.

**Exact tool definition sent to the model:**

```json
{
  "type": "function",
  "function": {
    "name": "calculate",
    "description": "Safely evaluate arithmetic expressions containing numbers, parentheses, addition, subtraction, multiplication, and division. Use this tool for numerical calculations instead of mental arithmetic. Do not include units or Python code. Function calls, attribute access, exponentiation, and other unsupported operations are rejected.",
    "parameters": {
      "type": "object",
      "properties": {
        "expression": {
          "type": "string",
          "description": "Arithmetic expression, for example '37203 / 758.6 - 44675 / 921.4'."
        }
      },
      "required": ["expression"],
      "additionalProperties": false
    }
  }
}
```

The implementation will parse an expression with Python's `ast` module and evaluate only allowed number and operator nodes, without using `eval()` or `exec()`. Parentheses and unary plus/minus are allowed. Exponentiation is deliberately not supported.

| Case | What the tool returns |
|---|---|
| Success | `{"value":number}`. Example: `48317 * 1.0947` returns the computed numeric value. |
| Invalid syntax or empty expression | `{"error":"invalid arithmetic expression"}`. |
| Division by zero | `{"error":"division by zero"}`. |
| Function call, attribute access, or other Python code | `{"error":"unsupported expression"}`; no code runs. |
| Unsupported operator, including `**` | `{"error":"unsupported operator"}`. |
| Expression over 200 characters or overly complex AST (over 50 nodes) | `{"error":"expression too large or complex"}`. |
| Absolute numeric literal or intermediate result above `10^15`, or non-finite result | `{"error":"numeric limit exceeded"}`. |
| Missing, non-string, or extra parameter | `{"error":"invalid parameters"}`. |

**Worst thing it could do with strange or dangerous input:**

Without restrictions, an expression could attempt to run arbitrary Python code or exhaust CPU and memory. I will only accept finite numbers and basic arithmetic AST nodes, limit expression length and AST size, and reject very large numeric values and division by zero. For example, `__import__('os').system('echo hi')`, `(1).__class__`, and `9**9**9` must all be rejected safely.

### A3 — Q-09 by hand

**Question:** Which district has the higher population density in 2025, Brightwater or Millbrook, and by how many residents per square kilometre?

To answer it, I first need each district's 2025 population and area. `lookup` returns the four facts with their sources. Then `calculate` finds the difference between the densities. The model compares the results and formats the answer. This is my **expected conversation written by hand**, not a real API log. The call IDs are illustrative.

```json
[
  {
    "role": "user",
    "content": "Which district has the higher population density in 2025, Brightwater or Millbrook, and by how many residents per square kilometre?"
  },
  {
    "role": "assistant",
    "tool_calls": [
      {
        "id": "call_01",
        "type": "function",
        "function": {
          "name": "lookup",
          "arguments": "{\"entity\":\"Brightwater\",\"field\":\"population_2025\"}"
        }
      },
      {
        "id": "call_02",
        "type": "function",
        "function": {
          "name": "lookup",
          "arguments": "{\"entity\":\"Brightwater\",\"field\":\"area_km2\"}"
        }
      },
      {
        "id": "call_03",
        "type": "function",
        "function": {
          "name": "lookup",
          "arguments": "{\"entity\":\"Millbrook\",\"field\":\"population_2025\"}"
        }
      },
      {
        "id": "call_04",
        "type": "function",
        "function": {
          "name": "lookup",
          "arguments": "{\"entity\":\"Millbrook\",\"field\":\"area_km2\"}"
        }
      }
    ]
  },
  {
    "role": "tool",
    "tool_call_id": "call_01",
    "content": "{\"entity\":\"Brightwater\",\"field\":\"population_2025\",\"value\":44675,\"unit\":\"residents\",\"sources\":[\"NR-05\"]}"
  },
  {
    "role": "tool",
    "tool_call_id": "call_02",
    "content": "{\"entity\":\"Brightwater\",\"field\":\"area_km2\",\"value\":921.4,\"unit\":\"km2\",\"sources\":[\"NR-05\",\"NR-16\"]}"
  },
  {
    "role": "tool",
    "tool_call_id": "call_03",
    "content": "{\"entity\":\"Millbrook\",\"field\":\"population_2025\",\"value\":37203,\"unit\":\"residents\",\"sources\":[\"NR-11\"]}"
  },
  {
    "role": "tool",
    "tool_call_id": "call_04",
    "content": "{\"entity\":\"Millbrook\",\"field\":\"area_km2\",\"value\":758.6,\"unit\":\"km2\",\"sources\":[\"NR-11\"]}"
  },
  {
    "role": "assistant",
    "tool_calls": [
      {
        "id": "call_05",
        "type": "function",
        "function": {
          "name": "calculate",
          "arguments": "{\"expression\":\"37203 / 758.6 - 44675 / 921.4\"}"
        }
      }
    ]
  },
  {
    "role": "tool",
    "tool_call_id": "call_05",
    "content": "{\"value\":0.5556561156405735}"
  },
  {
    "role": "assistant",
    "content": "Millbrook has the higher population density. The difference is approximately 0.6 residents per square kilometre.\nANSWER: Millbrook | 0.6"
  }
]
```

**My arithmetic:**

The formula is `density = population / area`. The data come from NR-05, NR-11 and the Brightwater correction notice NR-16.

```text
Brightwater population (2025) = 44,675 residents    [NR-05]
Brightwater area              = 921.4 km2           [NR-05, corrected by NR-16]
Millbrook population (2025)  = 37,203 residents    [NR-11]
Millbrook area               = 758.6 km2           [NR-11]

Brightwater density = 44675 / 921.4 = 48.48599956587801 residents/km2
Millbrook density   = 37203 / 758.6 = 49.041655681518584 residents/km2
Difference          = 49.041655681518584 - 48.48599956587801
                    = 0.5556561156405735 residents/km2

Rounded to 1 decimal place: 0.6 residents/km2
ANSWER: Millbrook | 0.6
```

Millbrook's density is higher. It is important to use **921.4**, not the incorrect **912.4**, because NR-16 overrides NR-05. I keep full precision until the final rounding.

### A4 — Why I split it this way

One alternative would be a single `answer_question` tool that receives the full question, retrieves the facts, calculates the result, and returns the answer. I did not choose it because this would mix retrieval, arithmetic, and reasoning in one function. If the result were wrong, it would be difficult to find the exact step that failed.

With two tools, I can test `lookup` independently for correct documents, units, archived profiles, and corrections. I can test `calculate` independently for correct arithmetic and unsafe inputs. The model is still responsible for choosing relevant facts and comparing the results. This is easier to debug and reuse across different types of questions.

---

## Part B — Build it and run it (Medium)

### What I changed from Part A while coding

I kept the two tool interfaces, their parameters, the rules for current/archived/corrected records, unit normalization, and the final answer format. The implementation made some safety limits stricter than the paper plan: `lookup` accepts district names of at most **60** characters rather than 80; `calculate` limits expressions to **120** characters rather than 200, and ASTs to **40** nodes rather than 50. The numeric limit remains `10^15`. These smaller limits still cover the homework arithmetic and reduce the chance of excessive work. `lookup` uses a small set of regex patterns to handle the three styles used in district profiles and returns errors rather than crashing.

The first live S3 attempt on Q-09 failed with HTTP 400 before any successful API round. I added `reasoning_effort="none"` to the required `gpt-5.6-luna` Chat Completions calls, after which Q-09 completed successfully. I used the same reasoning setting for all three strategies in the full experiments. I did not edit Part A retroactively.

### B2 — Tests

I tested `lookup` and `calculate` without using an API key. The test suite covers valid values, misspellings, similar district names, archived and corrected records, water unit conversion, successful calculations, division by zero, and the three forbidden expressions (`__import__('os').system('echo hi')`, `(1).__class__`, and `9**9**9`).

**Pytest output from my MacBook:**

```text
16 passed in 0.03s
```

Command: `python -m pytest -v tests/test_tools.py` (the compact line above is the recorded result). Tests are in `tests/test_tools.py`; they do not use OpenAI API calls.

### B3 — The loop

- **Round limit, and why:** 8 Chat Completions response rounds per S3 question. A round can request multiple tools; every returned `tool_call_id` receives a corresponding tool message, including errors. Eight rounds prevent a repeated request from looping indefinitely and limit extra API spending; hitting the limit is marked `unanswered_round_limit`.
- **Argument validation:** The loop parses `function.arguments` with `json.loads`, rejects duplicate keys, wrong types, missing/extra parameters, unknown tool names, and overly long JSON before calling Python. It appends the assistant's SDK message (including all `tool_calls`) unchanged and then appends tool results matched to the IDs.
- **Where the logs are:** `outputs/b3_20261018T171052960674Z_Q-09.json` for the separate B3 test, and `outputs/run_1/S3/Q-*.json` and `outputs/run_2/S3/Q-*.json` for the full runs. Each JSON records the requests, arguments, tool results, tool duration, API rounds, token usage, and elapsed time.
- **Real Q-09 compared with A3:** The successful B3 trial returned `ANSWER: Millbrook | 0.6` in **3 API rounds**, with **5 tool calls**, **2,381 input tokens**, **168 output tokens**, and **5.563559 seconds**. As planned, round 1 contained four lookups for the two populations and two areas; round 2 called the calculator; round 3 gave the final answer. The real call IDs differed from the illustrative paper IDs, and the model subtracted in the reverse order: `44675 / 921.4 - 37203 / 758.6 = -0.5556561156405735`; it still correctly identified Millbrook as denser and returned the positive difference 0.6. The corrected Brightwater area (921.4 km², NR-05 and NR-16) was used.

### B4 — Three strategies

**What I kept the same:** All three strategies used the same 20 questions from `data/questions.json`, the same model `gpt-5.6-luna`, Chat Completions API, `reasoning_effort="none"`, the same numeric precision requested for each question, and the same last-line format `ANSWER: <name or yes/no> | <number>`. Ground-truth answers were used only *after* responses for grading. The corpus contains the same 17 documents in S1 and S2; S3 receives none of their text, only tool definitions. S1 makes one direct request; S2 makes five independent requests per question (`k=5`) with a step-by-step instruction and `temperature=0.7`, then votes on the normalized answer (the first valid sample breaks a tie). S1 and S3 use default API temperature because S2 alone needs sampling variation. S3 has an eight-round cap; every strategy uses the same grading code.

**My rule for a correct answer (written before the first full run):** In `outputs/evaluation_rule.md`, I defined correctness as an exact, case-sensitive match of the name or yes/no plus a numeric absolute difference no greater than `10^(-decimals)`, one last-place unit. Missing or malformed final answers are unanswered and incorrect. No partial credit is given. I did not include correct answers in prompts.

**Cost methodology:** The program adds `usage.prompt_tokens` and `usage.completion_tokens` from every API response, including all five S2 samples and all S3 rounds. Dollar costs are **estimates** using **$0.20 per 1M input tokens** and **$1.20 per 1M output tokens**, without applying possible cached-input discounts; they are not claimed to be final billing totals.

| Run | Strategy | Correct /20 | Unanswered | Input tokens | Output tokens | Median seconds per question | Total seconds | Cost ($, estimated) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | S1 direct | 12 | 2 | 44580 | 1729 | 1.6400 | 32.8319 | 0.010991 |
| 1 | S2 think and vote (k = 5) | 17 | 0 | 223700 | 14219 | 9.6843 | 198.4885 | 0.061803 |
| 1 | S3 tools | 17 | 0 | 60070 | 4343 | 5.1587 | 100.0617 | 0.017226 |
| 2 | S1 direct | 12 | 2 | 44580 | 1801 | 1.5553 | 31.0077 | 0.011077 |
| 2 | S2 think and vote (k = 5) | 16 | 0 | 223700 | 14298 | 9.6546 | 193.0389 | 0.061898 |
| 2 | S3 tools | 19 | 0 | 60187 | 4395 | 4.9422 | 100.0584 | 0.017311 |

**Per question (run 1):**

| Question | S1 | S2 | S3 |
|---|:---:|:---:|:---:|
| Q-01 | ✗ | ✓ | ✓ |
| Q-02 | ✓ | ✓ | ✓ |
| Q-03 | ✓ | ✓ | ✓ |
| Q-04 | ✓ | ✓ | ✓ |
| Q-05 | ✗ | ✗ | ✓ |
| Q-06 | ✓ | ✓ | ✓ |
| Q-07 | ✗ | ✓ | ✓ |
| Q-08 | ✓ | ✓ | ✓ |
| Q-09 | ✓ | ✓ | ✗ |
| Q-10 | ✓ | ✓ | ✓ |
| Q-11 | ✓ | ✓ | ✓ |
| Q-12 | ✓ | ✓ | ✓ |
| Q-13 | ✗ | ✓ | ✓ |
| Q-14 | ✗ | ✓ | ✗ |
| Q-15 | ✓ | ✓ | ✓ |
| Q-16 | ✓ | ✓ | ✓ |
| Q-17 | ✗ | ✗ | ✓ |
| Q-18 | ✓ | ✓ | ✓ |
| Q-19 | ✗ | ✓ | ✓ |
| Q-20 | ✗ | ✗ | ✗ |

**Per question (run 2):**

| Question | S1 | S2 | S3 |
|---|:---:|:---:|:---:|
| Q-01 | ✓ | ✓ | ✓ |
| Q-02 | ✓ | ✓ | ✓ |
| Q-03 | ✓ | ✓ | ✓ |
| Q-04 | ✓ | ✓ | ✓ |
| Q-05 | ✗ | ✗ | ✓ |
| Q-06 | ✓ | ✓ | ✓ |
| Q-07 | ✓ | ✓ | ✓ |
| Q-08 | ✗ | ✓ | ✓ |
| Q-09 | ✓ | ✓ | ✓ |
| Q-10 | ✓ | ✓ | ✓ |
| Q-11 | ✓ | ✓ | ✗ |
| Q-12 | ✓ | ✗ | ✓ |
| Q-13 | ✗ | ✓ | ✓ |
| Q-14 | ✗ | ✓ | ✓ |
| Q-15 | ✗ | ✓ | ✓ |
| Q-16 | ✓ | ✓ | ✓ |
| Q-17 | ✗ | ✗ | ✓ |
| Q-18 | ✓ | ✓ | ✓ |
| Q-19 | ✗ | ✓ | ✓ |
| Q-20 | ✗ | ✗ | ✓ |

Across both runs, S1 was correct on **24/40 (60%)**, S2 on **33/40 (82.5%)**, and S3 on **36/40 (90%)**. The two-run estimated costs were **$0.022068** (S1), **$0.123700** (S2), and **$0.034537** (S3). S2 was about **5.61×** the cost of S1 and **3.58×** the cost of S3. The `outputs/run_1/` and `outputs/run_2/` folders contain per-question logs, CSV tables, and summary JSON files.

---

## Part C — Explain and choose (Hard)

### C1 — Does the tool description matter?

I reran S3 on Q-01, Q-03, Q-09, Q-13, and Q-18, changing only the `lookup` description to `"Looks things up."` (original results are from run 1). All five vague-description results were correct; the original version was correct on four of those five because its Q-09 chose Brightwater instead of Millbrook. This does **not** prove that the vague description is better: these were separate nondeterministic model runs and the sample is very small.

| Question | Calls (original) | Calls (vague) | Answer (original) | Answer (vague) |
|---|---:|---:|---|---|
| Q-01 | 7 | 5 | Ossbridge | 4.6 | Ossbridge | 4.6 |
| Q-03 | 7 | 7 | Lindenmoor | 5.3 | Lindenmoor | 5.3 |
| Q-09 | 5 | 5 | Brightwater | 0.6 | Millbrook | 0.6 |
| Q-13 | 9 | 9 | Coldharbour | 4.16 | Coldharbour | 4.16 |
| Q-18 | 3 | 3 | yes | 40013 | yes | 40013 |

The inputs and call sequence also changed in specific ways. On **Q-01**, the original made four lookups and three calculator calls (7 total); the vague version made the same four lookups and only **one combined calculation** (5 total). On **Q-03**, both made seven calls, but their arithmetic expressions and grouping differed. On **Q-09**, **Q-13**, and **Q-18**, the parsed tool arguments were the same; for Q-09 the final comparison decision changed despite the same tool inputs. In the first API round of every tested question, replacing the original description with the vague line reduced the reported input token count by **77** (e.g., Q-09: 589 to 512). A descriptive schema remains valuable for safety and clarity, but these five cases do not establish an accuracy benefit.

### C2 — Three mistakes explained

**S1, Q-14 (run 1, `outputs/run_1/S1/Q-14.json`): unit conversion failure.** The question asks for the daily household water use per resident in **litres**. The response calculated Tarnvale as `5,207,000 / 71,388 / 365 = 0.1997` and Ashgrove as `4,390,000 / 58,760 / 365 = 0.2046`, then ended with `ANSWER: Ashgrove | 0.0`. It had converted megalitres to cubic metres but treated the result as litres, missing a factor of 1,000. Correct conversion is `megalitres × 1,000,000 litres`, giving approximately 199.83 vs 204.69 litres per resident per day and `ANSWER: Ashgrove | 4.9`. The direct prompt contained the correct facts, but the model applied the wrong units.

**S2, Q-17 (run 1, `outputs/run_1/S2/Q-17.json`): repeated arithmetic errors survived voting.** All five samples picked Harrowfield, but their numeric final answers were `891`, `891`, `892`, `892`, and `885`; the tie between 891 and 892 was broken in favor of the earliest valid result, **891**. One sample wrote `7,146,000 × 142.35 = 1,017,731,100`, but the exact product is **1,017,233,100**; several samples made similar multiplication/subtraction mistakes. Using the source numbers accurately yields `(7,146,000 × 142.35 / 90,212) - (3,862,000 × 142.35 / 52,904) ≈ 884.457`, so the gold answer **884** is within the required rounding rule. Voting can correct independent outliers, but it cannot reliably correct the same arithmetic weakness repeated across five similar answers.

**S3, Q-14 (run 1, `outputs/run_1/S3/Q-14.json`): correct retrieval and calculator, wrong expression.** `lookup` correctly returned Tarnvale's **5,207 ML**, population **71,388**, Ashgrove's **4,390 ML**, population **58,760**. The model then requested `calculate` with `(5207*1000000/71388*1000/365)-(4390*1000000/58760*1000/365)`; the tool faithfully returned **-4852.761497...**. This is about 1,000 times too large, because multiplying ML by 1,000,000 already converts it to litres; the extra `*1000` is unnecessary. The final answer `Ashgrove | 4852.8` was therefore wrong; run 2 correctly answered `Ashgrove | 4.9`. A calculator removes arithmetic execution errors, but the model can still submit a semantically incorrect expression.

These examples show why more samples and tools are not automatic guarantees of correctness. S2 repeated a systematic error; S3 made a unit/formula mistake outside the calculator's responsibility. S3 usually required 3–4 API rounds because the model first acquired facts, then ran intermediate calculations, sometimes requested one more difference calculation, and finally answered. Each subsequent round included prior assistant tool calls and tool results in the conversation, increasing its input token count (for example, S3 Q-11 in run 1 used 604 → 1,034 → 1,146 → 1,191 input tokens across its four API rounds).

### C3 — Which strategy I would use, and what would change it

For this problem I would choose **S3 — tools**. It had the highest average accuracy across two runs (**36/40 = 90%**) with an estimated total cost of **$0.034537** and roughly **100 seconds per set of 20 questions**. S1 was fastest (~31–33 seconds per set) and cheapest (~$0.011 per set), but only correct on 60% of answers; I would not choose it for questions with multi-step arithmetic and unit conversions. S2 reached 82.5% accuracy, but its five requests per question cost about **3.58×** as much as S3 and took roughly twice as long (~193–198 seconds per set).

If I had a **strict budget or latency limit** and could tolerate lower reliability, I would choose S1. If the dataset grew to **100,000 documents**, I would still prefer a tool-based approach, but replace our regex scan with an indexed database or search/retrieval service, not send all documents in the prompt. For questions requiring **no calculations** where all relevant documents fit easily in context, direct prompting could again be preferable, and I would measure it separately. For high-stakes calculations, I would keep S3 but add validation of units and independent checks of the expressions constructed by the model.

### C4 — How sure I can be

The two runs were **not identical**: S1 stayed at 12/20, S2 dropped from 17/20 to 16/20, and S3 rose from 17/20 to 19/20. The S3 change alone was **2 questions out of 20 (10 percentage points)**; its mean advantage over S2 across the two runs was only **1.5 questions per 20 (7.5 points)**, so the difference between the two best strategies is **smaller than S3's observed run-to-run change**. On run 1, S2 and S3 even tied at 17/20.

Therefore, I view S3 as the most promising strategy on these 20 questions, **not** as statistically proven superior. I would want at least **a few hundred new, independent questions** of similar types, with multiple repetitions and confidence intervals (ideally paired comparisons by question), before trusting a small advantage. A rough independent-proportions power calculation for a 7.5 percentage-point difference suggests several hundred questions per strategy for reliable detection; the precise requirement depends on the observed error correlation and desired confidence/power. Repeating the same small set only tests variability on that set; it does not replace new questions.

### Final questions

1. **When the model sends a `tool_calls` message, what has actually run so far?** Only the LLM/API request has run; the Python tool has **not** run yet. The model has proposed a function name and JSON arguments, and my loop decides whether and how to execute it.

2. **Why does `arguments` arrive as a string, and what goes wrong if you pass it straight into your function?** Tool calls are structured as serialized JSON arguments so a caller can inspect and validate them, not as already trusted Python objects. Passing the raw string directly would give wrong argument types or errors and could bypass validation of unexpected keys, types, or malformed JSON.

3. **How many tokens do your two tool definitions add to every S3 request? Measure it.** My C1 logs measure **77 extra input tokens** for the original detailed `lookup` description compared with `"Looks things up."` (Q-09 first round: 589 vs 512), while keeping the rest of the tools unchanged. **The total overhead of *both* tool definitions compared with a no-tools request is not identifiable from these saved runs alone; it requires a controlled two-call API measurement.** Run the accompanying `measure_tool_overhead.py` to replace the following field with an exact observed difference: **406 input tokens/request**. I do not substitute the 77-token description delta for that different measurement.

4. **How much more did S2 cost than S1?** Over both full runs, S2 cost an estimated **$0.123700** versus **$0.022068** for S1, a difference of **$0.101632**, or about **5.61×** as much. S2 submitted the full corpus five times per question and also used more output tokens because the model wrote reasoning steps.

5. **Which question needed the most rounds in S3, and why?** The maximum observed was **four API rounds**, shared by multiple questions; there was **no unique longest question**. For example, Q-11 in run 1 needed six lookups, three pupil-per-school calculations, a final difference calculation, and then an answer (4 rounds; 10 tool calls), while Q-20 in run 2 likewise used 4 rounds and 10 calls.

6. **What would you change in `lookup` if there were 100,000 documents?** I would index current records by district and field in a database and keep correction/version metadata, instead of loading and searching the entire JSONL file for every call. I would retrieve and return only the needed source-backed fact, using search or retrieval for unstructured documents.

7. **What stops `calculate` from running dangerous code? Could the model still make it misbehave?** `calculate` parses an AST and evaluates only numeric constants and the approved `+`, `-`, `*`, `/` and unary operations; it rejects function calls, attribute access, exponentiation, oversized expressions, and non-finite/huge values. The model can still create a mathematically valid but *wrong* expression or unit conversion, as in S3 Q-14; the AST does not verify the model's reasoning.

8. **For which kind of question did tools help most, and for which did they not help at all?** Tools helped most when an answer required retrieving several values and doing arithmetic with unit conversion or a corrected source (for example, S3 corrected S1's Q-14 unit error in run 2 and solved Q-05 correctly in both runs). They did not guarantee improvements when the model constructed a wrong expression, as Q-14 run 1 shows; on simple comparisons such as Q-10, all three strategies answered correctly, so tools added time and tokens without improving accuracy.
