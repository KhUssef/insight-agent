"""The read-only SQL guard is a security boundary; test it exhaustively."""

import pytest

from insight_agent.sql_guard import SQLValidationError, validate_read_only

ALLOWED = [
    "SELECT 1",
    "select * from sales",
    "SELECT region, sum(revenue) FROM sales GROUP BY region",
    "WITH q AS (SELECT * FROM sales) SELECT * FROM q",
    "SELECT * FROM sales;",
    "SELECT * FROM sales -- trailing comment",
    "/* leading comment */ SELECT 1",
    "SELECT 'insert update delete' AS words",
    "SELECT \"select\" FROM (SELECT 1 AS \"select\")",
    "SELECT * FROM sales WHERE region = 'West''s'",
    "SELECT count(*) FROM sales OFFSET 10",
    "SELECT * FROM sales; -- trailing comment after terminator",
    "SELECT * FROM sales EXCEPT SELECT * FROM sales",
]

REJECTED = [
    "",
    "   ",
    "-- only a comment",
    "INSERT INTO sales VALUES (1)",
    "UPDATE sales SET revenue = 0",
    "DELETE FROM sales",
    "DROP TABLE sales",
    "CREATE TABLE t (a INT)",
    "ALTER TABLE sales ADD COLUMN x INT",
    "TRUNCATE sales",
    "ATTACH 'other.db'",
    "DETACH other",
    "COPY sales TO 'out.csv'",
    "EXPORT DATABASE 'dir'",
    "INSTALL httpfs",
    "LOAD httpfs",
    "CALL pragma_version()",
    "PRAGMA database_list",
    "SET memory_limit = '1GB'",
    "BEGIN TRANSACTION",
    "COMMIT",
    "VACUUM",
    "CHECKPOINT",
    "SELECT 1; DROP TABLE sales",
    "SELECT 1; SELECT 2",
    "WITH q AS (SELECT 1) INSERT INTO sales SELECT * FROM q",
    "explain SELECT 1",
    "SHOW TABLES",
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allows_read_only_statements(sql: str) -> None:
    assert validate_read_only(sql) == sql


@pytest.mark.parametrize("sql", REJECTED)
def test_rejects_unsafe_statements(sql: str) -> None:
    with pytest.raises(SQLValidationError):
        validate_read_only(sql)


def test_keyword_inside_string_literal_is_not_flagged() -> None:
    sql = "SELECT * FROM sales WHERE product = 'drop table'"
    assert validate_read_only(sql) == sql


def test_keyword_inside_line_comment_is_ignored() -> None:
    sql = "SELECT 1 -- drop table sales"
    assert validate_read_only(sql) == sql


def test_keyword_inside_block_comment_is_ignored() -> None:
    sql = "SELECT /* delete everything */ 1"
    assert validate_read_only(sql) == sql
