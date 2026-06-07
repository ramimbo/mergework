# PR Review Guide

This guide helps contributors review open MergeWork pull requests with concrete evidence for MRWK bounties.

## Review Criteria

1. **Correctness**: Does the PR solve the issue described? Verify by running tests or checking logic.
2. **Completeness**: Are all required changes included (code, tests, docs)?
3. **Style**: Does the code follow the project's style (ruff, mypy)? Check with `ruff format --check .` and `mypy app`.
4. **Evidence**: Provide screenshots, logs, or test output showing the PR works.

## Steps for a Review

1. Check out the PR branch locally.
2. Run existing tests (`pytest`) and any new tests added.
3. If the PR adds a feature or bugfix, manually verify the behavior.
4. Leave a review comment on the PR with your findings and attach evidence (screenshots, test results).
5. For bounty claims, ensure the PR author has followed the [contribution guidelines](../CONTRIBUTING.md).

## Evidence Examples

- Screenshot of a successfully rendered page after changes.
- Log output showing a previously broken API now returns 200.
- Test suite output showing all tests pass.
- Code diff analysis highlighting no unrelated changes.

> **Remember**: The maintainer will verify your review before awarding the bounty. Clear, reproducible evidence is key.
