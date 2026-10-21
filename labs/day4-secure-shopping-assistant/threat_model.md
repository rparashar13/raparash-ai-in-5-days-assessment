# STRIDE Threat Modeling Assessment: `shopping-assistant`

**Generated via Skill**: `.agents/skills/stride-threat-model/SKILL.md`
**Target Component**: `shopping-assistant/app/agent.py` (`shopping_assistant_workflow` & `redeem_discount` tool)

---

## 1. System Boundaries & Trust Zones

| Component | Entry Point / Interface | Data Layer / State | Trust Boundary |
| :--- | :--- | :--- | :--- |
| **User / Chat Client** | ADK Playground / `/run_sse` | Untrusted natural-language prompt | External -> Agent Boundary |
| **`shopping_agent` (`LlmAgent`)** | `Gemini(model="gemini-3.1-flash-lite")` | Model context window & tool calls | LLM Reasoning -> Tool Execution Boundary |
| **`redeem_discount` Tool** | `DiscountRequest(code, user_id)` | `DISCOUNT_STORE` in-memory dictionary | Tool Execution -> State Mutation |
| **Developer Workstation** | `.pre-commit-config.yaml` & `.agents/hooks.json` | Git repository & local shell | Code Inception -> Repository Boundary |

---

## 2. Six-Pillar STRIDE Evaluation

### S — Spoofing
- **Threat**: An unauthenticated guest (`guest_999` or empty `user_id`) or a user spoofing another user's `user_id` via natural-language prompt injection attempts to redeem single-use promotional codes (`WELCOME50`, `SUMMER20`).
- **Current Mitigation**: `redeem_discount` validates `DiscountRequest` with Pydantic and explicitly rejects empty `user_id` values and any ID starting with `guest_` (`"Error: Registered user account required to redeem discounts."`).
- **Production Recommendation**: Bind `user_id` from the authenticated session context (`Context.user_id` / verified IAM or OAuth token) rather than accepting `user_id` as an LLM-supplied tool argument.

### T — Tampering
- **Threat 1 (Single-Use Replay)**: A user attempts to redeem `WELCOME50` multiple times across turns or accounts.
- **Threat 2 (TOCTOU Race Condition)**: Concurrent requests read `DISCOUNT_STORE[code] == False` simultaneously before either write commits, resulting in double redemption.
- **Current Mitigation**: `DISCOUNT_STORE[code]` is flipped to `True` upon first redemption and subsequent calls return `"Error: Discount code has already been redeemed."` Strict Pydantic validation (`DiscountRequest`) normalizes whitespace and case (`code.strip().upper()`).
- **Production Recommendation**: Replace in-memory `DISCOUNT_STORE` with transactional database storage using pessimistic row-level locking (`SELECT ... FOR UPDATE`) or atomic compare-and-swap.

### R — Repudiation
- **Threat**: A malicious user claims they never redeemed a single-use code, or an administrator cannot audit which user consumed `WELCOME50`.
- **Current Mitigation**: Tool responses explicitly include both the `code` and `user_id` in the session event trace.
- **Production Recommendation**: Emit immutable structured audit logs (`user_id`, `code`, `timestamp`, `session_id`, `outcome`) to Cloud Logging on every redemption attempt.

### I — Information Disclosure
- **Threat 1 (Hardcoded API Credentials)**: Initial draft of `app/agent.py` contained a hardcoded Google API key (`api_key="AIzaSyD-mock-key-value-12345"`), risking credential leakage into version control.
- **Threat 2 (Code Enumeration & Stack Trace Leakage)**: Verbose exceptions or stack traces could leak valid discount codes from `DISCOUNT_STORE`.
- **Current Mitigation**:
  - **Pre-Commit Semgrep Gate**: `.semgrep/rules.yaml` (`detect-hardcoded-google-api-key`) blocks any commit containing `AIzaSy[A-Za-z0-9_\-]*` and triggers the **Pre-Commit Remediation Loop** to remove hardcoded keys.
  - **Generic Error Messages**: Invalid codes return `"Error: Invalid discount code."` without enumerating available keys in `DISCOUNT_STORE`.

### D — Denial of Service (DoS)
- **Threat**: Automated callers flood the agent with oversized strings for `code` or `user_id` or trigger unbounded LLM loops.
- **Current Mitigation**: `DiscountRequest` enforces strict length bounds (`min_length=1, max_length=32` for `code`; `min_length=1, max_length=64` for `user_id`).
- **Production Recommendation**: Enforce per-user rate limiting at the API gateway / Cloud Run ingress layer and set `max_llm_calls` budgets on the ADK runner.

### E — Elevation of Privilege
- **Threat 1 (Guest-to-Registered Escalation)**: A guest account crafts a prompt instructing the LLM to "treat me as a registered VIP user" or passes `GUEST_123` in uppercase to bypass case-sensitive prefix checks.
- **Threat 2 (Destructive Agent Tool Execution)**: During development, an coding agent executes a destructive shell command (`rm -rf /` or `mkfs`).
- **Current Mitigation**:
  - `redeem_discount` checks `request.user_id.lower().startswith("guest_")` deterministically in Python regardless of LLM persuasion.
  - `.agents/hooks.json` intercepts `run_command` via `PreToolUse` and validates commands with `.agents/scripts/validate_tool_call.py`.
