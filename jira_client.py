"""Minimal Jira Cloud REST client (base Jira, no Assets add-on)."""
import json
import os
import re
from pathlib import Path

import requests


def adf_doc(rows, note=None):
    """Atlassian Document Format: a two-column table of (field, value) plus an optional note."""
    def cell(text, header=False):
        return {"type": "tableHeader" if header else "tableCell",
                "content": [{"type": "paragraph",
                             "content": [{"type": "text", "text": str(text) or "-"}]}]}
    table = {"type": "table", "attrs": {"isNumberColumnEnabled": False, "layout": "default"},
             "content": [{"type": "tableRow", "content": [cell(k, True), cell(v)]} for k, v in rows]}
    content = [table]
    if note:
        content.append({"type": "paragraph", "content": [{"type": "text", "text": note}]})
    return {"type": "doc", "version": 1, "content": content}


class JiraError(RuntimeError):
    pass


class Jira:
    def __init__(self, site, email, token, project):
        site = site.strip().rstrip("/")
        if not site.startswith("http"):
            site = "https://" + site
        self.site, self.project = site, project
        self.s = requests.Session()
        self.s.auth = (email, token)
        self.s.headers.update({"Accept": "application/json"})
        self._types = None

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
        return p["name"], p.get("style", "")

    def issue_type_id(self, name):
        if self._types is None:
            data = self._req("GET", f"/rest/api/3/issue/createmeta/{self.project}/issuetypes")
            self._types = {t["name"].lower(): t["id"] for t in data.get("issueTypes", data.get("values", []))}
        for n in [name.lower()] + {"subtask": ["sub-task"], "sub-task": ["subtask"]}.get(name.lower(), []):
            if n in self._types:
                return self._types[n]
        raise JiraError(f"Issue type '{name}' not in project {self.project}: {sorted(self._types)}")

    def search(self, jql, fields=("summary", "labels", "duedate", "parent")):
        out, token = [], None
        while True:
            body = {"jql": jql, "fields": list(fields), "maxResults": 100}
            if token:
                body["nextPageToken"] = token
            d = self._req("POST", "/rest/api/3/search/jql", json=body)
            out += d.get("issues", [])
            token = d.get("nextPageToken")
            if not token or d.get("isLast", True):
                return out

    def create(self, issuetype, summary, description, labels, duedate=None, parent=None):
        f = {"project": {"key": self.project}, "issuetype": {"id": self.issue_type_id(issuetype)},
             "summary": summary, "description": description, "labels": labels}
        if duedate:
            f["duedate"] = duedate
        if parent:
            f["parent"] = {"key": parent}
        return self._req("POST", "/rest/api/3/issue", json={"fields": f})["key"]

    def attach(self, key, path):
        with open(path, "rb") as fh:
            self._req("POST", f"/rest/api/3/issue/{key}/attachments",
                      headers={"X-Atlassian-Token": "no-check"},
                      files={"file": (Path(path).name, fh, "image/png")})


class FakeJira:
    """Dry-run stand-in that keeps issues in a local JSON file, so duplicate checks work."""
    def __init__(self, store, project="LAB", site="https://example.atlassian.net"):
        self.store, self.project, self.site = Path(store), project, site
        self.issues = json.loads(self.store.read_text()) if self.store.exists() else []

    def _save(self):
        self.store.write_text(json.dumps(self.issues, indent=1))

    def browse_url(self, key):
        return f"{self.site}/browse/{key}"

    def whoami(self):
        return "dry-run"

    def check_project(self):
        return "Lab Equipment (dry run)", "next-gen"

    def search(self, jql, fields=None):
        labels = re.findall(r'labels\s*=\s*"([^"]+)"', jql)
        keys = re.findall(r'key\s*=\s*"([^"]+)"', jql)
        itype = re.search(r'issuetype\s*=\s*(\w+)', jql)
        hits = [i for i in self.issues if all(l in i["fields"]["labels"] for l in labels)
                and (not keys or i["key"] in keys)
                and (not itype or i["fields"]["issuetype"].lower() == itype.group(1).lower())]
        if "ORDER BY duedate" in jql:
            hits.sort(key=lambda i: i["fields"].get("duedate") or "9999")
        return hits

    def create(self, issuetype, summary, description, labels, duedate=None, parent=None):
        key = f"{self.project}-{len(self.issues) + 1}"
        self.issues.append({"key": key, "fields": {"issuetype": issuetype, "summary": summary,
                            "labels": labels, "duedate": duedate,
                            "parent": {"key": parent} if parent else None,
                            "description": description}})
        self._save()
        return key

    def attach(self, key, path):
        pass


def from_env(dry_run=False, store="dryrun_jira.json"):
    project = os.environ.get("JIRA_PROJECT", "LAB")
    if dry_run:
        return FakeJira(store, project)
    missing = [k for k in ("JIRA_SITE", "JIRA_EMAIL", "JIRA_API_TOKEN") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"Missing in .env: {', '.join(missing)} (see .env.example), or use --dry-run")
    return Jira(os.environ["JIRA_SITE"], os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"], project)
