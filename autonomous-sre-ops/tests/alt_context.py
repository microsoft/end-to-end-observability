"""Shared helpers for tests that must prove a module follows the deployment context.

``run_with_context`` runs a snippet in a fresh interpreter that loads another context file
(default: the alternate synthetic context). ``leaks_in_module`` imports a module under the
alternate context and reports every attribute, function constant or class member that still
contains a value that only the primary synthetic context has, which is how a retained
hard-coded identifier is detected.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SYNTHETIC = FIXTURES / "context.synthetic.json"
ALT = FIXTURES / "context.alt.json"


def _leaves(value, prefix=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, f"{prefix}.{key}" if prefix else key)
    elif isinstance(value, str):
        yield prefix, value


def synthetic_only_values():
    """Lower-cased values of the synthetic context that differ from the alternate context."""
    synthetic = dict(_leaves(json.loads(SYNTHETIC.read_text(encoding="utf-8"))))
    alt = dict(_leaves(json.loads(ALT.read_text(encoding="utf-8"))))
    return sorted({value.lower() for path, value in synthetic.items() if alt.get(path) != value})


def run_with_context(code, context_file=ALT, extra_env=None, timeout=120, pythonpath=()):
    """Run ``python -c code`` from the subproject root with the given context file (None = no context)."""
    env = {key: value for key, value in os.environ.items() if key not in ("ASRE_CONTEXT", "ASRE_CONTEXT_FILE")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), *map(str, pythonpath)])
    if context_file is not None:
        env["ASRE_CONTEXT_FILE"] = str(context_file)
    env.update(extra_env or {})
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True,
                          text=True, timeout=timeout, check=False)


_SCAN = r"""
import collections.abc, functools, importlib, json, re, sys, types
needles = json.loads(sys.argv[1])
module = importlib.import_module(sys.argv[2])
found = set()
seen = set()

def walk(value, path, depth=0):
    if depth > 8 or id(value) in seen:
        return
    if isinstance(value, (str, bytes)):
        text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
        if any(needle in text.lower() for needle in needles):
            found.add(path)
        return
    seen.add(id(value))
    if isinstance(value, re.Pattern):
        walk(value.pattern, path + ".pattern", depth + 1)
    elif isinstance(value, types.CodeType):
        for index, item in enumerate(value.co_consts):
            walk(item, f"{path}.const[{index}]", depth + 1)
    elif isinstance(value, types.FunctionType):
        walk(value.__code__, path, depth + 1)
        walk(value.__defaults__, path + ".defaults", depth + 1)
        walk(value.__kwdefaults__, path + ".kwdefaults", depth + 1)
    elif isinstance(value, (staticmethod, classmethod)):
        walk(value.__func__, path, depth + 1)
    elif isinstance(value, functools.partial):
        walk(value.func, path + ".func", depth + 1)
        walk(value.args, path + ".args", depth + 1)
        walk(value.keywords, path + ".keywords", depth + 1)
    elif hasattr(value, "__wrapped__"):
        walk(value.__wrapped__, path + ".wrapped", depth + 1)
    elif isinstance(value, property):
        for name in ("fget", "fset", "fdel"):
            walk(getattr(value, name), f"{path}.{name}", depth + 1)
    elif isinstance(value, type):
        if value.__module__ == module.__name__:
            for name, item in vars(value).items():
                if not name.startswith("__"):
                    walk(item, f"{path}.{name}", depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            walk(key, path + "[key]", depth + 1)
            walk(item, f"{path}[{key!r}]", depth + 1)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            walk(item, f"{path}[{index}]", depth + 1)
    elif hasattr(value, "__dataclass_fields__"):
        for name in value.__dataclass_fields__:
            walk(getattr(value, name), f"{path}.{name}", depth + 1)
    elif isinstance(value, collections.abc.Mapping):
        walk(dict(value), path, depth + 1)

for name, value in list(vars(module).items()):
    if name.startswith("__"):
        continue
    if isinstance(value, types.ModuleType):
        continue
    if isinstance(value, (types.FunctionType, type)) and getattr(value, "__module__", None) != module.__name__:
        continue
    walk(value, name)
print(json.dumps(sorted(found)))
"""


def leaks_in_module(module_name, context_file=ALT, pythonpath=()):
    """Names in ``module_name`` that still hold a value unique to the primary synthetic context.

    The module is imported in a fresh interpreter under ``context_file``; an empty list means every
    deployment-specific value in the module came from that context.
    """
    arguments = f"import sys\nsys.argv = ['scan', {json.dumps(synthetic_only_values())!r}, {module_name!r}]\n"
    result = run_with_context(arguments + _SCAN, context_file=context_file, pythonpath=pythonpath)
    if result.returncode != 0:
        raise AssertionError(f"Importing {module_name} under {context_file} failed:\n{result.stderr[-2000:]}")
    return json.loads(result.stdout.strip().splitlines()[-1])
