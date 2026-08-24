"""One-shot handshake verification — loads CyberMatrix's LLMRouter with the
usb_ai provider config and proves the round-trip works without a full scan."""
import sys, os, yaml
sys.path.insert(0, r"C:\Users\JYOTHI\pro\cybermatrix")
os.chdir(r"C:\Users\JYOTHI\pro\cybermatrix")
from core.llm_router import LLMRouter

with open("config.yaml") as f:
    cfg = yaml.safe_load(f)

print(f"provider={cfg['provider']}  model={cfg['model']}")
print(f"base_url={cfg['base_url']}  api_key={cfg['api_key']!r}")
print()

r = LLMRouter(cfg)
print(f"LLMRouter ready: provider={r.provider}, base_url={r.base_url}, model={r.model}")
print()

print("=== first chat() call ===")
out = r.chat([{"role": "user", "content": "Reply with just the word OK."}], system="Be terse.")
print(f"REPLY: {out!r}")
print()

print("=== second chat() call (planning-shaped) ===")
out2 = r.chat(
    [{"role": "user", "content": "List 2 OWASP Juice Shop attack categories in JSON: [{\"name\":...,\"why\":...}]"}],
    system="You are a pentest planner. Reply with JSON only, no prose.",
)
print(f"REPLY: {out2!r}")
print()

print(f"tokens_used={r.tokens_used}  last_prompt={r.last_prompt_tokens}  last_completion={r.last_completion_tokens}")
