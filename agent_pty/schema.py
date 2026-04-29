from __future__ import annotations

import inspect
import json
from typing import Any, get_type_hints

from agent_pty import Pty

_PUBLIC_METHODS = ("spawn", "send", "snapshot", "wait_for", "kill", "list")


def _type_str(t: Any) -> str:
    if t is None or t is type(None):
        return "None"
    return getattr(t, "__name__", None) or str(t)


def generate_schema() -> dict[str, Any]:
    methods: dict[str, Any] = {}
    for name in _PUBLIC_METHODS:
        func = getattr(Pty, name)
        sig = inspect.signature(func)
        try:
            hints = get_type_hints(func)
        except Exception:
            hints = {}
        params = []
        for pname, param in sig.parameters.items():
            entry: dict[str, Any] = {
                "name": pname,
                "type": _type_str(hints.get(pname)),
                "required": param.default is inspect.Parameter.empty,
            }
            if param.default is not inspect.Parameter.empty:
                entry["default"] = param.default
            params.append(entry)
        methods[name] = {
            "params": params,
            "returns": _type_str(hints.get("return")),
        }
    return {"name": "agent_pty.Pty", "methods": methods}


def main() -> None:
    print(json.dumps(generate_schema(), indent=2, default=str))


if __name__ == "__main__":
    main()
