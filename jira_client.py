"""Minimal Jira Cloud REST client for base Jira (no Assets add-on).

Custom fields are addressed by their display name (e.g. "Serial Number"); the client looks up the
field ids for this project once. Search results come back with fields keyed by those same names.
"""
import json
import os
import re
from pathlib import Path

import requests

SYSTEM_FIELDS = {"summary", "labels", "duedate", "parent", "issuetype", "status", "created", "issuelinks"}


def adf(text):
    """Plain text -> Atlassian Document Format (one paragraph per line)."""
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": line}]} if line else {"type": "paragraph"}
        for line in str(text).splitlines() or [""]]}


class JiraError(RuntimeError):
    pass


class Jira:
    def __init__(self, site, email, token, project):
        site = site.strip().rstrip("/")
        self.site = site if site.startswith("http") else "https://" + site
        self.project = project
        self.s = requests.Session()
        self.s.auth = (email, token)
        self.s.headers.update({"Accept": "application/json"})
        self._types = self._fields = self._project_id = None

    def _req(self, method, path, **kw):
        r = self.s.request(method, self.site + path, timeout=30, **kw)
        if r.status_code >= 400:
            raise JiraError(f"{method} {path} -> {r.status_code}: {r.text[:400]}")
        return r.json() if r.content else {}

    def browse_url(self, key):
        return f"{self.site}/browse/{key}"

    def whoami(self):
        return self._req("GET", "/rest/api/3/myself")["displayName"]

    def check_project(self):
        p = self._req("GET", f"/rest/api/3/project/{self.project}")
        self._project_id = p["id"]
        return p["name"], p.get("style", "")

    # --- schema lookups -------------------------------------------------------------------
    def issue_types(self):
        if self._types is None:
            d = self._req("GET", f"/rest/api/3/issue/createmeta/{self.project}/issuetypes")
            self._types = {t["name"]: t["id"] for t in d.get("issueTypes", d.get("values", []))}
        return self._types

    def issue_type_id(self, name):
        for n, i in self.issue_types().items():
            if n.lower() == name.lower():
                return i
        raise JiraError(f"Work type '{name}' not in {self.project}. Have: {sorted(self.issue_types())}")

    def fields(self):
        """name -> (id, schema type). Prefers fields scoped to this (team-managed) project."""
        if self._fields is None:
            if self._project_id is None:
                self.check_project()
            out = {}
            for f in self._req("GET", "/rest/api/3/field"):
                scope = (f.get("scope") or {}).get("project", {}).get("id")
                if scope and scope != self._project_id:
                    continue
                if f["name"] in out and not scope:
                    continue  # keep the project-scoped one
                out[f["name"]] = (f["id"], (f.get("schema") or {}).get("type", "string"))
            self._fields = out
        return self._fields

    def missing_fields(self, names):
        have = {n.lower() for n in self.fields()}
        return [n for n in names if n.lower() not in have]

    def _fid(self, name):
        if name in SYSTEM_FIELDS:
            return name, None
        for n, (fid, typ) in self.fields().items():
            if n.lower() == name.lower():
                return fid, typ
        raise JiraError(f"Field '{name}' not found in {self.project}; add it in Space settings > Work types")

    def _encode(self, values):
        out = {}
        for name, v in values.items():
            if v is None or v == "":
                continue
            fid, typ = self._fid(name)
            if typ == "option":
                v = {"value": v}
            elif typ == "array" and isinstance(v, str):
                v = [{"value": v}]
            out[fid] = v
        return out

    def _decode(self, issue, names):
        f = issue.get("fields", {})
        out = {}
        for n in names:
            fid = n if n in SYSTEM_FIELDS else self._fid(n)[0]
            v = f.get(fid)
            if isinstance(v, dict) and "value" in v:
                v = v["value"]
            out[n] = v
        return {"key": issue["key"], "fields": out}

    # --- issues ---------------------------------------------------------------------------
    def search(self, jql, names=("summary", "labels", "duedate", "parent")):
        ids = [n if n in SYSTEM_FIELDS else self._fid(n)[0] for n in names]
        out, token = [], None
        while True:
            body = {"jql": jql, "fields": ids, "maxResults": 100}
            if token:
                body["nextPageToken"] = token
            d = self._req("POST", "/rest/api/3/search/jql", json=body)
            out += [self._decode(i, names) for i in d.get("issues", [])]
            token = d.get("nextPageToken")
            if not token or d.get("isLast", True):
                return out

    def create(self, issuetype, summary, fields=None, labels=(), duedate=None, parent=None, description=None):
        f = {"project": {"key": self.project}, "issuetype": {"id": self.issue_type_id(issuetype)},
             "summary": summary, "labels": list(labels)}
        f.update(self._encode(fields or {}))
        if duedate:
            f["duedate"] = duedate
        if parent:
            f["parent"] = {"key": parent}
        if description:
            f["description"] = adf(description)
        return self._req("POST", "/rest/api/3/issue", json={"fields": f})["key"]

    def update(self, key, fields=None, duedate=None, labels_add=(), labels_remove=(), summary=None):
        f = self._encode(fields or {})
        if duedate:
            f["duedate"] = duedate
        if summary:
            f["summary"] = summary
        body = {"fields": f}
        ops = [{"add": l} for l in labels_add] + [{"remove": l} for l in labels_remove]
        if ops:
            body["update"] = {"labels": ops}
        self._req("PUT", f"/rest/api/3/issue/{key}", json=body)

    def link(self, inward, outward, kind="Relates"):
        self._req("POST", "/rest/api/3/issueLink", json={
            "type": {"name": kind}, "inwardIssue": {"key": inward}, "outwardIssue": {"key": outward}})

    def attach(self, key, path, mime="application/octet-stream"):
        with open(path, "rb") as fh:
            self._req("POST", f"/rest/api/3/issue/{key}/attachments",
                      headers={"X-Atlassian-Token": "no-check"}, files={"file": (Path(path).name, fh, mime)})


