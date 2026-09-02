import os

# Nomes oficiais no Hosting. Sempre os mesmos caminhos/URLs; cada update sobrescreve.
ARQUIVOS_M3U = (
    ("tv", "lista-canal.m3u"),
    ("vod", "lista-filme.m3u"),
    ("completa", "lista.m3u"),
)

def gerar_arquivo_m3u(lista_canais, caminho_saida):
    os.makedirs(os.path.dirname(caminho_saida), exist_ok=True)
    with open(caminho_saida, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        for canal in lista_canais:
            tvg_id = f' tvg-id="{canal["tvg_id"]}"' if canal.get("tvg_id") else ''
            tvg_logo = f' tvg-logo="{canal["tvg_logo"]}"' if canal.get("tvg_logo") else ''
            grupo = canal.get("group_title") or ("Filmes" if canal.get("tipo") == "VOD" else "Canais")
            grupo = str(grupo).replace('"', "'")
            group_title = f' group-title="{grupo}"'
            linha_extinf = f'#EXTINF:-1{tvg_id}{tvg_logo}{group_title}, {canal["nome_exibicao"]}\n'
            f.write(linha_extinf)
            f.write(f'{canal["url"]}\n')

def exportar_listas(dicionario_organizado, diretorio_saida="output"):
    os.makedirs(diretorio_saida, exist_ok=True)
    for chave, nome_arquivo in ARQUIVOS_M3U:
        caminho = os.path.join(diretorio_saida, nome_arquivo)
        gerar_arquivo_m3u(dicionario_organizado[chave], caminho)
        print(f"Sobrescrito: {caminho}")
