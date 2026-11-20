"""Use PostgreSQL's parser to check a small reporting-only SQL subset."""

from pglast import ast, parse_sql
from pglast.enums import LimitOption, SetOperation
from pglast.parser import ParseError
from pglast.stream import RawStream
from pglast.visitors import Visitor

from assistant import MAX_ROWS, SCHEMA

# The parser represents SELECT clauses, expressions, and literals as nodes.
# New or unfamiliar node types are rejected by default.
ALLOWED_NODES = {
    "SelectStmt", "ResTarget", "RangeVar", "Alias", "ColumnRef", "String",
    "A_Star", "A_Const", "Integer", "Float", "Boolean", "A_Expr",
    "BoolExpr", "NullTest", "SortBy", "FuncCall",
}
ALLOWED_FUNCTIONS = {"count", "sum", "avg", "min", "max", "round", "date_trunc"}
ALLOWED_OPERATORS = {"+", "-", "*", "/", "%", "=", "<>", "!=", "<", "<=", ">", ">=", "~~", "!~~", "~~*", "!~~*"}


class UnsafeSQL(ValueError):
    """The query is outside the app's supported SELECT syntax."""


class CheckNodes(Visitor):
    def __init__(self, names):
        self.names = names

    def visit(self, ancestors, node):
        if type(node).__name__ not in ALLOWED_NODES:
            raise UnsafeSQL("The query contains unsupported SQL. Try a simpler sales question.")

        if isinstance(node, ast.FuncCall):
            parts = tuple(part.sval for part in node.funcname)
            if parts[-1] not in ALLOWED_FUNCTIONS or parts[:-1] not in ((), ("pg_catalog",)):
                raise UnsafeSQL("The query uses a function outside the reporting allowlist.")

        if isinstance(node, ast.A_Expr):
            if any(part.sval not in ALLOWED_OPERATORS for part in node.name or ()):
                raise UnsafeSQL("The query uses an unsupported SQL operator.")

        if isinstance(node, ast.ColumnRef):
            # Accept the view's columns, aliases used for sorting, and SELECT *.
            field = node.fields[-1]
            if isinstance(field, ast.String) and field.sval not in self.names:
                raise UnsafeSQL("The query refers to an unknown reporting column.")


def validate_sql(sql):
    if len(sql) > 4000:
        raise UnsafeSQL("The generated SQL is too long.")
    try:
        statements = parse_sql(sql)
    except (ParseError, ValueError) as exc:
        raise UnsafeSQL("The generated SQL could not be parsed.") from exc

    if len(statements) != 1 or not isinstance(statements[0].stmt, ast.SelectStmt):
        raise UnsafeSQL("Only one read-only SELECT is allowed.")

    query = statements[0].stmt
    if (
        query.intoClause or query.withClause or query.lockingClause or query.valuesLists
        or query.op != SetOperation.SETOP_NONE or query.limitOffset
        or query.limitOption == LimitOption.LIMIT_OPTION_WITH_TIES
    ):
        raise UnsafeSQL("Use a simple SELECT without locks, set operations, or offsets.")

    sources = query.fromClause or ()
    if len(sources) != 1 or not isinstance(sources[0], ast.RangeVar):
        raise UnsafeSQL("The query must use only reporting.sales.")
    source = sources[0]
    if source.catalogname or source.schemaname != "reporting" or source.relname != "sales":
        raise UnsafeSQL("The query must use only reporting.sales.")

    aliases = {column.name for column in query.targetList or () if column.name}
    CheckNodes(set(SCHEMA) | aliases)(query)

    # PostgreSQL returns at most 101 rows, including the extra row for the UI.
    limit = MAX_ROWS + 1
    if query.limitCount:
        value = query.limitCount
        if not isinstance(value, ast.A_Const) or not isinstance(value.val, ast.Integer) or value.val.ival < 0:
            raise UnsafeSQL("Use a non-negative integer as the LIMIT.")
        limit = min(limit, value.val.ival)
    query.limitCount = ast.A_Const(val=ast.Integer(ival=limit))
    query.limitOption = LimitOption.LIMIT_OPTION_COUNT

    # Rebuild canonical SQL from the checked nodes and execute that copy only.
    return RawStream()(query)
