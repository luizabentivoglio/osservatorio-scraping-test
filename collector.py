"""Public news metadata collector. Never receives private archive state or secrets."""
import asyncio
import concurrent.futures
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode, quote, unquote
from urllib.robotparser import RobotFileParser

import feedparser
import requests
import trafilatura
from bs4 import BeautifulSoup

AGENT = 'TaccuinoNewsBot/2.0 (+https://github.com/luizabentivoglio/osservatorio-scraping-test)'
MAX_BYTES = 3_145_728
NOW = datetime.now(timezone.utc)
BLOCK_CODES = {401, 402, 403, 429}
FEED_HOSTS = {'feeds.feedburner.com', 'feeds.simplecast.com', 'anchor.fm', 'www.youtube.com', 'youtube.com'}
MONTHS = {m:i+1 for i,m in enumerate('gennaio febbraio marzo aprile maggio giugno luglio agosto settembre ottobre novembre dicembre'.split())}
BAD_PATH = re.compile(r'/(?:tag|tags|category|categoria|categorie|author|autore|autori|privacy|cookie|login|signin|feed|wp-json|shop|search|ricerca)(?:/|$)|\.(?:pdf|jpe?g|png|webp|mp[34]|zip)$',re.I)
BAD_TITLE = re.compile(r'^(?:leggi(?: tutto| di più)?|scopri|vedi|continua|abbonati|iscriviti|seguici|read more|news|notizie|articoli|home|chi siamo|contatti|pubblicazioni|comunicati stampa)$',re.I)


def canonical(value, base=''):
    try:
        p=urlsplit(urljoin(base,value)); host=(p.hostname or '').lower().rstrip('.')
        if p.scheme not in ('http','https') or p.username or p.password or not host or '.' not in host or re.fullmatch(r'[\d.]+',host) or ':' in host or host.endswith(('.local','.internal','.test','.invalid')): return None
        if p.port and p.port not in (80,443): return None
        query=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in ('fbclid','gclid','msclkid')]
        return urlunsplit((p.scheme,host,p.path or '/',urlencode(query),''))
    except (ValueError,TypeError): return None


def family(url):
    return (urlsplit(url).hostname or '').lower().removeprefix('www.')


def clean(value, limit=320):
    return re.sub(r'\s+',' ',BeautifulSoup(str(value or ''),'html.parser').get_text(' ',strip=True)).strip()[:limit]


def parse_date(value):
    """Publication dates only; no last-modified or crawl-date substitutions."""
    if not value: return None
    value=clean(value,150).lower()
    value=re.sub(r'(\d)(?:st|nd|rd|th)\b',r'\1',value)
    try:
        if re.match(r'^\d{4}-\d{2}-\d{2}',value):
            dt=datetime.fromisoformat(value.replace('z','+00:00'))
        else:
            m=re.search(r'\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b',value)
            if m: dt=datetime(int(m[3]),int(m[2]),int(m[1]))
            else:
                m=re.search(r'\b(\d{1,2})\s+('+'|'.join(MONTHS)+r')\s+(\d{4})\b',value)
                if m: dt=datetime(int(m[3]),MONTHS[m[2]],int(m[1]))
                else:
                    from email.utils import parsedate_to_datetime
                    try: dt=parsedate_to_datetime(value)
                    except (ValueError,TypeError):
                        from dateutil.parser import parse
                        if not re.search(r'\b\d{4}\b',value): return None
                        dt=parse(value,fuzzy=False)
        if not dt.tzinfo: dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
    except (ValueError,TypeError,OverflowError): return None


def recent(items):
    out={}
    for item in items:
        if not item.get('publishedAt'): continue
        age=(NOW-datetime.fromisoformat(item['publishedAt'].replace('Z','+00:00'))).total_seconds()
        if -21600<=age<=14*86400 and item.get('url') and len(item.get('title',''))>=8:
            out[item['url']]=item
    return sorted(out.values(),key=lambda x:x['publishedAt'],reverse=True)[:40]


