# Agent watch — `python run.py agent-watch --run-id <id>`

**When**: as every bot workflow finishes (`agent-watch.yml`, `workflow_run`); the inventory push
only when it did not succeed. By hand: run the workflow with a run id.

* Claude (Opus 5.5, effort `AGENT_EFFORT`, default medium) reads the finished run with read-only
  tools (`bindal/agent/tools.py`): the decrypted run.log (errors / warnings / last lines), the
  files the run wrote (xlsx: rows, filled columns, counts per month or status), earlier runs of
  the same workflow (duration, failures). The bots' docs (this folder + ARCHITECTURE.md) are its
  background, cached between calls.
* Answer STATUS: OK = nothing is sent. STATUS: ALERT = a short Hinglish Telegram message: what
  happened, the cause, what it affected, the suggested next step, with the run's link.
* It changes nothing: no reruns, no fixes (the next steps of plan.md add those, with approval).
* Needs the `ANTHROPIC_API_KEY` repo secret; without it the job does nothing. Each check logs its
  token use and cost (~$0.05-0.20).
