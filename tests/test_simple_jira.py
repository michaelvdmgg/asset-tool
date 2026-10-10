import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jira_client import SimpleJira, adf_with_data, data_from_adf  # noqa: E402


class Recorder(SimpleJira):
    """SimpleJira with HTTP replaced by an in-memory issue store."""
    def __init__(self):
        self.project, self.site, self.issues, self._types = "LAB", "https://x", {}, {"Task": "1", "Subtask": "2"}

    def _req(self, method, path, **kw):
        if method == "POST" and path.endswith("/issue"):
            key = f"LAB-{len(self.issues) + 1}"
            self.issues[key] = kw["json"]["fields"]
            return {"key": key}
        if method == "GET":
            return {"fields": self.issues[path.split("/")[-1].split("?")[0]]}
        if method == "PUT":
            self.issues[path.split("/")[-1]].update(kw["json"]["fields"])
            return {}
        if method == "POST" and path.endswith("/search/jql"):
            assert '"Task"' in kw["json"]["jql"] or "issuetype" not in kw["json"]["jql"]
            return {"issues": [{"key": k, "fields": v} for k, v in self.issues.items()], "isLast": True}
        raise AssertionError(path)


def test_roundtrip_fields_through_description():
    j = Recorder()
    k = j.create("Equipment", "PT", {"Equipment ID": "GG-EQ-000002", "Model": "PX309", "Notes": None},
                 ["equipment"], description="hello")
    assert j.issues[k]["issuetype"] == {"id": "1"}
    j.update(k, {"Calibration Status": "Valid"}, "2027-01-01")
    got = j.search('project = "LAB" AND issuetype = "Equipment"', ["summary", "duedate", "Model", "Calibration Status"])
    assert got[0]["fields"] == {"summary": "PT", "duedate": "2027-01-01", "Model": "PX309", "Calibration Status": "Valid"}


def test_adf_data_block():
    assert data_from_adf(adf_with_data({"A": "1"}, "note")) == {"A": "1"}
    assert data_from_adf(None) == {}
