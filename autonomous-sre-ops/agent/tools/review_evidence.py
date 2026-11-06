def main(action: str, resource_id: str, request_id: str = "", page: int = 0) -> dict:
    """Request Review evidence or read its receipt. Concatenate numbered receipt_fragment values for large receipts. Never repairs or creates tickets."""
    import base64
    import json
    from datetime import datetime, timezone
    from email.utils import format_datetime
    from time import monotonic, sleep
    from urllib.error import HTTPError, URLError
    from urllib.request import HTTPRedirectHandler, Request, build_opener
    from uuid import UUID, uuid4
    from xml.etree import ElementTree

    from azure.identity import DefaultAzureCredential

    subscription = "00000001-000a-4000-a000-000000000001"
    targets = {
        f"/subscriptions/{subscription}/resourcegroups/rg-asre-{role}-cus/providers/microsoft.compute/virtualmachines/vm-asre-{role}-cus"
        for role in ("linux", "patch", "backup")
    }
    commands = {"inspect", "telemetry", "patch_jobs", "backup_jobs"}
    if isinstance(page, bool) or not isinstance(page, int) or page < 0 or (action != "result" and page != 0):
        raise ValueError("Only result lookup accepts a nonnegative integer receipt page.")
    if not isinstance(resource_id, str) or resource_id.lower().rstrip("/") not in targets:
        raise PermissionError("The evidence target is outside the three fixed lab VMs.")
    target = resource_id.lower().rstrip("/")
    if action not in commands | {"result"}:
        raise PermissionError("Only fixed Review-only evidence and result lookup are admitted.")
    if action == "result":
        if not request_id or str(UUID(request_id)) != request_id:
            raise ValueError("Result lookup requires the exact canonical UUID returned by submission.")
    elif request_id:
        raise ValueError("Do not replace or reuse request IDs; pass an ID only for result lookup.")
    else:
        request_id = str(uuid4())
    if action == "patch_jobs" and "/rg-asre-patch-cus/" not in target:
        raise PermissionError("Patch evidence is restricted to the patch VM.")
    if action == "backup_jobs" and "/rg-asre-backup-cus/" not in target:
        raise PermissionError("Backup evidence is restricted to the protected backup VM.")

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise PermissionError("Authenticated evidence requests cannot follow redirects.")

    token = DefaultAzureCredential().get_token("https://storage.azure.com/.default").token
    headers = {
        "Authorization": "Bearer " + token,
        "x-ms-version": "2023-11-03",
        "x-ms-date": format_datetime(datetime.now(timezone.utc), usegmt=True),
        "x-ms-client-request-id": request_id,
    }
    host = "stasre8a327010f9"
    uri = f"https://{host}.blob.core.windows.net/evidence/tool-results/{request_id}.json"
    if action == "result":
        request = Request(uri, headers=headers)
    else:
        payload = {
            "request_id": request_id, "command": action, "resource_id": target,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        envelope = ElementTree.Element("QueueMessage")
        ElementTree.SubElement(envelope, "MessageText").text = base64.b64encode(json.dumps(payload).encode()).decode()
        request = Request(
            f"https://{host}.queue.core.windows.net/agent-requests/messages?messagettl=600",
            method="POST", data=ElementTree.tostring(envelope),
            headers={**headers, "Content-Type": "application/xml"},
        )
    deadline = monotonic() + 90 if action == "result" and page == 0 else None
    opener = build_opener(NoRedirect)
    while True:
        timeout = min(30, max(1, deadline - monotonic())) if deadline is not None else 30
        try:
            with opener.open(request, timeout=timeout) as response:
                status = response.status
                raw = response.read(4 * 1024 * 1024 + 1)
            break
        except HTTPError as error:
            if action == "result" and error.code == 404:
                error.close()
                remaining = deadline - monotonic() if deadline is not None else 0
                if remaining > 0:
                    sleep(min(5, remaining))
                    continue
                return {
                    "status": "not_available", "request_id": request_id, "mode": "Review",
                    "message": "No terminal receipt exists within the bounded lookup window. This is not proof of completion, healthy state or recovery.",
                }
            raise RuntimeError(f"Private Azure evidence request {request_id} failed with HTTP {error.code}.") from error
        except (URLError, TimeoutError) as error:
            raise RuntimeError(
                f"Evidence submission or lookup outcome is unknown for {request_id}; inspect its receipt before resubmitting."
            ) from error
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("The evidence exceeded its independent response bound.")
    if action == "result":
        result = json.loads(raw)
        if (
            not isinstance(result, dict) or result.get("request_id") != request_id
            or result.get("resource_id") != target or result.get("mode") != "Review"
            or result.get("command") not in commands or result.get("status") not in {"completed", "failed"}
        ):
            raise ValueError("The immutable receipt does not match the requested Review-only target and request.")
        wire = json.dumps(result, ensure_ascii=True)
        if len(wire) <= 2600:
            if page != 0:
                raise ValueError("The complete receipt has only page zero.")
            return {**result, "receipt_uri": uri}
        page_count = (len(wire) + 999) // 1000
        if page >= page_count:
            raise ValueError("The requested page is outside the immutable receipt.")
        return {
            "status": result["status"], "request_id": request_id, "resource_id": target,
            "mode": "Review", "command": result["command"], "receipt_uri": uri,
            "representation": "receipt_fragment", "page": page, "page_count": page_count,
            "next_page": page + 1 if page + 1 < page_count else None,
            "receipt_fragment": wire[page * 1000:(page + 1) * 1000],
            "message": "This is not the complete evidence. Concatenate all numbered fragments and parse the JSON before drawing conclusions.",
        }
    if status != 201:
        raise RuntimeError(f"Azure did not acknowledge queue acceptance for request {request_id}.")
    message_id = ElementTree.fromstring(raw).findtext(".//MessageId")
    if not message_id:
        raise ValueError(f"Azure omitted its queue acknowledgment for request {request_id}.")
    return {
        "status": "accepted", "request_id": request_id, "azure_queue_message_id": message_id,
        "mode": "Review", "receipt_uri": uri,
        "message": "Accepted for asynchronous evidence collection only; retrieve the terminal receipt with action=result.",
    }
