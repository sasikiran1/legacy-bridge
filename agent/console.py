"""Operator console: pick a task and scenario, watch the agent's steps, approve or decline what it asks.

    uv run uvicorn agent.console:app --host 0.0.0.0 --port 8095      # make console -> http://localhost:8095

One run at a time. Steps come from agent state, which holds tokens only (ADR 0012), so the page shows
placeholders, never raw values. Approvals and handoffs go to the person at the page instead of the runner's
approval policy. The result is graded against the database diff like any runner task, but not saved: the
console is for watching and demos, experiments go through agent.runner.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from agent.runner import open_services, run_task
from agent.settings import agent_settings
from testbed.scenarios import load_scenarios
from testbed.tasks import load_tasks


class Start(BaseModel):
    task: str
    scenario: str
    approvals: str = "ask"  # ask | task


class Decision(BaseModel):
    approved: bool


class Console:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.running: asyncio.Task[None] | None = None
        self.waiting: asyncio.Future[dict[str, Any]] | None = None

    async def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    async def ask(self, request: dict[str, Any]) -> dict[str, Any]:
        self.waiting = asyncio.get_running_loop().create_future()
        await self.emit({"type": "ask", **request})
        try:
            return await self.waiting
        finally:
            self.waiting = None

    async def run(self, start: Start) -> None:
        task = next(t for t in load_tasks() if t.id == start.task)
        scenario = load_scenarios()[start.scenario]
        settings = agent_settings()
        await self.emit(
            {"type": "start", "task": task.id, "intent": task.intent.value, "scenario": scenario.name}
        )
        try:
            async with open_services(settings, settings.injection_scanner) as services:
                row = await run_task(
                    task,
                    scenario,
                    settings,
                    services,
                    "console",
                    approvals="task",
                    resolve_walls=False,
                    record=False,
                    on_event=lambda e: self.emit({"type": "step", **e}),
                    ask=self.ask if start.approvals == "ask" else None,
                )
                await services["admin"].apply(load_scenarios()["healthy"].chaos)
            await self.emit({"type": "done", **row.model_dump(exclude={"models", "api_failures"})})
        except Exception as e:  # shown on the page; the console must not die with the run
            await self.emit({"type": "error", "message": f"{type(e).__name__}: {str(e)[:300]}"})


console = Console()
app = FastAPI(title="legacy-bridge operator console")


@app.get("/", response_class=HTMLResponse)
async def page() -> str:
    return PAGE


@app.get("/api/catalog")
async def catalog() -> dict[str, Any]:
    """Task ids and intents only: instructions carry (synthetic) PII, and this page shows tokens alone."""
    return {
        "tasks": [{"id": t.id, "intent": t.intent.value} for t in load_tasks()],
        "scenarios": [{"name": s.name, "description": s.description} for s in load_scenarios().values()],
    }


@app.post("/api/runs")
async def start(body: Start) -> dict[str, str]:
    if console.running is not None and not console.running.done():
        raise HTTPException(409, "a run is in progress")
    if body.scenario not in load_scenarios() or body.task not in {t.id for t in load_tasks()}:
        raise HTTPException(400, "unknown task or scenario")
    console.events = []
    console.running = asyncio.create_task(console.run(body))
    return {"status": "started"}


@app.get("/api/events")
async def events(after: int = 0) -> dict[str, Any]:
    running = console.running is not None and not console.running.done()
    return {"events": console.events[after:], "next": len(console.events), "running": running}


@app.post("/api/decision")
async def decide(body: Decision) -> dict[str, str]:
    if console.waiting is None or console.waiting.done():
        raise HTTPException(409, "nothing is waiting for a decision")
    request: dict[str, Any] = next((e for e in reversed(console.events) if e["type"] == "ask"), {})
    answer = {"approved": body.approved} if request.get("kind") == "approval" else {"resolved": body.approved}
    console.waiting.set_result(answer)
    await console.emit({"type": "decision", **answer})
    return {"status": "ok"}


@app.on_event("shutdown")
async def stop() -> None:
    if console.running is not None:
        console.running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await console.running


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent Console</title>
<style>
  :root { --bg: #f6f6f3; --panel: #fff; --ink: #1d1d1b; --muted: #6b6b66; --line: #dcdcd6;
          --accent: #1f5f8b; --ok: #2e7d32; --bad: #b3261e; --warn: #a15c00; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #161615; --panel: #1f1f1d; --ink: #ececea; --muted: #9a9a94; --line: #34342f;
            --accent: #7fb3d9; --ok: #7bc47f; --bad: #f08a80; --warn: #e0a85a; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink); font: 14px/1.45 system-ui, sans-serif; }
  header { padding: 14px 16px; border-bottom: 1px solid var(--line); background: var(--panel); }
  header h1 { margin: 0; font-size: 16px; }
  header p { margin: 2px 0 0; color: var(--muted); font-size: 12px; }
  main { max-width: 980px; margin: 0 auto; padding: 16px; display: grid; gap: 16px; }
  .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; }
  form { display: flex; flex-wrap: wrap; gap: 10px; align-items: end; }
  label { display: grid; gap: 4px; font-size: 12px; color: var(--muted); }
  select, button { font: inherit; padding: 6px 10px; border-radius: 6px; border: 1px solid var(--line);
                   background: var(--panel); color: var(--ink); }
  button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  button:disabled { opacity: .5; }
  #ask { display: none; border-color: var(--warn); }
  #ask h2 { margin: 0 0 8px; font-size: 14px; color: var(--warn); }
  #ask .buttons { display: flex; gap: 8px; margin-top: 10px; }
  ol { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
  li { border-left: 3px solid var(--line); padding: 4px 10px; }
  li.step { border-color: var(--accent); }
  li.done.pass { border-color: var(--ok); } li.done.fail, li.error { border-color: var(--bad); }
  li.ask, li.decision { border-color: var(--warn); }
  .node { font-weight: 600; } .muted { color: var(--muted); font-size: 12px; }
  pre { margin: 6px 0 0; white-space: pre-wrap; word-break: break-word;
        font: 12px/1.4 ui-monospace, monospace; max-height: 220px; overflow: auto;
        background: var(--bg); padding: 6px; border-radius: 4px; }
  .verdict { font-size: 15px; font-weight: 600; }
</style>
</head>
<body>
<header>
  <h1>Agent console</h1>
  <p>Steps show placeholders only. Values are restored at the portal boundary, never on this page.</p>
</header>
<main>
  <section class="panel">
    <form id="start">
      <label>Scenario <select id="scenario"></select></label>
      <label>Task <select id="task"></select></label>
      <label>Approvals
        <select id="approvals">
          <option value="ask">ask me</option>
          <option value="task">runner policy</option>
        </select>
      </label>
      <button class="primary" id="go">Run</button>
      <span class="muted" id="status"></span>
    </form>
    <p class="muted" id="scenario-help"></p>
  </section>
  <section class="panel" id="ask">
    <h2>The agent is waiting for you</h2>
    <pre id="ask-body"></pre>
    <div class="buttons">
      <button class="primary" id="yes">Approve</button>
      <button id="no">Decline</button>
    </div>
  </section>
  <section class="panel">
    <ol id="log"></ol>
  </section>
</main>
<script>
const $ = (id) => document.getElementById(id);
let next = 0, scenarios = [];
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;"}[c]));

async function load() {
  const data = await (await fetch("/api/catalog")).json();
  scenarios = data.scenarios;
  $("scenario").innerHTML = scenarios.map((s) => `<option>${esc(s.name)}</option>`).join("");
  $("task").innerHTML = data.tasks.map((t) => `<option value="${esc(t.id)}">${esc(t.id)}</option>`).join("");
  help();
}
function help() {
  const s = scenarios.find((x) => x.name === $("scenario").value);
  $("scenario-help").textContent = s ? s.description : "";
}
$("scenario").onchange = help;

$("start").onsubmit = async (e) => {
  e.preventDefault();
  const body = {task: $("task").value, scenario: $("scenario").value, approvals: $("approvals").value};
  const r = await fetch("/api/runs", {method: "POST", headers: {"Content-Type": "application/json"},
                                      body: JSON.stringify(body)});
  if (!r.ok) { $("status").textContent = (await r.json()).detail; return; }
  $("log").innerHTML = ""; next = 0; poll();
};

async function decide(approved) {
  await fetch("/api/decision", {method: "POST", headers: {"Content-Type": "application/json"},
                                body: JSON.stringify({approved})});
  $("ask").style.display = "none";
}
$("yes").onclick = () => decide(true);
$("no").onclick = () => decide(false);

function render(e) {
  const li = document.createElement("li");
  if (e.type === "start") {
    li.innerHTML = `<span class="node">${esc(e.task)}</span>`
      + ` <span class="muted">${esc(e.intent)} on ${esc(e.scenario)}</span>`;
  } else if (e.type === "step") {
    li.className = "step";
    let html = `<span class="node">${esc(e.node)}</span>`;
    for (const k of ["tier", "fallback_reason", "replay_outcome", "handoff_reason", "outcome"]) {
      if (e[k] !== undefined) html += ` <span class="muted">${k.replace("_", " ")}: ${esc(e[k])}</span>`;
    }
    if (e.calls) {
      const calls = e.calls.map((c) => c.tool + " " + JSON.stringify(c.arguments)).join("\\n");
      html += `<pre>${esc(calls)}</pre>`;
    }
    if (e.result) html += `<pre>${esc(e.result)}</pre>`;
    li.innerHTML = html;
  } else if (e.type === "ask") {
    li.className = "ask";
    const what = e.kind === "approval"
      ? (e.actions || []).map((a) => `${a.approval}: ${a.element || a.tool}`
          + (a.arguments ? " " + JSON.stringify(a.arguments) : "")).join("\\n")
      : `handoff: ${e.reason}`;
    li.innerHTML = `<span class="node">waiting</span> <span class="muted">${esc(what)}</span>`;
    $("ask-body").textContent = what;
    $("yes").textContent = e.kind === "approval" ? "Approve" : "I cleared it";
    $("no").textContent = e.kind === "approval" ? "Decline" : "Give up";
    $("ask").style.display = "block";
  } else if (e.type === "decision") {
    li.className = "decision";
    li.innerHTML = `<span class="muted">decision: ${esc(JSON.stringify(e))}</span>`;
  } else if (e.type === "done") {
    li.className = "done " + (e.passed ? "pass" : "fail");
    const verdict = e.passed ? "Passed: the database shows exactly the expected change"
      : e.wrong_action ? "Wrong action: the database changed outside the task" : "Not passed";
    li.innerHTML = `<div class="verdict">${verdict}</div>
      <div class="muted">outcome ${esc(e.outcome)}, tiers ${esc(e.tiers.join(" > "))},
      ${e.llm_calls} model calls, ${e.seconds}s</div>` + (e.missing.length || e.unexpected.length
      ? `<pre>${esc([...e.missing, ...e.unexpected].join("\\n"))}</pre>` : "");
  } else if (e.type === "error") {
    li.className = "error";
    li.innerHTML = `<span class="node">error</span> <span class="muted">${esc(e.message)}</span>`;
  }
  $("log").appendChild(li);
  li.scrollIntoView({block: "nearest"});
}

async function poll() {
  const data = await (await fetch(`/api/events?after=${next}`)).json();
  data.events.forEach(render);
  next = data.next;
  $("go").disabled = data.running;
  $("status").textContent = data.running ? "running" : "";
  if (data.running) setTimeout(poll, 1000);
}
load().then(poll);
</script>
</body>
</html>
"""
