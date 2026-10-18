# HW4 — submission

**AI assistance:** I used gpt to help design the two tool interfaces, and draft and review the Part A explanations. I will record implementation changes and real experiment results from my own runs in the later parts.

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

<!-- what changed in your plan, and why. "None" is an answer too. -->

### B2 — Tests

```text
<!-- pytest output -->
```

### B3 — The loop

- Round limit, and why:
- Where the logs are:
- Real Q-09 conversation compared with my A3 version, what is different:

### B4 — Three strategies

**What I kept the same:**

<!-- model, temperature, k, prompts, answer format, anything else -->

**My rule for a correct answer** (written before the first full run):

| Run | Strategy | Correct /20 | Unanswered | Input tokens | Output tokens | Median seconds per question | Total seconds | Cost ($) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | S1 direct | | | | | | | |
| 1 | S2 think and vote (k = ) | | | | | | | |
| 1 | S3 tools | | | | | | | |
| 2 | S1 direct | | | | | | | |
| 2 | S2 think and vote (k = ) | | | | | | | |
| 2 | S3 tools | | | | | | | |

**Per question (run 1):**

| Question | S1 | S2 | S3 |
|---|:---:|:---:|:---:|
| Q-01 | | | |
| Q-02 | | | |
| Q-03 | | | |
| Q-04 | | | |
| Q-05 | | | |
| Q-06 | | | |
| Q-07 | | | |
| Q-08 | | | |
| Q-09 | | | |
| Q-10 | | | |
| Q-11 | | | |
| Q-12 | | | |
| Q-13 | | | |
| Q-14 | | | |
| Q-15 | | | |
| Q-16 | | | |
| Q-17 | | | |
| Q-18 | | | |
| Q-19 | | | |
| Q-20 | | | |

---

## Part C — Explain and choose (Hard)

### C1 — Does the tool description matter?

| Question | Calls (your description) | Calls (vague) | Answer (yours) | Answer (vague) |
|---|---:|---:|---|---|
| | | | | |

### C2 — Three mistakes explained

### C3 — Which strategy I would use, and what would change it

### C4 — How sure I can be

### Final questions

1.
2.
3.
4.
5.
6.
7.
8.
