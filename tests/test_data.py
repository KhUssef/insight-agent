"""The data layer: deterministic generation and multi-table DuckDB loading."""

from pathlib import Path

import duckdb

from insight_agent.data import (
    connect,
    ensure_sample_data,
    generate_region_targets,
    generate_sample_dataset,
)


def test_generation_is_deterministic(tmp_path: Path) -> None:
    a = generate_sample_dataset(tmp_path / "a.csv").read_text(encoding="utf-8")
    b = generate_sample_dataset(tmp_path / "b.csv").read_text(encoding="utf-8")
    assert a == b


def test_region_targets_generation_is_deterministic(tmp_path: Path) -> None:
    sales_path = generate_sample_dataset(tmp_path / "sales.csv")
    a = generate_region_targets(tmp_path / "a.csv", sales_path).read_text(encoding="utf-8")
    b = generate_region_targets(tmp_path / "b.csv", sales_path).read_text(encoding="utf-8")
    assert a == b


def test_dataset_loads_into_duckdb(con: duckdb.DuckDBPyConnection) -> None:
    row = con.execute("SELECT count(*) FROM sample_sales").fetchone()
    assert row is not None
    assert row[0] > 3000
    columns = {
        name
        for (name,) in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'sample_sales'"
        ).fetchall()
    }
    assert columns == {
        "order_id",
        "order_date",
        "region",
        "category",
        "product",
        "units",
        "unit_price",
        "revenue",
        "customer_id",
        "product_id",
    }


def test_west_drops_most_in_q3(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute(
        """
        WITH quarterly AS (
            SELECT region,
                   sum(CASE WHEN month(order_date) IN (4, 5, 6) THEN revenue ELSE 0 END) AS q2,
                   sum(CASE WHEN month(order_date) IN (7, 8, 9) THEN revenue ELSE 0 END) AS q3
            FROM sample_sales GROUP BY region
        )
        SELECT region FROM quarterly ORDER BY (q3 - q2) ASC LIMIT 1
        """
    ).fetchall()
    assert rows[0][0] == "West"


def test_connect_exposes_all_sample_tables(con: duckdb.DuckDBPyConnection) -> None:
    table_names = {
        row[0]
        for row in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    }
    assert {
        "sample_sales",
        "region_targets",
        "products",
        "customers",
        "marketing_spend",
        "returns",
    } <= table_names


def test_join_across_sales_and_targets_through_run_sql(con: duckdb.DuckDBPyConnection) -> None:
    from insight_agent import tools

    result = tools.run_sql(
        con,
        "SELECT s.region, sum(s.revenue) AS actual, t.revenue_target "
        "FROM sample_sales s JOIN region_targets t ON s.region = t.region "
        "GROUP BY s.region, t.revenue_target ORDER BY s.region",
    )
    assert result["columns"] == ["region", "actual", "revenue_target"]
    assert result["row_count"] == 4


def test_west_misses_target_other_regions_exceed_it(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute(
        "SELECT s.region, sum(s.revenue) AS actual, t.revenue_target "
        "FROM sample_sales s JOIN region_targets t ON s.region = t.region "
        "GROUP BY s.region, t.revenue_target"
    ).fetchall()
    actual_by_region = {region: (actual, target) for region, actual, target in rows}
    assert len(actual_by_region) == 4
    west_actual, west_target = actual_by_region["West"]
    assert west_actual < west_target
    for region, (actual, target) in actual_by_region.items():
        if region != "West":
            assert actual > target


def test_ensure_sample_data_does_not_touch_user_provided_directory(tmp_path: Path) -> None:
    user_dir = tmp_path / "user_data"
    user_dir.mkdir()
    existing = user_dir / "my_own_data.csv"
    existing.write_text("a,b\n1,2\n", encoding="utf-8")

    ensure_sample_data(user_dir)

    assert {p.name for p in user_dir.iterdir()} == {"my_own_data.csv"}


def test_connect_on_user_provided_directory_does_not_generate_samples(tmp_path: Path) -> None:
    user_dir = tmp_path / "user_data"
    user_dir.mkdir()
    (user_dir / "my_own_data.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    connection = connect(user_dir)
    try:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }
        assert table_names == {"my_own_data"}
    finally:
        connection.close()


def test_connect_on_missing_directory_generates_sample_data(tmp_path: Path) -> None:
    fresh_dir = tmp_path / "does_not_exist_yet"
    connection = connect(fresh_dir)
    try:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }
        assert {
            "sample_sales",
            "region_targets",
            "products",
            "customers",
            "marketing_spend",
            "returns",
        } <= table_names
    finally:
        connection.close()
    assert (fresh_dir / "sample_sales.csv").exists()


def test_connect_on_empty_existing_directory_generates_sample_data(tmp_path: Path) -> None:
    empty_but_existing = tmp_path / "empty"
    empty_but_existing.mkdir()
    connection = connect(empty_but_existing)
    try:
        row = connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchone()
        assert row is not None
        assert row[0] == 6
    finally:
        connection.close()


def test_every_sales_customer_id_exists_in_customers(con: duckdb.DuckDBPyConnection) -> None:
    row = con.execute(
        "SELECT count(*) FROM sample_sales s "
        "LEFT JOIN customers c ON s.customer_id = c.customer_id "
        "WHERE c.customer_id IS NULL"
    ).fetchone()
    assert row is not None
    assert row[0] == 0


def test_sales_customer_region_matches_customer_region(con: duckdb.DuckDBPyConnection) -> None:
    row = con.execute(
        "SELECT count(*) FROM sample_sales s "
        "JOIN customers c ON s.customer_id = c.customer_id "
        "WHERE s.region != c.region"
    ).fetchone()
    assert row is not None
    assert row[0] == 0


def test_every_product_has_positive_margin(con: duckdb.DuckDBPyConnection) -> None:
    row = con.execute("SELECT count(*) FROM products WHERE unit_cost >= list_price").fetchone()
    assert row is not None
    assert row[0] == 0


def test_west_outdoor_q3_returns_exceed_other_cells(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute(
        """
        SELECT s.region, s.category, count(*) AS return_count
        FROM returns r JOIN sample_sales s ON r.order_id = s.order_id
        WHERE month(s.order_date) IN (7, 8, 9)
        GROUP BY s.region, s.category
        """
    ).fetchall()
    counts = {(region, category): count for region, category, count in rows}
    west_outdoor = counts[("West", "Outdoor")]
    other_cells = [count for cell, count in counts.items() if cell != ("West", "Outdoor")]
    assert west_outdoor > max(other_cells)


def test_west_outdoor_q3_marketing_spend_is_cut(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute(
        "SELECT month, spend FROM marketing_spend "
        "WHERE region = 'West' AND category = 'Outdoor' ORDER BY month"
    ).fetchall()
    spend_by_month = dict(rows)
    q3_spend = [spend_by_month[m] for m in (7, 8, 9)]
    other_months_spend = [
        spend for month, spend in spend_by_month.items() if month not in (7, 8, 9)
    ]
    assert max(q3_spend) < min(other_months_spend)


def test_connect_without_samples_leaves_empty_directory_untouched(tmp_path: Path) -> None:
    folder = tmp_path / "user_folder"
    folder.mkdir()
    con = connect(folder, ensure_samples=False)
    try:
        assert con.execute("SHOW TABLES").fetchall() == []
    finally:
        con.close()
    assert list(folder.iterdir()) == []
