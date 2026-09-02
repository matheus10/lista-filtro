"""
Validacao real de streams, portada do motor do listar-iptv (linkChecker.ts).

Politica: falso negativo (canal no ar marcado morto) e pior do que deixar
lixo na lista. So remove o que for morto com confirmacao.

Causas reais de falso offline (e o que fazemos):
- Canal lento: se o stream ainda entrega dados no timeout (>= 8 KB), vivo.
  Timeout sem fluxo suficiente = incerto, nao morto.
- Flood no mesmo painel (403/429/conexao derrubada): fila por host (nunca
  mais que N conexoes no mesmo servidor) e 403/429 como retry/incerto.
- So apaga morto confirmado (404/placeholder). Incerto permanece.

Pre-validacao para o teste caber no tempo:
- TCP/DNS por servidor (2s): host morto nao gasta timeout de stream.
- Passagem rapida (3s, 8 KB): vivos saem cedo; 404 ja e candidato a remocao.
- Teste pesado (10s, 128 KB) so no que ficou incerto na TV.
- Backup nao e testado se o principal ficou.
- VOD (filmes/series) nao e testado: sobe intacto para caber no limite de 6h do GitHub Actions.
"""
from __future__ import annotations

import os
import re
import socket
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
    quick: bool = False,
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
        connect_t = min(2.0 if quick else 6.0, max(1.0, remaining))
        resp = sess.get(
            url,
            stream=True,
            timeout=(connect_t, max(1.0, remaining)),
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
            return _verify_url(
                sess, nxt, deadline, depth + 1, visited, referer=final_url, quick=quick
            )

        if content_type.startswith("text/"):
            return "dead"
        try:
            path = urlparse(final_url).path
        except Exception:
            path = ""
        if PLACEHOLDER_PATH_RE.search(path):
            return "dead"

        if quick:
            min_bytes = FLOWING_STREAM_MIN_BYTES
        else:
            min_bytes = DIRECT_STREAM_MIN_BYTES if depth == 0 else SEGMENT_MIN_BYTES
        return _read_stream_bytes(resp, min_bytes, deadline)
    finally:
        resp.close()


def _probe(sess: requests.Session, url: str, timeout_s: float, quick: bool = False) -> Verdict:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "alive"
    deadline = time.time() + timeout_s
    verdict = _verify_url(sess, url, deadline, 0, set(), quick=quick)
    if verdict == "retry" and not quick:
        time.sleep(RETRY_DELAY_S)
        verdict = _verify_url(sess, url, time.time() + timeout_s, 0, set(), quick=quick)
    if verdict == "retry":
        return "uncertain"
    return verdict


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or url).lower()
    except Exception:
        return url


def _endpoint(url: str) -> Tuple[str, int]:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    port = p.port or (443 if p.scheme == "https" else 80)
    return host, int(port)


