"""A synthetic retail star schema for the lakehouse under test.

We generate the dataset natively rather than depending on a TPC-H extension so
that the harness runs on a laptop, in CI, and on restricted networks with no
external downloads. The schema is deliberately conventional -- one fact table,
four dimensions -- because the point of the harness is to stress the *platform*,
not to pose an interesting modelling problem.

Scale is controlled by `n_sales`. The defaults are small enough to run in
seconds; the fleet-size sweep is the expensive axis, not the data.
"""

from __future__ import annotations

import dataclasses

import duckdb

N_CUSTOMERS = 5_000
N_PRODUCTS = 1_000
N_STORES = 50
N_DAYS = 730


@dataclasses.dataclass(frozen=True)
class TableSpec:
    """A table in the lakehouse under test.

    Attributes:
        name: Table name as the agent sees it.
        namespace: Logical namespace, used for catalog request skew analysis.
        grain: Human-readable description of one row, exposed to agents during
            discovery in the same way a catalog comment would be.
    """

    name: str
    namespace: str
    grain: str


SCHEMA: tuple[TableSpec, ...] = (
    TableSpec("fact_sales", "sales", "one row per line item on a sales order"),
    TableSpec("dim_customer", "sales", "one row per customer"),
    TableSpec("dim_product", "catalog", "one row per sellable product"),
    TableSpec("dim_store", "retail", "one row per physical store"),
    TableSpec("dim_date", "shared", "one row per calendar day"),
)


def build(con: duckdb.DuckDBPyConnection, n_sales: int = 500_000, seed: int = 7) -> None:
    """Materialise the star schema into `con`.

    Args:
        con: An open DuckDB connection. Tables are created in its default schema.
        n_sales: Number of fact rows. Drives dataset size roughly linearly.
        seed: Seed for DuckDB's RNG, making generation deterministic.

    Raises:
        ValueError: If `n_sales` is not positive.
    """
    if n_sales <= 0:
        raise ValueError(f"n_sales must be positive, got {n_sales}")

    con.execute(f"SELECT setseed({(seed % 100) / 100.0})")

    con.execute(f"""
        CREATE OR REPLACE TABLE dim_customer AS
        SELECT i AS customer_id,
               'customer_' || i AS customer_name,
               ['US','UK','DE','IN','JP','BR'][(i % 6) + 1] AS country,
               ['enterprise','mid_market','smb'][(i % 3) + 1] AS segment
        FROM range({N_CUSTOMERS}) t(i)
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE dim_product AS
        SELECT i AS product_id,
               'product_' || i AS product_name,
               ['electronics','apparel','grocery','home','sports'][(i % 5) + 1] AS category,
               round(5 + (i % 400) * 1.25, 2) AS list_price
        FROM range({N_PRODUCTS}) t(i)
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE dim_store AS
        SELECT i AS store_id,
               'store_' || i AS store_name,
               ['north','south','east','west'][(i % 4) + 1] AS region
        FROM range({N_STORES}) t(i)
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE dim_date AS
        SELECT i AS date_id,
               DATE '2024-01-01' + i::INTEGER AS calendar_date,
               year(DATE '2024-01-01' + i::INTEGER) AS year,
               month(DATE '2024-01-01' + i::INTEGER) AS month,
               quarter(DATE '2024-01-01' + i::INTEGER) AS quarter
        FROM range({N_DAYS}) t(i)
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE fact_sales AS
        SELECT i AS sale_id,
               (i * 7919) % {N_CUSTOMERS} AS customer_id,
               (i * 6271) % {N_PRODUCTS} AS product_id,
               (i * 3571) % {N_STORES} AS store_id,
               (i * 2311) % {N_DAYS} AS date_id,
               1 + (i % 9) AS quantity,
               round(((i % 500) + 1) * 2.5, 2) AS net_amount,
               round(((i % 500) + 1) * 2.5 * 0.08, 2) AS tax_amount
        FROM range({n_sales}) t(i)
    """)


def namespace_of(table: str) -> str | None:
    """Return the logical namespace for `table`, or None if it is unknown."""
    for spec in SCHEMA:
        if spec.name == table:
            return spec.namespace
    return None
