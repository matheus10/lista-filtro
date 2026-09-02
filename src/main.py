import json
import requests
import os
from supabase import create_client, Client
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
    
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    
    if supabase_url and supabase_key:
        print("Fazendo upload para o Supabase (bucket 'public')...")
        supabase: Client = create_client(supabase_url, supabase_key)
        
        arquivos = ["lista-canal.m3u", "lista-filme.m3u", "lista.m3u"]
        for arquivo in arquivos:
            caminho_local = os.path.join(diretorio_saida, arquivo)
            with open(caminho_local, "rb") as f:
                supabase.storage.from_("public").upload(
                    file=f,
                    path=arquivo,
                    file_options={"x-upsert": "true", "content-type": "audio/x-mpegurl"}
                )
        print("Deploy finalizado com sucesso!")
    else:
        print("Credenciais do Supabase não encontradas. Arquivos gerados apenas localmente.")

if __name__ == "__main__":
    main()
