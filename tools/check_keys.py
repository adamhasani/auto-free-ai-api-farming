"""Health-check harvested keys against the live provider APIs.

Classifies every row in out/keys.txt:

    live      probe returned 200            -> out/keys_live.txt
    dead      401/403 (invalid/expired)     -> out/keys_dead.txt
    limited   429 (rate-limited = still ok) -> out/keys_live.txt (marked)
    unknown   network error or no probe url -> out/keys_unknown.txt

Probe strategy per provider:
    * LLM providers (Groq, Cerebras, OpenRouter, …) -> 1-token /chat/completions
    * any provider with api_base in the registry      -> GET {api_base}/models
    * otherwise                                       -> unknown

Pure stdlib (urllib), threaded, no new dependencies. Exit code 0 always (a dead
key is a valid result, not a failure).

Usage:
    python tools/check_keys.py                 # checks out/keys.txt
    python tools/check_keys.py out/key.txt     # custom file
"""
from __future__ import annotations
import sys
import json
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from farmer import keyfile, keypool

CHAT_BODY = json.dumps({
    "model": None,  # filled per provider
    "messages": [{"role": "user", "content": "ping"}],
    "max_tokens": 1,
}).encode("utf-8")

# provider name (registry) -> (probe url, json body or None for GET)
def _probe_for(provider: str) -> tuple[str, bytes | None, dict | None] | None:
    name = provider.strip().lower()
    if name in keypool.LLM_PROVIDERS:
        base, model, _vis = keypool.LLM_PROVIDERS[name]
        body = json.loads(CHAT_BODY)
        body["model"] = model
        return base, json.dumps(body).encode("utf-8"), None
    base = keyfile.provider_base_urls().get(provider.strip())
    if base:
        return base.rstrip("/") + "/models", None, None
    return None


def classify(key: str, probe: tuple) -> str:
    url, body, _ = probe
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return "live"
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return "live"          # rate-limited: key works
        if e.code in (401, 403):
            return "dead"          # invalid / expired / revoked
        return "unknown"
    except Exception:
        return "unknown"


def check_file(path: Path) -> dict:
    rows = keyfile.read_keys() if path == keyfile.KEYFILE else _read_any(path)
    out: dict[str, list] = {"live": [], "dead": [], "unknown": []}
    tasks = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for r in rows:
            probe = _probe_for(r["provider"])
            if probe is None:
                out["unknown"].append((r, "no_probe_url"))
                continue
            f = ex.submit(classify, r["key"], probe)
            tasks.append((r, f))
        for r, f in tasks:
            try:
                status = f.result()
            except Exception:
                status = "unknown"
            out[status].append((r, status))
    return out


def _read_any(path: Path) -> list[dict]:
    rows = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        p = ln.split("\t")
        rows.append({"provider": p[0].strip(), "key": p[1].strip(),
                     "account": "", "at": ""})
    return rows


def _write_bucket(path: Path, items):
    with path.open("w", encoding="utf-8") as f:
        for r, _st in items:
            f.write(f"{r['provider']}\t{r['key']}\taccount={r['account']}\t{r['at']}\n")


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    path = Path(args[0]) if args else keyfile.KEYFILE
    print(f"Checking keys in {path} …")
    res = check_file(path)
    keyfile.OUT.mkdir(parents=True, exist_ok=True)
    _write_bucket(keyfile.OUT / "keys_live.txt", res["live"])
    _write_bucket(keyfile.OUT / "keys_dead.txt", res["dead"])
    _write_bucket(keyfile.OUT / "keys_unknown.txt", res["unknown"])
    print(f"  live    : {len(res['live'])}  -> out/keys_live.txt")
    print(f"  dead    : {len(res['dead'])}  -> out/keys_dead.txt")
    print(f"  unknown : {len(res['unknown'])}  -> out/keys_unknown.txt")
    dead = [r["provider"] + "/" + (r["key"][:10] + "…" if r["key"] else "?")
            for r, _st in res["dead"][:10]]
    if dead:
        print("  dead keys:", ", ".join(dead))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())