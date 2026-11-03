"""Build-CaseTool.ps1 fills this trusted generated bundle; no repo imports."""

__CASE_TOOL_BUNDLE__ = ""
_case_runtime = None
_case_directory = None


def _runtime():
    global _case_runtime, _case_directory
    if _case_runtime is not None:
        return _case_runtime
    if not __CASE_TOOL_BUNDLE__:
        raise RuntimeError("The reviewed generated case-tool bundle is required.")
    import base64
    import importlib.util
    import io
    import json
    import sys
    import tempfile
    import zipfile
    from pathlib import Path

    directory = tempfile.TemporaryDirectory(prefix="asre-case-")
    try:
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(__CASE_TOOL_BUNDLE__, validate=True))) as archive:
            allowed = {
                "_asre_case/__init__.py", "_asre_case/agent_case.py", "_asre_case/servicenow.py",
                "_asre_case/patch_provider.py",
                "_asre_case/commissioning.json",
                "_asre_case/runtime-configuration.json",
                *("_asre_case/contracts/" + name + ".schema.json" for name in (
                    "servicenow-case", "servicenow-case-write", "servicenow-note-write",
                    "servicenow-feedback-ack", "servicenow-action-audit", "servicenow-adapter-response",
                )),
            }
            if set(archive.namelist()) != allowed or len(archive.namelist()) != len(allowed):
                raise RuntimeError("Unexpected generated case-tool package members.")
            archive.extractall(directory.name)
        package = Path(directory.name) / "_asre_case"
        spec = importlib.util.spec_from_file_location(
            "_asre_case", package / "__init__.py", submodule_search_locations=[str(package)],
        )
        if "_asre_case" in sys.modules:
            raise RuntimeError("An unrelated case-tool package is already loaded.")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
            from _asre_case import agent_case
            agent_case.freeze_runtime_configuration(json.loads((package / "runtime-configuration.json").read_text(encoding="utf-8")))
        except Exception:
            for name in tuple(sys.modules):
                if name == "_asre_case" or name.startswith("_asre_case."):
                    sys.modules.pop(name, None)
            raise
        _case_runtime, _case_directory = agent_case, directory
        return agent_case
    except Exception:
        directory.cleanup()
        raise RuntimeError("The generated case-tool package could not be loaded.") from None


def main(action: str, investigation_id: str = "", alert_id: str = "", evidence_json: str = "",
         findings_note: str = "", recurrence_id: str = "", profile: str = "disk") -> dict:
    try:
        return _runtime().main(action, investigation_id, alert_id, evidence_json, findings_note, recurrence_id, profile)
    except Exception:
        return {"status": "blocked", "message": "The reviewed fixed case-tool runtime package is unavailable."}
