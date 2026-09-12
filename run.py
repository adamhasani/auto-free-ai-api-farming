"""Entry point. Usage:

  python run.py                              # all sites, headed, logged-in profile
  python run.py Groq                         # single site
  python run.py Groq Cerebras                # several sites
  python run.py --headless                   # force headless (debug/CI)
  python run.py --no-map                     # skip auto-generating/opening out/path.html
  python run.py --accounts accounts.txt      # batch: one account per line, see below
  python run.py --list                       # print automatable sites, then exit
  python run.py --check                      # health-check out/keys.txt (live/dead/unknown)
  python run.py --export sh                  # emit `omniroute providers add` commands

Batch accounts.txt format — one account per line, blanks and #-comments ignored:
    email:password:Display Name:profile_dir
    email:password
    email

Output: out/results.json (status per site) + out/trace.txt (readable steps)
        + out/debug.jsonl (everything) + out/keys.txt (harvested keys, deduped).
Non-interactive: runs, saves, exits. Monitor from your phone by reading out/trace.txt
or set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID for an end-of-run digest.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from farmer.browser import Browser
from farmer.log import Log
from farmer.sites import SITES, site_cfg
from farmer import keyfile, registry, tree, forms, notify

OUT = keyfile.OUT
RESULTS = OUT / "results.json"
STOP = OUT / "STOP"  # create this file to stop the run between sites


def _load():
    if RESULTS.exists():
        try:
            return json.loads(RESULTS.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save(d):
    RESULTS.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


async def _run_account(ctx, targets, account: str, headless: bool) -> tuple[dict, list[str]]:
    """One account pass over the target sites. Returns (results, keys_new)."""
    results = {}
    keys_new = []
    log = Log(account or "run")
    log.head(f"ACCOUNT {account or '(env)'} · {len(targets)} siti · headless={headless}")
    for site in targets:
        nm = site["name"]
        if STOP.exists():
            log.step("STOP", nm, "requested by user (out/STOP)", "warn")
            break
        if keyfile.have_key(nm, account):
            log.step("SALTO", nm, f"gia presa per {account}", "skip")
            results[nm] = {"status": "skip", "provider": nm}
            continue
        if not registry.automatable(nm):
            log.step("MURO", nm, f"wall={registry.wall_of(nm)}", "warn")
            results[nm] = {"status": "wall", "wall": registry.wall_of(nm), "provider": nm}
            continue
        lg = Log(nm)
        try:
            res = await tree.run_site(ctx, site, lg)
        except Exception as e:
            lg.err("RUN", e)
            res = {"status": "error", "detail": str(e)[:120]}
        res["account"] = account
        res["provider"] = nm
        res["at"] = time.strftime("%Y-%m-%d %H:%M")
        results[nm] = res
        if res.get("status") == "ok" and res.get("key"):
            meta = {k: v for k, v in res.items()
                    if k not in ("status", "key", "account", "provider", "at")}
            if keyfile.append_key(nm, res["key"], account, meta=meta):
                keys_new.append(nm)
    ok = sum(1 for v in results.values() if v.get("status") == "ok")
    log.head(f"ACCOUNT {account}: {ok}/{len(targets)} key")
    return results, keys_new


async def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    if args.list:
        print("\n".join(registry.names(only_automatable=True)))
        return 0
    if args.check:
        sys.path.insert(0, str(Path(__file__).parent / "tools"))
        import check_keys
        return check_keys.main([])
    if args.export is not None:
        sys.path.insert(0, str(Path(__file__).parent / "tools"))
        import export_omniroute
        return export_omniroute.main(["--format", args.export])

    if not forms.EMAIL:
        raise RuntimeError(
            "SIGNUP_ACCOUNT is required for live runs (no placeholder fallback — set it via env "
            "var or the OS keyring so a run never proceeds silently against fake data).")

    if args.only:
        args.sites.append(args.only)
    targets = ([site_cfg(n) for n in args.sites]
               if args.sites else [site_cfg(s["name"]) for s in SITES])

    all_results, keys_new = {}, []
    async with Browser(headless=args.headless, profile=True) as ctx:
        entries = _read_accounts(Path(args.accounts)) if args.accounts else [_AccountConf(forms.EMAIL)]
        for acct in entries:
            if args.accounts:
                _apply_account(acct)
            res, new = await _run_account(ctx, targets, acct.email, args.headless)
            all_results.update(res)
            keys_new += new
            _save(all_results)

    ok = sum(1 for v in all_results.values() if v.get("status") == "ok")
    print(f"\nFINE · {ok}/{len(all_results)} key ottenute")
    for nm, v in all_results.items():
        print(f"  {v.get('status','?'):8} {nm}"
              + (f"  {v.get('key','')[:14]}" if v.get("key") else ""))

    if args.notify and all_results:
        notify.send(notify.run_summary(all_results, keys_new))
    return 0


def _parse(argv):
    p = argparse.ArgumentParser(prog="auto-free-ai-api-farming", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sites", nargs="*", help="provider names from data/sites.json (default: all)")
    p.add_argument("-o", "--only", help="single provider (alias for positional)")
    p.add_argument("--headless", action="store_true", help="no visible window")
    p.add_argument("--no-map", action="store_true", help="skip live out/path.html map")
    p.add_argument("--accounts", metavar="FILE", help="batch accounts file (email:password:name:profile)")
    p.add_argument("--list", action="store_true", help="list automatable sites and exit")
    p.add_argument("--check", action="store_true", help="health-check out/keys.txt (live/dead)")
    p.add_argument("--export", choices=["sh", "list", "env"], help="export keys (omniroute sh / list / env)")
    args = p.parse_args(argv)
    if args.no_map:
        os.environ["SIGNUP_NO_MAP"] = "1"
    args.notify = notify.config() is not None
    return args


def _read_accounts(path: Path) -> list["_AccountConf"]:
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        parts = [p.strip() for p in ln.split(":")]
        out.append(_AccountConf(*parts, *([""] * (4 - len(parts)))))
    return out


def _apply_account(a: "_AccountConf"):
    os.environ["SIGNUP_ACCOUNT"] = a.email
    os.environ["SIGNUP_PASSWORD"] = a.password or os.environ.get("SIGNUP_PASSWORD", "")
    os.environ["SIGNUP_NAME"] = a.name or os.environ.get("SIGNUP_NAME", "")
    os.environ["SIGNUP_PROFILE"] = a.profile or os.environ.get("SIGNUP_PROFILE", "")


class _AccountConf:
    def __init__(self, email: str, password: str = "", name: str = "", profile: str = ""):
        self.email = email
        self.password = password
        self.name = name
        self.profile = profile


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))