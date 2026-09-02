import re
import unicodedata

QUALIDADE_ORDEM = {
    "8K": 70,
    "4K": 60,
    "2160P": 60,
    "FHD": 50,
    "1080P": 50,
    "HD": 40,
    "720P": 35,
    "SD": 10,
}

EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U00002700-\U000027BF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U00002600-\U000026FF"
    "\U0001F1E0-\U0001F1FF"
    "]+",
    flags=re.UNICODE,
)


def _slug(texto):
    nfkd = unicodedata.normalize("NFKD", str(texto or ""))
    sem = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", sem.lower())


def eh_serie(canal_ou_texto, nome="", url=""):
    if isinstance(canal_ou_texto, dict):
        texto = (
            f"{canal_ou_texto.get('group_title', '')} "
            f"{canal_ou_texto.get('nome_exibicao', '')} "
            f"{canal_ou_texto.get('nome_base', '')} "
            f"{canal_ou_texto.get('url', '')}"
        ).lower()
    else:
        texto = f"{canal_ou_texto} {nome} {url}".lower()
    if re.search(r"[sS]\d{2}[eE]\d{2}", texto) or re.search(r"[tT]\d{1,2}\s*[eE][pP]\d{1,2}", texto):
        return True
    if "/series/" in texto:
        return True
    return any(t in texto for t in ("série", "serie", "season", "temporada"))


def limpar_grupo(grupo):
    if not grupo:
        return ""
    limpo = EMOJI_RE.sub(" ", grupo)
    limpo = re.sub(r"[✅✨❇️⚽★☆•|]+", " ", limpo)
    limpo = " ".join(limpo.split()).strip(" -_|")
    return limpo


def grupo_padrao(tipo, nome_limpo, grupo, url, qualidade="SD"):
    atual = limpar_grupo(grupo)
    q = qualidade_chave(qualidade)
    if tipo == "VOD":
        base = atual if atual else ("Séries" if eh_serie(grupo, nome_limpo, url) else "Filmes")
        tokens = re.split(r"[\s|/]+", base.upper())
        if q and q.upper() not in tokens:
            return f"{base} | {q}"
        return base
    if atual:
        return atual
    return "Canais"


def qualidade_chave(qualidade):
    """Agrupa rotulos equivalentes (1080p=FHD, 2160p=4K) sem misturar HD com FHD."""
    q = str(qualidade or "SD").upper()
    if q in ("2160P", "4K"):
        return "4K"
    if q in ("1080P", "FHD"):
        return "FHD"
    if q in ("720P", "HD"):
        return "HD"
    if q == "8K":
        return "8K"
    return "SD"


def qualidade_rank(qualidade):
    return QUALIDADE_ORDEM.get(qualidade_chave(qualidade), 0)


def normalizar_lista(lista_canais_parseados):
    return [normalizar_canal(c) for c in lista_canais_parseados]


def normalizar_canal(canal):
    nome_cru = canal["raw_name"]
    match_ano = re.search(r"\((19|20)\d{2}\)", nome_cru)
    ano = match_ano.group(0)[1:-1] if match_ano else ""

    match_qualidade = re.search(
        r"\b(FHD|HD|SD|4K|8K|2160p|1080p|720p)\b", nome_cru, re.IGNORECASE
    )
    qualidade = match_qualidade.group(1).upper() if match_qualidade else "SD"

    nome_limpo = re.sub(r"\[.*?\]|\(.*?\)|\|.*?\|", "", nome_cru)
    nome_limpo = re.sub(
        r"\b(FHD|HD|SD|4K|8K|2160p|1080p|720p|TV|Ao Vivo|H265|HEVC)\b",
        "",
        nome_limpo,
        flags=re.IGNORECASE,
    )
    nome_limpo = re.sub(r"[¹²³]+", "", nome_limpo)
    nome_limpo = nome_limpo.replace("-", " ").replace(".", " ").strip()
    nome_limpo = " ".join(nome_limpo.split())

    if not nome_limpo:
        nome_limpo = canal["tvg_name"] if canal.get("tvg_name") else "Canal Desconhecido"

    tipo_conteudo = classificar_tipo(
        nome_limpo,
        canal.get("group_title") or "",
        canal.get("url") or "",
        nome_cru,
    )
    grupo = grupo_padrao(
        tipo_conteudo,
        nome_limpo,
        canal.get("group_title") or "",
        canal.get("url") or "",
        qualidade,
    )

    slug = _slug(nome_limpo)
    qchave = qualidade_chave(qualidade)
    if tipo_conteudo == "VOD":
        fingerprint = f"vod|{slug}|{ano}|{qchave}"
    else:
        fingerprint = f"tv|{slug}|{qchave}"

    return {
        "fingerprint": fingerprint,
        "tipo": tipo_conteudo,
        "nome_base": nome_limpo,
        "nome_exibicao": f"{nome_limpo} {qualidade}".strip(),
        "qualidade": qualidade,
        "ano": ano,
        "url": canal["url"],
        "tvg_id": canal.get("tvg_id") or "",
        "tvg_logo": canal.get("tvg_logo") or "",
        "group_title": grupo,
        "source": canal.get("source") or "",
        "priority": canal.get("priority", 999),
    }


def classificar_tipo(nome_limpo, grupo, url, nome_cru=""):
    nome_lower = (nome_limpo or "").lower()
    grupo_lower = (grupo or "").lower()
    url_lower = (url or "").lower()
    cru_lower = (nome_cru or "").lower()
    termos_vod = [
        "filme",
        "filmes",
        "série",
        "serie",
        "series",
        "cinema",
        "vod",
        "lançamento",
        "lancamento",
        "movie",
        "movies",
    ]

    path = url_lower.split("?", 1)[0]
    if any(p in path for p in ("/movie/", "/movies/", "/series/", "/vod/", "/film/")):
        return "VOD"
    if path.endswith((".mp4", ".mkv", ".avi", ".rmvb", ".mpg", ".mpeg")):
        return "VOD"
    if any(t in grupo_lower for t in termos_vod):
        return "VOD"
    if re.search(r"\((19|20)\d{2}\)", nome_cru or nome_limpo):
        return "VOD"
    if re.search(r"[sS]\d{2}[eE]\d{2}", nome_lower + cru_lower) or re.search(
        r"[tT]\d{1,2}\s*[eE][pP]\d{1,2}", nome_lower + cru_lower
    ):
        return "VOD"
    if any(t in nome_lower for t in ["filme", "série", "serie"]) and not re.search(
        r"\b(hd|sd|fhd|4k|h265)\b", nome_lower
    ):
        return "VOD"
    return "TV"
