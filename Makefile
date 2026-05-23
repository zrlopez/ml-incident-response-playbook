.PHONY: lint test docs

lint:
	python -m compileall src pipelines validation observability api

test:
	pytest -q

docs:
	python -m http.server 8000 -d .
