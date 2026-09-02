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
        fontes = json.load(f)["tv"]
    
    lista_bruta = []
    
    for fonte in fontes:
        print(f"Baixando: {fonte['name']}...")
        resposta = requests.get(fonte['url'])
        if resposta.status_code == 200:
            canais = parse_m3u(resposta.text, fonte['name'], fonte['priority'])
            lista_bruta.extend(canais)
            
    print("Normalizando nomes e extraindo resoluções...")
    lista_normalizada = normalizar_lista(lista_bruta)
    
    print("Deduplicando e gerando backups...")
    lista_deduplicada = deduplicar_canais(lista_normalizada)
    
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
