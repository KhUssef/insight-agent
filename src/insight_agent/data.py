"""DuckDB data layer and synthetic sample datasets.

The primary dataset is a seeded, deterministic synthetic sales table: one row
per order with region, category, product, units, unit price, and revenue over
the calendar year 2025. The generator plants one deliberate story in the
noise: the West region's Outdoor category collapses in Q3, so questions like
"which region dropped most in Q3" have a stable, verifiable answer. Five more
tables share the same story world and are joinable against it: `products`
(one row per product, with a cost and a list price so margins are askable),
`customers` (a roster spread across the four regions and a handful of
segments, referenced from sample_sales by customer_id), `region_targets`
(per-region revenue targets derived from the sales data's actual totals, so
the West region misses its target while the other three exceed theirs),
`marketing_spend` (monthly spend by region and category, cut sharply for
West's Outdoor line in Q3, a discoverable cause of the collapse), and
`returns` (a subset of orders with a reason and a refund, elevated for West's
Outdoor orders in Q3, a second discoverable cause).

A data directory can hold any number of files; connect() loads every table
insight_agent.ingest discovers from it. A missing or empty directory is
populated with all six sample files first, so the project works out of the
box, but a directory that already contains user files is left untouched.
"""

import calendar
import random
from datetime import date, timedelta
from pathlib import Path

import duckdb

from insight_agent.ingest import discover_tables

_SEED = 20260711
_PRODUCTS_SEED = _SEED + 101
_CUSTOMERS_SEED = _SEED + 202
_CUSTOMER_ASSIGNMENT_SEED = _SEED + 303
_MARKETING_SEED = _SEED + 404
_RETURNS_SEED = _SEED + 505

_REGIONS: dict[str, float] = {"North": 1.0, "South": 0.85, "East": 1.1, "West": 1.2}

_CATEGORIES: dict[str, tuple[float, list[str]]] = {
    "Outdoor": (1.3, ["Trail Backpack", "Camping Tent", "Hiking Boots"]),
    "Electronics": (1.6, ["Wireless Earbuds", "Smart Speaker", "Action Camera"]),
    "Clothing": (1.0, ["Rain Jacket", "Merino Sweater", "Running Shorts"]),
    "Home": (0.9, ["Cast Iron Pan", "Desk Lamp", "Throw Blanket"]),
    "Grocery": (0.7, ["Coffee Beans", "Olive Oil", "Protein Bars"]),
}

_ORDERS_PER_CELL = 15
_BASE_MONTHLY_REVENUE = 40_000.0

_SAMPLE_SALES_FILENAME = "sample_sales.csv"
_REGION_TARGETS_FILENAME = "region_targets.csv"
_PRODUCTS_FILENAME = "products.csv"
_CUSTOMERS_FILENAME = "customers.csv"
_MARKETING_SPEND_FILENAME = "marketing_spend.csv"
_RETURNS_FILENAME = "returns.csv"

_WEST_TARGET_MULTIPLIER = 1.10
_OTHER_TARGET_MULTIPLIER = 0.95

_PRODUCT_BASE_PRICE = 60.0

_CUSTOMERS_PER_REGION = 100
_SEGMENTS = ("Consumer", "Small Business", "Enterprise")
_SIGNUP_RANGE_START = date(2024, 1, 1)
_SIGNUP_RANGE_END = date(2025, 12, 31)

_WEST_OUTDOOR_Q3_MARKETING_MULTIPLIER = 0.2
_MARKETING_BASE_SPEND = 3_000.0

_RETURN_REASONS = (
    "Defective",
    "Wrong Item",
    "No Longer Needed",
    "Damaged in Transit",
    "Changed Mind",
)
_BASE_RETURN_PROBABILITY = 0.06
_WEST_OUTDOOR_Q3_RETURN_PROBABILITY = 0.45


def _monthly_multiplier(region: str, category: str, month: int) -> float:
    """Seasonal and story multipliers for a region-category-month cell."""
    seasonal = 1.0 + 0.15 * ((month % 6) - 2.5) / 2.5
    if region == "West" and category == "Outdoor" and month in (7, 8, 9):
        return seasonal * 0.35
    return seasonal