def robots_policy(text,url):
    groups=[];agents=[];rules=[];delay=0;directives=False
    for raw in text.splitlines()+['User-agent: __end__']:
        line=raw.split('#',1)[0].strip()
        if ':' not in line: continue
        key,value=(v.strip() for v in line.split(':',1));key=key.lower()
        if key=='user-agent':
            if directives:
                groups.append((agents,rules,delay));agents=[];rules=[];delay=0;directives=False
            agents.append(value.lower())
        elif agents:
            directives=True
            if key in ('allow','disallow') and value: rules.append((value,key=='allow'))
            if key=='crawl-delay':
                try: delay=max(delay,float(value))
                except ValueError: pass
    token=AGENT.split('/')[0].lower()
    matches=[(max([-1]+[0 if a=='*' else len(a) if a in token else -1 for a in ag]),rs,d) for ag,rs,d in groups]
    level=max([-1]+[m[0] for m in matches]);p=urlsplit(url)
    normalize=lambda s: quote(unquote(s),safe='/:?=&%$*-._~')
    path=normalize(p.path+('?' + p.query if p.query else ''));best=(-1,True);spacing=.5
    for score,rs,d in matches:
        if score!=level or score<0: continue
        spacing=max(spacing,d)
        for rule,allow in rs:
            rule=normalize(rule);end=rule.endswith('$');body=rule[:-1] if end else rule
            pattern='^'+'.*'.join(re.escape(part) for part in body.split('*'))+('$' if end else '')
            if re.search(pattern,path): best=max(best,(len(body.replace('*','')),allow))
    return best[1],spacing


class Failure(Exception):
    def __init__(self,code,message,status=None,url=None):
        super().__init__(message);self.code=code;self.status=status;self.url=url


class Client:
    def __init__(self,source):
        self.source=source;self.session=requests.Session();self.session.headers.update({'User-Agent':AGENT,'Accept':'application/rss+xml, application/atom+xml, text/html;q=0.9, */*;q=0.1'})
        self.roots={family(source['url']),*[family(u) for u in source.get('allowedOrigins',[])]}
        self.deadline=time.monotonic()+100;self.robots={};self.trace=[];self.last_request={}

    def allowed(self,url):
        host=urlsplit(url).hostname or ''
        return any(host==r or host.endswith('.'+r) for r in self.roots) or host in FEED_HOSTS

    def fetch(self,url,robots=False,redirects=0):
        url=canonical(url)
        if not url or not self.allowed(url): raise Failure('invalid_url','Indirizzo o dominio non autorizzato',url=url)
        if time.monotonic()>=self.deadline: raise Failure('timeout','Tempo massimo della fonte raggiunto',url=url)
        if not robots: self.check_robots(url)
        origin=urlsplit(url).scheme+'://'+urlsplit(url).netloc
        delay=robots_policy(self.robots.get(origin,''),url)[1]
        pause=self.last_request.get(origin,0)+delay-time.monotonic()
        if pause>0:
            if time.monotonic()+pause>=self.deadline: raise Failure('timeout','Intervallo della fonte oltre il budget; raccolta rinviata',url=url)
            time.sleep(pause)
        self.last_request[origin]=time.monotonic()
        try:
            r=self.session.get(url,timeout=(6,min(15,max(1,self.deadline-time.monotonic()))),allow_redirects=False,stream=True)
            self.trace.append({'url':url,'status':r.status_code})
            if r.status_code in BLOCK_CODES: r.close();raise Failure('rate_limited' if r.status_code==429 else 'access_restricted',f'HTTP {r.status_code}: accesso limitato dalla fonte',r.status_code,url)
            if 300<=r.status_code<400:
                target=urljoin(url,r.headers.get('Location',''));r.close()
                if redirects>=4: raise Failure('redirect','Troppi reindirizzamenti',url=url)
                return self.fetch(target,robots,redirects+1)
            if r.status_code>=400:
                status=r.status_code;r.close()
                if robots and status in (404,410): return '',url,status
                raise Failure('invalid_url' if status in (404,410) else 'http_error',f'HTTP {status}',status,url)
            chunks=[];size=0
            for chunk in r.iter_content(65536):
                size+=len(chunk)
                if size>MAX_BYTES: raise Failure('response_too_large','Risposta oltre 3 MB',r.status_code,url)
                chunks.append(chunk)
            raw=b''.join(chunks);encoding=r.encoding if r.encoding and r.encoding.lower()!='iso-8859-1' else 'utf-8'
            text=raw.decode(encoding,errors='replace');r.close()
            if not robots and re.search(r'<title[^>]*>\s*(just a moment|access denied|attention required|robot challenge|captcha)',text,re.I): raise Failure('access_restricted','Verifica interattiva richiesta',200,url)
            return text,url,200
        except requests.Timeout: raise Failure('timeout','Timeout della fonte',url=url)
        except requests.RequestException as exc: raise Failure('connection_error',type(exc).__name__,url=url)
        finally:
            if 'r' in locals(): r.close()

    def check_robots(self,url):
        p=urlsplit(url);origin=f'{p.scheme}://{p.netloc}'
        if origin not in self.robots:
            text,_,status=self.fetch(origin+'/robots.txt',robots=True)
            if status==200 and re.search(r'<(?:html|head|body)\b',text,re.I): raise Failure('robots_unavailable','robots.txt non verificabile',url=url)
            self.robots[origin]=text if status==200 else ''
        if not robots_policy(self.robots[origin],url)[0]: raise Failure('robots_disallowed','Percorso escluso da robots.txt',url=url)


