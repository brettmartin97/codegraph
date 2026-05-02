"""Tests for runtime parsers: Dockerfile, docker-compose, K8s YAML, GitHub Actions."""
from __future__ import annotations

from pathlib import Path

from codegraph_mcp.analyzers.base import SourceFile
from codegraph_mcp.analyzers.runtime import RuntimeAnalyzer
from codegraph_mcp.utils import stable_id


def _source(text, language, path="test_file", name="test_file"):
    return SourceFile(
        repo_id="repo1",
        file_id=stable_id("repo1", path),
        path=Path(path),
        relative_path=path,
        language=language,
        text=text,
    )


analyzer = RuntimeAnalyzer()


def test_dockerfile_cmd():
    src = _source('FROM python:3.11\nCMD ["python", "app.py"]\n', "dockerfile", "Dockerfile")
    result = analyzer.analyze(src)
    kinds = {rb.kind for rb in result.runtime_bindings}
    assert "cmd" in kinds


def test_dockerfile_entrypoint():
    src = _source('FROM python:3.11\nENTRYPOINT ["uvicorn", "app:app"]\n', "dockerfile", "Dockerfile")
    result = analyzer.analyze(src)
    kinds = {rb.kind for rb in result.runtime_bindings}
    assert "entrypoint" in kinds


def test_dockerfile_expose():
    src = _source("FROM python:3.11\nEXPOSE 8080\n", "dockerfile", "Dockerfile")
    result = analyzer.analyze(src)
    kinds = {rb.kind for rb in result.runtime_bindings}
    assert "expose" in kinds


def test_docker_compose_services():
    yaml_text = """
services:
  web:
    image: myapp:latest
    command: uvicorn app:app --host 0.0.0.0
  db:
    image: postgres:15
"""
    src = _source(yaml_text, "compose", "docker-compose.yml")
    result = analyzer.analyze(src)
    names = {rb.name for rb in result.runtime_bindings}
    assert "web" in names
    assert "db" in names


def test_k8s_deployment():
    yaml_text = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
spec:
  template:
    spec:
      containers:
      - name: app
        image: myapp:1.0
"""
    src = _source(yaml_text, "yaml", "k8s/deployment.yaml")
    result = analyzer.analyze(src)
    kinds = {rb.kind for rb in result.runtime_bindings}
    assert any("deployment" in k for k in kinds)


def test_github_actions_job():
    yaml_text = """
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
  build:
    runs-on: ubuntu-latest
    steps:
      - run: make build
"""
    src = _source(yaml_text, "yaml", ".github/workflows/ci.yml")
    result = analyzer.analyze(src)
    names = {rb.name for rb in result.runtime_bindings}
    assert "test" in names
    assert "build" in names


def test_malformed_yaml_does_not_crash():
    src = _source("{ invalid yaml: [", "yaml", "bad.yaml")
    result = analyzer.analyze(src)
    assert len(result.diagnostics) > 0
