import os

def gerar_arquivo_m3u(lista_canais, caminho_saida):
    os.makedirs(os.path.dirname(caminho_saida), exist_ok=True)
    with open(caminho_saida, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        for canal in lista_canais:
            tvg_id = f' tvg-id="{canal["tvg_id"]}"' if canal.get("tvg_id") else ''
            tvg_logo = f' tvg-logo="{canal["tvg_logo"]}"' if canal.get("tvg_logo") else ''
            group_title = f' group-title="{canal["group_title"]}"' if canal.get("group_title") else ''
            linha_extinf = f'#EXTINF:-1{tvg_id}{tvg_logo}{group_title}, {canal["nome_exibicao"]}\n'
            f.write(linha_extinf)
            f.write(f'{canal["url"]}\n')

def exportar_listas(dicionario_organizado, diretorio_saida="output"):
    arquivos = [
        ("tv", "lista-canal.m3u"),
        ("vod", "lista-filme.m3u"),
        ("completa", "lista.m3u")
    ]
    for chave, nome_arquivo in arquivos:
        caminho = os.path.join(diretorio_saida, nome_arquivo)
        gerar_arquivo_m3u(dicionario_organizado[chave], caminho)
    print(f"Arquivos gerados no diretório '{diretorio_saida}/'")
