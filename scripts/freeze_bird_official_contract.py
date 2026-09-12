"""Freeze the BIRD-Interact official contract into a JSON fixture.

Reads ONLY the explicit allowlisted files below from the pinned upstream
checkout, extracts contract facts via `ast`, and writes
`tests/fixtures/bird/official-contract.v1.json`.

Safety properties:
- verifies the upstream checkout HEAD equals the frozen revision first;
- never traverses beyond the allowlisted files;
- never reads or prints evaluator-only content, SQL text, or task content;
- fails closed when any expected extraction comes back empty.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_ROOT = REPO_ROOT / "_upstream" / "BIRD-Interact"
EXPECTED_REVISION = "451fe2c3518ee1cf908d8139e2913483bd519381"
FIXTURE_RELPATH = Path("tests/fixtures/bird/official-contract.v1.json")

ALLOWLISTED_FILES = (
    "BIRD-Interact-ADK/shared/models.py",
    "BIRD-Interact-ADK/shared/config.py",
    "BIRD-Interact-ADK/system_agent/server.py",
    "BIRD-Interact-ADK/system_agent/tools.py",
    "BIRD-Interact-ADK/system_agent/adk_runtime.py",
    "BIRD-Interact-ADK/orchestrator/runner.py",
    "BIRD-Interact-ADK/orchestrator/ainteract.py",
    "BIRD-Interact-ADK/orchestrator/cinteract.py",
)

_COST_PATTERN = re.compile(r"Cost:\s*([0-9]+(?:\.[0-9]+)?)\s*bird-coins?\.")


def _verify_revision() -> None:
    completed = subprocess.run(
        ["git", "-C", str(UPSTREAM_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    head = completed.stdout.strip()
    if completed.returncode != 0 or head != EXPECTED_REVISION:
        raise SystemExit(
            f"upstream revision drift: HEAD={head!r} expected={EXPECTED_REVISION!r}"
        )


def _parse(relpath: str) -> ast.Module:
    source = (UPSTREAM_ROOT / relpath).read_text(encoding="utf-8")
    return ast.parse(source, filename=relpath)


def _class_field_names(tree: ast.Module, class_name: str) -> list[str]:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return [
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
            ]
    raise SystemExit(f"class not found: {class_name}")


def _returned_dict_keys(tree: ast.Module, class_name: str, method_name: str) -> list[str]:
    for node in tree.body:
        if not (isinstance(node, ast.ClassDef) and node.name == class_name):
            continue
        for statement in node.body:
            if not (
                isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
                and statement.name == method_name
            ):
                continue
            keys: list[str] = []
            for item in ast.walk(statement):
                if isinstance(item, ast.Return) and isinstance(item.value, ast.Dict):
                    keys.extend(
                        key.value
                        for key in item.value.keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    )
            return sorted(set(keys))
    raise SystemExit(f"method not found: {class_name}.{method_name}")


def _assign_dict_keys(tree: ast.Module, function_name: str, target_name: str) -> list[str]:
    for node in ast.walk(tree):
        if not (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ):
            continue
        for item in ast.walk(node):
            if (
                isinstance(item, ast.Assign)
                and len(item.targets) == 1
                and isinstance(item.targets[0], ast.Name)
                and item.targets[0].id == target_name
                and isinstance(item.value, ast.Dict)
            ):
                return [
                    key.value
                    for key in item.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                ]
    raise SystemExit(f"assignment not found: {function_name} -> {target_name}")


def _collect_action_names(tree: ast.Module, builder_name: str, factory_name: str) -> list[str]:
    for node in tree.body:
        if not (isinstance(node, ast.FunctionDef) and node.name == builder_name):
            continue
        names: list[str] = []
        for item in ast.walk(node):
            if (
                isinstance(item, ast.Call)
                and isinstance(item.func, ast.Name)
                and item.func.id == factory_name
                and item.args
                and isinstance(item.args[0], ast.Name)
            ):
                names.append(item.args[0].id)
        return names
    raise SystemExit(f"action builder not found: {builder_name}")


def _action_cost(tree: ast.Module, action_name: str) -> str:
    for node in tree.body:
        if not (isinstance(node, ast.FunctionDef) and node.name == action_name):
            continue
        docstring = ast.get_docstring(node) or ""
        match = _COST_PATTERN.search(docstring)
        if match is None:
            raise SystemExit(f"cost docstring not found for action: {action_name}")
        return match.group(1)
    raise SystemExit(f"action function not found: {action_name}")


def _post_endpoints(tree: ast.Module, action_name: str) -> list[dict[str, Any]]:
    for node in tree.body:
        if not (isinstance(node, ast.FunctionDef) and node.name == action_name):
            continue
        endpoints: list[dict[str, Any]] = []
        for item in ast.walk(node):
            if not (isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute)):
                continue
            if item.func.attr != "post" or not item.args:
                continue
            url_node = item.args[0]
            path: str | None = None
            if isinstance(url_node, ast.Call) and url_node.args:
                if isinstance(url_node.args[0], ast.Constant):
                    path = str(url_node.args[0].value)
            elif isinstance(url_node, ast.JoinedStr) and url_node.values:
                tail = url_node.values[-1]
                if isinstance(tail, ast.Constant):
                    path = str(tail.value)
            if path is None:
                raise SystemExit(f"unresolved endpoint path in action: {action_name}")
            request_fields: list[str] = []
            for keyword in item.keywords:
                if keyword.arg == "json" and isinstance(keyword.value, ast.Dict):
                    request_fields = [
                        key.value
                        for key in keyword.value.keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    ]
            response_keys = _response_keys_for_call(node, item)
            endpoints.append(
                {
                    "action": action_name,
                    "path": path,
                    "request_fields": request_fields,
                    "response_keys": response_keys,
                }
            )
        if not endpoints:
            raise SystemExit(f"no HTTP post calls found in action: {action_name}")
        return endpoints
    raise SystemExit(f"action function not found: {action_name}")


def _response_keys_for_call(function_node: ast.AST, post_call: ast.Call) -> list[str]:
    """Collect response keys read from `resp = <post_call>` -> `data = resp.json()` -> `x.get(...)`."""

    response_names: set[str] = set()
    for item in ast.walk(function_node):
        if (
            isinstance(item, ast.Assign)
            and item.value is post_call
            and len(item.targets) == 1
            and isinstance(item.targets[0], ast.Name)
        ):
            response_names.add(item.targets[0].id)
    for item in ast.walk(function_node):
        if (
            isinstance(item, ast.Assign)
            and len(item.targets) == 1
            and isinstance(item.targets[0], ast.Name)
            and isinstance(item.value, ast.Call)
            and isinstance(item.value.func, ast.Attribute)
            and item.value.func.attr == "json"
            and isinstance(item.value.func.value, ast.Name)
            and item.value.func.value.id in response_names
        ):
            response_names.add(item.targets[0].id)
    keys: list[str] = []
    for item in ast.walk(function_node):
        if (
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Attribute)
            and item.func.attr == "get"
            and item.args
            and isinstance(item.args[0], ast.Constant)
            and isinstance(item.args[0].value, str)
        ):
            receiver = item.func.value
            is_json_chain = (
                isinstance(receiver, ast.Call)
                and isinstance(receiver.func, ast.Attribute)
                and receiver.func.attr == "json"
                and isinstance(receiver.func.value, ast.Name)
                and receiver.func.value.id in response_names
            )
            is_parsed_name = (
                isinstance(receiver, ast.Name) and receiver.id in response_names
            )
            if is_json_chain or is_parsed_name:
                keys.append(item.args[0].value)
        if (
            isinstance(item, ast.Subscript)
            and isinstance(item.value, ast.Name)
            and item.value.id in response_names
            and isinstance(item.slice, ast.Constant)
            and isinstance(item.slice.value, str)
        ):
            keys.append(item.slice.value)
    return sorted(set(keys))


def _arg_options(tree: ast.Module, function_name: str) -> list[str]:
    for node in tree.body:
        if not (isinstance(node, ast.FunctionDef) and node.name == function_name):
            continue
        options: list[str] = []
        for item in ast.walk(node):
            if (
                isinstance(item, ast.Call)
                and isinstance(item.func, ast.Attribute)
                and item.func.attr == "add_argument"
                and item.args
                and isinstance(item.args[0], ast.Constant)
                and isinstance(item.args[0].value, str)
                and item.args[0].value.startswith("--")
            ):
                options.append(item.args[0].value)
        return options
    raise SystemExit(f"argparse function not found: {function_name}")


def _module_name(relpath: str) -> str:
    without_prefix = relpath.removeprefix("BIRD-Interact-ADK/")
    return without_prefix.removesuffix(".py").replace("/", ".")


def _require_non_empty(label: str, values: list[str] | tuple[str, ...]) -> list[str]:
    if not values:
        raise SystemExit(f"empty extraction: {label}")
    return list(values)


def main() -> int:
    _verify_revision()

    models_tree = _parse("BIRD-Interact-ADK/shared/models.py")
    config_tree = _parse("BIRD-Interact-ADK/shared/config.py")
    server_tree = _parse("BIRD-Interact-ADK/system_agent/server.py")
    tools_tree = _parse("BIRD-Interact-ADK/system_agent/tools.py")
    runtime_tree = _parse("BIRD-Interact-ADK/system_agent/adk_runtime.py")
    runner_tree = _parse("BIRD-Interact-ADK/orchestrator/runner.py")
    ainteract_tree = _parse("BIRD-Interact-ADK/orchestrator/ainteract.py")
    cinteract_tree = _parse("BIRD-Interact-ADK/orchestrator/cinteract.py")

    action_names = _require_non_empty(
        "actions",
        _collect_action_names(tools_tree, "get_ainteract_tools", "FunctionTool"),
    )
    actions = [
        {"name": name, "coin_cost": _action_cost(tools_tree, name)}
        for name in action_names
    ]
    outbound_endpoints: list[dict[str, Any]] = []
    for name in action_names:
        outbound_endpoints.extend(_post_endpoints(tools_tree, name))

    runner_options = _require_non_empty("runner options", _arg_options(runner_tree, "main"))
    argv_pattern: list[str] = []
    for option in runner_options:
        argv_pattern.append(option)
        argv_pattern.append("{" + option.removeprefix("--").replace("-", "_") + "}")

    fixture: dict[str, Any] = {
        "source_revision": EXPECTED_REVISION,
        "frozen_by": "scripts/freeze_bird_official_contract.py",
        "extracted_files": list(ALLOWLISTED_FILES),
        "system_agent": {
            "init_session": {
                "endpoint": "/init_session",
                "request_fields": _require_non_empty(
                    "init request fields",
                    _class_field_names(server_tree, "SessionInitRequest"),
                ),
                "response_fields": _require_non_empty(
                    "init response fields",
                    _returned_dict_keys(runtime_tree, "AdkRuntime", "init_session"),
                ),
            },
            "run_session": {
                "endpoint": "/run_session",
                "request_fields": _require_non_empty(
                    "run request fields",
                    _class_field_names(server_tree, "SessionRunRequest"),
                ),
                "response_fields": _require_non_empty(
                    "run response fields",
                    _returned_dict_keys(runtime_tree, "AdkRuntime", "run_turn"),
                ),
            },
        },
        "actions": actions,
        "submit_sql_response_fields": _require_non_empty(
            "submit response fields",
            _class_field_names(models_tree, "SubmitSQLResponse"),
        ),
        "outbound_endpoints": outbound_endpoints,
        "orchestrator": {
            "module": _module_name("BIRD-Interact-ADK/orchestrator/runner.py"),
            "argv_pattern": argv_pattern,
            "json_output_fields": _require_non_empty(
                "runner output keys", _assign_dict_keys(runner_tree, "_save", "output")
            ),
            "a_interact_result_fields": _require_non_empty(
                "a-interact result keys",
                _assign_dict_keys(ainteract_tree, "run_single_task", "result"),
            ),
            "c_interact_result_fields": _require_non_empty(
                "c-interact result keys",
                _assign_dict_keys(cinteract_tree, "run_single_task", "result"),
            ),
            "env_names": [
                name.upper()
                for name in _require_non_empty(
                    "settings fields", _class_field_names(config_tree, "Settings")
                )
            ],
            "reviewed_annotations": {
                "load_tasks_semantics": "head-truncation: runner.load_tasks parses the whole JSONL then slices tasks[:limit], so a single-task run requires a pre-split per-task data file",
                "per_task_data_file_required": True,
                "gt_surface": "the --data JSONL contains GT fields and may only be consumed by the official orchestrator process; the Runner and system agent never read it",
            },
        },
    }

    destination = REPO_ROOT / FIXTURE_RELPATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"revision verified: {EXPECTED_REVISION}")
    print(f"files read: {len(ALLOWLISTED_FILES)} (allowlist only)")
    print(f"actions extracted: {len(actions)}")
    print(f"outbound endpoints extracted: {len(outbound_endpoints)}")
    print(f"fixture written: {FIXTURE_RELPATH} ({destination.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
