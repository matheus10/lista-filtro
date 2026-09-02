"""
Validacao real de streams, portada do motor do listar-iptv (linkChecker.ts).

Politica: falso negativo (canal no ar marcado morto) e pior do que deixar
lixo na lista. So remove o que for morto com confirmacao.

Causas reais de falso offline (e o que fazemos):
- Canal lento: se o stream ainda entrega dados no timeout (>= 8 KB), vivo.
  Timeout sem fluxo suficiente = incerto, nao morto.
- Flood no mesmo painel (403/429/conexao derrubada): fila por host (nunca
  mais que N conexoes no mesmo servidor) e 403/429 como retry/incerto.
- So apaga depois de 2 veredictos 'dead' consecutivos (nunca 'uncertain').
"""
from __future__ import annotations

import os
import re
import threading
import time
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DIRECT_STREAM_MIN_BYTES = 128 * 1024
SEGMENT_MIN_BYTES = 64 * 1024
ENDED_STREAM_MIN_BYTES = 32 * 1024
FLOWING_STREAM_MIN_BYTES = 8 * 1024
MANIFEST_MAX_BYTES = 1024 * 1024
MAX_PLAYLIST_DEPTH = 3
RETRY_DELAY_S = 0.35
# 403 entra aqui: painel saturado recusa, nao e canal fora do ar.
RETRYABLE_HTTP = {403, 408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}
HARD_DEAD_HTTP = {404, 410, 451}
HLS_URL_RE = re.compile(r"\.m3u8(\?.*)?$", re.I)
PLACEHOLDER_PATH_RE = re.compile(r"/(?:video/)?(?:black|blank|placeholder|null)\.ts$", re.I)
REQUEST_HEADERS = {
    "User-Agent": "VLC/3.0.20 LibVLC/3.0.20",
    "Accept": "*/*",
}

Verdict = str  # alive | dead | retry | uncertain


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
    variants: List[Tuple[str, int]] = []
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


def _origin(url: str) -> str:
    p = urlparse(url)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}"
    return ""


def _read_stream_bytes(resp: requests.Response, min_bytes: int, deadline: float) -> Verdict:
    received = 0
    try:
        for chunk in resp.iter_content(chunk_size=16 * 1024):
            if chunk:
                received += len(chunk)
            if received >= min_bytes:
                return "alive"
            if time.time() >= deadline:
                # Stream ainda fluindo quando o tempo acaba = canal lento, nao morto.
                return "alive" if received >= FLOWING_STREAM_MIN_BYTES else "uncertain"
        if received >= min(ENDED_STREAM_MIN_BYTES, min_bytes):
            return "alive"
        # Servidor fechou o corpo de proposito (vazio ou lixo curto).
        return "dead" if received == 0 else "uncertain"
    except Exception:
        if received >= FLOWING_STREAM_MIN_BYTES:
            return "alive"
        return "uncertain" if time.time() >= deadline - 0.05 else "retry"


def _verify_url(
    sess: requests.Session,
    url: str,
    deadline: float,
    depth: int,
    visited: Set[str],
    referer: Optional[str] = None,
) -> Verdict:
    if depth > MAX_PLAYLIST_DEPTH:
        return "uncertain"
    remaining = deadline - time.time()
    if remaining <= 0:
        return "uncertain"

    normalized = url.split("#")[0]
    if normalized in visited:
        return "dead"
    visited.add(normalized)

    headers = {}
    if referer:
        headers["Referer"] = referer
        origin = _origin(referer)
        if origin:
            headers["Origin"] = origin

    try:
        resp = sess.get(
            url,
            stream=True,
            timeout=(min(6.0, max(2.0, remaining)), max(1.0, remaining)),
            allow_redirects=True,
            verify=False,
            headers=headers or None,
        )
    except Exception:
        return "uncertain" if time.time() >= deadline - 0.05 else "retry"

    try:
        code = resp.status_code
        if code != 200:
            if code in RETRYABLE_HTTP:
                return "retry"
            if code in HARD_DEAD_HTTP:
                return "dead"
            return "uncertain"
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
                return "retry" if time.time() < deadline - 0.05 else "uncertain"
            manifest = buf.decode("utf-8", errors="replace")
            if not manifest.lstrip().startswith("#EXTM3U"):
                return "uncertain" if timed_out else "dead"
            if _has_drm(manifest):
                return "alive"
            nxt = _extract_next_url(final_url, manifest)
            if not nxt:
                return "retry"
            return _verify_url(sess, nxt, deadline, depth + 1, visited, referer=final_url)

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


def _probe(sess: requests.Session, url: str, timeout_s: float) -> Verdict:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "alive"
    deadline = time.time() + timeout_s
    verdict = _verify_url(sess, url, deadline, 0, set())
    if verdict == "retry":
        time.sleep(RETRY_DELAY_S)
        verdict = _verify_url(sess, url, time.time() + timeout_s, 0, set())
    if verdict == "retry":
        return "uncertain"
    return verdict


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or url).lower()
    except Exception:
        return url