def _product_catalog() -> list[tuple[int, str, str]]:
    """Return the fixed catalog of (product_id, product, category) rows.

    Every product name across every category in _CATEGORIES is numbered 1
    upward in category-then-product order. The mapping is a pure function of
    _CATEGORIES, so a product's id and category are identical on every call
    without any randomness involved.
    """
    catalog: list[tuple[int, str, str]] = []
    product_id = 1
    for category, (_, products) in _CATEGORIES.items():
        for product in products:
            catalog.append((product_id, product, category))
            product_id += 1
    return catalog


def _customer_ids_for_region(region: str) -> list[int]:
    """Return the fixed block of customer_id values that belong to a region.

    Customers are laid out in _REGIONS order in contiguous blocks of
    _CUSTOMERS_PER_REGION ids each, so a region's id range is a pure function
    of its position in _REGIONS and needs no lookup against the customers
    table itself.
    """
    index = list(_REGIONS).index(region)
    start = index * _CUSTOMERS_PER_REGION + 1
    return list(range(start, start + _CUSTOMERS_PER_REGION))


def generate_products_dataset(path: Path) -> Path:
    """Write the product catalog to a CSV file and return its path.

    One row is written per product across every category in _CATEGORIES,
    each with a deterministic unit_cost strictly below its list_price so
    per-product margins are directly askable. Generation draws from its own
    seeded random stream, independent of the sales dataset's, so this table
    can be regenerated without affecting sample_sales in any way.
    """
    rng = random.Random(_PRODUCTS_SEED)
    lines = ["product_id,product,category,unit_cost,list_price"]
    for product_id, product, category in _product_catalog():
        weight = _CATEGORIES[category][0]
        list_price = round(_PRODUCT_BASE_PRICE * weight * rng.uniform(0.8, 1.6), 2)
        unit_cost = round(list_price * rng.uniform(0.4, 0.7), 2)
        lines.append(f"{product_id},{product},{category},{unit_cost},{list_price}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def generate_customers_dataset(path: Path) -> Path:
    """Write the customer roster to a CSV file and return its path.

    _CUSTOMERS_PER_REGION customers are generated for each of the four
    regions in _REGIONS, laid out in the contiguous customer_id blocks
    _customer_ids_for_region assigns, each given a segment from _SEGMENTS and
    a signup_date somewhere between _SIGNUP_RANGE_START and
    _SIGNUP_RANGE_END. Generation draws from its own seeded random stream,
    independent of the sales dataset's.
    """
    rng = random.Random(_CUSTOMERS_SEED)
    signup_range_days = (_SIGNUP_RANGE_END - _SIGNUP_RANGE_START).days
    lines = ["customer_id,region,segment,signup_date"]
    for region in _REGIONS:
        for customer_id in _customer_ids_for_region(region):
            segment = rng.choice(_SEGMENTS)
            signup_date = _SIGNUP_RANGE_START + timedelta(days=rng.randint(0, signup_range_days))
            lines.append(f"{customer_id},{region},{segment},{signup_date.isoformat()}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def generate_sample_dataset(path: Path) -> Path:
    """Write the synthetic sales dataset to a CSV file and return its path.

    Generation is fully deterministic: the same seed always produces the same
    file, so the CSV can be regenerated at any time without changing results.
    Each row also carries a customer_id, drawn from the customers generated
    for the order's own region by a separate seeded random stream, and a
    product_id looked up from _product_catalog, so the table joins onto both
    customers and products without disturbing the random stream that decides
    every other column's value.
    """
    rng = random.Random(_SEED)
    assignment_rng = random.Random(_CUSTOMER_ASSIGNMENT_SEED)
    product_ids = {product: product_id for product_id, product, _ in _product_catalog()}
    rows: list[tuple[int, str, str, str, str, int, float, float, int, int]] = []
    order_id = 1
    for month in range(1, 13):
        days_in_month = calendar.monthrange(2025, month)[1]
        for region, region_weight in _REGIONS.items():
            for category, (category_weight, products) in _CATEGORIES.items():
                cell_total = (
                    _BASE_MONTHLY_REVENUE
                    * region_weight
                    * category_weight
                    * _monthly_multiplier(region, category, month)
                )
                for _ in range(_ORDERS_PER_CELL):
                    product = rng.choice(products)
                    order_revenue = cell_total / _ORDERS_PER_CELL * rng.uniform(0.6, 1.4)
                    units = rng.randint(1, 8)
                    unit_price = round(order_revenue / units, 2)
                    revenue = round(unit_price * units, 2)
                    order_date = date(2025, month, rng.randint(1, days_in_month))
                    customer_id = assignment_rng.choice(_customer_ids_for_region(region))
                    rows.append(
                        (
                            order_id,
                            order_date.isoformat(),
                            region,
                            category,
                            product,
                            units,
                            unit_price,
                            revenue,
                            customer_id,
                            product_ids[product],
                        )
                    )
                    order_id += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "order_id,order_date,region,category,product,units,unit_price,revenue,"
        "customer_id,product_id"
    )
    lines = [header]
    for row in rows:
        lines.append(",".join(str(value) for value in row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def ensure_sample_dataset(path: Path) -> Path:
    """Return the dataset path, generating the CSV first if it does not exist."""
    if not path.exists():
        generate_sample_dataset(path)
    return path


def generate_region_targets(path: Path, sales_path: Path) -> Path:
    """Write deterministic per-region revenue targets to a CSV file and return its path.

    Each region's target is derived from its actual 2025 revenue in the sales
    dataset at sales_path, computed with a DuckDB query so the result is as
    deterministic as the seeded sales data it is based on. The West region's
    target is 110% of its actual revenue, so it misses its target; the other
    three regions get 95% of their actual revenue, so they exceed theirs.
    Each target is rounded to the nearest whole number.
    """
    con = duckdb.connect(database=":memory:")
    try:
        rows = con.execute(
            "SELECT region, sum(revenue) AS actual FROM read_csv_auto(?) "
            "GROUP BY region ORDER BY region",
            [str(sales_path)],
        ).fetchall()
    finally:
        con.close()

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["region,revenue_target"]
    for region, actual in rows:
        multiplier = _WEST_TARGET_MULTIPLIER if region == "West" else _OTHER_TARGET_MULTIPLIER
        lines.append(f"{region},{round(actual * multiplier)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _monthly_marketing_multiplier(region: str, category: str, month: int) -> float:
    """Return the spend multiplier for a region-category-month marketing cell.

    West's Outdoor spend is cut to _WEST_OUTDOOR_Q3_MARKETING_MULTIPLIER of
    its baseline in July through September - precisely the region, category,
    and quarter in which sample_sales' revenue collapses - so the spend cut
    is a discoverable, joinable cause of the drop rather than a coincidence.
    Every other cell keeps its full baseline spend.
    """
    if region == "West" and category == "Outdoor" and month in (7, 8, 9):
        return _WEST_OUTDOOR_Q3_MARKETING_MULTIPLIER
    return 1.0


def generate_marketing_spend_dataset(path: Path) -> Path:
    """Write monthly 2025 marketing spend by region and category to a CSV file.

    Baseline spend for a region-category-month cell scales with the same
    region and category weights sample_sales uses, perturbed by a small
    deterministic random factor, so bigger cells get bigger budgets. West's
    Outdoor spend is cut sharply for July through September by
    _monthly_marketing_multiplier, giving the Q3 collapse in sample_sales a
    discoverable marketing cause. Generation draws from its own seeded random
    stream, independent of the sales dataset's.
    """
    rng = random.Random(_MARKETING_SEED)
    lines = ["month,region,category,spend"]
    for month in range(1, 13):
        for region, region_weight in _REGIONS.items():
            for category, (category_weight, _) in _CATEGORIES.items():
                base = _MARKETING_BASE_SPEND * region_weight * category_weight
                spend = round(
                    base
                    * rng.uniform(0.85, 1.15)
                    * _monthly_marketing_multiplier(region, category, month),
                    2,
                )
                lines.append(f"{month},{region},{category},{spend}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def generate_returns_dataset(path: Path, sales_path: Path) -> Path:
    """Write a deterministic subset of returned orders to a CSV file.

    Every order in the sales dataset at sales_path is a candidate, read back
    in order_id order via DuckDB so the outcome does not depend on file
    layout. Each order returns with _BASE_RETURN_PROBABILITY, except West's
    Outdoor orders in July through September, which return at the much higher
    _WEST_OUTDOOR_Q3_RETURN_PROBABILITY and refund a larger fraction of their
    revenue, giving the Q3 collapse in sample_sales a second discoverable
    cause alongside the marketing spend cut. The random reason and refund
    fraction come from their own seeded stream, independent of the sales
    dataset's.
    """
    con = duckdb.connect(database=":memory:")
    try:
        rows = con.execute(
            "SELECT order_id, month(order_date) AS month, region, category, revenue "
            "FROM read_csv_auto(?) ORDER BY order_id",
            [str(sales_path)],
        ).fetchall()
    finally:
        con.close()

    rng = random.Random(_RETURNS_SEED)
    lines = ["order_id,reason,refund"]
    for order_id, month, region, category, revenue in rows:
        is_west_outdoor_q3 = region == "West" and category == "Outdoor" and month in (7, 8, 9)
        probability = (
            _WEST_OUTDOOR_Q3_RETURN_PROBABILITY
            if is_west_outdoor_q3
            else _BASE_RETURN_PROBABILITY
        )
        if rng.random() < probability:
            reason = rng.choice(_RETURN_REASONS)
            refund_fraction = (
                rng.uniform(0.6, 1.0) if is_west_outdoor_q3 else rng.uniform(0.3, 0.7)
            )
            refund = round(revenue * refund_fraction, 2)
            lines.append(f"{order_id},{reason},{refund}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def ensure_sample_data(data_dir: Path) -> None:
    """Populate a missing or empty data directory with the sample datasets.

    Six tables are generated together, so a freshly created data directory
    ships a joinable story world: products (catalog and margins), customers
    (roster by region and segment), sample_sales (the per-order sales rows,
    referencing both by customer_id and product_id), region_targets
    (per-region revenue targets derived from sample_sales), marketing_spend
    (monthly spend by region and category), and returns (a subset of orders
    with a reason and refund). A directory that already contains any files is
    left untouched - sample data is never written on top of a user's own
    files.
    """
    if data_dir.exists() and any(data_dir.iterdir()):
        return
    generate_products_dataset(data_dir / _PRODUCTS_FILENAME)
    generate_customers_dataset(data_dir / _CUSTOMERS_FILENAME)
    sales_path = generate_sample_dataset(data_dir / _SAMPLE_SALES_FILENAME)
    generate_region_targets(data_dir / _REGION_TARGETS_FILENAME, sales_path)
    generate_marketing_spend_dataset(data_dir / _MARKETING_SPEND_FILENAME)
    generate_returns_dataset(data_dir / _RETURNS_FILENAME, sales_path)


def connect(data_dir: Path, ensure_samples: bool = True) -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB connection with every table in data_dir loaded.

    When ensure_samples is true, a missing or empty data directory is
    populated with the sample datasets first; callers pass false for a
    user-supplied folder, which is loaded exactly as found. Every table
    insight_agent.ingest.discover_tables finds is then created from its CSV
    file, under its sanitized, collision-free name.
    """
    if ensure_samples:
        ensure_sample_data(data_dir)
    con = duckdb.connect(database=":memory:")
    for source in discover_tables(data_dir):
        con.execute(
            f'CREATE TABLE "{source.table_name}" AS SELECT * FROM read_csv_auto(?)',
            [str(source.csv_path)],
        )
    return con
