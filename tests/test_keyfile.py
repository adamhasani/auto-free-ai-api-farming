"""Offline tests for farmer/keyfile.py — dedup, parse, append. No network, no Playwright."""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from farmer import keyfile


def _fresh(tmp_path):
    import farmer.keyfile as kf
    old = (kf.KEYFILE, kf.OUT)
    kf.OUT = tmp_path / "out"
    kf.KEYFILE = kf.OUT / "keys.txt"
    kf.METAFILE = kf.OUT / "meta.jsonl"
    return kf, old


def test_append_dedup(tmp_path):
    kf, old = _fresh(tmp_path)
    try:
        assert kf.append_key("Groq", "gsk_AAA", "a@b.com") is True
        assert kf.append_key("Groq", "gsk_AAA", "a@b.com") is False       # same provider+key
        assert kf.append_key("Groq", "gsk_BBB", "a@b.com") is True
        assert kf.append_key("Cerebras", "cr_CCC", "a@b.com") is True     # diff provider ok
        rows = kf.read_keys()
        assert len(rows) == 3
        assert {r["key"] for r in rows} == {"gsk_AAA", "gsk_BBB", "cr_CCC"}
        assert all(r["account"] == "a@b.com" for r in rows)
        # format round-trips through tabs
        raw = kf.KEYFILE.read_text(encoding="utf-8").splitlines()
        assert all("\t" in r and "account=" in r for r in raw)
    finally:
        kf.KEYFILE, kf.OUT = old


def test_have_key(tmp_path):
    kf, old = _fresh(tmp_path)
    try:
        kf.append_key("Groq", "gsk_X", "one@x.io")
        assert kf.have_key("Groq", "one@x.io") is True
        assert kf.have_key("Groq", "other@x.io") is False
        assert kf.have_key("OpenRouter", "one@x.io") is False
    finally:
        kf.KEYFILE, kf.OUT = old


def test_append_meta(tmp_path):
    kf, old = _fresh(tmp_path)
    try:
        kf.append_key("Groq", "gsk_M", "m@x.io", meta={"engine": "ok"})
        meta = kf.METAFILE.read_text(encoding="utf-8")
        assert "gsk_M" in meta and "engine" in meta
    finally:
        kf.KEYFILE, kf.OUT = old