def _probe_urls(urls: List[str], timeout_s: float, concurrency: int, per_host: int) -> Dict[str, Verdict]:
    """Fila por host: worker so pega URL de servidor que ainda tem vaga."""
    results: Dict[str, Verdict] = {}
    lock = threading.Lock()
    queues: Dict[str, List[str]] = defaultdict(list)
    for u in urls:
        queues[_host(u)].append(u)
    hosts = list(queues.keys())
    active_by_host: Dict[str, int] = defaultdict(int)
    remaining = [len(urls)]
    rr = [0]
    cond = threading.Condition()
    total = len(urls)
    done = [0]
    workers_n = max(1, min(concurrency, total or 1))
    per_host = max(1, per_host)
    tls = threading.local()

    def _sess() -> requests.Session:
        s = getattr(tls, "sess", None)
        if s is None:
            s = _session()
            tls.sess = s
        return s

    def take_next() -> Optional[Tuple[str, str]]:
        with cond:
            while True:
                if remaining[0] <= 0:
                    return None
                saw_pending = False
                n = len(hosts) or 1
                for step in range(n):
                    host = hosts[(rr[0] + step) % n]
                    q = queues[host]
                    if not q:
                        continue
                    saw_pending = True
                    if active_by_host[host] < per_host:
                        rr[0] = (rr[0] + step + 1) % n
                        remaining[0] -= 1
                        active_by_host[host] += 1
                        return host, q.pop(0)
                if saw_pending:
                    cond.wait(timeout=0.05)
                    continue
                return None

    def release_host(host: str) -> None:
        with cond:
            active_by_host[host] = max(0, active_by_host[host] - 1)
            cond.notify_all()

    def worker() -> None:
        sess = _sess()
        while True:
            nxt = take_next()
            if nxt is None:
                return
            host, u = nxt
            try:
                try:
                    verdict = _probe(sess, u, timeout_s)
                except Exception:
                    verdict = "uncertain"
            finally:
                release_host(host)
            with lock:
                results[u] = verdict
                done[0] += 1
                n = done[0]
                if n % 200 == 0 or n == total:
                    ok = sum(1 for v in results.values() if v == "alive")
                    mortos = sum(1 for v in results.values() if v == "dead")
                    inc = n - ok - mortos
                    print(
                        f"  {n}/{total} | vivos={ok} mortos={mortos} incertos={inc}",
                        flush=True,
                    )

    print(
        f"Validando {total} URLs unicas "
        f"(timeout={timeout_s:.0f}s, conc={workers_n}, por_servidor={per_host}, hosts={len(hosts)})...",
        flush=True,
    )
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def _deve_remover(historico: List[Verdict]) -> bool:
    """So remove com 2+ 'dead' e nenhum 'alive'. Incerto nunca apaga sozinho."""
    if not historico or "alive" in historico:
        return False
    mortos = sum(1 for v in historico if v == "dead")
    return mortos >= 2


def filtrar_canais_ativos(canais: List[dict]) -> List[dict]:
    timeout_s = float(os.environ.get("VALIDATE_TIMEOUT_SEC", "10"))
    confirm_timeout = float(os.environ.get("VALIDATE_CONFIRM_TIMEOUT_SEC", "12"))
    concurrency = int(os.environ.get("VALIDATE_CONCURRENCY", "30"))
    per_host = int(os.environ.get("VALIDATE_PER_HOST", "5"))
    extra_passes = int(os.environ.get("VALIDATE_CONFIRM_PASSES", "2"))
    delay_s = float(os.environ.get("VALIDATE_RECHECK_DELAY_SEC", "3"))

    http_urls: List[str] = []
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

    historico: Dict[str, List[Verdict]] = {u: [] for u in http_urls}
    status = _probe_urls(http_urls, timeout_s, concurrency, per_host)
    for u, v in status.items():
        historico[u].append(v)

    pendentes = [u for u in http_urls if status.get(u) != "alive"]
    for passagem in range(1, extra_passes + 1):
        if not pendentes:
            break
        if delay_s > 0:
            print(f"Esperando {delay_s:.0f}s antes da confirmacao {passagem}/{extra_passes}...")
            time.sleep(delay_s)
        # Confirmacao mais conservadora: menos pressao no painel, mais tempo.
        conc = max(8, concurrency // 2)
        host_lim = max(2, min(3, per_host))
        tmo = confirm_timeout if passagem > 1 else max(timeout_s, confirm_timeout)
        print(
            f"Confirmacao {passagem}/{extra_passes}: {len(pendentes)} URLs "
            f"(timeout={tmo:.0f}s, por_servidor={host_lim})..."
        )
        nova = _probe_urls(pendentes, tmo, conc, host_lim)
        ainda: List[str] = []
        revived = 0
        for u in pendentes:
            v = nova.get(u, "uncertain")
            historico[u].append(v)
            if v == "alive":
                status[u] = "alive"
                revived += 1
            else:
                status[u] = v
                ainda.append(u)
        print(f"  Recuperados nesta passagem: {revived}")
        pendentes = ainda

    # Host com 0 vivos e volume alto: quase certamente IP bloqueado, nao painel morto.
    por_host: Dict[str, List[str]] = defaultdict(list)
    for u in http_urls:
        por_host[_host(u)].append(u)
    hosts_bloqueados = set()
    for host, group in por_host.items():
        if len(group) < 15:
            continue
        vivos_h = sum(1 for u in group if status.get(u) == "alive")
        if vivos_h == 0:
            hosts_bloqueados.add(host)
            print(
                f"Servidor {host}: 0 vivos em {len(group)} URLs — "
                "mantendo todos (provavel bloqueio de IP, nao lista morta)."
            )

    vivos = 0
    inativos = 0
    mantidos_incertos = 0
    mantidos: List[dict] = []
    for c in canais:
        u = c.get("url") or ""
        if not (u.startswith("http://") or u.startswith("https://")):
            mantidos.append(c)
            continue
        if _host(u) in hosts_bloqueados:
            mantidos.append(c)
            mantidos_incertos += 1
            continue
        if _deve_remover(historico.get(u, [])):
            inativos += 1
            continue
        mantidos.append(c)
        if status.get(u) == "alive":
            vivos += 1
        else:
            mantidos_incertos += 1

    print(
        f"Validacao concluida: {vivos} vivos + {mantidos_incertos} mantidos sem certeza, "
        f"{inativos} inativos removidos (so com 2x morto confirmado)."
    )
    return mantidos
