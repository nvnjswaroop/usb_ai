"""Capture the live request/response pair CyberMatrix ↔ USB AI sends during
a connection test, then run a real Juice Shop planning call via AIBrain._plan_attack."""
import json, os, sys, time, logging, yaml

sys.path.insert(0, r"C:\Users\JYOTHI\pro\cybermatrix")
os.chdir(r"C:\Users\JYOTHI\pro\cybermatrix")

logging.basicConfig(level=logging.WARNING)  # quiet

from core.llm_router import LLMRouter
from openai.resources.chat.completions import Completions as _Completions

captured = []
_orig_create = _Completions.create

def hooked_create(self, **kwargs):
    t0 = time.time()
    safe = {k: v for k, v in kwargs.items() if k != "messages"}
    captured.append({"dir": "REQ", "t": t0, "kwargs": safe,
                     "msgs_count": len(kwargs.get("messages", []))})
    try:
        resp = _orig_create(self, **kwargs)
        dt = time.time() - t0
        try:
            content = resp.choices[0].message.content
            usage = resp.usage.model_dump() if resp.usage else {}
        except Exception:
            content, usage = "<unparseable>", {}
        captured.append({"dir": "RESP", "t": t0, "dt": round(dt, 2),
                         "model": resp.model, "content": content[:400],
                         "usage": usage, "finish": resp.choices[0].finish_reason})
        return resp
    except Exception as e:
        dt = time.time() - t0
        captured.append({"dir": "ERR", "t": t0, "dt": round(dt, 2),
                         "type": type(e).__name__, "msg": str(e)[:400]})
        raise

_Completions.create = hooked_create

with open("config.yaml") as f:
    cfg = yaml.safe_load(f)

print("=" * 72)
print("STAGE 1: handshake — CyberMatrix.test_connection()")
print("=" * 72)
r = LLMRouter(cfg)
t0 = time.time()
ok, msg = r.test_connection()
dt_handshake = time.time() - t0
print(f"test_connection: ok={ok}  reply={msg!r}  ({dt_handshake:.2f}s)")
print()

print("=" * 72)
print("STAGE 2: AIBrain._plan_attack() on Juice Shop")
print("=" * 72)
# Construct AIBrain — needs event_bus + session
from core.event_bus import EventBus
from core.session import Session
from core.ai_brain import AIBrain

bus = EventBus()
session = Session()
session.target = "http://127.0.0.1:3000"
brain = AIBrain(llm=r, event_bus=bus, session=session)

available_modules = ["recon", "network", "vuln", "auth_analyzer",
                     "exploit_suggester", "crypto_analyzer", "osint"]
t0 = time.time()
plan = brain._plan_attack(
    target="http://127.0.0.1:3000",
    available_modules=available_modules,
)
dt_plan = time.time() - t0
print(f"_plan_attack returned in {dt_plan:.2f}s")
print(f"  plan: {plan!r}")
print(f"  (filtered against whitelist = {available_modules})")
print()

print("=" * 72)
print("STAGE 3: second planning call — finding-aware re-plan")
print("=" * 72)
# feed a finding into the brain to trigger a re-plan-ish second call
brain._on_finding({
    "title": "SQL Injection in /rest/products/search",
    "severity": "HIGH",
    "module": "vuln_scanner",
    "description": "Search parameter is concatenated into SQL without parameterization",
    "evidence": "GET /rest/products/search?q=test' OR '1'='1 returns all rows",
})
print(f"  total findings captured: {len(brain.findings)}")

print()
print("=" * 72)
print(f"CAPTURED HTTP TRAFFIC — {len(captured)} events")
print("=" * 72)
total_t = 0.0
ok_count = 0
err_count = 0
for i, c in enumerate(captured, 1):
    if c["dir"] == "REQ":
        msgs = c["msgs_count"]
        print(f"[{i:2d}] REQ  model={c['kwargs'].get('model','?')}  max_tokens={c['kwargs'].get('max_tokens','?')}  temp={c['kwargs'].get('temperature','?')}  msgs={msgs}")
    elif c["dir"] == "RESP":
        dt = c["dt"]; total_t += dt; ok_count += 1
        u = c["usage"]
        print(f"[{i:2d}] RESP {dt:5.2f}s  finish={c['finish']}  usage[prompt={u.get('prompt_tokens','?')},comp={u.get('completion_tokens','?')},tot={u.get('total_tokens','?')}]")
        print(f"          content={c['content']!r}")
    else:
        err_count += 1
        print(f"[{i:2d}] ERR  {c['dt']:5.2f}s  {c['type']}: {c['msg']}")

print()
print("=" * 72)
print("SUMMARY")
print("=" * 72)
print(f"  total round-trips:    {len([c for c in captured if c['dir']=='REQ'])}")
print(f"  successful responses: {ok_count}")
print(f"  errors/timeouts:     {err_count}")
print(f"  total LLM time:      {total_t:.2f}s")
print(f"  handshake latency:   {dt_handshake:.2f}s")
print(f"  planning latency:    {dt_plan:.2f}s")
print(f"  plan outcome:        {plan!r}  (valid modules={len(plan)})")

# persist for the report
out = {
    "config": cfg,
    "captured": captured,
    "summary": {
        "round_trips": len([c for c in captured if c["dir"]=="REQ"]),
        "successful": ok_count, "errors": err_count,
        "total_llm_seconds": round(total_t, 2),
        "handshake_seconds": round(dt_handshake, 2),
        "plan_seconds": round(dt_plan, 2),
        "plan": plan,
        "plan_modules_valid": len([m for m in plan if m in available_modules]),
    },
}
os.makedirs(r"C:\Users\JYOTHI\pro\newfolder\reports", exist_ok=True)
with open(r"C:\Users\JYOTHI\pro\newfolder\reports\scan_capture.json", "w") as f:
    json.dump(out, f, indent=2, default=str)
print()
print(f"  saved -> C:\\Users\\JYOTHI\\pro\\newfolder\\reports\\scan_capture.json")
