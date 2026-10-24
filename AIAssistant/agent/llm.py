# Minimal OpenAI-compatible Chat Completions client on urllib (no requests in Python 3.5 bundle).

import json
import ssl
import urllib.error
import urllib.request


def _ssl_context():
    # The Windows certificate store comes first: the certifi bundle shipped with
    # the slicer (2020) lacks newer roots such as GlobalSign R46 and fails verification.
    context = ssl.create_default_context()
    try:
        import certifi
        context.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass
    context.verify_flags |= getattr(ssl, "VERIFY_X509_TRUSTED_FIRST", 0)
    return context


class LLMError(Exception):
    pass


def chat_completion(base_url, api_key, model, messages, tools=None, timeout=180):
    url = base_url.rstrip("/") + "/chat/completions"
    # Low temperature: the same part and specification should give the same engineering decisions.
    payload = {"model": model, "messages": messages, "temperature": 0}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + api_key,
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise LLMError("LLM вернула ошибку %d: %s" % (e.code, detail))
    except urllib.error.URLError as e:
        raise LLMError("Нет связи с LLM: %s" % e.reason)
    try:
        return body["choices"][0]["message"]
    except (KeyError, IndexError):
        raise LLMError("Неожиданный ответ LLM: %s" % json.dumps(body)[:500])