class FakeJira:
    """Dry-run stand-in: keeps issues in a local JSON file, understands the JQL this tool uses."""
    def __init__(self, store, project="LAB", site="https://example.atlassian.net"):
        self.store, self.project, self.site = Path(store), project, site
        d = json.loads(self.store.read_text()) if self.store.exists() else {}
        self.issues, self.links = d.get("issues", []), d.get("links", [])

    def _save(self):
        self.store.write_text(json.dumps({"issues": self.issues, "links": self.links}, indent=1))

    def browse_url(self, key):
        return f"{self.site}/browse/{key}"

    def whoami(self):
        return "dry-run"

    def check_project(self):
        return "Lab Equipment (dry run)", "next-gen"

    def issue_types(self):
        return {"Equipment": "1", "Calibration Event": "2", "Epic": "3"}

    def missing_fields(self, names):
        return []

    def search(self, jql, names=None):
        labels = re.findall(r'labels\s*=\s*"([^"]+)"', jql)
        keys = re.findall(r'\bkey\s*=\s*"([^"]+)"', jql)
        parent = re.findall(r'parent\s*=\s*"([^"]+)"', jql)
        itype = re.search(r'issuetype\s*=\s*"([^"]+)"', jql)
        hits = [i for i in self.issues
                if all(l in i["fields"]["labels"] for l in labels)
                and (not keys or i["key"] in keys)
                and (not parent or (i["fields"].get("parent") or {}).get("key") in parent)
                and (not itype or i["fields"]["issuetype"].lower() == itype.group(1).lower())]
        return [{"key": i["key"], "fields": dict(i["fields"])} for i in hits]

    def create(self, issuetype, summary, fields=None, labels=(), duedate=None, parent=None, description=None):
        key = f"{self.project}-{len(self.issues) + 1}"
        f = {"issuetype": issuetype, "summary": summary, "labels": list(labels), "duedate": duedate,
             "parent": {"key": parent} if parent else None}
        f.update({k: v for k, v in (fields or {}).items() if v not in (None, "")})
        self.issues.append({"key": key, "fields": f})
        self._save()
        return key

    def update(self, key, fields=None, duedate=None, labels_add=(), labels_remove=(), summary=None):
        i = next(i for i in self.issues if i["key"] == key)
        i["fields"].update({k: v for k, v in (fields or {}).items() if v not in (None, "")})
        if duedate:
            i["fields"]["duedate"] = duedate
        if summary:
            i["fields"]["summary"] = summary
        i["fields"]["labels"] = [l for l in i["fields"]["labels"] if l not in labels_remove] + list(labels_add)
        self._save()

    def link(self, inward, outward, kind="Relates"):
        self.links.append([inward, outward, kind])
        self._save()

    def attach(self, key, path, mime=None):
        pass


