import json
import requests
import os
import sys
from parser import parse_m3u
from normalizer import normalizar_lista
from deduplicator import deduplicar_canais
from organizer import organizar_por_tipo
from generator import exportar_listas
from validator import filtrar_canais_ativos

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "..", "config", "sources.json")
    
    with open(config_path, "r", encoding="utf-8") as f:
        fontes = sorted(json.load(f)["tv"], key=lambda x: x.get("priority", 999))
    
    lista_bruta = []
    
    for fonte in fontes:
        print(f"Baixando: {fonte['name']}...")
        resposta = requests.get(fonte['url'], timeout=300)
        if resposta.status_code != 200:
            print(f"  Falha HTTP {resposta.status_code}, ignorando.")
            continue
        canais = parse_m3u(resposta.text, fonte['name'], fonte['priority'])
        canais = normalizar_lista(canais)
        if fonte.get("only") == "VOD":
            canais = [c for c in canais if c.get("tipo") == "VOD"]
            print(f"  Mantidos {len(canais)} itens de filmes/series.")
        lista_bruta.extend(canais)
            
    print("Deduplicando e gerando backups...")
    lista_deduplicada = deduplicar_canais(lista_bruta)
    print(f"  {len(lista_bruta)} itens antes -> {len(lista_deduplicada)} depois da filtragem.")

    if os.environ.get("VALIDATE_STREAMS", "0") == "1":
        n_tv = sum(1 for c in lista_deduplicada if c.get("tipo") != "VOD")
        n_vod = len(lista_deduplicada) - n_tv
        print(
            f"Validando streams reais so de TV ({n_tv} itens); "
            f"{n_vod} filmes/series sobem sem teste de stream."
        )
        antes_tv = n_tv
        lista_deduplicada = filtrar_canais_ativos(lista_deduplicada)
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
    
    diretorio_saida = os.environ.get("FIREBASE_PUBLIC_DIR")
    if not diretorio_saida:
        candidatos = [
            r"C:\Users\racoon\Desktop\projetos\lista-iptv\public",
            os.path.normpath(os.path.join(base_dir, "..", "..", "..", "lista-iptv", "public")),
            os.path.join(base_dir, "..", "public"),
        ]
        diretorio_saida = next((p for p in candidatos if os.path.isdir(p)), candidatos[-1])

    exportar_listas(dicionario_organizado, diretorio_saida)
    print(f"Sobrescritos na pasta public (URLs fixas): {diretorio_saida}")

if __name__ == "__main__":
    main()