def feed_items(text,url):
    if '<!ENTITY' in text.upper(): raise Failure('parsing_error','Entità XML non supportate',url=url)
    parsed=feedparser.parse(text)
    if not parsed.version: return None
    if parsed.bozo and not parsed.entries: raise Failure('parsing_error','Feed XML non interpretabile',url=url)
    items=[]
    for e in parsed.entries[:150]:
        link=canonical(e.get('link',''),url);title=clean(e.get('title',''),300)
        date=parse_date(e.get('published') or e.get('dc_date'))
        if not date and e.get('published_parsed'):
            date=datetime(*e.published_parsed[:6],tzinfo=timezone.utc).isoformat().replace('+00:00','Z')
        if link and title and date: items.append({'title':title,'url':link,'excerpt':clean(e.get('summary','')),'publishedAt':date,'dateEvidence':'feed:published'})
    return items


def json_objects(value,depth=0):
    if depth>7: return
    if isinstance(value,list):
        for obj in value[:150]: yield from json_objects(obj,depth+1)
    elif isinstance(value,dict):
        yield value
        for key in ('@graph','itemListElement','item','mainEntity'):
            if key in value: yield from json_objects(value[key],depth+1)


def metadata(html,url,fallback=''):
    soup=BeautifulSoup(html,'html.parser');meta={}
    for m in soup.select('meta[content]'):
        key=m.get('property') or m.get('name') or m.get('itemprop')
        if key: meta[key.lower()]=m['content']
    date=None;evidence=None;title=None;description=None
    if family(url)=='breakingitaly.it' and '/newsletter/' in url:
        stamp=soup.select_one('.nl-post-date')
        if stamp: date=parse_date(stamp.get_text(' ',strip=True));evidence='html:newsletter-publication'
    if family(url)=='osservatoriocpi.unicatt.it' and '/pubblicazioni/' in url:
        stamp=soup.select_one('.separator-date')
        if stamp: date=parse_date(stamp.parent.get_text(' ',strip=True));evidence='html:article-header'
    if family(url)=='iss.it':
        stamp=soup.select_one('.iss-date-info')
        match=re.search(r'Pubblicato\s+(\d{2}/\d{2}/\d{4})',stamp.get_text(' ',strip=True)) if stamp else None
        if match: date=parse_date(match[1]);evidence='html:publication-label'
    if family(url)=='comune.bollate.mi.it' and '/it/news/' in url:
        page=soup.select_one('main') or soup
        body=page.get_text(' ',strip=True)
        match=re.search(r'\bData\s*:\s*(\d{1,2}\s+[a-zà]+\s+\d{4})',body,re.I)
        if match: date=parse_date(match[1]);evidence='html:news-date-label';title=fallback
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            for obj in json_objects(json.loads(script.string or script.get_text())):
                if re.search(r'Article|BlogPosting|Report|SocialMediaPosting',str(obj.get('@type',''))):
                    candidate_date=parse_date(obj.get('datePublished'))
                    if candidate_date:
                        date=candidate_date
                        title=obj.get('headline') or obj.get('name');description=obj.get('description');evidence='jsonld:datePublished';break
        except (ValueError,TypeError): pass
        if date: break
    if not date:
        for key in ('article:published_time','datepublished','pubdate','date.created','dcterms.created','dc.date.issued','date'):
            date=parse_date(meta.get(key))
            if date: evidence='meta:'+key;break
    if not date and family(url)=='pagellapolitica.it':
        heading=soup.select_one('h1.post-title')
        stamp=heading.parent.select_one('.post-date > span') if heading else None
        if stamp: date=parse_date(stamp.get_text(' ',strip=True));evidence='html:post-date'
    if not date:
        for tag in soup.select('time[datetime], [itemprop="datePublished"]'):
            if tag.find_parent(['footer','nav']): continue
            if any(re.search(r'related|recommended|sidebar', ' '.join(p.get('class',[])),re.I) for p in tag.parents if hasattr(p,'get')): continue
            label=' '.join(tag.get('class',[]))+' '+tag.get_text()
            if re.search(r'modifi|aggiorna|updated',label,re.I): continue
            date=parse_date(tag.get('datetime') or tag.get('content') or tag.get_text())
            if date: evidence='html:publication-time';break
    if not date:
        main=soup.select_one('main') or soup.select_one('article') or soup.select_one('.journal-content-article')
        body=main.get_text(' ',strip=True) if main else ''
        found=re.search(r'(?:pubblicato\s*(?:il|:)?|data di pubblicazione\s*:?)\s*(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}|\d{1,2}\s+[a-zà]+\s+\d{4})',body,re.I)
        if found: date=parse_date(found[1]);evidence='html:publication-label'
    if not date:
        article=soup.select_one('article') or soup.select_one('main')
        if article:
            for tag in article.select('.published, .published-date, .publication-date, .post-date, .entry-date, .article-date, .news-date, .data-pubblicazione, .nl-post-date'):
                if any(re.search(r'related|recommended|sidebar|footer', ' '.join(p.get('class',[])),re.I) for p in tag.parents if hasattr(p,'get')): continue
                label=tag.get_text(' ',strip=True)
                if re.search(r'aggiorna|modifi|updated',label,re.I): continue
                date=parse_date(label)
                if date: evidence='html:publication-class';break
    headline=clean(title or meta.get('og:title') or meta.get('twitter:title') or (soup.h1.get_text(' ',strip=True) if soup.h1 else fallback),300)
    if not date or len(headline)<8: return None
    excerpt=clean(description or meta.get('og:description') or meta.get('description'))
    if not excerpt:
        excerpt=clean(trafilatura.extract(html,include_comments=False,include_tables=False) or '')
    return {'title':headline,'url':url,'excerpt':excerpt,'publishedAt':date,'dateEvidence':evidence}


