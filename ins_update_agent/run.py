import json
import logging
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OPTIONS = Path("/data/options.json")

TARGET_NAME = "INS-EI Pilot"
TARGET_SUFFIX = "_ins_ei"
AGENT_VERSION = "0.1.2"


def load_options():
    return json.loads(OPTIONS.read_text(encoding="utf-8"))


def supervisor_token():
    token = os.environ.get("SUPERVISOR_TOKEN")
    if token:
        return token

    for path in (
        Path("/run/s6/container_environment/SUPERVISOR_TOKEN"),
        Path("/var/run/s6/container_environment/SUPERVISOR_TOKEN"),
    ):
        try:
            token = path.read_text().strip()
            if token:
                return token
        except OSError:
            pass

    return None


def request_json(url, token=None, method="GET", body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode("utf-8")

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = Request(
        url,
        data=data,
        method=method,
        headers=headers,
    )

    with urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        payload = json.loads(raw) if raw else {}
        return response.status, payload


def find_ins_ei_slug(token):
    _, payload = request_json(
        "http://supervisor/addons",
        token=token,
    )

    addons = (
        (payload.get("data") or {}).get("addons")
        or payload.get("addons")
        or []
    )

    for addon in addons:
        slug = str(addon.get("slug") or "")

        if (
            addon.get("name") == TARGET_NAME
            or slug.endswith(TARGET_SUFFIX)
        ):
            return slug

    raise RuntimeError("INS-EI add-on not found")


def report_result(server, installation_id, command_id, ok, error=None):
    try:
        request_json(
            server.rstrip("/")
            + f"/api/v1/fleet/{installation_id}/command/{command_id}/result",
            method="POST",
            body={
                "ok": ok,
                "error": error,
            },
            timeout=10,
        )
    except Exception:
        pass


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    log = logging.getLogger("ins_update_agent")

    token = supervisor_token()

    if not token:
        log.error("Supervisor token unavailable")
        return

    options = load_options()

    server = options["server_url"].rstrip("/")
    installation_id = options["installation_id"]
    interval = max(
        30,
        int(options.get("interval_seconds", 60)),
    )

    log.info(
        "INS Update Agent started | installation=%s | interval=%ss",
        installation_id,
        interval,
    )

    while True:
        try:
            try:
                request_json(server+f"/api/v1/fleet/{installation_id}/update-agent/heartbeat",
                             method="POST",body={"agent_version":AGENT_VERSION},timeout=10)
            except Exception as exc:
                log.warning("heartbeat | failed | %s",exc)

            _, payload = request_json(
                server
                + f"/api/v1/fleet/{installation_id}/update-agent/command",
                timeout=15,
            )

            command = payload.get("command")

            if command and command.get("type") == "UPDATE_ADDON":
                command_id = command["id"]
                target = command.get("target_version")

                slug = find_ins_ei_slug(token)

                log.warning(
                    "update | claimed | command=%s | target=%s | slug=%s",
                    command_id,
                    target,
                    slug,
                )

                try:
                    status, _ = request_json(
                        "http://supervisor/store/addons/"
                        + slug
                        + "/update",
                        token=token,
                        method="POST",
                        body={
                            "backup": False,
                            "background": False,
                        },
                        timeout=120,
                    )

                    ok = 200 <= status < 300

                    report_result(
                        server,
                        installation_id,
                        command_id,
                        ok,
                    )

                    log.warning(
                        "update | supervisor accepted | command=%s | target=%s",
                        command_id,
                        target,
                    )

                except HTTPError as exc:
                    detail = exc.read().decode(
                        "utf-8",
                        errors="replace",
                    )

                    report_result(
                        server,
                        installation_id,
                        command_id,
                        False,
                        f"HTTP_{exc.code}: {detail[:300]}",
                    )

                    log.error(
                        "update | failed | command=%s | HTTP %s | %s",
                        command_id,
                        exc.code,
                        detail,
                    )

        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
            RuntimeError,
        ) as exc:
            log.warning("poll | failed | %s", exc)

        time.sleep(interval)


if __name__ == "__main__":
    main()
