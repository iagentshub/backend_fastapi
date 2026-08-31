"""Barrera de contrato para no reintroducir paginación posicional en API v2."""

from __future__ import annotations


def test_ci_all_v2_list_gets_expose_only_cursor_pagination(admin_client):
    schema = admin_client.app.openapi()
    checked: list[str] = []
    violations: list[str] = []
    forbidden = {"offset", "page", "page_size"}

    for path, path_item in schema["paths"].items():
        operation = path_item.get("get")
        if not operation or not any(
            str(tag).endswith("-v2") for tag in operation.get("tags", [])
        ):
            continue
        checked.append(path)
        query_parameters = {
            parameter["name"]
            for parameter in operation.get("parameters", [])
            if parameter.get("in") == "query"
        }
        obsolete = sorted(query_parameters & forbidden)
        missing = sorted({"cursor", "limit"} - query_parameters)
        if obsolete or missing:
            violations.append(
                f"{path}: obsolete={obsolete!r}, missing_cursor={missing!r}"
            )

    assert len(checked) == 15, (
        "La superficie cursor cambió: actualiza deliberadamente la barrera; "
        f"rutas detectadas={sorted(checked)!r}"
    )
    assert violations == [], "Contratos de listado v2 inválidos: " + "; ".join(
        violations
    )
