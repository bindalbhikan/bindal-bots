# Agent watch — `python run.py agent-watch --run-id <id>`

**When**: as every bot workflow finishes (`agent-watch.yml`, `workflow_run`); the inventory push
only when it did not succeed. By hand: run the workflow with a run id.

* **Free model**: Cloudflare Workers AI `@cf/openai/gpt-oss-120b` (repo variable `AGENT_MODEL` to
  change it) on the account's free daily allocation (10,000 neurons), through the Telegram
  worker's `/api/ai` route with the `DASHBOARD_TOKEN` - no API key. 20 Oct 2026: a check is about
  13-20K tokens (~$0.005-0.008 of the ~$0.11 a day free), so ~15 checks a day fit. Models on the
  Workers Free plan with tool calling: gpt-oss-120b / -20b, qwen3.8-27b, nemotron-3-120b (GLM 5.x,
  Kimi, DeepSeek v4 need the paid plan).
* A plainly fine run (succeeded, no more than twice its usual minutes, no ERROR / Traceback line in
  its log) is not sent to the model at all.
* Otherwise the model reads the run with read-only tools (`bindal/agent/tools.py`): the decrypted
  run.log (grep / tail), the files the run wrote (xlsx: rows, filled columns, counts per month or
  status), earlier runs of the workflow. The bot's own doc in this folder is its background.
* Answer STATUS: OK = nothing is sent. STATUS: ALERT = a short Hinglish Telegram message: what
  happened, the cause, what it affected, the suggested next step, with the run's link.
* Model unreachable (free allocation used up for the day, model down): a plain alert with the
  run's ERROR lines instead.
* It changes nothing: no reruns, no fixes (the next steps of plan.md add those, with approval).
