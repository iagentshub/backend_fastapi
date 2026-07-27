import json

import pytest

from app.services.workflow_runner import execution_order, run_workflow
from app.services.workflow_validator import validate_workflow


def _node(node_id, agent_id=None, **extra):
    return {"id": node_id, "agent_id": agent_id or node_id, **extra}


def _agent(agent_id):
    return {
        "id": agent_id,
        "name": agent_id,
        "system_prompt": f"Agente {agent_id}",
        "use_memory": False,
    }


async def _events(definition, monkeypatch, replies):
    pending = {key: list(values) for key, values in replies.items()}

    async def fake_stream_chat(agent, _connection, _messages, _history):
        reply = pending[agent.id].pop(0)
        yield f"data: {json.dumps({'type': 'done', 'reply': reply})}\n\n"

    async def resolve(agent_id):
        return _agent(agent_id), {"id": f"connection-{agent_id}"}

    monkeypatch.setattr("app.services.workflow_runner.stream_chat", fake_stream_chat)
    return [
        event
        async for event in run_workflow(
            validate_workflow(definition),
            "entrada",
            resolve,
        )
    ]


def test_accepts_legacy_linear_workflow():
    result = validate_workflow(
        {
            "nodes": [_node("a", "analyst"), _node("b", "reviewer")],
            "edges": [{"source": "a", "target": "b"}],
        }
    )
    assert result["edges"] == [
        {"source": "a", "target": "b", "type": "sequence"}
    ]
    assert [node["kind"] for node in result["nodes"]] == ["agent", "agent"]


def test_preserves_and_normalizes_node_position():
    result = validate_workflow(
        {
            "nodes": [_node("a", position={"x": 10.126, "y": -24.555})],
            "edges": [],
        }
    )
    assert result["nodes"][0]["position"] == {"x": 10.13, "y": -24.55}


def test_rejects_untyped_sequence_cycle():
    with pytest.raises(ValueError, match="secuencia"):
        validate_workflow(
            {
                "nodes": [_node("a"), _node("b")],
                "edges": [
                    {"source": "a", "target": "b"},
                    {"source": "b", "target": "a"},
                ],
            }
        )


def test_execution_order_ignores_loop_edges():
    definition = validate_workflow(
        {
            "nodes": [_node("review", "two"), _node("build", "one")],
            "edges": [
                {"source": "build", "target": "review"},
                {
                    "source": "review",
                    "target": "build",
                    "type": "loop",
                    "mode": "fixed",
                    "iterations": 2,
                },
            ],
        }
    )
    ordered = execution_order(definition)
    assert [node["id"] for node in ordered] == ["build", "review"]


