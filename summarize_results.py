"""Validate report completeness; source failures remain diagnostic results."""
import json
import os
from pathlib import Path

EXPECTED = {'pagella-politica', 'banca-italia', 'iai', 'comune-bollate', 'breaking-italy'}


def describe(method, chars_key):
    if method.get('skipped'):
        return 'Non eseguito: accesso limitato'
    status = method.get('status')
    if method.get('error') or method.get('success') is False:
        label = f'HTTP {status}' if status else method.get('errorType', 'Errore')
        return label + ': ' + (method.get('error') or 'estrazione fallita').replace('|', '/').replace('\n', ' ')[:200]
    count = method.get(chars_key, 0)
    return f'HTTP {status}; {count} caratteri' + ('; testo vuoto' if not count else '')


def main():
    results = json.loads(Path('results/test-results.json').read_text(encoding='utf-8'))
    if len(results) != 5 or {r['sourceId'] for r in results} != EXPECTED:
        raise ValueError('Il report deve contenere esattamente le cinque fonti previste')
    lines = [
        '# Test Crawl4AI + Trafilatura', '',
        'Test diagnostico su cinque pagine pubbliche. Nessuna modifica al sito o al database.', '',
        '| Fonte | Trafilatura (HTTP diretto) | Crawl4AI (Chromium) |',
        '| --- | --- | --- |',
    ]
    for result in results:
        methods = result['methods']
        if set(methods) != {'trafilatura', 'crawl4ai'}:
            raise ValueError('Risultato incompleto: ' + result['sourceId'])
        lines.append(f"| {result['sourceId']} | {describe(methods['trafilatura'], 'textChars')} | {describe(methods['crawl4ai'], 'markdownChars')} |")
    lines += ['', 'La riuscita del workflow indica che il test e il report sono completi; non che tutte le fonti siano accessibili.',
              'Il testo estratto dalle pagine elenco non certifica titolo, data, link o copertura dei singoli articoli.',
              'Dettagli: artifact `crawling-results`, file `test-results.json` e `installed-versions.txt`.']
    summary = '\n'.join(lines) + '\n'
    Path('results/summary.md').write_text(summary, encoding='utf-8')
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as f:
            f.write(summary)


if __name__ == '__main__':
    main()
