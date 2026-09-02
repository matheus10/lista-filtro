import re

def parse_m3u(conteudo_m3u, nome_fonte, prioridade):
    canais = []
    linhas = conteudo_m3u.strip().splitlines()
    canal_atual = None
    
    for linha in linhas:
        linha = linha.strip()
        if not linha or (linha.startswith("#") and not linha.startswith("#EXTINF")):
            continue
            
        if linha.startswith("#EXTINF"):
            tvg_id = re.search(r'tvg-id="(.*?)"', linha, re.IGNORECASE)
            tvg_name = re.search(r'tvg-name="(.*?)"', linha, re.IGNORECASE)
            tvg_logo = re.search(r'tvg-logo="(.*?)"', linha, re.IGNORECASE)
            group_title = re.search(r'group-title="(.*?)"', linha, re.IGNORECASE)
            nome_cru = linha.split(",")[-1].strip()
            
            canal_atual = {
                "raw_name": nome_cru,
                "tvg_id": tvg_id.group(1) if tvg_id else "",
                "tvg_name": tvg_name.group(1) if tvg_name else "",
                "tvg_logo": tvg_logo.group(1) if tvg_logo else "",
                "group_title": group_title.group(1) if group_title else "",
                "source": nome_fonte,
                "priority": prioridade
            }
        elif linha.startswith("http") and canal_atual is not None:
            canal_atual["url"] = linha
            canais.append(canal_atual)
            canal_atual = None
            
    return canais
