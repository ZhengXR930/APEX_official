"""Typed semantic roles executed through one OpenAI Agents SDK run."""
from __future__ import annotations

import asyncio
import json
import threading


class AgentRoleError(RuntimeError):
    """An Agent run ended without one deterministically valid proposal."""


def typed_tool(name: str, description: str, properties: dict,
               required=()) -> dict:
    """Build the single submission tool shared by every defender Agent."""
    return {"type": "function", "function": {
        "name": str(name), "description": str(description),
        "parameters": {
            "type": "object", "properties": dict(properties),
            "required": list(required), "additionalProperties": False,
        },
    }}


def run_typed_agent(*, name: str, model: str, prompt: str,
                    tool_schema: dict, instructions: str = "",
                    validator=None, timeout_seconds: float = 120.0
                    ) -> tuple[dict, list[dict]]:
    """Run one Agent session; validator feedback stays inside that session.

    The tool records only a candidate accepted by deterministic validation.
    There is no second request, direct-API fallback, or partial result.
    """
    from agents import (Agent, FunctionTool, ModelSettings, Runner,
                        ToolsToFinalOutputResult, set_tracing_disabled)
    from apex.core.client import _NO_TEMP, agent_sdk_model

    function = dict(tool_schema.get("function") or {})
    tool_name = str(function.get("name") or "")
    parameters = function.get("parameters")
    if not tool_name or not isinstance(parameters, dict):
        raise ValueError("typed Agent requires one valid function tool schema")

    accepted: list[dict] = []
    feedback: list[list[str]] = []

    async def submit(_context, raw_arguments: str):
        try:
            value = json.loads(raw_arguments)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            errors = ["invalid JSON: " + str(exc)]
            feedback.append(errors)
            return json.dumps({"accepted": False, "errors": errors})
        if not isinstance(value, dict):
            errors = ["typed submission must be an object"]
            feedback.append(errors)
            return json.dumps({"accepted": False, "errors": errors})
        candidate, errors = value, []
        if callable(validator):
            verdict = validator(value)
            if (isinstance(verdict, tuple) and len(verdict) == 2):
                candidate, errors = verdict
            else:
                errors = verdict
            errors = [str(item) for item in (errors or ())]
        if errors:
            feedback.append(errors)
            return json.dumps({
                "accepted": False, "errors": errors,
                "instruction": "Correct the complete proposal and call the tool again."
            }, ensure_ascii=False)
        if not accepted:
            accepted.append(candidate)
        return json.dumps({
            "accepted": True,
            "instruction": "The candidate was recorded. Finish the run."
        })

    submit_tool = FunctionTool(
        name=tool_name,
        description=str(function.get("description") or
                        "Submit one semantic candidate."),
        params_json_schema=parameters,
        on_invoke_tool=submit,
        strict_json_schema=False,
    )
    role_instructions = (
        instructions.strip() +
        "\nYou generate semantic candidates, never authorization. Call " +
        tool_name + " until its deterministic validator returns accepted=true, "
        "then finish. Do not use any alternative output path."
    ).strip()
    def finish_on_valid_submission(_context, _tool_results):
        # A validator rejection stays in the same Agent session. Once accepted,
        # the typed proposal itself is the final output; asking the model for a
        # ceremonial "done" turn wastes one provider request.
        return ToolsToFinalOutputResult(
            is_final_output=bool(accepted),
            final_output="accepted" if accepted else None)

    agent = Agent(
        name=name,
        instructions=role_instructions,
        model=agent_sdk_model(model),
        tools=[submit_tool],
        model_settings=ModelSettings(
            temperature=None if model in _NO_TEMP else 0.0,
            tool_choice="auto"),
        tool_use_behavior=finish_on_valid_submission,
    )
    set_tracing_disabled(True)

    async def execute():
        return await asyncio.wait_for(
            Runner.run(agent, prompt, max_turns=6),
            timeout=max(1.0, float(timeout_seconds)))

    run_result = {}
    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            run_result["value"] = asyncio.run(execute())
        else:
            # Tool mediation may be invoked synchronously from an async target
            # Agent callback. Run the one semantic Agent session on a private
            # loop instead of nesting asyncio.run in the caller's loop.
            def run_private_loop():
                try:
                    run_result["value"] = asyncio.run(execute())
                except BaseException as exc:  # re-raised in caller thread
                    run_result["error"] = exc

            worker = threading.Thread(target=run_private_loop, daemon=True)
            worker.start()
            worker.join(max(1.0, float(timeout_seconds)) + 5.0)
            if worker.is_alive():
                raise TimeoutError(f"typed Agent exceeded {timeout_seconds}s")
            if "error" in run_result:
                raise run_result["error"]
    except Exception as exc:
        raise AgentRoleError(type(exc).__name__ + ":" + str(exc)[:240]) from exc
    if len(accepted) != 1:
        raise AgentRoleError("valid_submission_count:" + str(len(accepted)))
    sdk_result = run_result.get("value")
    model_requests = len(getattr(sdk_result, "raw_responses", ()) or ())
    return accepted[0], [{
        "run": 1, "ok": True, "transport": "openai-agents-sdk",
        "model_requests": model_requests,
        "tool_submissions": 1 + len(feedback),
        "validator_rejections": feedback,
    }]
