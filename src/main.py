import json
import requests
import os
import sys
from datetime import datetime, timezone
from parser import parse_m3u
from normalizer import normalizar_lista
from deduplicator import deduplicar_canais, escolher_par_ativo, filtrar_vod_por_saude
from organizer import organizar_por_tipo, contar_filmes_series
from generator import exportar_listas
from validator import filtrar_canais_ativos


def _diretorio_saida(base_dir):
    diretorio_saida = os.environ.get("FIREBASE_PUBLIC_DIR")
    if diretorio_saida:
        return diretorio_saida
    return os.path.normpath(os.path.join(base_dir, "..", "public"))


def _gravar_stats(diretorio_saida, stats):
    os.makedirs(diretorio_saida, exist_ok=True)
    caminho = os.path.join(diretorio_saida, "stats.json")
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(f"Painel: {caminho}")


def _partir_tv_vod(lista):
    tv = [c for c in lista if c.get("tipo") != "VOD"]
    vod = [c for c in lista if c.get("tipo") == "VOD"]
    return tv, vod


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "..", "config", "sources.json")

    with open(config_path, "r", encoding="utf-8") as f:
        fontes = sorted(json.load(f)["tv"], key=lambda x: x.get("priority", 999))

    lista_bruta = []
    status_fontes = []

    for fonte in fontes:
        print(f"Baixando: {fonte['name']}...")
        entrada = {"name": fonte["name"], "online": False, "http": 0, "itens": 0}
        try:
            resposta = requests.get(fonte["url"], timeout=300)
            entrada["http"] = resposta.status_code
            if resposta.status_code != 200:
                print(f"  Falha HTTP {resposta.status_code}, ignorando.")
                status_fontes.append(entrada)
                continue
            canais = parse_m3u(resposta.text, fonte["name"], fonte["priority"])
            canais = normalizar_lista(canais)
            if fonte.get("only") == "VOD":
                canais = [c for c in canais if c.get("tipo") == "VOD"]
                print(f"  Mantidos {len(canais)} itens de filmes/series.")
            lista_bruta.extend(canais)
            entrada["online"] = True
            entrada["itens"] = len(canais)
        except Exception as e:
            print(f"  Falha ao baixar: {e}")
        status_fontes.append(entrada)

    print("Deduplicando (URL unica, ate 3 hosts por qualidade; backup depois do teste)...")
    lista_deduplicada, filtro_stats = deduplicar_canais(lista_bruta)

    resumo_validacao = {
        "tv_vivos": sum(1 for c in lista_deduplicada if c.get("tipo") != "VOD"),
        "tv_incertos": 0,
        "tv_removidos": 0,
        "vod_intactos": sum(1 for c in lista_deduplicada if c.get("tipo") == "VOD"),
        "executada": False,
        "principais": 0,
        "backups": 0,
        "hosts_saudaveis": [],
        "hosts_mortos": [],
        "hosts": [],
        "vod_drop_host": 0,
        "vod_backups": 0,
        "probe_segundos": 0,
        "cache_vivos": 0,
        "urls_testadas": 0,
        "vod_amostra": 0,
    }
    abortar = False
    abort_msg = ""

    if os.environ.get("VALIDATE_STREAMS", "0") == "1":
        n_tv = sum(1 for c in lista_deduplicada if c.get("tipo") != "VOD")
        n_vod = len(lista_deduplicada) - n_tv
        print(
            f"Validando streams reais so de TV ({n_tv} candidatos); "
            f"{n_vod} filmes/series sobem sem teste URL a URL."
        )
        lista_deduplicada, resumo = filtrar_canais_ativos(lista_deduplicada)
        resumo_validacao.update(resumo)
        resumo_validacao["executada"] = True
        antes_grupos = resumo.get("tv_grupos_antes") or n_tv
        depois_grupos = resumo.get("tv_grupos_depois", 0)
        max_drop = float(os.environ.get("VALIDATE_MAX_DROP_RATIO", "0.85"))
        if antes_grupos >= 100 and depois_grupos < antes_grupos * (1.0 - max_drop):
            abortar = True
            abort_msg = (
                f"ABORTADO: validacao removeu {antes_grupos - depois_grupos}/{antes_grupos} "
                f"grupos de TV (acima de {max_drop:.0%}). Provavel bloqueio de IP do runner. "
                "O Firebase nao sera atualizado; artefatos do run ficam no Actions."
            )
            print(abort_msg)
    else:
        tv, vod = _partir_tv_vod(lista_deduplicada)
        print("Sem teste de stream: 1 canal de TV por qualidade (sem backup).")
        tv, par_stats = escolher_par_ativo(tv, lambda _c: "uncertain")
        lista_deduplicada = tv + vod
        resumo_validacao["principais"] = par_stats["principais"]
        resumo_validacao["backups"] = par_stats["backups"]
        resumo_validacao["tv_incertos"] = par_stats["tv_incertos"]

    tv, vod = _partir_tv_vod(lista_deduplicada)
    print("Filtrando filmes/series por saude do servidor (resultado da TV)...")
    hosts_s = None if not resumo_validacao["executada"] else resumo_validacao.get("hosts_saudaveis")
    hosts_m = None if not resumo_validacao["executada"] else resumo_validacao.get("hosts_mortos")
    vod, vod_stats = filtrar_vod_por_saude(vod, hosts_s, hosts_m)
    lista_deduplicada = tv + vod
    resumo_validacao["vod_drop_host"] = vod_stats["vod_drop_host"]
    resumo_validacao["vod_backups"] = vod_stats["vod_backups"]

    n_tv_backup = sum(1 for c in tv if "(Backup)" in str(c.get("nome_exibicao") or ""))
    tv_principais = resumo_validacao.get("principais")
    if tv_principais is None:
        tv_principais = len(tv) - n_tv_backup
    tv_backups = resumo_validacao.get("backups")
    if tv_backups is None:
        tv_backups = n_tv_backup
    n_principais = tv_principais + (len(vod) - vod_stats["vod_backups"])
    n_backups = tv_backups + vod_stats["vod_backups"]

    print("Separando TV e VOD...")
    dicionario_organizado = organizar_por_tipo(lista_deduplicada)
    filmes, series = contar_filmes_series(dicionario_organizado["vod"])

    diretorio_saida = _diretorio_saida(base_dir)
    exportar_listas(dicionario_organizado, diretorio_saida)

    stats = {
        "atualizado_em": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "filmes": filmes,
        "series": series,
        "tv_online": resumo_validacao["tv_vivos"] if resumo_validacao["executada"] else len(dicionario_organizado["tv"]),
        "tv_incertos": resumo_validacao["tv_incertos"] if resumo_validacao["executada"] else 0,
        "tv_removidos": resumo_validacao["tv_removidos"] if resumo_validacao["executada"] else 0,
        "tv_na_lista": len(dicionario_organizado["tv"]),
        "vod_na_lista": len(dicionario_organizado["vod"]),
        "total_na_lista": len(dicionario_organizado["completa"]),
        "bruto": filtro_stats["bruto"],
        "apos_url": filtro_stats["apos_url"],
        "apos_nome": len(dicionario_organizado["completa"]),
        "principais": n_principais,
        "backups": n_backups,
        "vod_drop_host": resumo_validacao["vod_drop_host"],
        "vod_amostra": resumo_validacao.get("vod_amostra", 0),
        "probe_segundos": resumo_validacao.get("probe_segundos", 0),
        "cache_vivos": resumo_validacao.get("cache_vivos", 0),
        "urls_testadas": resumo_validacao.get("urls_testadas", 0),
        "hosts": resumo_validacao.get("hosts") or [],
        "hosts_saudaveis": resumo_validacao.get("hosts_saudaveis") or [],
        "hosts_mortos": resumo_validacao.get("hosts_mortos") or [],
        "validacao_tv": resumo_validacao["executada"],
        "abortado": abortar,
        "fontes": status_fontes,
    }
    _gravar_stats(diretorio_saida, stats)
    print(f"Sobrescritos na pasta public (URLs fixas): {diretorio_saida}")
    if abortar:
        sys.exit(1)


if __name__ == "__main__":
    main()
