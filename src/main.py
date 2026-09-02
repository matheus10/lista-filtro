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
    
    diretorio_saida = os.path.join(base_dir, "..", "output")
    exportar_listas(dicionario_organizado, diretorio_saida)
    print("Arquivos prontos para o deploy no Firebase Hosting.")

if __name__ == "__main__":
    main()
