import os
import urllib.parse
import urllib.request
import json


PUSHSAFER_API_URL = "https://www.pushsafer.com/api"


def send_pushsafer(
    message: str,
    title: str = "INS",
) -> dict:
    private_key = os.getenv("PUSHSAFER_PRIVATE_KEY")

    if not private_key:
        raise RuntimeError(
            "PUSHSAFER_PRIVATE_KEY ist nicht konfiguriert"
        )

    data = urllib.parse.urlencode(
        {
            "k": private_key,
            "t": title,
            "m": message,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        PUSHSAFER_API_URL,
        data=data,
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8")

    result = json.loads(body)

    if result.get("status") != 1:
        raise RuntimeError(
            f"Pushsafer Fehler: {result}"
        )

    return result
