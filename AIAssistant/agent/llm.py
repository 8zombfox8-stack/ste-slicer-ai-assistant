# Minimal OpenAI-compatible Chat Completions client on urllib (no requests in Python 3.5 bundle).

import json
import os
import socket
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

# Public root CAs shipped with the plugin: on some PCs the Windows store is outdated and the
# certifi bundle of the slicer (2020) lacks newer roots such as GlobalSign R46.
CA_BUNDLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cacert.pem")
TLS_VERSION_TAG = "TLS v3"  # shown in connection errors to tell which plugin build is installed


def _context(windows_store, certifi_bundle):
    context = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
    context.options |= ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    context.verify_flags |= getattr(ssl, "VERIFY_X509_TRUSTED_FIRST", 0)
    cafiles = [CA_BUNDLE]
    if certifi_bundle:
        try:
            import certifi
            cafiles.append(certifi.where())
        except Exception:
            pass
    for cafile in cafiles:
        try:
            context.load_verify_locations(cafile=cafile)
        except Exception:
            pass
    if windows_store:
        try:
            context.load_default_certs()
        except Exception:
            pass
    return context


# Trust sets tried in order. The plugin roots alone come first: expired roots with the same name
# in an old Windows store can break verification in OpenSSL 1.0.2. The Windows store is needed
# when an antivirus or a corporate proxy re-signs HTTPS with its own root.
_TRUST_SETS = [(False, False), (True, False), (True, True)]
_working_set = [0]


def _is_cert_error(error):
    if isinstance(error, ssl.CertificateError):
        return True
    reason = getattr(error, "reason", None)
    return isinstance(reason, (ssl.SSLError, ssl.CertificateError)) and "CERTIFICATE" in str(reason).upper()


def _open(request, timeout):
    order = [_working_set[0]] + [i for i in range(len(_TRUST_SETS)) if i != _working_set[0]]
    last = None
    for index in order:
        try:
            response = urllib.request.urlopen(request, timeout=timeout, context=_context(*_TRUST_SETS[index]))
            _working_set[0] = index
            return response
        except urllib.error.HTTPError as e:
            _working_set[0] = index  # TLS worked, the server answered
            raise
        except (urllib.error.URLError, ssl.CertificateError) as e:
            if not _is_cert_error(e):
                raise
            last = e
    raise last


def _name(fields):
    values = dict(item[0] for item in fields if item)
    return values.get("organizationName") or values.get("commonName") or "?"


def diagnose_certificate(url):
    """Plain-language reason why the server certificate is not trusted on this PC."""
    parts = urllib.parse.urlsplit(url)
    host, port = parts.hostname, parts.port or 443
    now = time.strftime("%d.%m.%Y %H:%M")
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
        context.verify_mode = ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=15) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
        handle, path = tempfile.mkstemp(suffix=".pem")
        with os.fdopen(handle, "w") as f:
            f.write(ssl.DER_cert_to_PEM_cert(der))
        try:
            cert = ssl._ssl._test_decode_cert(path)
        finally:
            os.remove(path)
    except Exception as e:
        return "Сертификат сервера получить не удалось (%s). Дата на компьютере: %s." % (e, now)
    issuer = _name(cert.get("issuer", ()))
    not_before = ssl.cert_time_to_seconds(cert["notBefore"])
    not_after = ssl.cert_time_to_seconds(cert["notAfter"])
    text = "Сервер %s предъявил сертификат от «%s», он действует с %s по %s; дата на этом компьютере %s." % (
        host, issuer, time.strftime("%d.%m.%Y", time.localtime(not_before)),
        time.strftime("%d.%m.%Y", time.localtime(not_after)), now)
    if not not_before <= time.time() <= not_after:
        return text + " Причина: на компьютере неверная дата или время. Исправьте их (Параметры Windows → Время и язык → Синхронизировать) и повторите запрос."
    if "globalsign" not in issuer.lower() and host.endswith("timeweb.ai"):
        return text + (" Причина: защищённое соединение перехватывает антивирус или прокси сети («%s»). "
                       "Отключите в антивирусе проверку защищённых соединений для STESlicer.exe или подключитесь к другой сети." % issuer)
    return text + " Корневой сертификат этого центра не найден ни в плагине, ни в Windows."


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
        with _open(request, timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise LLMError("LLM вернула ошибку %d: %s" % (e.code, detail))
    except (urllib.error.URLError, ssl.CertificateError) as e:
        if _is_cert_error(e):
            raise LLMError("Нет связи с LLM: сертификат сервера не прошёл проверку [%s]. %s" % (
                TLS_VERSION_TAG, diagnose_certificate(url)))
        raise LLMError("Нет связи с LLM: %s" % getattr(e, "reason", e))
    try:
        return body["choices"][0]["message"]
    except (KeyError, IndexError):
        raise LLMError("Неожиданный ответ LLM: %s" % json.dumps(body)[:500])
