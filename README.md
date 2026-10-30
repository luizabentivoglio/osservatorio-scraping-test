# Osservatorio: test Crawl4AI + Trafilatura

Test non distruttivo su cinque fonti pubbliche. Non modifica il Site né il database.

Il workflow parte al caricamento o alla modifica del codice su `main`. Può essere avviato anche da **Actions > Test crawling fonti Osservatorio > Run workflow**. Non è pianificato periodicamente.

Consulta la tabella nel riepilogo dell'esecuzione e scarica l'artifact `crawling-results`: contiene `test-results.json`, `summary.md` e le versioni effettivamente installate in `installed-versions.txt`.

| Fonte | Pagina sottoposta a test |
| --- | --- |
| Pagella Politica | https://pagellapolitica.it/articoli |
| Banca d'Italia | https://www.bancaditalia.it/media/notizie/index.html |
| IAI | https://www.iai.it/it/pubblicazioni/lista/iai-commentaries |
| Comune di Bollate | https://comune.bollate.mi.it/it/news |
| Breaking Italy | https://breakingitaly.it/ |

Per ogni pagina si prova prima HTTP diretto + Trafilatura, poi Chromium + Crawl4AI. Dopo HTTP 401/403/429 il browser viene saltato; non vengono aggirati blocchi di accesso. Il report registra codici HTTP, reindirizzamenti, errori e quantità di testo estratto. Le pagine e i metodi provengono dal progetto preparato; il conteggio Markdown è compatibile con `MarkdownGenerationResult` di Crawl4AI.

La riuscita del workflow significa che l'esecuzione e il report delle cinque fonti sono completi. Errori delle singole fonti sono dati diagnostici e compaiono nel riepilogo, senza interrompere le altre prove.

Esecuzione locale (Python 3.11):

```bash
python -m pip install -r requirements.txt
python -m playwright install --with-deps chromium
python test_sources.py
python summarize_results.py
```

Una risposta HTTP 200 o testo estratto non dimostrano che titolo, data e link di ogni articolo siano corretti. Il test è diagnostico, non una certificazione di copertura. Non usare risultati senza verificare la data di pubblicazione.


## Raccolta per il Site

`collector.py` raccoglie esclusivamente metadati pubblici dalle fonti configurate in `sources.json`, prima da feed ufficiali e HTML con Trafilatura e, quando necessario, da pagine renderizzate con Crawl4AI. Rispetta robots.txt e i limiti di accesso: nessun aggiramento di 401/402/403/429.

Il workflow **Raccoglitore fonti Osservatorio** parte alle 02:45, 06:45, 10:45, 14:45, 18:45 e 22:45 UTC, oltre che manualmente. Conserva in `data/sources/` titolo, link, breve estratto, data di pubblicazione e diagnostica. Il Site privato legge questi JSON; note, selezioni, preferiti, newsletter e credenziali del Site non sono inviati a questo repository.

Sono inclusi solo articoli con data di pubblicazione verificabile entro 14 giorni (tolleranza futura 6 ore). Un feed leggibile senza articoli recenti viene distinto dagli errori di raccolta. Non sono usate date di ultima modifica come date di pubblicazione.

Eseguire `python -m unittest -v test_collector` per i controlli del parser e `python collector.py` per la raccolta. `SOURCE_IDS` consente una lista di ID separati da virgole. Il report distingue fonti riuscite, percorsi non validi, timeout, parsing e accesso limitato.
