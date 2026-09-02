from normalizer import eh_serie


def organizar_por_tipo(lista_deduplicada):
    canais_tv = []
    canais_vod = []

    for canal in lista_deduplicada:
        if canal.get("tipo") == "VOD":
            canais_vod.append(canal)
        else:
            canais_tv.append(canal)

    canais_tv.sort(key=lambda x: (x.get("group_title", ""), x.get("nome_exibicao", "")))
    canais_vod.sort(key=lambda x: (x.get("group_title", ""), x.get("nome_exibicao", "")))

    return {
        "tv": canais_tv,
        "vod": canais_vod,
        "completa": canais_tv + canais_vod
    }


def contar_filmes_series(canais_vod):
    """Heuristica: grupo/nome com serie/temporada conta como serie; o resto e filme."""
    filmes = 0
    series = 0
    for canal in canais_vod:
        if eh_serie(canal):
            series += 1
        else:
            filmes += 1
    return filmes, series
