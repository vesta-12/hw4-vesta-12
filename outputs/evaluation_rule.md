# Evaluation rule — recorded before the first full B4 run

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
