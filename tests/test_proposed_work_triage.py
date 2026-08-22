--- /dev/null
+++ b/tests/test_proposed_work_triage.py
@@ -0,0 +1,61 @@
+"""Unit tests for proposed-work triage bounty filtering (issue #803)."""
+
+from scripts.proposed_work_triage import (
+    classify_phrase_hit,
+    looks_like_intake_template,
+)
+
+
+def _issue(title="", body="", labels=None):
+    return {"number": 1, "title": title, "body": body, "labels": labels or []}
+
+
+def test_bounty_title_prefix_excluded():
+    issue = _issue(
+        title="MRWK bounty: 50 MRWK - accepted proposed-work requests, round 2"
+    )
+    keep, reason = classify_phrase_hit(issue)
+    assert keep is False
+    assert reason == "bounty_title_prefix"
+
+
+def test_bounty_label_excluded():
+    issue = _issue(title="Notes about proposed work", labels=[{"name": "mrwk:bounty"}])
+    keep, reason = classify_phrase_hit(issue)
+    assert keep is False
+    assert reason == "bounty_label"
+
+
+def test_bounty_lifecycle_text_excluded():
+    body = "Do not submit implementation work until this bounty is reserved."
+    issue = _issue(title="Accepted proposed-work requests", body=body)
+    keep, reason = classify_phrase_hit(issue)
+    assert keep is False
+    assert reason == "bounty_lifecycle_text"
+
+
+def test_template_shaped_unlabeled_intake_kept():
+    body = "## Problem\n\nSomething\n\n## Proposed work\n\nDo the thing."
+    issue = _issue(title="Add CLI export for claims", body=body)
+    keep, reason = classify_phrase_hit(issue)
+    assert keep is True
+    assert reason is None
+    assert looks_like_intake_template(body) is True
+
+
+def test_template_beats_weak_lifecycle_marker():
+    body = (
+        "## Problem\n\nn/a\n\n## Proposed work\n\nSee plan.\n\n"
+        "Note: do not submit implementation work before approval."
+    )
+    issue = _issue(title="Proposed work: weekly digest", body=body)
+    keep, reason = classify_phrase_hit(issue)
+    assert keep is True
+    assert reason is None
+
+
+def test_plain_unlabeled_issue_still_analyzed():
+    issue = _issue(title="proposed work idea", body="short note")
+    keep, reason = classify_phrase_hit(issue)
+    assert keep is True
+    assert reason is None