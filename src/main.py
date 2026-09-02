import json
import requests
import os
import sys
from datetime import datetime, timezone
from parser import parse_m3u
from normalizer import normalizar_lista
from deduplicator import deduplicar_canais
from organizer import organizar_por_tipo, contar_filmes_series
from generator import exportar_listas
from validator import filtrar_canais_ativos


def _diretorio_saida(base_dir):
    diretorio_saida = os.environ.get("FIREBASE_PUBLIC_DIR")
    if diretorio_saida:
        return diretorio_saida
    candidatos = [
        r"C:\Users\racoon\Desktop\projetos\lista-iptv\public",
        os.path.normpath(os.path.join(base_dir, "..", "..", "..", "lista-iptv", "public")),
        os.path.join(base_dir, "..", "public"),
    ]
    return next((p for p in candidatos if os.path.isdir(p)), candidatos[-1])


def _gravar_stats(diretorio_saida, stats):
    os.makedirs(diretorio_saida, exist_ok=True)
    caminho = os.path.join(diretorio_saida, "stats.json")
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(f"Painel: {caminho}")


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

    print("Deduplicando (URL unica, um titulo, sem backups)...")
    lista_deduplicada, filtro_stats = deduplicar_canais(lista_bruta)

    resumo_validacao = {
        "tv_vivos": sum(1 for c in lista_deduplicada if c.get("tipo") != "VOD"),
        "tv_incertos": 0,
        "tv_removidos": 0,
        "vod_intactos": sum(1 for c in lista_deduplicada if c.get("tipo") == "VOD"),
        "executada": False,
    }

    if os.environ.get("VALIDATE_STREAMS", "0") == "1":
        n_tv = sum(1 for c in lista_deduplicada if c.get("tipo") != "VOD")
        n_vod = len(lista_deduplicada) - n_tv
        print(
            f"Validando streams reais so de TV ({n_tv} itens); "
            f"{n_vod} filmes/series sobem sem teste de stream."
        )
        antes_tv = n_tv
        lista_deduplicada, resumo = filtrar_canais_ativos(lista_deduplicada)
        resumo_validacao.update(resumo)
        resumo_validacao["executada"] = True
        depois_tv = sum(1 for c in lista_deduplicada if c.get("tipo") != "VOD")
        max_drop = float(os.environ.get("VALIDATE_MAX_DROP_RATIO", "0.85"))
        if antes_tv >= 100 and depois_tv < antes_tv * (1.0 - max_drop):
            print(
                f"ABORTADO: validacao removeu {antes_tv - depois_tv}/{antes_tv} canais de TV "
                f"(acima de {max_drop:.0%}). Provavel bloqueio de IP do runner. "
                "O Firebase nao sera atualizado."
            )
            sys.exit(1)

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
        "apos_nome": filtro_stats["apos_nome"],
        "validacao_tv": resumo_validacao["executada"],
        "fontes": status_fontes,
    }
    _gravar_stats(diretorio_saida, stats)
    print(f"Sobrescritos na pasta public (URLs fixas): {diretorio_saida}")


if __name__ == "__main__":
    main()
