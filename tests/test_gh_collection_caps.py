from scripts import gh_collection_caps as gcc


def test_gh_collection_caps_are_positive() -> None:
    assert gcc.GH_PR_SAFETY_CAP >= 1
    assert gcc.GH_ISSUE_SAFETY_CAP >= 1
