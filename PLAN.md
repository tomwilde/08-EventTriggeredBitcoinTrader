# Acceptance-First Delivery Plan (Project-Agnostic)

## Summary

This repository is currently spec-only (no `app.py`, `src/`, or `tests/`).
The first milestone is to make end-to-end acceptance tests executable and mandatory for sign-off, before broader feature buildout.

This plan avoids product-specific naming and can be reused for any Streamlit + Python workflow project.

## Ordered Ticket Checklist

### P0 — Foundation and test harness

- [ ] **P0.1 Bootstrap Python project skeleton**
  - Create `pyproject.toml` with runtime + test tool configuration.
  - Create minimal `app.py`, `src/` package, and `tests/` directory.
  - Add pytest configuration and acceptance marker registration.

- [ ] **P0.2 Define deterministic scenario fixture contract**
  - Create a scenario catalog with stable `scenario_id` keys.
  - Define fixture fields: input text, selected options/scopes, mocked backend responses, expected trace checkpoints, expected outcome summary.
  - Keep fixtures deterministic and offline-safe.

- [ ] **P0.3 Add acceptance test execution contract**
  - Define canonical commands in docs and Makefile (`full`, `acceptance-only`).
  - Ensure acceptance tests are clearly separated via marker (e.g. `acceptance`).

### P1 — RED: acceptance tests first

- [ ] **P1.1 Write failing AppTest acceptance suite**
  - Add `tests/test_acceptance_e2e.py` using `streamlit.testing.v1.AppTest`.
  - For each baseline scenario: load scenario, run flow, assert trace checkpoints, assert outcome panel.

- [ ] **P1.2 Add UI control integrity acceptance tests**
  - Validate scenario select/load behavior.
  - Validate reset-to-defaults behavior.
  - Validate that run uses currently loaded scenario fixture.

- [ ] **P1.3 Prove RED state**
  - Run `pytest -m acceptance -v` and capture failing output before implementation.

### P2 — GREEN: minimal implementation to satisfy acceptance

- [ ] **P2.1 Implement thin Streamlit UI wiring**
  - Add scenario selector, load/reset controls, run action, trace display, and outcome display.
  - Keep UI logic thin; delegate behavior to `src/`.

- [ ] **P2.2 Implement deterministic backend execution path**
  - Execute loaded scenario fixtures and produce expected trace/outcome artifacts.
  - Support denial/degraded states defined by fixtures.

- [ ] **P2.3 Turn acceptance suite green**
  - Run targeted tests until acceptance tests pass.
  - Run full suite (`pytest tests/ -v`).

### P3 — QA gate and repeatability

- [ ] **P3.1 Add final QA command path**
  - Add Makefile targets for `test`, `acceptance`, and optional `lint`/`type`.
  - Document mandatory local sign-off sequence in `README.md`.

- [ ] **P3.2 Record acceptance evidence**
  - Include command output summary showing acceptance suite pass.
  - Include scenario coverage summary.

## Acceptance Matrix

The detailed acceptance coverage matrix is maintained in `acceptance-tests.md`.

## Target Commands (when scaffold exists)

- Install deps: `uv sync`
- Full suite: `pytest tests/ -v`
- Acceptance only: `pytest -m acceptance -v`
- App-focused tests: `pytest tests/test_app.py -v`
- Lint/type: `ruff check . && ty check`

## Definition of Done

- Acceptance tests exist as executable pytest/AppTest tests.
- At least one RED run is captured before implementation, then GREEN after implementation.
- Acceptance suite is part of standard QA/sign-off commands.
- Documentation clearly states how to run and verify acceptance locally.
