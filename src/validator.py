"""
Validacao real de streams, portada do motor do listar-iptv (linkChecker.ts).

Objetivo: minimizar falso negativo (canal no ar marcado como morto).
- GET no stream (nunca so HEAD)
- HLS seguido ate um segmento de midia
- Se o timeout estoura com dados fluindo, considera vivo
- Retry em erro transitorio
- Limite de conexoes por servidor (paineis bloqueiam flood)
- URL unica testada uma vez
- Segunda passagem so nos mortos antes de excluir
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter

DIRECT_STREAM_MIN_BYTES = 128 * 1024
SEGMENT_MIN_BYTES = 64 * 1024
ENDED_STREAM_MIN_BYTES = 32 * 1024
FLOWING_STREAM_MIN_BYTES = 8 * 1024
MANIFEST_MAX_BYTES = 1024 * 1024
MAX_PLAYLIST_DEPTH = 3
RETRY_DELAY_S = 0.25
RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}
HLS_URL_RE = re.compile(r"\.m3u8(\?.*)?$", re.I)
PLACEHOLDER_PATH_RE = re.compile(r"/(?:video/)?(?:black|blank|placeholder|null)\.ts$", re.I)
REQUEST_HEADERS = {
    "User-Agent": "VLC/3.0.20 LibVLC/3.0.20",
    "Accept": "*/*",
}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(REQUEST_HEADERS)
    s.mount("http://", HTTPAdapter(pool_connections=64, pool_maxsize=64))
    s.mount("https://", HTTPAdapter(pool_connections=64, pool_maxsize=64))
    return s


def _is_hls(content_type: str, url: str) -> bool:
    return "mpegurl" in (content_type or "").lower() or bool(HLS_URL_RE.search(url or ""))


def _has_drm(playlist: str) -> bool:
    lower = playlist.lower()
    if "#ext-x-key" not in lower and "#ext-x-session-key" not in lower:
        return False
    for raw in lower.splitlines():
        line = raw.strip()
        if not line.startswith("#ext-x-key") and not line.startswith("#ext-x-session-key"):
            continue
        m = re.search(r"method=([^,\"\s]+)", line)
        method = m.group(1) if m else ""
        if method and method not in ("none", "aes-128"):
            return True
    return False


def _extract_next_url(base_url: str, playlist: str) -> Optional[str]:
    variants: List[tuple] = []
    pending_bw: Optional[int] = None
    first_uri: Optional[str] = None
    for raw in playlist.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-STREAM-INF:"):
            m = re.search(r"BANDWIDTH=(\d+)", line, re.I)
            pending_bw = int(m.group(1)) if m else 0
            continue
        if line.startswith("#"):
            continue
        try:
            resolved = urljoin(base_url, line)
        except Exception:
            continue
        if pending_bw is not None:
            variants.append((resolved, pending_bw))
            pending_bw = None
            continue
        if first_uri is None:
            first_uri = resolved
    if variants:
        variants.sort(key=lambda x: x[1])
        return variants[0][0]
    return first_uri


def _read_stream_bytes(resp: requests.Response, min_bytes: int, deadline: float) -> str:
    received = 0
    try:
        for chunk in resp.iter_content(chunk_size=16 * 1024):
            if chunk:
                received += len(chunk)
            if received >= min_bytes:
                return "alive"
            if time.time() >= deadline:
                return "alive" if received >= FLOWING_STREAM_MIN_BYTES else "dead"
        return "alive" if received >= min(ENDED_STREAM_MIN_BYTES, min_bytes) else "dead"
    except Exception:
        return "alive" if received >= FLOWING_STREAM_MIN_BYTES else "dead"


def _verify_url(sess: requests.Session, url: str, deadline: float, depth: int, visited: Set[str]) -> str:
    if depth > MAX_PLAYLIST_DEPTH:
        return "dead"
    remaining = deadline - time.time()
    if remaining <= 0:
        return "dead"

    normalized = url.split("#")[0]
    if normalized in visited:
        return "dead"
    visited.add(normalized)

    try:
        resp = sess.get(
            url,
            stream=True,
            timeout=max(1.0, remaining),
            allow_redirects=True,
            verify=False,
        )
    except Exception:
        return "dead" if time.time() >= deadline - 0.05 else "retry"

    try:
        if resp.status_code != 200:
            return "retry" if resp.status_code in RETRYABLE_HTTP else "dead"
        content_type = (resp.headers.get("content-type") or "").lower()
        final_url = resp.url or url

        if _is_hls(content_type, final_url):
            buf = bytearray()
            timed_out = False
            try:
                for chunk in resp.iter_content(chunk_size=16 * 1024):
                    if chunk:
                        buf.extend(chunk)
                    if len(buf) > MANIFEST_MAX_BYTES:
                        return "dead"
                    if time.time() >= deadline:
                        timed_out = True
                        break
            except Exception:
                return "retry" if time.time() < deadline - 0.05 else "dead"
            manifest = buf.decode("utf-8", errors="replace")
            if not manifest.lstrip().startswith("#EXTM3U"):
                return "retry" if timed_out else "dead"
            if _has_drm(manifest):
                return "alive"
            nxt = _extract_next_url(final_url, manifest)
            if not nxt:
                return "retry"
            return _verify_url(sess, nxt, deadline, depth + 1, visited)

        if content_type.startswith("text/"):
            return "dead"
        try:
            path = urlparse(final_url).path
        except Exception:
            path = ""
        if PLACEHOLDER_PATH_RE.search(path):
            return "dead"

        min_bytes = DIRECT_STREAM_MIN_BYTES if depth == 0 else SEGMENT_MIN_BYTES
        return _read_stream_bytes(resp, min_bytes, deadline)
    finally:
        resp.close()


def _probe(sess: requests.Session, url: str, timeout_s: float) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return True
    deadline = time.time() + timeout_s
    verdict = _verify_url(sess, url, deadline, 0, set())
    if verdict == "retry":
        time.sleep(RETRY_DELAY_S)
        verdict = _verify_url(sess, url, time.time() + timeout_s, 0, set())
    return verdict == "alive"


def _host(url: str) -> str:
    try:
        return urlparse(url).hostname or url
    except Exception:
        return url


def _probe_urls(urls: List[str], timeout_s: float, concurrency: int, per_host: int) -> Dict[str, bool]:
    import queue
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    results: Dict[str, bool] = {}
    lock = threading.Lock()
    host_lock = threading.Lock()
    host_sem: Dict[str, threading.Semaphore] = {}
    work: "queue.Queue[str]" = queue.Queue()
    for u in urls:
        work.put(u)
    total = len(urls)
    done = [0]
    workers_n = max(1, min(concurrency, total or 1))
    tls = threading.local()

    def _sess() -> requests.Session:
        s = getattr(tls, "sess", None)
        if s is None:
            s = _session()
            tls.sess = s
        return s

    def _sem(host: str) -> threading.Semaphore:
        with host_lock:
            sem = host_sem.get(host)
            if sem is None:
                sem = threading.Semaphore(per_host)
                host_sem[host] = sem
            return sem

    def worker() -> None:
        sess = _sess()
        while True:
            try:
                u = work.get_nowait()
            except queue.Empty:
                return
            sem = _sem(_host(u))
            sem.acquire()
            try:
                try:
                    alive = _probe(sess, u, timeout_s)
                except Exception:
                    alive = False
            finally:
                sem.release()
            with lock:
                results[u] = alive
                done[0] += 1
                n = done[0]
                if n % 200 == 0 or n == total:
                    ok = sum(1 for v in results.values() if v)
                    print(f"  {n}/{total} testadas | ativas={ok} inativas={n - ok}", flush=True)

    print(
        f"Validando {total} URLs unicas (timeout={timeout_s:.0f}s, conc={workers_n}, por_servidor={per_host})...",
        flush=True,
    )
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def filtrar_canais_ativos(canais: List[dict]) -> List[dict]:
    timeout_s = float(os.environ.get("VALIDATE_TIMEOUT_SEC", "8"))
    concurrency = int(os.environ.get("VALIDATE_CONCURRENCY", "30"))
    per_host = int(os.environ.get("VALIDATE_PER_HOST", "5"))
    recheck = os.environ.get("VALIDATE_RECHECK_DEAD", "1") != "0"

    http_urls = []
    seen = set()
    for c in canais:
        u = c.get("url") or ""
        if u.startswith("http://") or u.startswith("https://"):
            if u not in seen:
                seen.add(u)
                http_urls.append(u)

    if not http_urls:
        print("Nenhuma URL http(s) para validar.")
        return canais

    status = _probe_urls(http_urls, timeout_s, concurrency, per_host)

    if recheck:
        mortos = [u for u, ok in status.items() if not ok]
        if mortos:
            print(f"Revalidando {len(mortos)} URLs marcadas inativas (reduz falso negativo)...")
            segunda = _probe_urls(mortos, timeout_s, max(8, concurrency // 2), max(3, per_host // 2))
            revived = 0
            for u, ok in segunda.items():
                if ok:
                    status[u] = True
                    revived += 1
            print(f"  Recuperados na revalidacao: {revived}")

    vivos = 0
    inativos = 0
    mantidos = []
    for c in canais:
        u = c.get("url") or ""
        if not (u.startswith("http://") or u.startswith("https://")):
            mantidos.append(c)
            continue
        if status.get(u, True):
            mantidos.append(c)
            vivos += 1
        else:
            inativos += 1
    print(f"Validacao concluida: {vivos} ativos mantidos, {inativos} inativos removidos.")
    return mantidos
