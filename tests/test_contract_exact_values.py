from apex.defender.contract.compiler import validate_contract


def _validate(clauses, schema):
    task = "Get the transcript from https://example.test/v, including timestamps."
    return validate_contract(
        {"task": task, "clauses": clauses}, task,
        {"tool"}, {"task"}, {"tool": {"url", "includeTimestamps"}},
        {"tool": {"url", "includeTimestamps"}}, set(), set(),
        {"tool": {
            "url": {"type": "string"},
            "includeTimestamps": schema,
        }}, {})


def test_task_only_derive_cannot_materialize_exact_boolean():
    errors = _validate([
        {"id": "c0", "type": "derive", "instruction": "Derive timestamp flag",
         "from": ["task"], "output": "flag"},
        {"id": "c1", "type": "effect", "instruction": "Get transcript",
         "action": "tool", "arguments": {
             "url": {"literal": "https://example.test/v"},
             "includeTimestamps": {"from": "c0.flag"},
         }},
    ], {"type": "boolean"})
    assert any("exact-only argument" in error for error in errors)


def test_typed_boolean_literal_closes_exact_argument():
    errors = _validate([
        {"id": "c0", "type": "effect", "instruction": "Get transcript",
         "action": "tool", "arguments": {
             "url": {"literal": "https://example.test/v"},
             "includeTimestamps": {"literal": True},
         }},
    ], {"type": "boolean"})
    assert errors == []


def test_wrong_literal_wire_type_is_rejected():
    errors = _validate([
        {"id": "c0", "type": "effect", "instruction": "Get transcript",
         "action": "tool", "arguments": {
             "url": {"literal": "https://example.test/v"},
             "includeTimestamps": {"literal": "true"},
         }},
    ], {"type": "boolean"})
    assert any("does not match operator schema" in error for error in errors)


def test_task_only_derive_remains_valid_for_semantic_string():
    errors = _validate([
        {"id": "c0", "type": "derive", "instruction": "Derive prose",
         "from": ["task"], "output": "flag"},
        {"id": "c1", "type": "effect", "instruction": "Get transcript",
         "action": "tool", "arguments": {
             "url": {"literal": "https://example.test/v"},
             "includeTimestamps": {"from": "c0.flag"},
         }},
    ], {"type": "string", "x-task-derived": True})
    assert errors == []


def test_manifest_can_mark_local_path_as_task_derived_content():
    task = "Create and run the requested local script."
    clauses = [
        {"id": "c0", "type": "derive",
         "instruction": "derive the workspace-local script path",
         "from": ["task"], "output": "path"},
        {"id": "c1", "type": "effect", "instruction": "run script",
         "action": "run", "arguments": {
             "path": {"from": "c0.path"},
         }},
    ]
    errors = validate_contract(
        {"task": task, "clauses": clauses}, task,
        {"run"}, {"task"}, {"run": {"path"}}, {"run": {"path"}},
        set(), set(),
        {"run": {"path": {
            "type": "string",
            "x-task-derived": True,
        }}}, {}, {"run": {"path": "path"}})
    assert errors == []


def test_nested_object_literal_must_match_exact_subtree_schema():
    task = "Create a payment for product PROD-1."
    schema = {"type": "array", "minItems": 1, "items": {
        "type": "object",
        "properties": {
            "product_id": {"type": "string"},
            "quantity": {"type": "integer", "minimum": 1},
        },
        "required": ["product_id", "quantity"],
        "additionalProperties": False,
    }}
    clauses = [{
        "id": "c0", "type": "effect", "instruction": "Create payment",
        "action": "pay", "arguments": {"cart": {"literal": [
            {"product_id": "PROD-1", "amount": 50},
        ]}},
    }]
    errors = validate_contract(
        {"task": task, "clauses": clauses}, task, {"pay"}, {"task"},
        {"pay": {"cart"}}, {"pay": {"cart"}}, set(), set(),
        {"pay": {"cart": schema}}, {})
    assert any("does not match operator schema" in error for error in errors)


