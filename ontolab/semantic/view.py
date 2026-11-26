"""Semantic layer: logical tables, dimensions, facts and metrics over operational data.

Callers ask for metrics and dimensions by name. The layer validates the request
and compiles read-only SQL; no caller-supplied SQL text is ever executed. Filter
values are bound as parameters.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from ..db import Database
from ..errors import ToolError

Operator = Literal["=", "!=", "in", "not_in", ">", ">=", "<", "<=", "contains"]
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")


class SemanticError(ToolError):
    pass


class SemanticFilter(BaseModel):
    dimension: str = Field(description="Name of a dimension of the semantic view.")
    operator: Operator = Field(default="=", description="Comparison. Use 'in' / 'not_in' with a list value.")
    value: Any = Field(description="Value to compare with; a list for 'in' and 'not_in'.")


class SemanticQuery(BaseModel):
    metrics: list[str] = Field(default_factory=list, description="Metric names to compute.")
    dimensions: list[str] = Field(default_factory=list, description="Dimension names to group by.")
    filters: list[SemanticFilter] = Field(default_factory=list, description="Row filters, combined with AND.")
    order_by: str | None = Field(default=None, description="A selected metric or dimension to sort by.")
    descending: bool = Field(default=True, description="Sort direction for order_by.")
    limit: int = Field(default=50, ge=1, le=500, description="Maximum rows to return.")


class SemanticView:
    def __init__(self, db: Database, definition: dict | str | Path):
        if not isinstance(definition, dict):
            definition = yaml.safe_load(Path(definition).read_text(encoding="utf-8"))
        self.db = db
        self.name: str = definition["name"]
        self.description: str = definition.get("description", "")
        self.tables: dict[str, dict] = definition["logical_tables"]
        self.metrics: dict[str, dict] = definition["metrics"]
        for name in list(self.metrics) + [c for t in self.tables.values()
                                          for c in list(t["dimensions"]) + list(t.get("facts", {}))]:
            if not _IDENTIFIER.match(name):
                raise SemanticError("invalid_definition", f"'{name}' is not a valid semantic name.")
        for name, metric in self.metrics.items():
            if metric["table"] not in self.tables:
                raise SemanticError("invalid_definition", f"metric {name}: unknown logical table '{metric['table']}'.")

    @staticmethod
    def view_name(table: str) -> str:
        return f"sv_{table}"

    def install(self) -> None:
        """Create one SQL view per logical table, exposing only the governed columns."""
        for name, table in self.tables.items():
            columns = {**table["dimensions"], **table.get("facts", {})}
            select = ",\n  ".join(f"{spec['expr']} AS {column}" for column, spec in columns.items())
            joins = "\n".join(table.get("joins", []))
            self.db.execute(f"CREATE OR REPLACE VIEW {self.view_name(name)} AS\nSELECT\n  {select}\nFROM {table['base']}\n{joins}")

    def catalog(self) -> dict:
        """Everything a caller may ask for, with business definitions."""
        return {
            "name": self.name, "description": self.description,
            "logical_tables": [{
                "name": name, "description": t.get("description", ""), "grain": t.get("grain", ""),
                "base": t["base"], "joins": t.get("joins", []), "relationships": t.get("relationships", []),
                "dimensions": [{"name": d, "description": s.get("description", "")} for d, s in t["dimensions"].items()],
                "facts": [{"name": f, "description": s.get("description", "")} for f, s in t.get("facts", {}).items()],
            } for name, t in self.tables.items()],
            "metrics": [{
                "name": name, "logical_table": m["table"], "formula": m["expr"], "format": m.get("format", "number"),
                "definition": m.get("definition", ""), "numerator": m.get("numerator"), "denominator": m.get("denominator"),
            } for name, m in self.metrics.items()],
        }

    def describe_for_prompt(self) -> str:
        """Compact catalogue for a tool description."""
        lines = ["Metrics (each belongs to one logical table):"]
        for name, m in self.metrics.items():
            lines.append(f"- `{name}` [{m['table']}]: {m.get('definition', '')}")
        lines.append("Dimensions by logical table (group by or filter on these):")
        for name, t in self.tables.items():
            lines.append(f"- {name}: " + ", ".join(f"`{d}`" for d in t["dimensions"]))
        return "\n".join(lines)

    # ------------------------------------------------------------ compiling

    def compile(self, query: SemanticQuery) -> tuple[str, list, str]:
        if not query.metrics and not query.dimensions:
            raise SemanticError("empty_query", "Ask for at least one metric or dimension.")
        unknown = [m for m in query.metrics if m not in self.metrics]
        if unknown:
            raise SemanticError("unknown_metric", f"Unknown metrics: {unknown}", valid=sorted(self.metrics))
        needed = list(query.dimensions) + [f.dimension for f in query.filters]

        if query.metrics:
            tables = {self.metrics[m]["table"] for m in query.metrics}
            if len(tables) > 1:
                raise SemanticError(
                    "mixed_tables", "These metrics belong to different logical tables; query them separately.",
                    metric_tables={m: self.metrics[m]["table"] for m in query.metrics})
            table = tables.pop()
        else:
            table = next((name for name, t in self.tables.items() if all(d in t["dimensions"] for d in needed)), None)
            if table is None:
                raise SemanticError("unknown_dimension", "No logical table has all of these dimensions.",
                                    requested=needed, valid=self._dimensions_by_table())
        dimensions = self.tables[table]["dimensions"]
        unknown = sorted({d for d in needed if d not in dimensions})
        if unknown:
            raise SemanticError("unknown_dimension",
                                f"Dimensions {unknown} are not available with logical table '{table}'.",
                                valid=sorted(dimensions), dimensions_by_table=self._dimensions_by_table())

        select = list(query.dimensions) + [f"{self.metrics[m]['expr']} AS {m}" for m in query.metrics]
        where, params = [], []
        for f in query.filters:
            clause, values = self._filter(f)
            where.append(clause)
            params += values
        sql = f"SELECT {'DISTINCT ' if not query.metrics else ''}{', '.join(select)}\nFROM {self.view_name(table)}"
        if where:
            sql += "\nWHERE " + " AND ".join(where)
        if query.metrics and query.dimensions:
            sql += "\nGROUP BY " + ", ".join(query.dimensions)
        selected = list(query.dimensions) + list(query.metrics)
        if query.order_by:
            if query.order_by not in selected:
                raise SemanticError("invalid_order_by", "order_by must be one of the selected metrics or dimensions.",
                                    valid=selected)
            sql += f"\nORDER BY {query.order_by} {'DESC' if query.descending else 'ASC'} NULLS LAST"
            tie = [d for d in query.dimensions if d != query.order_by]
            if tie:
                sql += ", " + ", ".join(tie)
        elif query.dimensions:
            sql += "\nORDER BY " + ", ".join(query.dimensions)
        sql += f"\nLIMIT {int(query.limit)}"
        return sql, params, table

    def _filter(self, f: SemanticFilter) -> tuple[str, list]:
        if f.operator in ("in", "not_in"):
            if not isinstance(f.value, list) or not f.value:
                raise SemanticError("invalid_filter", f"Filter on {f.dimension}: '{f.operator}' needs a non-empty list.")
            marks = ", ".join("?" for _ in f.value)
            return f"{f.dimension} {'IN' if f.operator == 'in' else 'NOT IN'} ({marks})", list(f.value)
        if isinstance(f.value, (list, dict)):
            raise SemanticError("invalid_filter", f"Filter on {f.dimension}: '{f.operator}' needs a single value.")
        if f.operator == "contains":
            return f"CAST({f.dimension} AS VARCHAR) ILIKE ?", [f"%{f.value}%"]
        return f"{f.dimension} {f.operator} ?", [f.value]

    def _dimensions_by_table(self) -> dict:
        return {name: sorted(t["dimensions"]) for name, t in self.tables.items()}

    def run(self, query: SemanticQuery | dict) -> dict:
        if isinstance(query, dict):
            query = SemanticQuery.model_validate(query)
        sql, params, table = self.compile(query)
        if not re.match(r"^\s*SELECT\b", sql) or ";" in sql:
            raise SemanticError("unsafe_sql", "Compiled statement is not a single SELECT.")
        rows = self.db.query(sql, params)
        for row in rows:
            for key, value in row.items():
                if isinstance(value, float):
                    row[key] = round(value, 4)
        return {
            "semantic_view": self.name, "logical_table": table, "sql": render_sql(sql, params), "rows": rows,
            "row_count": len(rows), "truncated": len(rows) >= query.limit,
            "metric_definitions": {m: {k: self.metrics[m].get(k) for k in ("definition", "numerator", "denominator", "format")}
                                   for m in query.metrics},
            "data_notice": "synthetic data",
        }


def render_sql(sql: str, params: list) -> str:
    """Inline bound parameters for display only (the executed statement stays parameterised)."""
    parts = sql.split("?")
    out = parts[0]
    for value, rest in zip(params, parts[1:]):
        if isinstance(value, bool):
            literal = "TRUE" if value else "FALSE"
        elif isinstance(value, (int, float)):
            literal = str(value)
        else:
            literal = "'" + str(value).replace("'", "''") + "'"
        out += literal + rest
    return out
