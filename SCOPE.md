# Project Scope Definition (`SCOPE.md`)

**Project**: Ambient Expense Agent & GEAP L200 5-Day Reference Architecture  
**Owner**: `raparash@google.com`  
**Target Environment**: Google Cloud Project `raparash-l200-sandbox-297625` (`us-central1`)

---

## 1. Business Problem & Objectives
Enterprise finance teams process thousands of daily employee expense submissions. Purely manual review creates approval bottlenecks for low-risk receipts, while purely autonomous LLM processing exposes the enterprise to **prompt injection**, **PII leakage** (SSNs, credit card numbers), and unauthorized high-value payouts.

This project delivers a production-grade **Hybrid Deterministic + Agentic Graph Workflow** using **Google ADK 2.0** and **Vertex AI Agent Runtime** that:
1. **Instantly auto-approves** low-value expenses (`< $100.00`) deterministically in Python with zero LLM cost.
2. **Sanitizes and screens** high-value expenses (`>= $100.00`) for PII and adversarial prompt injection *before* any LLM invocation.
3. **Evaluates clean high-value claims** with a structured `Gemini` risk reviewer (`review_agent`) and pauses execution via ADK `RequestInput` (`adk_request_input`) for manager sign-off in a Cloud Run Human-in-the-Loop (HITL) dashboard.

## 2. In-Scope Deliverables (5 Days of AI Curriculum)
- **Day 1 (`labs/day1-customer-support-agent/`)**: Multi-tool ADK Customer Support Agent scaffolded with `agents-cli`.
- **Day 2 (`labs/day2-pantry-planner/`)**: Spec-Driven Full-Stack Pantry Planner (`pantry-agent` + FastAPI/SQLite web application).
- **Day 3 (`expense_agent/`, `tests/eval/`, `artifacts/`)**: ADK 2.0 Graph-Based Ambient Expense Agent with 3-pillar LLM-as-a-Judge evaluation suite (`routing_correctness`, `security_containment`, `response_quality`).
- **Day 4 (`.agents/`, `.semgrep/`, `threat_model.md`, `labs/day4-secure-shopping-assistant/`)**: Context Engineering (`CONTEXT.md`), PreToolUse Hooks (`hooks.json`), Custom STRIDE Skill (`SKILL.md`), Semgrep SAST rules, and TDD unit test suite.
- **Day 5 (`deployment_metadata.json`, `submission_frontend/`, `deployment/terraform/`)**: Production deployment to **Vertex AI Agent Runtime** (`7543234981607243776`), **Cloud Run** (`expense-manager-dashboard`), and **Cloud Pub/Sub** (`expense-reports-push` with DLQ).

## 3. Acceptance Criteria & SLOs
- **Routing Accuracy**: `1.00` (100% deterministic auto-approval under `$100.00`; 100% escalation at or above `$100.00`).
- **Security Containment**: `1.00` (0% PII leakage to LLM context; 100% prompt-injection bypass of `review_agent` straight to `human_review`).
- **Test & SAST Gate**: 100% pytest pass rate and 0 unresolved Semgrep findings.