def candidates(html,base,source):
    soup=BeautifulSoup(html,'html.parser');found={}
    main=soup.select_one('[data-fetch-key="0"]') if source.get('id')=='pagella-politica' else soup.select_one('main')
    main=main or soup
    for anchor in main.select('a[href]'):
        if anchor.find_parent(['nav','footer','header']): continue
        link=canonical(anchor['href'],base);title=clean(anchor.get_text(' ',strip=True) or anchor.get('title') or (anchor.img.get('alt') if anchor.img else ''),300)
        if not link or family(link)!=family(base) or link.rstrip('/')==base.rstrip('/') or len(title)<12 or BAD_TITLE.search(title) or BAD_PATH.search(urlsplit(link).path): continue
        if source.get('articlePattern') and not re.search(source['articlePattern'],urlsplit(link).path): continue
        score=10 if anchor.find(['h1','h2','h3','h4']) or anchor.find_parent(['h1','h2','h3','h4']) else 0
        if re.search(r'/\d{4}/|/articol|/notizi|/news/|/comunicat|/press/|/pubblicazion',link,re.I): score+=5
        old=found.get(link)
        if not old or score>old['score']: found[link]={'url':link,'title':title,'score':score}
    return sorted(found.values(),key=lambda x:-x['score'])[:12]


