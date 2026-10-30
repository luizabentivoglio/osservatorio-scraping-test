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
