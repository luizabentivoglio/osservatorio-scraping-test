import unittest
from datetime import datetime,timezone,timedelta
from collector import canonical,parse_date,metadata,candidates,feed_items,recent,Failure,Client


class CollectorTests(unittest.TestCase):
    def test_canonical_preserves_article_query(self):
        self.assertEqual(canonical('https://www.senato.it/attualita/archivio-notizie?nid=125977&utm_source=x#top'),'https://www.senato.it/attualita/archivio-notizie?nid=125977')
        self.assertIsNone(canonical('http://127.0.0.1/foo'))
        self.assertIsNone(canonical('https://user:password@example.org/'))

    def test_italian_and_english_publication_dates(self):
        for s in ['29 ottobre 2026','29/10/2026','29th October 2026','2026-10-29']:
            self.assertTrue(parse_date(s).startswith('2026-10-29'),s)
        self.assertIsNone(parse_date('31/02/2026'))
        self.assertIsNone(parse_date('1'))

    def test_modified_is_not_published(self):
        html='<h1>Un articolo senza data di pubblicazione</h1><script type="application/ld+json">{"@type":"NewsArticle","dateModified":"2026-10-29"}</script>'
        self.assertIsNone(metadata(html,'https://example.org/a'))
        atom='<feed xmlns="http://www.w3.org/2005/Atom"><title>Feed</title><entry><title>Notizia senza data pubblicazione</title><id>https://example.org/a</id><link href="https://example.org/a"/><updated>2026-10-29T12:00:00Z</updated></entry></feed>'
        self.assertEqual(feed_items(atom,'https://example.org/feed'),[])

    def test_article_metadata_over_footer_date(self):
        html='<main><h1>La notizia con data verificata</h1><time datetime="2026-10-29">29 ottobre 2026</time></main><footer><time datetime="2026-10-30">oggi</time></footer>'
        self.assertEqual(metadata(html,'https://example.org/a')['publishedAt'],'2026-10-29T00:00:00Z')

    def test_navigation_excluded_and_source_pattern_used(self):
        html='<nav><a href="/articoli/menu">Tutte le notizie in evidenza</a></nav><main><a href="/contatti">Contatti della redazione</a><h2><a href="/articoli/vera-notizia">Una notizia da verificare</a></h2></main>'
        links=candidates(html,'https://example.org/articoli',{'articlePattern':r'^/articoli/'})
        self.assertEqual([x['url'] for x in links],['https://example.org/articoli/vera-notizia'])

    def test_feed_date_and_xxe_rejection(self):
        xml='<rss version="2.0"><channel><title>Test</title><item><title>Una notizia verificata</title><link>https://example.org/a</link><pubDate>Thu, 29 Oct 2026 12:00:00 GMT</pubDate></item></channel></rss>'
        self.assertEqual(feed_items(xml,'https://example.org/feed')[0]['publishedAt'],'2026-10-29T12:00:00Z')
        with self.assertRaises(Failure): feed_items('<!ENTITY x SYSTEM "file:///etc/passwd">','https://example.org')

    def test_future_and_old_items_rejected(self):
        def item(days): return {'title':'Un articolo verificato','url':'https://example.org/'+str(days),'excerpt':'','publishedAt':(datetime.now(timezone.utc)+timedelta(days=days)).isoformat()}
        self.assertEqual(len(recent([item(-1),item(-30),item(2)])),1)

    def test_no_external_redirect_or_private_origin(self):
        client=Client({'url':'https://example.org/'})
        self.assertFalse(client.allowed('https://evil.example.net/'))
        self.assertTrue(client.allowed('https://news.example.org/'))
        client.session.close()


if __name__=='__main__': unittest.main()
