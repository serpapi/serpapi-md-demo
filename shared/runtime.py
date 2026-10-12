"""Small, dependency-free runtime helpers shared by live demos."""

import json
import os
import stat
import time
import urllib.error
import urllib.request
from pathlib import Path


DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"
SECRETS_FILE = Path(__file__).resolve().parents[1] / ".env"


def load_secrets_file(path=SECRETS_FILE):
    if not path.exists():
        return
    permissions = stat.S_IMODE(path.stat().st_mode)
    if permissions & 0o077:
        raise RuntimeError(
            "Secrets file permissions are too open: {}. Run: chmod 600 {}".format(
                oct(permissions), path
            )
        )
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            raise RuntimeError("Invalid secrets file line {}".format(line_number))
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        if key in {"SERPAPI_API_KEY", "DEEPSEEK_API_KEY"}:
            os.environ.setdefault(key, value)


def require_api_keys():
    load_secrets_file()
    serpapi_key = os.environ.get("SERPAPI_API_KEY")
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    if not serpapi_key or not deepseek_key:
        raise RuntimeError(
            "SERPAPI_API_KEY and DEEPSEEK_API_KEY must be set in the environment "
            "or {}".format(SECRETS_FILE)
        )
    return serpapi_key, deepseek_key


class DeepSeekClient:
    def __init__(self, api_key, model=DEEPSEEK_MODEL):
        self.api_key = api_key
        self.model = model

    def _request(self, messages, max_tokens, attempts=4):
        body = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "thinking": {"type": "disabled"},
                "temperature": 0,
                "max_tokens": max_tokens,
                "stream": False,
                "response_format": {"type": "json_object"},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        last_error = None
        for attempt in range(attempts):
            try:
                request = urllib.request.Request(
                    DEEPSEEK_URL,
                    data=body,
                    headers={
                        "Authorization": "Bearer " + self.api_key,
                        "Content-Type": "application/json",
                        "User-Agent": "serpapi-md-shopping-demo/1.0",
                    },
                )
                with urllib.request.urlopen(request, timeout=180) as response:
                    return json.loads(response.read().decode("utf-8"))
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                if isinstance(exc, urllib.error.HTTPError):
                    detail = exc.read().decode("utf-8", errors="replace")[:1000]
                    if exc.code < 500 and exc.code != 429:
                        raise RuntimeError("DeepSeek HTTP {}: {}".format(exc.code, detail)) from exc
                if attempt + 1 < attempts:
                    time.sleep(2 ** attempt)
        raise RuntimeError("DeepSeek request failed after {} attempts: {}".format(attempts, last_error))

    def measure_prompt_tokens(self, messages):
        response = self._request(messages, max_tokens=1)
        usage = response.get("usage") or {}
        if "prompt_tokens" not in usage:
            raise RuntimeError("DeepSeek response did not include usage.prompt_tokens")
        return int(usage["prompt_tokens"]), usage

    def analyze_json(self, messages):
        response = self._request(messages, max_tokens=2400)
        choices = response.get("choices") or []
        if not choices:
            raise RuntimeError("DeepSeek response did not contain a choice")
        content = ((choices[0].get("message") or {}).get("content") or "").strip()
        try:
            analysis = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError("DeepSeek did not return valid JSON") from exc
        return analysis, response.get("usage") or {}
