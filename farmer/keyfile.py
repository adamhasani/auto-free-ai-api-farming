"""Key file store: out/keys.txt read / deduplicated append.

Single helper for everything that touches the harvest file, so run.py, --check,
--export and the batch mode all agree on one format:

    <provider>\t<key>\taccount=<email>\t<YYYY-MM-DD HH:MM>\n

Dedup is (provider, key) — the same account re-farming a provider never writes
the same key twice, and a key accidentally harvested under two accounts is only
kept once.
"""
from __future__ import annotations
import json
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
OUT = ROOT / "out"
KEYFILE = OUT / "keys.txt"
METAFILE = OUT / "meta.jsonl"

STAMP = "%Y-%m-%d %H:%M"


def read_keys() -> list[dict]:
    """Parse out/keys.txt into rows: [{provider, key, account, at, raw}]."""
    rows = []
    if not KEYFILE.exists():
        return rows
    for ln in KEYFILE.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        p = ln.split("\t")
        row = {"provider": p[0].strip() if len(p) > 0 else "",
               "key": p[1].strip() if len(p) > 1 else "",
               "account": "", "at": ""}
        for extra in p[2:]:
            if extra.startswith("account="):
                row["account"] = extra[len("account="):]
            elif len(extra) >= 5 and extra[:4].isdigit() and extra[4] == "-":
                row["at"] = extra
        row["raw"] = ln
        if row["key"]:
            rows.append(row)
    return rows


def have_key(provider: str, account: str) -> bool:
    return any(r["provider"] == provider and r["account"] == account for r in read_keys())


def append_key(provider: str, key: str, account: str, meta: dict | None = None) -> bool:
    """Append one harvest row unless (provider, key) already exists.

    Returns True when a NEW key was written, False when it was a duplicate.
    Also appends to out/meta.jsonl when meta carries extra fields.
    """
    stamp = time.strftime(STAMP)
    deduped = read_keys()
    if any(r["provider"] == provider and r["key"] == key for r in deduped):
        return False
    OUT.mkdir(parents=True, exist_ok=True)
    with KEYFILE.open("a", encoding="utf-8") as f:
        f.write(f"{provider}\t{key}\taccount={account}\t{stamp}\n")
    if meta:
        payload = {"provider": provider, "key": key[:12] + "…" if key else "",
                   "account": account, "at": stamp}
        payload.update({k: v for k, v in meta.items()})
        with METAFILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return True


def provider_base_urls() -> dict:
    """provider name -> API base URL, merged from the site registry (api_base field)."""
    from . import registry
    out = {}
    for s in registry.load():
        base = s.get("api_base")
        if base:
            out[s["name"]] = base
    return out