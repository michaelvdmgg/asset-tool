"""Discovery report: JSON (everything) + Markdown (for people)."""
from __future__ import annotations

import json
from collections import Counter

# Where each expected item is connected decides which adapter can possibly see it.
VISIBILITY = {
    "windows-usb": "Windows PnP", "windows-serial": "Windows PnP / VISA", "windows-network": "VISA / network",
    "revel-ethernet": "Revel only", "revel-usb": "Revel only", "revel-ethercat": "Revel only",
    "analog": "no electronic identity (passive, wired to a DAQ channel)",
    "none": "not connected to anything", "network": "no documented identity interface",
}

NEXT_STEPS = {
    "revel-ethernet": "Ask Revel for a documented hardware/config export (NI module model+serial per slot, EtherCAT identity).",
    "revel-usb": "Ask Revel whether the Alicat device config exposes the serial (Alicats report it over their serial protocol); otherwise read it off the unit once.",
    "revel-ethercat": "Ask Revel for ENI/identity fields (vendor ID, product code, serial) through a supported interface.",
    "analog": "No way to read it electronically: register once by hand (or scan its label) and map it to its Revel channel. TEDS sensors would be the exception.",
    "windows-usb": "Plug it into this PC (or run discovery on the PC it's plugged into).",
    "none": "Register once by hand.", "network": "Register once by hand.",
}


def classify(row):
    e = row["entry"]
    if e is None:
        return "not seen"
    o = e["obs"]
    if o.serial and o.status in ("confirmed", "inferred"):
        return f"identified ({o.status})"
    return "seen, identity unresolved"


def build(rows, extra, conflicts, sources, revel_notes):
    results = [classify(r) for r in rows]
    counts = Counter(results)
    ident = sum(v for k, v in counts.items() if k.startswith("identified"))
    return {"sources": sources, "conflicts": conflicts, "revel": revel_notes,
            "coverage": {"expected": len(rows), "identified": ident, "by_result": dict(counts)},
            "expected": [{"item": r["item"], "result": res, "match": r["match"],
                          "observation": r["entry"]["obs"].to_dict() if r["entry"] else None,
                          "seen_by": r["entry"]["sources"] if r["entry"] else [],
                          "jira": (r["entry"] or {}).get("jira")} for r, res in zip(rows, results)],
            "unexpected": [{"observation": e["obs"].to_dict(), "seen_by": e["sources"], "jira": e.get("jira")}
                           for e in extra]}


def cell(v):
    return str(v if v not in (None, "") else "-").replace("|", "/").replace("\n", " ")


def markdown(rep, title):
    cov = rep["coverage"]
    pct = 100 * cov["identified"] / cov["expected"] if cov["expected"] else 0
    out = [f"# {title}", "", "Read-only discovery: nothing was written to Jira and no commands were sent to hardware.", "",
           "## Sources", ""]
    out += [f"- **{s['name']}**: {s['result']}" for s in rep["sources"]]
    out += ["", "## Coverage", "",
            f"**{cov['identified']} of {cov['expected']} expected items identified automatically ({pct:.0f}%).**", ""]
    out += [f"- {k}: {v}" for k, v in sorted(cov["by_result"].items())]
    out += ["", "Status meanings: *confirmed* = serial reported by the device itself; *inferred* = derived from names or "
            "configuration, check it once; *unresolved* = present, identity unknown.", "",
            "## Expected equipment", "",
            "| Item | Connected via | Visible to | Result | Mfr / model / S/N found | Source | Evidence |",
            "|---|---|---|---|---|---|---|"]
    for x in rep["expected"]:
        it, o = x["item"], x["observation"] or {}
        found = " / ".join(cell(o.get(k)) for k in ("manufacturer", "model", "serial")) if o else "-"
        res = x["result"] + (f" ({x['match']} match)" if x["match"] and x["observation"] else
                             f" ({x['match']})" if x["match"] else "")
        if x.get("jira"):
            res += f"; Jira {', '.join(x['jira'])}"
        out.append(f"| {cell(it['name'])} | {cell(it.get('connection'))} | {cell(VISIBILITY.get(it.get('connection'), '?'))} "
                   f"| {res} | {found} | {cell(', '.join(x['seen_by']))} | {cell(o.get('evidence'))} |")
    out += ["", "## Discovered but not on the expected list", ""]
    if rep["unexpected"]:
        out += ["| Name | Status | Mfr / model / S/N | Hardware ID | Connection | Evidence |", "|---|---|---|---|---|---|"]
        for x in rep["unexpected"]:
            o = x["observation"]
            out.append(f"| {cell(o['name'])} | {o['status']} ({o['confidence']}) | "
                       f"{' / '.join(cell(o[k]) for k in ('manufacturer', 'model', 'serial'))} | {cell(o['hardware_id'])} "
                       f"| {cell(o['connection'])} | {cell(o['evidence'])} |")
    else:
        out.append("None.")
    out += ["", "## Conflicts", ""] + ([f"- {c}" for c in rep["conflicts"]] or ["None."])
    rv = rep.get("revel")
    if rv:
        out += ["", "## Revel channels not tied to hardware", "",
                f"{len(rv['unmapped_channels'])} of {rv['channels_total']} channels carry no device identity in their name "
                "(user-named channels such as pt1). Their sensors need a manual channel-to-equipment mapping.", "",
                "```", *(", ".join(rv["unmapped_channels"][i:i + 8]) for i in range(0, len(rv["unmapped_channels"]), 8)), "```"]
        if rv.get("family_hints"):
            out += ["", "Name hints (not identity): " + "; ".join(f"{k}: {len(v)} channels" for k, v in rv["family_hints"].items())]
    gaps = sorted({x["item"].get("connection") for x in rep["expected"] if not x["result"].startswith("identified")} - {None})
    out += ["", "## What would raise coverage", ""] + [f"- **{g}**: {NEXT_STEPS.get(g, '-')}" for g in gaps]
    return "\n".join(out) + "\n"


def write(rep, md, base):
    base.parent.mkdir(parents=True, exist_ok=True)
    base.with_suffix(".json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    base.with_suffix(".md").write_text(md, encoding="utf-8")
    return base.with_suffix(".md"), base.with_suffix(".json")
