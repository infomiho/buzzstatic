import os
from urllib.request import Request, urlopen


def main() -> None:
    control_host = os.getenv("BUZZ_DOMAIN") or "localhost:8080"
    request = Request(
        "http://127.0.0.1:8080/health",
        headers={"Host": control_host},
    )

    with urlopen(request, timeout=3) as response:
        if response.status != 200:
            raise RuntimeError(f"Health check returned HTTP {response.status}")


if __name__ == "__main__":
    main()