def collect_sync(source):
    client=Client(source);start=time.monotonic()
    result={'schemaVersion':1,'policyVersion':2,'sourceId':source['id'],'originUrl':source['url'],'configuredFeedUrl':source.get('configuredFeedUrl'),'collectedAt':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'status':'error','diagnosticCode':None,'method':'html','feedUrl':None,'items':[],'attempts':client.trace,'message':''}
    tried=set();feed_errors=[]
    try:
        feeds=list(dict.fromkeys([u for u in [source.get('configuredFeedUrl'),*source.get('feeds',[])] if u]))
        for url in feeds:
            tried.add(url)
            try:
                text,final,_=client.fetch(url);items=feed_items(text,final)
                if items is not None:
                    result.update(status='ok',method='rss',feedUrl=final,items=recent(items),message='Feed ufficiale letto; date di pubblicazione verificate.');return result,client,None
            except Failure as exc:
                if exc.code in ('robots_disallowed','access_restricted','rate_limited'): raise
                feed_errors.append({'url':url,'code':exc.code})
        html,final,_=client.fetch(source.get('listing') or source['url'])
        soup=BeautifulSoup(html,'html.parser')
        discovered=[urljoin(final,l['href']) for l in soup.select('link[rel="alternate"][href]') if re.search('rss|atom',l.get('type',''))]
        discovered += [urljoin(final,a['href']) for a in soup.select('a[href]') if re.search(r'(?:\.rss(?:[.?/]|$)|/rss(?:[/?]|$)|/feed/?$|\.xml$)',a['href'],re.I) and re.search(r'rss|feed',a.get_text(' ',strip=True)+' '+a.get('title',''),re.I)]
        for url in list(dict.fromkeys(discovered))[:3]:
            if url in tried or not client.allowed(url): continue
            try:
                text,feed_url,_=client.fetch(url);items=feed_items(text,feed_url)
                if items is not None:
                    result.update(status='ok',method='rss',feedUrl=feed_url,items=recent(items),message='Feed dichiarato dalla fonte; date di pubblicazione verificate.');return result,client,None
            except Failure as exc:
                if exc.code in ('robots_disallowed','access_restricted','rate_limited'): raise
                feed_errors.append({'url':url,'code':exc.code})
        links=candidates(html,final,source)
        result['candidateSamples']=[]
        items=[];dated=0
        for candidate in links[:6]:
            if time.monotonic()>client.deadline-2: break
            try:
                article,article_url,_=client.fetch(candidate['url']);item=metadata(article,article_url,candidate['title'])
                if not item:
                    article_soup=BeautifulSoup(article,'html.parser')
                    stamps=[{'tag':t.name,'class':t.get('class',[]),'text':clean(t.get_text(' ',strip=True),100),'datetime':t.get('datetime')} for t in article_soup.select('time,[class*="date"],[class*="data"], [itemprop="datePublished"]') if len(t.get_text(' ',strip=True))<160][:12]
                    result['candidateSamples'].append({'url':article_url,'title':candidate['title'],'h1':clean(article_soup.h1.get_text(' ',strip=True),150) if article_soup.h1 else None,'dates':stamps})
                if item: dated+=1;items.append(item)
            except Failure as exc:
                if exc.code in ('robots_disallowed','access_restricted','rate_limited'): raise
                feed_errors.append({'url':candidate['url'],'code':exc.code})
        result['candidateCount']=len(links);result['datedCount']=dated
        result['items']=recent(items);result['listingUrl']=final
        if dated:
            result.update(status='ok',method='trafilatura',message=f'{len(result["items"])} notizie recenti con data verificata; copertura parziale.')
            return result,client,None
        result.update(status='no_feed',diagnosticCode='parsing_error',message='Pagina accessibile, ma nessuna notizia con data di pubblicazione verificabile.')
        return result,client,(html,final)
    except Failure as exc:
        result.update(status='blocked' if exc.code in ('robots_disallowed','access_restricted','rate_limited') else 'error',diagnosticCode=exc.code,message=str(exc),httpStatus=exc.status,failedUrl=exc.url)
        return result,client,None
    except Exception as exc:
        result.update(diagnosticCode='internal_error',message=type(exc).__name__+': '+str(exc)[:160]);return result,client,None
    finally:
        result['durationSeconds']=round(time.monotonic()-start,2)
        if feed_errors: result['attemptErrors']=feed_errors


