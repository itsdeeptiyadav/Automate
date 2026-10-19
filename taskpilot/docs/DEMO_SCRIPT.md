# TaskPilot demo script (about 8 minutes)

Setup: backend and frontend running (see README). Use a real reasoning engine if you have one (Ollama/Mistral or a hosted model). If you use `LLM_PROVIDER=demo`, say so out loud: the banner at the top of the page says it too. Start with an empty database (delete `backend\data\taskpilot.db*`) so the invoice table is empty.

## 1. Submit a natural-language task (30 s)

- Click **Example: invoice**, then **Run task**.
- Say: "I only typed a sentence. There is no hard-coded invoice script. The worker decides each step from what it observes."

## 2. The generated plan (30 s)

- Point at the **Plan** panel: goal, steps, and the line "needs your approval first".
- Say: "The plan is structured output validated by Pydantic. Code also refuses a plan whose company name is not in my sentence, so the model cannot invent one."

## 3. Real tool execution (60 s)

- Open the **Execution trace**. Expand **Details** on `get_latest_invoice`.
- Show: three Acme invoices considered, newest selected by date. The 4th file (`Acme Industries Ltd`, later date) was correctly excluded because the vendor must match exactly.
- Point at the amber **security** event: `invoice_004.pdf` contains "ignore previous instructions". Say: "Document text is data. It was flagged and ignored, and the worker has no tool that could delete anything."
- Show the **Current action** and **Step n of 12** in the status panel.

## 4. Approval, then extraction and insertion (45 s)

- The status is **Waiting for your approval**; the approval card shows the exact values.
- Show the invoice table on the right is still empty: "Nothing has been written."
- Click **Approve and save**. The row appears in **Internal invoice system**, read straight from SQLite.

## 5. Independent verification (45 s)

- Show **Result**: the checks table compares the stored row with the PDF re-read from disk, field by field, plus "exactly one record" and "no unintended records".
- Say: "The model saying 'done' is not enough. This gate recomputes the expected answer from the PDFs. Only a pass gives COMPLETED."

## 5b. Duplicate processing (20 s)

- Run the same example again. No approval is requested, the table still has one row, and the task completes by verifying the existing record.

## 6. A failure scenario (45 s)

- Type: `Find the latest invoice from Nonexistent Corp, extract its details and save it.` Run.
- Result: **The task did not succeed**; no invoice is invented; the table is unchanged. Expand the failed `get_latest_invoice` in the trace to see the `NOT_FOUND` error and the list of vendors that do exist.

## 6b. A recovery scenario (60 s)

- Stop the backend. In `backend\.env` set `FAULT_INJECTION=write_timeout_once`. Delete the database. Start the backend. Run the invoice example and approve.
- In the trace find the amber **recovery** event: the write committed but "timed out"; the worker inspected the database, found the identical record, and did **not** insert again. The table has exactly one row.
- Remove the setting afterwards.

## 7. Final result and evidence, plus reuse (30 s)

- Click **Example: file search** and run it. Same loop, same registry, different task. Evidence lists each matching file and line; `onboarding.txt` is excluded because "contractor" is not the word "contract".

## 8. Chat with the worker (60 s)

- Open the **Documents** tab, upload `sample_data/documents/refund_policy.txt` and `employee_handbook.md`.
- Open **Chat** and click "What do my documents say about refunds?". Expand **Used 1 tool**: the answer came from `search_documents`, not from memory.
- Type: `Process the latest invoice from Acme Corporation and save it`. The assistant starts a task and gives you a button to open it.
- Say: "The assistant can start work but cannot approve it. The task stops at *Waiting for your approval* in the Tasks tab, exactly like before."

## 9. Document Q&A with verified citations (45 s)

- In the **Tasks** tab click **Example: document Q&A** (or use **Ask as task** on the Documents tab), run it.
- Result shows the answer plus each citation as a quote with its file. Say: "The gate checked that every quote occurs verbatim in a document the worker actually read. A made-up quote, or a document it never opened, fails verification."

## 10. LangGraph: pause and resume (60 s)

- Run the invoice example and stop at *Waiting for your approval*. Open `http://127.0.0.1:8000/api/v1/tasks/<id>/graph` and show `"waiting_at": ["await_approval"]`.
- Stop the backend (Ctrl+C) and start it again. Reload the page, open the task from history: it is **still waiting**. Approve it; it finishes and verifies.
- Say: "That pause is a LangGraph interrupt saved by a SQLite checkpointer, not a flag in memory."

## If asked "what can go wrong?"

- If Ollama is not running, the task fails with a clear `llm_unavailable` message. It never silently switches to demo mode.
- Test coverage: `docs/TEST_RESULTS.txt`.
