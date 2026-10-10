"""Merge observations, match them to the expected inventory and (optionally) to Jira, flag problems.

Matching rules (never on model name alone):
  serial:<sn>          the observation's instrument serial equals <sn>
  hw:<prefix>          the observation's hardware_id starts with <prefix> (e.g. usb:32E4:9230,
                       ni-chassis:cDAQ9189-2A23393/slot3)
  name:<regex>         the OS / Revel name matches <regex> (case-insensitive) - weak, flagged as such
Several rules may be joined with '|'; the first that matches wins.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def merge(observations):
    """Same physical device seen by several adapters -> one entry. Key: manufacturer+serial when an
    instrument serial is known, otherwise hardware_id, otherwise source+interface_id."""
    merged, conflicts = {}, []
    for o in observations:
        key = (f"sn:{norm(o.manufacturer)}:{norm(o.serial)}" if o.serial else
               f"hw:{o.hardware_id}" if o.hardware_id else f"{o.source}:{o.interface_id}")
        if key not in merged:
            merged[key] = {"obs": o, "sources": [o.source], "key": key}
            continue
        m = merged[key]
        m["sources"].append(o.source)
        if o.model and m["obs"].model and norm(o.model) != norm(m["obs"].model):
            conflicts.append(f"{key}: model {m['obs'].model!r} ({m['sources'][0]}) vs {o.model!r} ({o.source})")
        rank = {"confirmed": 0, "inferred": 1, "unresolved": 2}
        if rank[o.status] < rank[m["obs"].status]:
            o.revel_channels = o.revel_channels or m["obs"].revel_channels
            m["obs"] = o
    # the same serial under two different manufacturers is suspicious too
    by_sn = {}
    for m in merged.values():
        o = m["obs"]
        if o.serial:
            by_sn.setdefault(norm(o.serial), []).append(o)
    for sn, os_ in by_sn.items():
        if len(os_) > 1:
            conflicts.append(f"serial {sn} reported for {len(os_)} devices: " +
                             ", ".join(f"{o.manufacturer} {o.model} ({o.source})" for o in os_))
    return list(merged.values()), conflicts


def load_inventory(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = [{k.strip(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(fh)]
    return [r for r in rows if r.get("name") and not r["name"].startswith("#")]


def _rule_hits(rule, o):
    kind, _, val = rule.partition(":")
    if kind == "serial":
        return bool(o.serial) and norm(o.serial) == norm(val), "strong"
    if kind == "hw":
        return bool(o.hardware_id) and o.hardware_id.upper().startswith(val.upper()), "strong"
    if kind == "name":
        return bool(o.name) and re.search(val, o.name, re.I) is not None, "weak"
    return False, ""


def match_inventory(entries, inventory):
    """Returns (rows, extra): one row per expected item with its match (if any), and the discovered
    entries that matched nothing expected."""
    used, rows = set(), []
    for item in inventory:
        hit, strength = None, None
        for rule in filter(None, (item.get("match") or "").split("|")):
            cands = [(i, e) for i, e in enumerate(entries) if i not in used and _rule_hits(rule.strip(), e["obs"])[0]]
            if len(cands) == 1:
                hit, strength = cands[0], _rule_hits(rule.strip(), cands[0][1]["obs"])[1]
                break
            if len(cands) > 1:
                strength = f"ambiguous: {len(cands)} devices match {rule.strip()}"
                break
        if hit:
            used.add(hit[0])
        rows.append({"item": item, "entry": hit[1] if hit else None, "match": strength})
    extra = [e for i, e in enumerate(entries) if i not in used]
    return rows, extra


def jira_lookup(jira, entries):
    """Read-only: find existing Jira assets by instrument serial (labels sn-...)."""
    from asset_tool import tag
    for e in entries:
        o = e["obs"]
        if not o.serial:
            continue
        found = jira.search(f'project = "{jira.project}" AND labels = "{tag("sn", o.serial)}"')
        e["jira"] = [i["key"] for i in found]