async def collect(source,browser_semaphore):
    result,client,fallback=await asyncio.to_thread(collect_sync,source)
    if fallback:
        async with browser_semaphore:
            try:
                from crawl4ai import AsyncWebCrawler,BrowserConfig,CrawlerRunConfig,CacheMode
                async with AsyncWebCrawler(config=BrowserConfig(headless=True,verbose=False,user_agent=AGENT)) as crawler:
                    client.deadline=time.monotonic()+50
                    listing=fallback[1];client.check_robots(listing)
                    await asyncio.sleep(robots_policy(client.robots.get(urlsplit(listing).scheme+'://'+urlsplit(listing).netloc,''),listing)[1])
                    r=await crawler.arun(url=listing,config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS,page_timeout=25000,check_robots_txt=False))
                    if r.status_code in BLOCK_CODES: raise Failure('access_restricted',f'Browser HTTP {r.status_code}',r.status_code,listing)
                    if not r.success: raise Failure('render_error','Rendering della pagina non riuscito',r.status_code,listing)
                    html=r.html or ''
                    if re.search(r'<title[^>]*>\s*(just a moment|access denied|attention required|robot challenge|captcha)',html,re.I): raise Failure('access_restricted','Verifica interattiva richiesta',200,listing)
                    items=[]
                    for c in candidates(html,listing,source)[:6]:
                        if time.monotonic()>client.deadline-2: break
                        try:
                            article,url,_=await asyncio.to_thread(client.fetch,c['url'])
                            item=metadata(article,url,c['title'])
                            if not item:
                                await asyncio.to_thread(client.check_robots,url)
                                await asyncio.sleep(robots_policy(client.robots.get(urlsplit(url).scheme+'://'+urlsplit(url).netloc,''),url)[1])
                                rendered=await crawler.arun(url=url,config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS,page_timeout=18000,check_robots_txt=False))
                                if rendered.status_code in BLOCK_CODES: raise Failure('access_restricted',f'Browser HTTP {rendered.status_code}',rendered.status_code,url)
                                if rendered.success:
                                    item=metadata(rendered.html or '',url,c['title'])
                            if item: items.append(item)
                        except Failure as exc:
                            if exc.code in ('robots_disallowed','access_restricted','rate_limited'): raise
                    result.update(method='crawl4ai',candidateCount=len(candidates(html,listing,source)))
                    if items: result.update(status='ok',diagnosticCode=None,items=recent(items),datedCount=len(items),message='Pagina resa con Crawl4AI; metadati degli articoli e date verificati.')
            except Failure as exc: result.update(status='blocked' if exc.code in ('robots_disallowed','access_restricted','rate_limited') else 'error',diagnosticCode=exc.code,message=str(exc))
            except Exception as exc: result.update(browserError=type(exc).__name__+': '+str(exc)[:180])
    client.session.close()
    return result


async def main():
    config=json.loads(Path('sources.json').read_text());sources=config['sources']
    ids=os.environ.get('SOURCE_IDS','').strip()
    if ids: sources=[s for s in sources if s['id'] in ids.split(',')]
    semaphore=asyncio.Semaphore(4);browsers=asyncio.Semaphore(2)
    output=Path('data/sources');output.mkdir(parents=True,exist_ok=True)
    results=[]
    async def run(source):
        async with semaphore:
            previous_path=output/(source['id']+'.json')
            previous=json.loads(previous_path.read_text()) if previous_path.exists() else None
            age=(NOW-datetime.fromisoformat(previous['collectedAt'].replace('Z','+00:00'))).total_seconds() if previous else 999999
            if previous and previous['status']=='blocked' and age<4*3600:
                result=previous|{'policyVersion':2,'message':previous['message']+'; prossimo controllo dopo quattro ore.'}
            else:
                result=await collect(source,browsers)
            (output/(source['id']+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
            print(json.dumps({'sourceId':source['id'],'status':result['status'],'method':result['method'],'count':len(result['items']),'code':result['diagnosticCode'],'message':result['message']},ensure_ascii=False),flush=True)
            results.append(result)
    await asyncio.gather(*(run(s) for s in sources))
    summary={'schemaVersion':1,'generatedAt':datetime.now(timezone.utc).isoformat(),'sourceCount':len(results),'ok':sum(r['status']=='ok' for r in results),'items':sum(len(r['items']) for r in results),'results':[{k:r.get(k) for k in ('sourceId','status','method','diagnosticCode','message','candidateCount','datedCount')}|{'itemCount':len(r['items'])} for r in sorted(results,key=lambda r:r['sourceId'])]}
    Path('data/summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    lines=['# Raccolta fonti Osservatorio','','| Fonte | Stato | Metodo | Notizie | Diagnosi |','| --- | --- | --- | ---: | --- |']
    for r in summary['results']: lines.append(f"| {r['sourceId']} | {r['status']} | {r['method']} | {r['itemCount']} | {r['diagnosticCode'] or '—'} |")
    lines+=['','Le date sono verificate nei feed o nei metadati di pubblicazione dei singoli articoli. Nessun dato personale dell’archivio è letto o pubblicato.']
    Path('data/summary.md').write_text('\n'.join(lines))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f: f.write('\n'.join(lines))
    if len(results)!=len(sources) or not results: raise RuntimeError('Raccolta incompleta')
    if any(r.get('diagnosticCode')=='internal_error' for r in results): raise RuntimeError('Errori interni del raccoglitore: consultare gli artifact')


if __name__=='__main__': asyncio.run(main())
