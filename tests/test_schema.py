import inspect
import json

from agent_pty import Pty
from agent_pty.schema import _PUBLIC_METHODS, generate_schema


def test_schema_is_json_serializable():
    schema = generate_schema()
    json.dumps(schema, default=str)


def test_schema_methods_match_public_api():
    schema = generate_schema()
    assert set(schema["methods"].keys()) == set(_PUBLIC_METHODS)


def test_schema_param_names_match_signatures():
    schema = generate_schema()
    for method_name in _PUBLIC_METHODS:
        actual = list(inspect.signature(getattr(Pty, method_name)).parameters.keys())
        documented = [p["name"] for p in schema["methods"][method_name]["params"]]
        assert documented == actual, f"param mismatch for {method_name}"


def test_schema_marks_required_vs_optional():
    schema = generate_schema()
    spawn_params = {p["name"]: p for p in schema["methods"]["spawn"]["params"]}
    assert spawn_params["name"]["required"] is True
    assert spawn_params["cmd"]["required"] is False
    assert spawn_params["cmd"]["default"] is None
    assert spawn_params["cols"]["default"] == 80