def test_preserves_step_instruction():
    result = validate_workflow(
        {
            "nodes": [
                _node(
                    "build",
                    "developer",
                    instruction="Implementa una solución mínima y probada.",
                )
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
                "nodes": [_node("a"), _node("b"), _node("c")],
                "edges": edges,
            }
        )


def test_rejects_duplicate_edges():
    with pytest.raises(ValueError, match="duplicadas"):
        validate_workflow(
            {
                "nodes": [_node("a"), _node("b")],
                "edges": [
                    {"source": "a", "target": "b"},
                    {"source": "a", "target": "b"},
                ],
            }
        )


def test_rejects_overlapping_and_nested_loops():
    with pytest.raises(ValueError, match="solaparse"):
        validate_workflow(
            {
                "nodes": [_node("a"), _node("b"), _node("c"), _node("d")],
                "edges": [
                    {"source": "a", "target": "b"},
                    {"source": "b", "target": "c"},
                    {"source": "c", "target": "d"},
                    {
                        "source": "c",
                        "target": "a",
                        "type": "loop",
                        "mode": "fixed",
                        "iterations": 2,
                    },
                    {
                        "source": "d",
                        "target": "b",
                        "type": "loop",
                        "mode": "fixed",
                        "iterations": 2,
                    },
                ],
            }
        )


def test_rejects_nested_loops():
    with pytest.raises(ValueError, match="solaparse"):
        validate_workflow(
            {
                "nodes": [_node("a"), _node("b"), _node("c"), _node("d")],
                "edges": [
                    {"source": "a", "target": "b"},
                    {"source": "b", "target": "c"},
                    {"source": "c", "target": "d"},
                    {
                        "source": "d",
                        "target": "a",
                        "type": "loop",
                        "mode": "fixed",
                        "iterations": 2,
                    },
                    {
                        "source": "c",
                        "target": "b",
                        "type": "loop",
                        "mode": "fixed",
                        "iterations": 2,
                    },
                ],
            }
        )


@pytest.mark.parametrize(
    "edge,match",
    [
        (
            {
                "source": "missing",
                "target": "a",
                "type": "loop",
                "mode": "fixed",
                "iterations": 2,
            },
            "existentes",
        ),
        (
            {
                "source": "a",
                "target": "b",
                "type": "loop",
                "mode": "fixed",
                "iterations": 2,
            },
            "anterior",
        ),
        (
            {
                "source": "b",
                "target": "a",
                "type": "loop",
                "mode": "fixed",
                "iterations": 21,
            },
            "entre 2 y 20",
        ),
    ],
)
def test_rejects_invalid_loop_references_direction_and_limits(edge, match):
    with pytest.raises(ValueError, match=match):
        validate_workflow(
            {
                "nodes": [_node("a"), _node("b")],
                "edges": [
                    {"source": "a", "target": "b"},
                    edge,
                ],
            }
        )


def test_rejects_condition_loop_without_evaluator():
    with pytest.raises(ValueError, match="evaluador"):
        validate_workflow(
            {
                "nodes": [_node("a"), _node("b")],
                "edges": [
                    {"source": "a", "target": "b"},
                    {
                        "source": "b",
                        "target": "a",
                        "type": "loop",
                        "mode": "condition",
                    },
                ],
            }
        )


@pytest.mark.asyncio
async def test_fixed_loop_runs_exact_number_of_passes(monkeypatch):
    events = await _events(
        {
            "nodes": [_node("a"), _node("b")],
            "edges": [
                {"source": "a", "target": "b"},
                {
                    "source": "b",
                    "target": "a",
                    "type": "loop",
                    "mode": "fixed",
                    "iterations": 3,
                },
            ],
        },
        monkeypatch,
        {
            "a": ["a1", "a2", "a3"],
            "b": ["b1", "b2", "b3"],
        },
    )
    assert [event["type"] for event in events].count("loop_iteration_started") == 2
    assert [event["node_id"] for event in events if event["type"] == "stage_done"] == [
        "a",
        "b",
        "a",
        "b",
        "a",
        "b",
    ]
    assert events[-1] == {"type": "workflow_done", "output": "b3"}


def _conditional_definition(max_iterations=5):
    return {
        "nodes": [
            _node("worker"),
            _node(
                "judge",
                kind="evaluator",
                evaluator={
                    "condition": "El resultado está revisado",
                    "max_iterations": max_iterations,
                },
            ),
        ],
        "edges": [
            {"source": "worker", "target": "judge"},
            {
                "source": "judge",
                "target": "worker",
                "type": "loop",
                "mode": "condition",
            },
        ],
    }


@pytest.mark.asyncio
async def test_evaluator_can_approve_first_pass(monkeypatch):
    events = await _events(
        _conditional_definition(),
        monkeypatch,
        {
            "worker": ["resultado"],
            "judge": ['{"approved": true, "reason": "Correcto"}'],
        },
    )
    decisions = [event for event in events if event["type"] == "evaluation_done"]
    assert decisions == [
        {
            "type": "evaluation_done",
            "node_id": "judge",
            "agent_id": "judge",
            "agent_name": "judge",
            "iteration": 1,
            "approved": True,
            "reason": "Correcto",
        }
    ]
    assert events[-1]["output"] == "resultado"


@pytest.mark.asyncio
async def test_evaluator_rejects_then_approves(monkeypatch):
    events = await _events(
        _conditional_definition(),
        monkeypatch,
        {
            "worker": ["borrador", "revisado"],
            "judge": [
                '{"approved": false, "reason": "Falta revisar"}',
                '{"approved": true, "reason": "Aprobado"}',
            ],
        },
    )
    assert [event["iteration"] for event in events if event["type"] == "evaluation_done"] == [
        1,
        2,
    ]
    assert events[-1]["output"] == "revisado"


@pytest.mark.asyncio
async def test_evaluator_limit_stops_execution(monkeypatch):
    with pytest.raises(RuntimeError, match="sin cumplir"):
        await _events(
            _conditional_definition(max_iterations=2),
            monkeypatch,
            {
                "worker": ["uno", "dos"],
                "judge": [
                    '{"approved": false, "reason": "No"}',
                    '{"approved": false, "reason": "Todavía no"}',
                ],
            },
        )


@pytest.mark.asyncio
async def test_invalid_evaluator_reply_retries_once_then_fails(monkeypatch):
    with pytest.raises(RuntimeError, match="decisión válida"):
        await _events(
            _conditional_definition(),
            monkeypatch,
            {
                "worker": ["resultado"],
                "judge": ["no es json", "tampoco"],
            },
        )


@pytest.mark.asyncio
async def test_evaluator_requires_reason_in_structured_reply(monkeypatch):
    with pytest.raises(RuntimeError, match="decisión válida"):
        await _events(
            _conditional_definition(),
            monkeypatch,
            {
                "worker": ["resultado"],
                "judge": ['{"approved": true}', '{"approved": true}'],
            },
        )
