from collections import defaultdict
from urllib.parse import urlparse

from normalizer import qualidade_rank


def deduplicar_canais(lista_normalizada):
    """URL unica; 4K/FHD/HD/SD separados; no maximo 1 backup de outro servidor."""
    bruto = len(lista_normalizada)
    por_url = {}
    for canal in lista_normalizada:
        url = (canal.get("url") or "").split("#")[0].strip()
        if not url:
            continue
        atual = por_url.get(url)
        if atual is None or _tem_prioridade_maior(canal, atual):
            por_url[url] = canal
    apos_url = len(por_url)

    grupos = defaultdict(list)
    for canal in por_url.values():
        grupos[_chave_dedup(canal)].append(canal)

    finais = []
    n_principais = 0
    n_backups = 0
    for itens in grupos.values():
        itens.sort(key=_ordem)
        principal = itens[0]
        finais.append(principal)
        n_principais += 1
        host_p = _host(principal.get("url") or "")
        backup = None
        for cand in itens[1:]:
            host_c = _host(cand.get("url") or "")
            if host_c and host_c != host_p:
                backup = cand
                break
        if backup:
            copia = backup.copy()
            nome = copia.get("nome_exibicao") or copia.get("nome_base") or ""
            if "(Backup)" not in nome:
                copia["nome_exibicao"] = f"{nome} (Backup)"
            finais.append(copia)
            n_backups += 1

    n_tv = sum(1 for c in finais if c.get("tipo") != "VOD")
    n_vod = len(finais) - n_tv
    print(
        f"  {bruto} brutos -> {apos_url} URLs unicas -> {len(finais)} publicados "
        f"(principais={n_principais} backups={n_backups} TV={n_tv} VOD={n_vod}). "
        "Qualidades 4K/FHD/HD/SD separadas; backup so de outro host."
    )
    return finais, {
        "bruto": bruto,
        "apos_url": apos_url,
        "apos_nome": len(finais),
        "principais": n_principais,
        "backups": n_backups,
        "tv": n_tv,
        "vod": n_vod,
    }


def _chave_dedup(canal):
    return canal.get("fingerprint") or (
        f"vod|{canal.get('nome_base', '')}|{canal.get('qualidade', 'SD')}"
        if canal.get("tipo") == "VOD"
        else f"tv|{canal.get('nome_base', '')}|{canal.get('qualidade', 'SD')}"
    )


def _host(url):
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _eh_brazuka3(canal):
    return "brazuka3" in str(canal.get("source", "")).lower()


def _tem_prioridade_maior(candidato, atual):
    if _eh_brazuka3(candidato) and not _eh_brazuka3(atual):
        return True
    if _eh_brazuka3(atual) and not _eh_brazuka3(candidato):
        return False
    return candidato.get("priority", 999) < atual.get("priority", 999)


def _ordem(canal):
    return (
        0 if _eh_brazuka3(canal) else 1,
        canal.get("priority", 999),
        -qualidade_rank(canal.get("qualidade")),
    )