def _tcp_aberto(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _precheck_endpoints(urls: List[str], timeout: float = 2.0) -> Set[Tuple[str, int]]:
    """TCP/DNS barato: endpoints inalcancaveis nao entram no teste pesado (mantidos)."""
    grupos: Dict[Tuple[str, int], List[str]] = defaultdict(list)
    for u in urls:
        grupos[_endpoint(u)].append(u)
    endpoints = list(grupos.keys())
    mortos: Set[Tuple[str, int]] = set()
    lock = threading.Lock()
    print(f"Pre-check TCP: {len(endpoints)} servidores/portas (timeout={timeout:.0f}s)...")

    def job(ep: Tuple[str, int]) -> None:
        host, port = ep
        ok = bool(host) and _tcp_aberto(host, port, timeout)
        if ok:
            return
        with lock:
            mortos.add(ep)
            print(f"  offline {host}:{port} ({len(grupos[ep])} URLs) — pulando teste pesado, mantendo na lista")

    threads = []
    for ep in endpoints:
        t = threading.Thread(target=job, args=(ep,), daemon=True)
        threads.append(t)
        t.start()
        if len(threads) >= 32:
            for x in threads:
                x.join()
            threads = []
    for t in threads:
        t.join()
    print(f"  {len(endpoints) - len(mortos)} servidores acessiveis, {len(mortos)} inalcancaveis.")
    return mortos


def _eh_backup(canal: dict) -> bool:
    return " (Backup" in str(canal.get("nome_exibicao") or "")


def _familia(canal: dict) -> Tuple[str, str]:
    return (str(canal.get("tipo") or ""), str(canal.get("nome_base") or "").lower())


def _probe_urls(
    urls: List[str],
    timeout_s: float,
    concurrency: int,
    per_host: int,
    quick: bool = False,
) -> Dict[str, Verdict]:
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
                    verdict = _probe(sess, u, timeout_s, quick=quick)
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

    modo = "rapido" if quick else "completo"
    print(
        f"Validando {total} URLs unicas [{modo}] "
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
    """Morto confirmado no ultimo teste e nunca vivo. Incerto permanece."""
    if not historico or "alive" in historico:
        return False
    return historico[-1] == "dead"


def filtrar_canais_ativos(canais: List[dict]) -> Tuple[List[dict], dict]:
    timeout_s = float(os.environ.get("VALIDATE_TIMEOUT_SEC", "10"))
    fast_timeout = float(os.environ.get("VALIDATE_FAST_TIMEOUT_SEC", "3"))
    tcp_timeout = float(os.environ.get("VALIDATE_TCP_TIMEOUT_SEC", "2"))
    concurrency = int(os.environ.get("VALIDATE_CONCURRENCY", "30"))
    per_host = int(os.environ.get("VALIDATE_PER_HOST", "5"))
    precheck_tcp = os.environ.get("VALIDATE_PRECHECK_TCP", "1") != "0"
    skip_backup = os.environ.get("VALIDATE_SKIP_BACKUP_URLS", "1") != "0"
    confirm_dead = os.environ.get("VALIDATE_CONFIRM_DEAD", "1") != "0"
    tv_only = os.environ.get("VALIDATE_TV_ONLY", "1") != "0"

    vod_canais: List[dict] = []
    tv_canais: List[dict] = []
    if tv_only:
        for c in canais:
            if c.get("tipo") == "VOD":
                vod_canais.append(c)
            else:
                tv_canais.append(c)
        vod_urls = {c.get("url") for c in vod_canais if c.get("url")}
        print(
            f"VOD: {len(vod_canais)} itens / {len(vod_urls)} URLs unicas "
            "mantidos sem teste de stream."
        )
        print(f"TV: {len(tv_canais)} itens vao para validacao real.")
        canais_tv = tv_canais
    else:
        canais_tv = canais

    principais = [c for c in canais_tv if not _eh_backup(c)]
    backups = [c for c in canais_tv if _eh_backup(c)] if skip_backup else []
    alvo = principais if skip_backup else canais_tv

    http_urls: List[str] = []
    seen = set()
    for c in alvo:
        u = c.get("url") or ""
        if not (u.startswith("http://") or u.startswith("https://")):
            continue
        if u not in seen:
            seen.add(u)
            http_urls.append(u)

    print(f"TV: {len(http_urls)} URLs unicas para probe (principais).")

    resumo_vazio = {
        "tv_vivos": 0,
        "tv_incertos": 0,
        "tv_removidos": 0,
        "vod_intactos": len(vod_canais),
    }

    if not http_urls:
        print("Nenhuma URL http(s) de TV para validar.")
        lista = (alvo + backups + vod_canais) if tv_only else canais
        n_tv = sum(1 for c in lista if c.get("tipo") != "VOD")
        resumo_vazio["tv_incertos"] = n_tv
        return lista, resumo_vazio

    historico: Dict[str, List[Verdict]] = {u: [] for u in http_urls}
    status: Dict[str, Verdict] = {}

    # 1) Pre-check TCP/DNS — servidor fechado nao gasta timeout de stream.
    puladas: Set[str] = set()
    if precheck_tcp:
        endpoints_off = _precheck_endpoints(http_urls, tcp_timeout)
        for u in http_urls:
            if _endpoint(u) in endpoints_off:
                status[u] = "uncertain"
                historico[u].append("uncertain")
                puladas.add(u)

    restantes = [u for u in http_urls if u not in puladas]

    # 2) Passagem rapida: 3s, 8 KB fluindo = vivo; 404 = morto. Sem retry.
    if restantes:
        print("Passagem rapida (pre-validacao, so TV)...")
        rapido = _probe_urls(restantes, fast_timeout, concurrency, per_host, quick=True)
        for u, v in rapido.items():
            status[u] = v
            historico[u].append(v)

    # 3) Teste real so no que ficou incerto.
    precisa_completo = [u for u in restantes if status.get(u) == "uncertain"]
    if precisa_completo:
        print(f"Teste real: {len(precisa_completo)} URLs de TV ainda incertas...")
        completo = _probe_urls(precisa_completo, timeout_s, concurrency, per_host, quick=False)
        for u, v in completo.items():
            status[u] = v
            historico[u].append(v)

    # 4) Confirma so os mortos (404 volta rapido; nao retesta timeout).
    if confirm_dead:
        mortos = [u for u in restantes if status.get(u) == "dead"]
        if mortos:
            print(f"Confirmando {len(mortos)} mortos de TV (404/placeholder)...")
            conf = _probe_urls(mortos, fast_timeout, max(8, concurrency // 2), max(3, per_host), quick=True)
            revived = 0
            for u, v in conf.items():
                historico[u].append(v)
                status[u] = v
                if v == "alive":
                    revived += 1
            print(f"  Mortos que na verdade estavam vivos: {revived}")

    # Host com 0 vivos e volume alto: IP bloqueado, nao apaga.
    por_host: Dict[str, List[str]] = defaultdict(list)
    for u in http_urls:
        por_host[_host(u)].append(u)
    hosts_bloqueados = set()
    for host, group in por_host.items():
        testados = [u for u in group if u not in puladas]
        if len(testados) < 15:
            continue
        vivos_h = sum(1 for u in testados if status.get(u) == "alive")
        if vivos_h == 0:
            hosts_bloqueados.add(host)
            print(
                f"Servidor {host}: 0 vivos em {len(testados)} URLs de TV — "
                "mantendo todos (provavel bloqueio de IP)."
            )

    familias_ok: Set[Tuple[str, str]] = set()
    vivos = 0
    inativos = 0
    mantidos_incertos = 0
    mantidos: List[dict] = []

    def _mantem_canal(c: dict) -> str:
        nonlocal vivos, inativos, mantidos_incertos
        u = c.get("url") or ""
        if not (u.startswith("http://") or u.startswith("https://")):
            mantidos.append(c)
            return "keep"
        if _host(u) in hosts_bloqueados:
            mantidos.append(c)
            mantidos_incertos += 1
            return "keep"
        if _deve_remover(historico.get(u, [])):
            inativos += 1
            return "drop"
        mantidos.append(c)
        if status.get(u) == "alive":
            vivos += 1
        else:
            mantidos_incertos += 1
        return "keep"

    for c in alvo:
        if _mantem_canal(c) == "keep":
            familias_ok.add(_familia(c))

    # Backups de TV: se o principal ficou, mantem sem testar. Se o principal morreu, testa o backup.
    if skip_backup and backups:
        backups_para_testar: List[str] = []
        seen_b = set(http_urls)
        for b in backups:
            u = b.get("url") or ""
            if _familia(b) in familias_ok:
                continue
            if u.startswith("http://") or u.startswith("https://"):
                if u not in seen_b:
                    seen_b.add(u)
                    backups_para_testar.append(u)
                    historico.setdefault(u, [])
        if backups_para_testar:
            print(f"Principais mortos: testando {len(backups_para_testar)} URLs de backup de TV...")
            br = _probe_urls(backups_para_testar, fast_timeout, concurrency, per_host, quick=True)
            for u, v in br.items():
                status[u] = v
                historico[u].append(v)
        for b in backups:
            u = b.get("url") or ""
            if _familia(b) in familias_ok:
                mantidos.append(b)
                continue
            if u in http_urls or u in backups_para_testar:
                if _host(u) in hosts_bloqueados or not _deve_remover(historico.get(u, [])):
                    mantidos.append(b)
                    if status.get(u) == "alive":
                        vivos += 1
                    else:
                        mantidos_incertos += 1
                else:
                    inativos += 1
            else:
                mantidos.append(b)
                mantidos_incertos += 1

    print(
        f"Validacao TV concluida: {vivos} vivos + {mantidos_incertos} mantidos sem certeza, "
        f"{inativos} inativos removidos; VOD intacto={len(vod_canais)}."
    )
    resumo = {
        "tv_vivos": vivos,
        "tv_incertos": mantidos_incertos,
        "tv_removidos": inativos,
        "vod_intactos": len(vod_canais),
    }
    return mantidos + vod_canais, resumo
