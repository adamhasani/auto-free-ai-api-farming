"""Offline tests for tools/check_keys.py classification + tools/export_omniroute.py + batch."""
from __future__ import annotations
import sys
import json
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import check_keys
import export_omniroute
from farmer import keyfile


class FakeResp:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(code: int):
    def fn(request, timeout=None):
        if code == 0:
            raise urllib.request.URLError("net down")
        if code >= 400:
            from urllib.error import HTTPError
            raise HTTPError(request.full_url, code, "err", {}, None)
        return FakeResp(200 if code < 400 else 200)
    return fn


def test_check_classify_live(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(200))
    probe = check_keys._probe_for("Groq")
    assert probe is not None and "chat/completions" in probe[0]
    assert check_keys.classify("gsk_xxx", probe) == "live"


def test_check_classify_dead(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(401))
    probe = check_keys._probe_for("Groq")
    assert check_keys.classify("gsk_bad", probe) == "dead"


def test_check_classify_ratelimited_live(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(429))
    probe = check_keys._probe_for("Groq")
    assert check_keys.classify("gsk_hot", probe) == "live"


def test_check_classify_unknown_network(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(0))
    probe = check_keys._probe_for("Groq")
    assert check_keys.classify("gsk_net", probe) == "unknown"


def test_check_no_probe_url():
    assert check_keys._probe_for("NoSuchProvider") is None


def _rows():
    return [
        {"provider": "Groq", "key": "gsk_AAA", "account": "a@b.com", "at": "2026-09-12 05:00"},
        {"provider": "OpenRouter", "key": "sk-or-BBB", "account": "a@b.com", "at": "2026-09-12 05:01"},
    ]


def test_export_sh_contains_add_cmd():
    lines = export_omniroute.sh_lines(_rows())
    joined = "\n".join(lines)
    assert "omniroute providers add groq --credential-stdin" in joined
    assert "omniroute providers add openrouter --credential-stdin" in joined
    assert "gsk_AAA" in joined


def test_export_list_format():
    lines = export_omniroute.list_lines(_rows())
    assert len(lines) == 2 and "Groq\tgsk_AAA" in lines[0]


def test_export_env_dedup_names():
    rows = _rows() + [{"provider": "Groq", "key": "gsk_CCC", "account": "x", "at": ""}]
    lines = export_omniroute.env_lines(rows)
    names = [ln.split("=")[0] for ln in lines]
    assert names == ["GROQ", "OPENROUTER", "GROQ_2"]


def test_batch_accounts_parse(tmp_path):
    from run import _read_accounts
    f = tmp_path / "accounts.txt"
    f.write_text("# comment\n\nalice@x.io:pass1:Alice:profA\nbob@x.io\n", encoding="utf-8")
    accs = _read_accounts(f)
    assert len(accs) == 2
    assert accs[0].email == "alice@x.io" and accs[0].password == "pass1"
    assert accs[0].name == "Alice" and accs[0].profile == "profA"
    assert accs[1].email == "bob@x.io" and accs[1].profile == ""


def test_notify_summary_builder():
    from farmer import notify
    res = {
        "Groq": {"status": "ok", "key": "gsk_"},
        "Cerebras": {"status": "ok", "key": "cr_"},
        "HuggingFace": {"status": "wall", "wall": "anti_bot"},
        "X": {"status": "error"},
    }
    s = notify.run_summary(res, ["Groq", "Cerebras"])
    assert "2 ok" in s and "1 wall" in s and "1 error" in s
    assert "Groq" in s and "Cerebras" in s