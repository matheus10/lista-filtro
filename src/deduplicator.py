from collections import defaultdict
from urllib.parse import urlparse

from normalizer import qualidade_rank

MAX_CANDIDATOS_POR_CHAVE = 3


def deduplicar_canais(lista_normalizada):
    """URL unica; 4K/FHD/HD/SD separados; ate 3 hosts candidatos (backup depois do teste)."""
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
    n_grupos = 0
    for itens in grupos.values():
        itens.sort(key=_ordem)
        escolhidos = []
        hosts = set()
        for cand in itens:
            h = _host(cand.get("url") or "")
            if h and h in hosts:
                continue
            escolhidos.append(cand)
            if h:
                hosts.add(h)
            if len(escolhidos) >= MAX_CANDIDATOS_POR_CHAVE:
                break
        if not escolhidos:
            continue
        n_grupos += 1
        finais.extend(escolhidos)

    n_tv = sum(1 for c in finais if c.get("tipo") != "VOD")
    n_vod = len(finais) - n_tv
    print(
        f"  {bruto} brutos -> {apos_url} URLs unicas -> {len(finais)} candidatos "
        f"({n_grupos} grupos, TV={n_tv} VOD={n_vod}, ate {MAX_CANDIDATOS_POR_CHAVE} hosts/qualidade)."
    )
    return finais, {
        "bruto": bruto,
        "apos_url": apos_url,
        "apos_nome": len(finais),
        "grupos": n_grupos,
        "principais": n_grupos,
        "backups": 0,
        "tv": n_tv,
        "vod": n_vod,
    }


def escolher_par_ativo(canais_tv, veredito_de):
    """Depois do teste: principal (Brazuka3/vivo) + 1 backup vivo de outro host."""
    grupos = defaultdict(list)
    for canal in canais_tv:
        grupos[_chave_dedup(canal)].append(canal)

    saida = []
    n_vivos = 0
    n_incertos = 0
    n_removidos = 0
    n_backups = 0
    n_principais = 0

    for itens in grupos.values():
        itens.sort(key=_ordem)
        vivos = []
        incertos = []
        for canal in itens:
            v = veredito_de(canal)
            if v == "dead":
                n_removidos += 1
            elif v == "alive":
                vivos.append(canal)
            else:
                incertos.append(canal)

        principal = next((c for c in vivos if _eh_brazuka3(c)), None)
        if principal is None:
            principal = next((c for c in incertos if _eh_brazuka3(c)), None)
        if principal is None and vivos:
            principal = vivos[0]
        if principal is None and incertos:
            principal = incertos[0]
        if principal is None:
            continue

        saida.append(principal)
        n_principais += 1
        if principal in vivos:
            n_vivos += 1
        else:
            n_incertos += 1

        host_p = _host(principal.get("url") or "")
        backup = None
        for cand in vivos:
            if cand is principal:
                continue
            h = _host(cand.get("url") or "")
            if h and h != host_p:
                backup = cand
                break
        if backup:
            copia = backup.copy()
            nome = copia.get("nome_exibicao") or copia.get("nome_base") or ""
            if "(Backup)" not in nome:
                copia["nome_exibicao"] = f"{nome} (Backup)"
            saida.append(copia)
            n_backups += 1
            n_vivos += 1

    print(
        f"  Pares TV: {n_principais} principais + {n_backups} backups vivos "
        f"(outros hosts). Removidos={n_removidos} incertos={n_incertos}."
    )
    return saida, {
        "tv_vivos": n_vivos,
        "tv_incertos": n_incertos,
        "tv_removidos": n_removidos,
        "principais": n_principais,
        "backups": n_backups,
    }


def filtrar_vod_por_saude(vod_canais, hosts_saudaveis, hosts_mortos):
    """Tira VOD de host morto na TV; 1 backup so de host que teve canal vivo.

    hosts_saudaveis=None significa que a TV nao foi testada: 1 backup de outro host.
    hosts_saudaveis=[] (teste rodou, ninguem vivo) = sem backup.
    """
    sem_teste = hosts_saudaveis is None
    mortos = set() if sem_teste else set(hosts_mortos or [])
    saudaveis = set() if sem_teste else set(hosts_saudaveis or [])
    filtrados = []
    n_drop_host = 0
    for canal in vod_canais:
        h = _host(canal.get("url") or "")
        if h and h in mortos:
            n_drop_host += 1
            continue
        filtrados.append(canal)

    grupos = defaultdict(list)
    for canal in filtrados:
        grupos[_chave_dedup(canal)].append(canal)

    finais = []
    n_backups = 0
    for itens in grupos.values():
        itens.sort(key=_ordem)
        principal = itens[0]
        finais.append(principal)
        host_p = _host(principal.get("url") or "")
        backup = None
        for cand in itens[1:]:
            h = _host(cand.get("url") or "")
            if not h or h == host_p or h in mortos:
                continue
            if not sem_teste and h not in saudaveis:
                continue
            backup = cand
            break
        if backup:
            copia = backup.copy()
            nome = copia.get("nome_exibicao") or copia.get("nome_base") or ""
            if "(Backup)" not in nome:
                copia["nome_exibicao"] = f"{nome} (Backup)"
            finais.append(copia)
            n_backups += 1

    print(
        f"  VOD: {len(vod_canais)} -> {len(finais)} "
        f"(drop host morto={n_drop_host}, backups={n_backups})."
    )
    return finais, {"vod_drop_host": n_drop_host, "vod_backups": n_backups}


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
