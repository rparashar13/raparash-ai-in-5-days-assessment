.PHONY: install playground generate-traces grade test

PORT ?= 8080

install:
	uv sync

playground:
	uv run --no-sync uvicorn expense_agent.fast_api_app:app --host 0.0.0.0 --port $(PORT)

generate-traces:
	uv run --no-sync python tests/eval/generate_traces.py

grade:
	agents-cli eval grade --traces artifacts/traces/generated_traces.json --config tests/eval/eval_config.yaml --output artifacts/grade_results

test:
	uv run --no-sync pytest tests/unit
