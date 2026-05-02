from __future__ import annotations

from pathlib import Path

from codegraph_mcp import config
from codegraph_mcp.graph.sqlite_store import SQLiteStore
from codegraph_mcp.indexing.indexer import Indexer
from codegraph_mcp.utils import detect_language


def _setup(tmp_path, monkeypatch):
    repo_root = tmp_path / "repos"
    repo_root.mkdir()
    monkeypatch.setattr(config.settings, "repo_root", repo_root)
    store = SQLiteStore(tmp_path / "cg.db")
    return store, Indexer(store), repo_root


def test_detect_language_recognizes_infra_config_paths():
    assert detect_language(Path("Dockerfile")) == "dockerfile"
    assert detect_language(Path("docker-compose.yaml")) == "compose"
    assert detect_language(Path("k8s/deployment.yaml")) == "kubernetes"
    assert detect_language(Path("charts/api/Chart.yaml")) == "helm"
    assert detect_language(Path("charts/api/templates/deployment.yaml")) == "helm"
    assert detect_language(Path("charts/api/templates/_helpers.tpl")) == "helm"
    assert detect_language(Path("charts/api/templates/config.yaml.gotmpl")) == "helm"
    assert detect_language(Path("config/settings.json")) == "json"


def test_indexer_runs_config_addon_alongside_runtime_bindings(tmp_path, monkeypatch):
    store, indexer, repo_root = _setup(tmp_path, monkeypatch)
    repo_dir = repo_root / "infra"
    (repo_dir / "k8s").mkdir(parents=True)
    (repo_dir / "charts" / "api" / "templates").mkdir(parents=True)
    (repo_dir / "config").mkdir()

    (repo_dir / "Dockerfile").write_text(
        "FROM python:3.12-slim AS runtime\n"
        "EXPOSE 8080\n"
        'CMD ["python", "app.py"]\n',
        encoding="utf-8",
    )
    (repo_dir / "docker-compose.yml").write_text(
        "services:\n"
        "  web:\n"
        "    build: .\n"
        "    command: python app.py\n"
        "  db:\n"
        "    image: postgres:16\n",
        encoding="utf-8",
    )
    (repo_dir / "k8s" / "deployment.yaml").write_text(
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n"
        "  name: api\n"
        "spec:\n"
        "  template:\n"
        "    spec:\n"
        "      containers:\n"
        "      - name: api\n"
        "        image: example/api:1.0\n",
        encoding="utf-8",
    )
    (repo_dir / "charts" / "api" / "Chart.yaml").write_text(
        "apiVersion: v2\n"
        "name: api\n"
        "version: 0.1.0\n",
        encoding="utf-8",
    )
    (repo_dir / "charts" / "api" / "templates" / "service.yaml").write_text(
        "apiVersion: v1\n"
        "kind: Service\n"
        "metadata:\n"
        '  name: {{ include "api.fullname" . }}\n'
        "spec:\n"
        "  ports:\n"
        "  - port: 80\n",
        encoding="utf-8",
    )
    (repo_dir / "config" / "settings.json").write_text(
        '{"name":"api","featureFlags":{"checkout":true}}',
        encoding="utf-8",
    )

    indexer.add_repo("infra", repo_dir)
    result = indexer.index_repo("infra")

    overview = store.overview(store.get_repo("infra").id)
    runtime = store.runtime_bindings(store.get_repo("infra").id)
    runtime_kinds = {binding.kind for binding in runtime}
    function_names = {
        row["qualified_name"]
        for row in store.conn.execute("SELECT qualified_name FROM functions").fetchall()
    }

    assert result["files_seen"] == 6
    assert result["functions_seen"] >= 6
    assert result["runtime_bindings_seen"] >= 5
    assert overview["languages"]["dockerfile"] == 1
    assert overview["languages"]["compose"] == 1
    assert overview["languages"]["kubernetes"] == 1
    assert overview["languages"]["helm"] == 2
    assert overview["languages"]["json"] == 1
    assert "cmd" in runtime_kinds
    assert "compose_service" in runtime_kinds
    assert "k8s_deployment" in runtime_kinds
    assert "k8s_service" in runtime_kinds
    assert any(name.endswith(".api") for name in function_names)
    assert any("Service." in name for name in function_names)
    assert any("DockerStage.runtime" == name for name in function_names)


def test_docker_env_variables_link_to_consuming_instructions(tmp_path, monkeypatch):
    store, indexer, repo_root = _setup(tmp_path, monkeypatch)
    repo_dir = repo_root / "docker-env"
    repo_dir.mkdir()
    (repo_dir / "Dockerfile").write_text(
        "FROM python:3.12-slim AS runtime\n"
        "ENV APP_HOME=/srv/app PATH=\"$APP_HOME/bin:$PATH\"\n"
        "WORKDIR $APP_HOME\n"
        "RUN echo \"$APP_HOME\"\n"
        'CMD python "$APP_HOME/app.py"\n',
        encoding="utf-8",
    )

    indexer.add_repo("docker-env", repo_dir)
    result = indexer.index_repo("docker-env")
    repo = store.get_repo("docker-env")
    assert repo is not None

    app_home = store.get_function_by_name(repo.id, "DockerEnv.APP_HOME")
    assert app_home is not None
    assert app_home.kind == "docker_env"
    assert app_home.start_line == 2
    assert result["edges_seen"] >= 3

    callees = {fn.qualified_name for fn in store.callees(app_home.id)}
    assert "DockerInstruction.WORKDIR:3" in callees
    assert "DockerInstruction.RUN:4" in callees
    assert "DockerInstruction.CMD:5" in callees


def test_package_json_extracts_scripts_and_dependencies(tmp_path, monkeypatch):
    store, indexer, repo_root = _setup(tmp_path, monkeypatch)
    repo_dir = repo_root / "json-package"
    repo_dir.mkdir()
    (repo_dir / "package.json").write_text(
        """{
  "name": "api",
  "scripts": {
    "start": "node server.js",
    "test": "vitest run"
  },
  "dependencies": {
    "express": "^5.0.0"
  },
  "devDependencies": {
    "vitest": "^2.0.0"
  }
}
""",
        encoding="utf-8",
    )

    indexer.add_repo("json-package", repo_dir)
    result = indexer.index_repo("json-package")
    repo = store.get_repo("json-package")
    assert repo is not None

    assert result["functions_seen"] == 5
    package = store.get_function_by_name(repo.id, "PackageJson.api")
    start = store.get_function_by_name(repo.id, "NpmScript.start")
    express = store.get_function_by_name(repo.id, "NpmDependency.express")
    assert package is not None
    assert start is not None
    assert start.kind == "json_script"
    assert express is not None
    assert express.kind == "json_dependency"

    callees = {fn.qualified_name for fn in store.callees(package.id)}
    assert {"NpmScript.start", "NpmScript.test", "NpmDependency.express", "NpmDependency.vitest"} <= callees
