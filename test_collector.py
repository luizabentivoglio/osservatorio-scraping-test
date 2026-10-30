import unittest
from datetime import datetime,timezone,timedelta
from collector import canonical,parse_date,metadata,candidates,feed_items,recent,Failure,Client,robots_policy,listing_metadata


class CollectorTests(unittest.TestCase):
    def test_rendered_article_date_uses_header_only(self):
        html='<h1>Notizia regionale verificata</h1><div><span><svg class="lucide-calendar"></svg>30 marzo 2026</span></div><section>Evento 4 novembre 2026</section>'
        self.assertEqual(metadata(html,'https://www.sud-chiamanord.it/blog/prova')['publishedAt'],'2026-03-30T00:00:00Z')

    def test_official_press_header_and_camera_publication(self):
        html='<h2 class="text-white">Comunicato nazionale verificato</h2><div class="border-t"><p class="text-light-grey">29 ottobre 2026</p></div>'
        item=metadata(html,'https://www.confindustria.it/comunicati-stampa/prova')
        self.assertEqual(item['title'],'Comunicato nazionale verificato')
        self.assertEqual(item['publishedAt'],'2026-10-29T00:00:00Z')
        html='<h1>Notizia parlamentare verificata</h1><p>notizia pubblicata il 30 Ottobre 2026</p><p>Evento 4 novembre 2026</p>'
        self.assertEqual(metadata(html,'https://comunicazione.camera.it/archivio-prima-pagina/19-63725')['publishedAt'],'2026-10-30T00:00:00Z')

    def test_libera_news_card_dates_are_scoped_to_article(self):
        card='<div class="content"><div class="categoria">News</div><h3><a href="it-schede-2873-notizia">Notizia ufficiale verificata</a></h3><p><strong>22 ott 2026</strong> - Il testo della scheda.</p></div>'
        event=card.replace('News','Eventi').replace('2873','1111')
        html=event+card+card+'<footer>30 ott 2026</footer>'
        items=listing_metadata(html,'https://www.libera.it/',{'id':'libera'})
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['publishedAt'],'2026-10-22T00:00:00Z')
        self.assertEqual(items[0]['excerpt'],'Il testo della scheda.')
        self.assertEqual(listing_metadata(html,'https://other.it/',{'id':'libera'}),[])

    def test_source_selector_avoids_promotional_cards(self):
        html='<h4><a href="/donazioni/">Sostieni il nostro lavoro</a></h4><h3><a href="/vera-notizia/">Una notizia con data verificata</a></h3>'
        self.assertEqual(candidates(html,'https://example.org/news/',{'candidateSelector':'h3 a[href]'})[0]['url'],'https://example.org/vera-notizia/')

    def test_robots_wildcards_allow_and_crawl_delay(self):
        rules='User-agent: *\nDisallow: /*feed*\nAllow: /feed/public$\nCrawl-delay: 10'
        self.assertEqual(robots_policy(rules,'https://example.org/feed/'),(False,10))
        self.assertEqual(robots_policy(rules,'https://example.org/feed/public'),(True,10))
        self.assertFalse(robots_policy('User-agent: *\nDisallow: /città/','https://example.org/citt%C3%A0/x')[0])

    def test_verified_publication_labels(self):
        b='<main><h1>Notizia comunale</h1><p>Data: 23 ottobre 2026</p><p>Ultimo aggiornamento 30 ottobre 2026</p></main>'
        self.assertTrue(metadata(b,'https://comune.bollate.mi.it/it/news/prova','Notizia comunale')['publishedAt'].startswith('2026-10-23'))
        n='<h1>La newsletter del giorno</h1><p class="nl-post-date">29 ottobre 2026</p>'
        self.assertTrue(metadata(n,'https://breakingitaly.it/newsletter/prova')['publishedAt'].startswith('2026-10-29'))
        i='<h1>Comunicato istituzionale</h1><span class="iss-date-info">Pubblicato 13/10/2025 - Modificato 13/10/2026</span>'
        self.assertTrue(metadata(i,'https://www.iss.it/-/comunicato')['publishedAt'].startswith('2025-10-13'))
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
