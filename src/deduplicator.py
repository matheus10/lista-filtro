def deduplicar_canais(lista_normalizada):
    canais_principais = {}
    canais_finais = {}
    contagem_backups = {}
    
    for canal in lista_normalizada:
        fp = canal["fingerprint"]
        if fp not in canais_principais:
            canais_principais[fp] = canal
            continue
            
        canal_existente = canais_principais[fp]
        if canal["url"] == canal_existente["url"]:
            continue

        if _tem_prioridade_maior(canal, canal_existente):
            _transformar_em_backup(canal_existente, contagem_backups, canais_finais)
            canais_principais[fp] = canal
        else:
            _transformar_em_backup(canal, contagem_backups, canais_finais)

    canais_finais.update(canais_principais)
    return list(canais_finais.values())

def _eh_brazuka3(canal):
    return "brazuka3" in str(canal.get("source", "")).lower()

def _tem_prioridade_maior(candidato, atual):
    if _eh_brazuka3(candidato) and not _eh_brazuka3(atual):
        return True
    if _eh_brazuka3(atual) and not _eh_brazuka3(candidato):
        return False
    return candidato.get("priority", 999) < atual.get("priority", 999)

def _transformar_em_backup(canal, contagem_backups, dict_finais):
    fp_original = canal["fingerprint"]
    contagem_backups[fp_original] = contagem_backups.get(fp_original, 0) + 1
    idx = contagem_backups[fp_original]
    novo_fp = f"{fp_original}_backup_{idx}"
    
    canal_backup = canal.copy()
    canal_backup["fingerprint"] = novo_fp
    sufixo = " (Backup)" if idx == 1 else f" (Backup {idx})"
    canal_backup["nome_exibicao"] = f"{canal_backup['nome_exibicao']}{sufixo}"
    dict_finais[novo_fp] = canal_backup
