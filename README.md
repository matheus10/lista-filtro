# lista-filtro

Pipeline que agrega, normaliza e publica listas IPTV (M3U) no Firebase Hosting do projeto **minha-lista**.

## Listas publicadas

Todas abrem como **texto puro** (`text/plain`), não como player de vídeo.

| Arquivo | O que é | URL |
| --- | --- | --- |
| `lista.m3u` | Lista **completa**: canais ao vivo (TV) + filmes e séries (VOD), juntos. | https://minha-lista-c880d.web.app/lista.m3u |
| `lista-canal.m3u` | Só **canais de TV** ao vivo, organizados por grupo. | https://minha-lista-c880d.web.app/lista-canal.m3u |
| `lista-filme.m3u` | Só **filmes e séries** (VOD). | https://minha-lista-c880d.web.app/lista-filme.m3u |

Cole a URL no player IPTV (IPTV Smarters, TiviMate, VLC, etc.) ou abra no navegador para ver o M3U em linhas (`#EXTM3U`, `#EXTINF`, URL).

As URLs **não mudam**. Cada atualização **sobrescreve** os mesmos três arquivos no Firebase Hosting (`lista.m3u`, `lista-canal.m3u`, `lista-filme.m3u`). Não é criado arquivo novo nem URL nova.

## Atualização automática

O workflow **Atualizador de Listas IPTV (M3U)** roda a cada 6 horas e também pode ser disparado em **Actions → Run workflow**.

Os headers do Hosting ficam em `firebase.json` (`text/plain`, sem `no-store`) para o navegador não abrir player e a leitura da lista não ser cortada.

Fontes: IPTV-Brazuka / Brazuka2 / **Brazuka3** (prioridade nos canais ao vivo) e, de [Iptv-Brasil-2026](https://github.com/xKzin/Iptv-Brasil-2026), só **CanaisBR01–BR04** filtrados para **filmes e séries**.
