"""Minimal APEX integration with one effectful capability."""
from __future__ import annotations

import os

from apex import Engine, ProtectedRuntime


TASK = 'Save the exact text "status: ready" as a note.'
ARGUMENTS = {"text": "status: ready"}

CAPABILITIES = [{
    "name": "save_note",
    "description": "Persist a text note in the local application.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "x-task-derived": True,
            },
        },
        "required": ["text"],
        "additionalProperties": False,
    },
    "argument_types": {"text": "natural_language"},
    "effect": True,
    "observation": False,
}]


def save_note(arguments: dict) -> dict:
    """Replace this function with the application's native implementation."""
    return {"saved": arguments["text"]}


def main() -> None:
    model = os.environ.get("APEX_MODEL")
    if not model:
        raise SystemExit("Set APEX_MODEL to an OpenAI-compatible model name.")

    engine = Engine(model)
    plan = engine.perceive(CAPABILITIES)
    contract = engine.contract(TASK)

    with ProtectedRuntime(
            engine, plan, contract, task_id="quickstart") as runtime:
        result = runtime.invoke(
            "save_note",
            ARGUMENTS,
            save_note,
        )

    print({
        "route": result.decision.route,
        "reason": result.decision.reason,
        "executed": result.executed,
        "value": result.value,
    })


if __name__ == "__main__":
    main()
