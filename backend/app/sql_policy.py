"""Independent fail-closed AST validation for fixed analytical SQL."""
from sqlglot import exp, parse
from sqlglot.errors import SqlglotError

VIEWS = {"rows", "sources"}
BINDINGS = {"org", "version", "domain", "start", "end", "prior_start", "as_of", "branch", "sku", "customer",
            "threshold", "result_limit"}
COLUMNS = frozenset("""
    org_id version_id payload domain version published_at order_id branch sku customer day quantity
    unit_price discount unit_cost movement_type stock_on_hand invoice due_on invoice_amount paid
    balance days_overdue bucket gross_sales net_revenue cogs gross_profit order_count average_order_value
    prior_net current_net prior_net_revenue sales_growth low_stock inventory_movements movement_count
    net_quantity outstanding overdue_amount overdue_invoices partial_invoices sort
""".split())
NODE_TYPES = frozenset("""
    select with cte table tablealias identifier column from where group order ordered limit join
    subquery values tuple alias star literal null placeholder eq gt gte lt lte and or is add sub mul div
    sum count max min coalesce nullif abs round distinct cast datatype datetype
    jsonextractscalar jsonpath jsonpathkey jsonpathroot paren case if filter
""".split())
JSON_FIELDS = frozenset("""
    order_id branch_id sku customer_id ordered_on quantity unit_price discount unit_cost status currency
    movement_type movement_on invoice_id due_on invoice_on paid_on invoice_amount amount
""".split())


class QueryRejected(ValueError):
    pass


def validate_sql(query: str) -> bool:
    if len(query) > 16_000:
        raise QueryRejected("Query exceeds the policy size limit")
    try:
        statements = parse(query, read="postgres")
    except SqlglotError as exc:
        raise QueryRejected("Invalid analytical SQL") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise QueryRejected("Only one SELECT statement is allowed")
    statement = statements[0]
    ctes = {cte.alias for cte in statement.find_all(exp.CTE)}
    physical = False
    for node in statement.walk():
        if node.key not in NODE_TYPES or node.comments:
            raise QueryRejected("SQL expression is outside the analytical policy")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise QueryRejected("Recursive queries are prohibited")
        if isinstance(node, exp.Table):
            if node.catalog or (node.db, node.name) not in {("reporting", view) for view in VIEWS}:
                if node.catalog or node.db or node.name not in ctes:
                    raise QueryRejected("Table is outside the reporting schema")
            else:
                physical = True
        if (isinstance(node, exp.Column) and node.name not in COLUMNS
                and not (node.name == "*" and node.table in ctes)):
            raise QueryRejected("Column is outside the semantic schema")
        if isinstance(node, exp.Placeholder) and node.name not in BINDINGS:
            raise QueryRejected("Unapproved SQL parameter")
        if isinstance(node, exp.JSONPathKey) and node.this not in JSON_FIELDS:
            raise QueryRejected("JSON field is outside the semantic schema")
        if isinstance(node, exp.DataType) and node.sql(dialect="postgres").upper() not in {"DATE", "TEXT", "DECIMAL"}:
            raise QueryRejected("Data type is outside the analytical policy")
    if not physical:
        raise QueryRejected("An approved reporting view is required")
    return statement.args.get("limit") is not None
