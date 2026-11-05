"""HTTP-сессия с корректной проверкой TLS и вежливым темпом запросов.

Сайт roskazna.gov.ru использует сертификат, выпущенный «Russian Trusted Sub CA»
(Минцифры России). Этих корневого и промежуточного сертификатов нет в стандартных
хранилищах (certifi, macOS, большинство Linux), а сервер не отдаёт промежуточный
сертификат в цепочке. Поэтому мы не отключаем проверку TLS, а добавляем к certifi
два сертификата из каталога ``certs/`` (см. ``certs/README.md``).

Портал отвечает только российским IP-адресам (с зарубежных — ``403 Forbidden``).
Адрес прокси с выходом в РФ задаётся переменной ``ROSKAZNA_PROXY``, например
``socks5h://127.0.0.1:1081`` (SSH-туннель ``ssh -N -D 1081 <ru-vps>``). Прокси
применяется только к сессиям этого проекта: глобальные ``HTTP(S)_PROXY`` и
``ALL_PROXY`` игнорируются.

Запросы идут последовательно, не чаще одного раза в ``REQUEST_INTERVAL`` секунд.
"""

from __future__ import annotations

import logging
import os
import time
from functools import lru_cache
from pathlib import Path

import certifi
import requests

from .config import CACHE_DIR, CERTS_DIR, REQUEST_INTERVAL, USER_AGENT

log = logging.getLogger(__name__)

_last_request_at = 0.0


@lru_cache(maxsize=1)
def ca_bundle_path() -> str:
    """Собирает CA-бандл: certifi + сертификаты Минцифры из certs/."""
    bundle = CACHE_DIR / "ca-bundle.pem"
    bundle.parent.mkdir(parents=True, exist_ok=True)
    parts = [Path(certifi.where()).read_text(encoding="ascii", errors="ignore")]
    for pem in sorted(CERTS_DIR.glob("*.pem")):
        parts.append(pem.read_text(encoding="ascii").strip() + "\n")
    bundle.write_text("\n".join(parts), encoding="ascii")
    return str(bundle)


def proxy_url() -> str | None:
    return os.environ.get("ROSKAZNA_PROXY") or None


def make_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False  # не подхватывать системные HTTP(S)_PROXY
    session.verify = ca_bundle_path()
    session.headers["User-Agent"] = USER_AGENT
    proxy = proxy_url()
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
        log.info("using proxy %s", proxy)
    return session


def _throttle() -> None:
    global _last_request_at
    wait = REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def get_with_retry(
    session: requests.Session,
    url: str,
    *,
    params: dict | None = None,
    attempts: int = 5,
    timeout: int = 120,
    stream: bool = False,
) -> requests.Response:
    """GET с повторами и экспоненциальной паузой при сетевых сбоях, 429 и 5xx.

    Ответ 403 не повторяется и возвращается вызывающему коду как есть.
    """
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        _throttle()
        try:
            resp = session.get(url, params=params, timeout=timeout, stream=stream)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code} for {resp.url}")
            return resp
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            last_exc = exc
            pause = min(300, 15 * 2 ** (attempt - 1))
            log.warning("attempt %s/%s failed (%s); sleep %ss", attempt, attempts, exc, pause)
            time.sleep(pause)
    assert last_exc is not None
    raise last_exc
