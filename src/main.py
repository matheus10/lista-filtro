import json
import requests
import os
from parser import parse_m3u
from normalizer import normalizar_lista
from deduplicator import deduplicar_canais
from organizer import organizar_por_tipo
from generator import exportar_listas

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "..", "config", "sources.json")
    
    with open(config_path, "r", encoding="utf-8") as f:
        fontes = sorted(json.load(f)["tv"], key=lambda x: x.get("priority", 999))
    
    lista_bruta = []
    
    for fonte in fontes:
        print(f"Baixando: {fonte['name']}...")
        resposta = requests.get(fonte['url'], timeout=120)
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