def from_env(dry_run=False, store="dryrun_jira.json"):
    project = os.environ.get("JIRA_PROJECT", "LAB")
    if dry_run:
        return FakeJira(store, project)
    missing = [k for k in ("JIRA_SITE", "JIRA_EMAIL", "JIRA_API_TOKEN") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"Missing in .env: {', '.join(missing)} (see .env.example), or use --dry-run")
    return Jira(os.environ["JIRA_SITE"], os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"], project)


DATA_MARK = "asset-data"


def adf_with_data(values, note=None):
    """Description = readable two-column table + a code block holding the exact values as JSON."""
    def cell(text, header=False):
        return {"type": "tableHeader" if header else "tableCell",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": str(text) or "-"}]}]}
    rows = [(k, v) for k, v in values.items() if v not in (None, "")]
    content = []
    if rows:
        content.append({"type": "table", "attrs": {"isNumberColumnEnabled": False, "layout": "default"},
                        "content": [{"type": "tableRow", "content": [cell(k, True), cell(v)]} for k, v in rows]})
    if note:
        content.append({"type": "paragraph", "content": [{"type": "text", "text": note}]})
    content.append({"type": "codeBlock", "attrs": {"language": DATA_MARK},
                    "content": [{"type": "text", "text": json.dumps(values, sort_keys=True)}]})
    return {"type": "doc", "version": 1, "content": content}


def data_from_adf(doc):
    for node in (doc or {}).get("content", []):
        if node.get("type") == "codeBlock" and (node.get("attrs") or {}).get("language") == DATA_MARK:
            try:
                return json.loads("".join(t.get("text", "") for t in node.get("content", [])))
            except ValueError:
                return {}
    return {}


class SimpleJira(Jira):
    """Same interface as Jira, but needs no custom fields or custom work types.

    "Equipment" maps to the default Task and "Calibration Event" to Subtask. Named field values are
    kept in the description (a readable table plus a JSON block the tool reads back). Searching
    still works through the labels the tool sets (equipment, eqid-…, sn-…, in-…).
    """
    TYPE_MAP = {"equipment": "Task", "calibration event": "Subtask"}

    def issue_type_id(self, name):
        return super().issue_type_id(self.TYPE_MAP.get(name.lower(), name))

    def missing_fields(self, names):
        return []

    def search(self, jql, names=("summary", "labels", "duedate", "parent")):
        jql = re.sub(r'issuetype\s*=\s*"([^"]+)"',
                     lambda m: f'issuetype = "{self.TYPE_MAP.get(m.group(1).lower(), m.group(1))}"', jql)
        sys_names = [n for n in names if n in SYSTEM_FIELDS]
        out, token = [], None
        while True:
            body = {"jql": jql, "fields": sys_names + ["description"], "maxResults": 100}
            if token:
                body["nextPageToken"] = token
            d = self._req("POST", "/rest/api/3/search/jql", json=body)
            for i in d.get("issues", []):
                f = i.get("fields", {})
                data = data_from_adf(f.get("description"))
                out.append({"key": i["key"], "fields": {n: (f.get(n) if n in SYSTEM_FIELDS else data.get(n))
                                                        for n in names}})
            token = d.get("nextPageToken")
            if not token or d.get("isLast", True):
                return out

    def create(self, issuetype, summary, fields=None, labels=(), duedate=None, parent=None, description=None):
        f = {"project": {"key": self.project}, "issuetype": {"id": self.issue_type_id(issuetype)},
             "summary": summary, "labels": list(labels),
             "description": adf_with_data({k: v for k, v in (fields or {}).items() if v not in (None, "")},
                                          description)}
        if duedate:
            f["duedate"] = duedate
        if parent:
            f["parent"] = {"key": parent}
        return self._req("POST", "/rest/api/3/issue", json={"fields": f})["key"]

    def update(self, key, fields=None, duedate=None, labels_add=(), labels_remove=(), summary=None):
        body = {"fields": {}}
        if fields:
            cur = self._req("GET", f"/rest/api/3/issue/{key}?fields=description")["fields"].get("description")
            data = data_from_adf(cur)
            data.update({k: v for k, v in fields.items() if v not in (None, "")})
            note = next(("".join(t.get("text", "") for t in n.get("content", []))
                         for n in (cur or {}).get("content", []) if n.get("type") == "paragraph"), None)
            body["fields"]["description"] = adf_with_data(data, note)
        if duedate:
            body["fields"]["duedate"] = duedate
        if summary:
            body["fields"]["summary"] = summary
        ops = [{"add": l} for l in labels_add] + [{"remove": l} for l in labels_remove]
        if ops:
            body["update"] = {"labels": ops}
        self._req("PUT", f"/rest/api/3/issue/{key}", json=body)


def connect(dry_run=False, store="dryrun_jira.json"):
    """Real Jira: uses the custom Equipment fields if they exist in the space, else SimpleJira."""
    j = from_env(dry_run, store)
    if dry_run:
        return j
    names = [t.lower() for t in j.issue_types()]
    mode = os.environ.get("JIRA_MODE", "auto").lower()
    full = "equipment" in names and "calibration event" in names and not j.missing_fields(
        ["Equipment ID", "Serial Number", "Calibration Status", "Calibration Date", "Result"])
    if mode == "simple" or (mode == "auto" and not full):  # half-finished setup falls back to simple
        s = SimpleJira.__new__(SimpleJira)
        s.__dict__.update(j.__dict__)
        return s
    return j
