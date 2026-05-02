#!/usr/bin/env bash
set -euo pipefail
export CODEGRAPH_DB_PATH=${CODEGRAPH_DB_PATH:-./data/codegraph.db}
export CODEGRAPH_REPO_ROOT=${CODEGRAPH_REPO_ROOT:-./examples}
mkdir -p data
codegraph repo add --name sample_repo --path ./examples/sample_repo
codegraph index --repo sample_repo --json
codegraph overview --repo sample_repo --json
codegraph prepare-change --repo sample_repo --task "modify create user validation" --json
