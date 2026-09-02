def deduplicar_canais(lista_normalizada):
    canais_principais = {}
    canais_finais = {}
    contagem_backups = {}
    
    for canal in lista_normalizada:
        chave = _chave_dedup(canal)
        if chave not in canais_principais:
            canais_principais[chave] = canal
            continue
            
        canal_existente = canais_principais[chave]
        if canal["url"] == canal_existente["url"]:
            continue

        if _tem_prioridade_maior(canal, canal_existente):
            _transformar_em_backup(canal_existente, chave, contagem_backups, canais_finais)
            canais_principais[chave] = canal
        else:
            _transformar_em_backup(canal, chave, contagem_backups, canais_finais)

    canais_finais.update(canais_principais)
    return list(canais_finais.values())

def _chave_dedup(canal):
    nome = str(canal.get("nome_base", "")).lower().replace(" ", "")
    if canal.get("tipo") == "VOD":
        return canal.get("fingerprint") or f"VOD|{nome}"
    return f"TV|{nome}"

def _eh_brazuka3(canal):
    return "brazuka3" in str(canal.get("source", "")).lower()

def _tem_prioridade_maior(candidato, atual):
    if _eh_brazuka3(candidato) and not _eh_brazuka3(atual):
        return True
    if _eh_brazuka3(atual) and not _eh_brazuka3(candidato):
        return False
    return candidato.get("priority", 999) < atual.get("priority", 999)

def _transformar_em_backup(canal, chave, contagem_backups, dict_finais):
    contagem_backups[chave] = contagem_backups.get(chave, 0) + 1
    idx = contagem_backups[chave]
    novo_fp = f"{chave}_backup_{idx}"
    
    canal_backup = canal.copy()
    canal_backup["fingerprint"] = novo_fp
    sufixo = " (Backup)" if idx == 1 else f" (Backup {idx})"
    canal_backup["nome_exibicao"] = f"{canal_backup['nome_exibicao']}{sufixo}"
    dict_finais[novo_fp] = canal_backup
