import pytest

from app.services.workflow_validator import validate_workflow
from app.services.workflow_runner import execution_order


def test_accepts_linear_workflow():
    result = validate_workflow(
        {
            "nodes": [
                {"id": "a", "agent_id": "analyst"},
                {"id": "b", "agent_id": "reviewer"},
            ],
            "edges": [{"source": "a", "target": "b"}],
        }
    )
    assert result["edges"] == [{"source": "a", "target": "b"}]


def test_rejects_cycles():
    with pytest.raises(ValueError, match="ciclo"):
        validate_workflow(
            {
                "nodes": [
                    {"id": "a", "agent_id": "one"},
                    {"id": "b", "agent_id": "two"},
                ],
                "edges": [
                    {"source": "a", "target": "b"},
                    {"source": "b", "target": "a"},
                ],
            }
        )


def test_execution_order_respects_edges():
    ordered = execution_order(
        {
            "nodes": [
                {"id": "review", "agent_id": "two"},
                {"id": "build", "agent_id": "one"},
            ],
            "edges": [{"source": "build", "target": "review"}],
        }
    )
    assert [node["id"] for node in ordered] == ["build", "review"]


def test_preserves_step_instruction():
    result = validate_workflow(
        {
            "nodes": [
                {
                    "id": "build",
                    "agent_id": "developer",
                    "instruction": "Implementa una solución mínima y probada.",
                }
            ],
            "edges": [],
        }
    )

    assert result["nodes"][0]["instruction"] == (
        "Implementa una solución mínima y probada."
    )


@pytest.mark.parametrize(
    "edges",
    [
        [],
        [{"source": "a", "target": "b"}, {"source": "a", "target": "c"}],
        [{"source": "a", "target": "c"}, {"source": "b", "target": "c"}],
    ],
)
def test_rejects_disconnected_or_branched_workflows(edges):
    with pytest.raises(ValueError, match="secuencia|varios"):
        validate_workflow(
            {
                "nodes": [
                    {"id": "a", "agent_id": "one"},
                    {"id": "b", "agent_id": "two"},
                    {"id": "c", "agent_id": "three"},
                ],
                "edges": edges,
            }
        )


def test_rejects_duplicate_edges():
    with pytest.raises(ValueError, match="duplicadas"):
        validate_workflow(
            {
                "nodes": [
                    {"id": "a", "agent_id": "one"},
                    {"id": "b", "agent_id": "two"},
                ],
                "edges": [
                    {"source": "a", "target": "b"},
                    {"source": "a", "target": "b"},
                ],
            }
        )
