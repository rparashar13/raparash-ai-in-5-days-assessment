# TDD Implementation Plan: `redeem_discount` & `award_loyalty_points`

Complies with `.agents/CONTEXT.md` (**Core Paved Roads** & **TDD Planning Gate**).

## Stage 1: Strict Schema & Tool Implementation
1. Define `DiscountRequest` Pydantic schema (`code`, `user_id` with length constraints and normalization).
2. Implement `redeem_discount(code: str, user_id: str) -> str` enforcing registered-user verification, code existence, and single-use state mutation.
3. Configure `Gemini` model without hardcoded credentials (enforced via `.semgrep/rules.yaml` pre-commit hook).

## Security Boundaries & Assertions
Per the **TDD Planning Gate** in `.agents/CONTEXT.md`, every tool must pass the following outcome-based security assertions prior to commit:
- **Single-Use Replay Boundary**: Redeeming `WELCOME50` once for `user_123` mutates `DISCOUNT_STORE["WELCOME50"] = True`; a second call by `user_456` MUST fail with `"Error: Discount code has already been redeemed."` and leave state unchanged.
- **Code Enumeration / Unknown Code Boundary**: Submitting `"INVALID999"` MUST return `"Error: Invalid discount code."` without leaking valid keys from `DISCOUNT_STORE`.
- **Guest Account Privilege Escalation Boundary**: Submitting `"guest_999"`, `"GUEST_ADMIN"`, or `""` MUST return `"Error: Registered user account required to redeem discounts."` and MUST NOT mutate `DISCOUNT_STORE["SUMMER20"]`.
- **Static Credential Boundary**: `semgrep --error --config .semgrep/rules.yaml` MUST return exit code `0` with zero `AIzaSy*` matches before `git commit` succeeds.
