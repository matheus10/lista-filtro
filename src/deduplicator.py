from normalizer import qualidade_rank


def deduplicar_canais(lista_normalizada):
    """Uma URL = um item; um titulo/canal = um item. Nao publica backups."""
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

    por_chave = {}
    for canal in por_url.values():
        chave = _chave_dedup(canal)
        atual = por_chave.get(chave)
        if atual is None:
            por_chave[chave] = canal
            continue
        if _melhor_que(canal, atual):
            por_chave[chave] = canal

    finais = list(por_chave.values())
    n_tv = sum(1 for c in finais if c.get("tipo") != "VOD")
    n_vod = len(finais) - n_tv
    print(
        f"  {bruto} brutos -> {apos_url} URLs unicas -> {len(finais)} apos nome/titulo "
        f"(TV={n_tv} VOD={n_vod}). Sem backups."
    )
    return finais, {
        "bruto": bruto,
        "apos_url": apos_url,
        "apos_nome": len(finais),
        "tv": n_tv,
        "vod": n_vod,
    }


def _chave_dedup(canal):
    return canal.get("fingerprint") or (
        f"VOD|{canal.get('nome_base', '')}"
        if canal.get("tipo") == "VOD"
        else f"TV|{canal.get('nome_base', '')}"
    )


def _eh_brazuka3(canal):
    return "brazuka3" in str(canal.get("source", "")).lower()


def _tem_prioridade_maior(candidato, atual):
    if _eh_brazuka3(candidato) and not _eh_brazuka3(atual):
        return True
    if _eh_brazuka3(atual) and not _eh_brazuka3(candidato):
        return False
    return candidato.get("priority", 999) < atual.get("priority", 999)


def _melhor_que(candidato, atual):
    if _tem_prioridade_maior(candidato, atual):
        return True
    if _tem_prioridade_maior(atual, candidato):
        return False
    return qualidade_rank(candidato.get("qualidade")) > qualidade_rank(atual.get("qualidade"))
