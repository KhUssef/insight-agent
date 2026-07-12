"""Shared fixtures: a temporary data directory and an open DuckDB connection."""

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from insight_agent.data import connect, ensure_sample_data


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("data")
    ensure_sample_data(path)
    return path


@pytest.fixture()
def con(data_dir: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    connection = connect(data_dir)
    yield connection
    connection.close()