def test_trusted_string_literal_matching_is_case_insensitive():
    task = "Extract the result as HTML."
    clauses = [{
        "id": "c0", "type": "effect", "instruction": "Extract HTML",
        "action": "extract", "arguments": {"format": {"literal": "html"}},
    }]
    errors = validate_contract(
        {"task": task, "clauses": clauses}, task, {"extract"}, {"task"},
        {"extract": {"format"}}, {"extract": {"format"}}, set(), set(),
        {"extract": {"format": {"type": "string"}}}, {})
    assert errors == []


def test_observation_dependent_identity_effect_arg_needs_contract_closure():
    task = "Review services and tune access rules for operational connectivity."
    clauses = [
        {"id": "c0", "type": "acquire", "instruction": "List services",
         "capability": "list_services", "arguments": {}, "output": "services"},
        {"id": "c1", "type": "effect", "instruction": "Configure service",
         "action": "configure_rule", "arguments": {
             "target_id": {"from": "c0.services", "delegated": True},
         }},
    ]
    errors = validate_contract(
        {"task": task, "clauses": clauses}, task,
        {"configure_rule"}, {"task"}, {
            "list_services": set(), "configure_rule": {"target_id"}},
        {"configure_rule": {"target_id"}}, set(), {"list_services"},
        {"configure_rule": {"target_id": {"type": "string"}}}, {},
        {"configure_rule": {"target_id": "identity"}})
    assert any("identity-bearing Effect argument" in error
               for error in errors)


def test_task_semantic_derive_cannot_close_identity_effect_arg():
    task = "Configure the matching service."
    clauses = [
        {"id": "c0", "type": "derive",
         "instruction": "Derive the matching service target identity",
         "from": ["task"], "output": "target_id"},
        {"id": "c1", "type": "effect", "instruction": "Configure service",
         "action": "configure_rule", "arguments": {
             "target_id": {"from": "c0.target_id"},
         }},
    ]
    errors = validate_contract(
        {"task": task, "clauses": clauses}, task,
        {"configure_rule"}, {"task"}, {"configure_rule": {"target_id"}},
        {"configure_rule": {"target_id"}}, set(), set(),
        {"configure_rule": {"target_id": {"type": "string"}}}, {},
        {"configure_rule": {"target_id": "identity"}})
    assert any("identity-bearing Effect argument" in error
               for error in errors)


def test_task_literal_identity_effect_arg_is_closed():
    task = "Configure target svc-1."
    clauses = [{
        "id": "c0", "type": "effect", "instruction": "Configure service",
        "action": "configure_rule",
        "arguments": {"target_id": {"literal": "svc-1"}},
    }]
    errors = validate_contract(
        {"task": task, "clauses": clauses}, task,
        {"configure_rule"}, {"task"}, {"configure_rule": {"target_id"}},
        {"configure_rule": {"target_id"}}, set(), set(),
        {"configure_rule": {"target_id": {"type": "string"}}}, {},
        {"configure_rule": {"target_id": "identity"}})
    assert errors == []


def test_closed_conditional_can_select_identity_effect_arg():
    task = "Configure target svc-1."
    clauses = [
        {"id": "c0", "type": "acquire", "instruction": "List services",
         "capability": "list_services", "arguments": {}, "output": "services"},
        {"id": "c1", "type": "conditional", "instruction": "Select svc-1",
         "operator": "select_eq",
         "operands": ["c0.services", {"literal": "id"},
                      {"literal": "svc-1"}],
         "output": "selected"},
        {"id": "c2", "type": "conditional", "instruction": "Project id",
         "operator": "field",
         "operands": ["c1.selected", {"literal": "id"}],
         "output": "target_id"},
        {"id": "c3", "type": "effect", "instruction": "Configure service",
         "action": "configure_rule",
         "arguments": {"target_id": {"from": "c2.target_id"}}},
    ]
    output_schema = {"list_services": {
        "type": "array",
        "items": {"type": "object", "properties": {"id": {"type": "string"}}},
    }}
    errors = validate_contract(
        {"task": task, "clauses": clauses}, task,
        {"configure_rule"}, {"task"}, {
            "list_services": set(), "configure_rule": {"target_id"}},
        {"configure_rule": {"target_id"}}, set(), {"list_services"},
        {"configure_rule": {"target_id": {"type": "string"}}},
        output_schema, {"configure_rule": {"target_id": "identity"}})
    assert errors == []
