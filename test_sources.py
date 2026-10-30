import asyncio, json, os
from datetime import datetime, timezone
import requests
import trafilatura
from bs4 import BeautifulSoup

SOURCES = {
 'pagella-politica': 'https://pagellapolitica.it/articoli',
 'banca-italia': 'https://www.bancaditalia.it/media/notizie/index.html',
 'iai': 'https://www.iai.it/it/pubblicazioni/lista/iai-commentaries',
 'comune-bollate': 'https://comune.bollate.mi.it/it/news',
 'breaking-italy': 'https://breakingitaly.it/',
}

async def main():
 from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
 results=[]
 async with AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False)) as crawler:
  for source_id,url in SOURCES.items():
   entry={'sourceId':source_id,'url':url,'testedAt':datetime.now(timezone.utc).isoformat(),'methods':{}}
   try:
    response=requests.get(url,timeout=18,headers={'User-Agent':'OsservatorioResearchTest/1.0 (public pages only)'})
    entry['methods']['trafilatura']={'status':response.status_code,'finalUrl':response.url}
    response.raise_for_status()
    soup=BeautifulSoup(response.content,'html.parser')
    doc=trafilatura.extract(response.text,output_format='json',with_metadata=True,include_comments=False)
    parsed=json.loads(doc) if doc else {}
    entry['methods']['trafilatura']={'status':response.status_code,'finalUrl':response.url,'title':parsed.get('title'),'date':parsed.get('date'),'textChars':len(parsed.get('text') or ''),'linksFound':len(soup.select('a[href]'))}
   except Exception as exc:
    entry['methods'].setdefault('trafilatura',{}).update({'error':str(exc)[:300],'errorType':type(exc).__name__})
   # Do not retry sites that report rate limiting or access restrictions.
   if entry['methods']['trafilatura'].get('status') in (401,403,429):
    entry['methods']['crawl4ai']={'skipped':'restricted response'}
   elif any(str(entry['methods']['trafilatura'].get('error','')).find(f'{c} ')>=0 for c in (401,403,429)):
    entry['methods']['crawl4ai']={'skipped':'restricted response'}
   else:
    try:
     r=await crawler.arun(url=url,config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS,page_timeout=20000))
     entry['methods']['crawl4ai']={'success':bool(r.success),'status':r.status_code,'finalUrl':getattr(r,'redirected_url',None) or r.url,'markdownChars':len(getattr(r.markdown, 'raw_markdown', r.markdown) or ''),'title':(r.metadata or {}).get('title'),'error':(r.error_message or '')[:250]}
    except Exception as exc:
     entry['methods']['crawl4ai']={'error':str(exc)[:300],'errorType':type(exc).__name__}
   results.append(entry)
   print(json.dumps(entry,ensure_ascii=False),flush=True)
 os.makedirs('results',exist_ok=True)
 with open('results/test-results.json','w',encoding='utf-8') as f:json.dump(results,f,ensure_ascii=False,indent=2)

if __name__=='__main__':asyncio.run(main())
