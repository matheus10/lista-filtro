import re

def normalizar_lista(lista_canais_parseados):
    return [normalizar_canal(c) for c in lista_canais_parseados]

def normalizar_canal(canal):
    nome_cru = canal["raw_name"]
    match_qualidade = re.search(r'\b(FHD|HD|SD|4K|8K|1080p|720p)\b', nome_cru, re.IGNORECASE)
    qualidade = match_qualidade.group(1).upper() if match_qualidade else "SD"
    
    nome_limpo = re.sub(r'\[.*?\]|\(.*?\)|\|.*?\|', '', nome_cru)
    nome_limpo = re.sub(r'\b(FHD|HD|SD|4K|8K|1080p|720p|TV|Ao Vivo)\b', '', nome_limpo, flags=re.IGNORECASE)
    nome_limpo = nome_limpo.replace('-', ' ').replace('.', ' ').strip()
    nome_limpo = " ".join(nome_limpo.split())
    
    if not nome_limpo:
        nome_limpo = canal["tvg_name"] if canal["tvg_name"] else "Canal Desconhecido"
        
    tipo_conteudo = classificar_tipo(nome_limpo, canal["group_title"], canal["url"])
    base_id = nome_limpo.lower().replace(" ", "")
    fingerprint = f"{base_id}_{qualidade.lower()}"
    
    return {
        "fingerprint": fingerprint,
        "tipo": tipo_conteudo,
        "nome_base": nome_limpo,
        "nome_exibicao": f"{nome_limpo} {qualidade}".strip(),
        "qualidade": qualidade,
        "url": canal["url"],
        "tvg_id": canal["tvg_id"],
        "tvg_logo": canal["tvg_logo"],
        "group_title": canal["group_title"],
        "source": canal["source"],
        "priority": canal["priority"]
    }

def classificar_tipo(nome_limpo, grupo, url):
    nome_lower = nome_limpo.lower()
    grupo_lower = grupo.lower()
    url_lower = url.lower()
    termos_vod = ['filme', 'série', 'serie', 'cinema', 'vod', 'lançamento', 'netflix', 'amazon', 'disney']
    
    if any(t in grupo_lower for t in termos_vod): return "VOD"
    if re.search(r'\((19|20)\d{2}\)', nome_limpo): return "VOD"
    if re.search(r'[sS]\d{2}[eE]\d{2}', nome_lower) or re.search(r'[tT]\d{1,2}\s*[eE][pP]\d{1,2}', nome_lower): return "VOD"
    if url_lower.endswith(('.mp4', '.mkv', '.avi', '.rmvb')): return "VOD"
    if any(t in nome_lower for t in ['filme', 'série', 'serie']) and not re.search(r'\b(hd|sd|fhd|4k|h265)\b', nome_lower):
        return "VOD"
    return "TV"
