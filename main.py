from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
import random
import time
from pydantic import BaseModel
import json
from itertools import cycle
import logging
import asyncio
import os
from threading import Thread

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Free Forex Economic Calendar API")

# ============= FREE PROXY MANAGEMENT =============
class FreeProxyRotator:
    def __init__(self):
        self.proxies = []
        self.proxy_cycle = None
        self.last_fetch_time = None
        self.fetch_interval = 300  # 5 minutes
        self.failed_proxies = set()
        
    def fetch_free_proxies(self) -> List[str]:
        """Fetch free proxies from multiple sources"""
        all_proxies = []
        
        # Source 1: PubProxy API (Free)
        try:
            response = requests.get(
                "http://pubproxy.com/api/proxy?limit=5&format=json&type=http&https=true",
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                for proxy in data.get('data', []):
                    all_proxies.append(f"http://{proxy['ipPort']}")
        except Exception as e:
            logger.error(f"PubProxy error: {e}")
        
        # Source 2: ProxyScrape (Free)
        try:
            response = requests.get(
                "https://api.proxyscrape.com/v2/?request=get&protocol=http&timeout=10000&country=all&ssl=yes&anonymity=all&simplified=true",
                timeout=10
            )
            if response.status_code == 200:
                proxy_list = response.text.strip().split('\n')
                all_proxies.extend([f"http://{proxy}" for proxy in proxy_list[:5]])
        except Exception as e:
            logger.error(f"ProxyScrape error: {e}")
        
        # Source 3: GetProxyList API (Free)
        try:
            response = requests.get(
                "https://api.getproxylist.com/proxy?protocol[]=http&protocol[]=https",
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                proxy_ip = data.get('ip')
                proxy_port = data.get('port')
                if proxy_ip and proxy_port:
                    all_proxies.append(f"http://{proxy_ip}:{proxy_port}")
        except Exception as e:
            logger.error(f"GetProxyList error: {e}")
        
        # Source 4: Free Proxy List (GitHub)
        try:
            response = requests.get(
                "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.json",
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                for proxy in data[:5]:
                    all_proxies.append(f"http://{proxy['ip']}:{proxy['port']}")
        except Exception as e:
            logger.error(f"GitHub proxy list error: {e}")
        
        return list(set(all_proxies))  # Remove duplicates
    
    def get_proxies(self) -> List[str]:
        """Get or refresh proxy list"""
        current_time = time.time()
        
        # Refresh proxies every 5 minutes or if empty
        if (not self.last_fetch_time or 
            current_time - self.last_fetch_time > self.fetch_interval or 
            not self.proxies):
            logger.info("Fetching fresh proxies...")
            self.proxies = self.fetch_free_proxies()
            self.last_fetch_time = current_time
            self.proxy_cycle = cycle(self.proxies) if self.proxies else None
            logger.info(f"Loaded {len(self.proxies)} proxies")
        
        return self.proxies
    
    def get_next_proxy(self) -> Optional[Dict[str, str]]:
        """Get next proxy in rotation"""
        self.get_proxies()  # Ensure we have proxies
        
        if not self.proxy_cycle:
            return None
        
        # Try to find a working proxy
        for _ in range(len(self.proxies)):
            proxy = next(self.proxy_cycle)
            if proxy not in self.failed_proxies:
                return {"http": proxy, "https": proxy}
        
        # All failed, reset and try again
        self.failed_proxies.clear()
        return self.get_next_proxy()
    
    def mark_failed(self, proxy: Dict[str, str]):
        """Mark proxy as failed"""
        if proxy:
            self.failed_proxies.add(proxy.get("http"))

proxy_rotator = FreeProxyRotator()

# ============= DATA MODELS =============
class CalendarEvent(BaseModel):
    source: str
    date: str
    time: str
    country: str
    event: str
    actual: Optional[str] = None
    previous: Optional[str] = None
    consensus: Optional[str] = None
    forecast: Optional[str] = None
    impact: Optional[str] = None
    currency: Optional[str] = None
    category: Optional[str] = None 
    description: Optional[str] = None
    event_id: Optional[str] = None # Added for frontend lookup

# ============= CACHE STORAGE =============
CACHE: Dict[str, Tuple[float, List[CalendarEvent]]] = {}
CACHE_TTL = 86400  # 24 hours
# Use absolute path relative to this file
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calendar_cache.json")

# Track fetch status for frontend
FETCH_STATUS: Dict[str, str] = {}  # cache_key -> "fetching" | "ready" | "error"

def save_cache():
    """Save in-memory cache to disk"""
    try:
        serializable_cache = {}
        for key, (timestamp, events) in CACHE.items():
            serializable_cache[key] = {
                "timestamp": timestamp,
                "events": [event.dict() for event in events]
            }
        
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(serializable_cache, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved {len(CACHE)} cache entries to {CACHE_FILE}")
    except Exception as e:
        logger.error(f"Failed to save cache: {e}")

def load_cache():
    """Load cache from disk on startup"""
    global CACHE
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            loaded_cache = {}
            for key, value in data.items():
                events = [CalendarEvent(**e) for e in value['events']]
                loaded_cache[key] = (value['timestamp'], events)
            
            CACHE = loaded_cache
            logger.info(f"Loaded {len(CACHE)} cache entries from {CACHE_FILE}")
            
            # Prune old cache
            current_time = time.time()
            expired_keys = [k for k, (ts, _) in CACHE.items() if current_time - ts > CACHE_TTL]
            for k in expired_keys:
                del CACHE[k]
                
    except Exception as e:
        logger.error(f"Failed to load cache: {e}")

def background_prefetch():
    """Prefetch next 30 days of data in background - runs every 5 minutes"""
    time.sleep(5)  # Wait for server to fully start
    
    while True:  # Loop forever
        logger.info("Starting background data refresh...")
        
        try:
            # Prefetch today-7 to today+30 days (wide range for all queries)
            today = datetime.now()
            date_from = (today - timedelta(days=7)).strftime('%Y-%m-%d')
            date_to = (today + timedelta(days=30)).strftime('%Y-%m-%d')
            
            # Scrape data
            logger.info(f"Fetching data for {date_from} to {date_to}...")
            all_events = scrape_trading_economics(date_from, date_to)
            
            if all_events:
                # Store in cache with multiple key patterns for flexibility
                cache_keys = [
                    f"{date_from}_{date_to}_None_all",
                    f"{today.strftime('%Y-%m-%d')}_{(today + timedelta(days=7)).strftime('%Y-%m-%d')}_None_all",
                    f"{today.strftime('%Y-%m-%d')}_{(today + timedelta(days=14)).strftime('%Y-%m-%d')}_None_all",
                ]
                for key in cache_keys:
                    CACHE[key] = (time.time(), all_events)
                
                save_cache()
                logger.info(f"Background refresh complete: {len(all_events)} events cached")
            else:
                logger.warning("Background refresh found zero events")
                
        except Exception as e:
            logger.error(f"Background refresh failed: {e}")
        
        # Wait 5 minutes before next refresh
        logger.info("Next refresh in 5 minutes...")
        time.sleep(300)  # 5 minutes

@app.on_event("startup")
async def startup_event():
    load_cache()
    # Start background refresh thread (runs every 5 mins)
    thread = Thread(target=background_prefetch)
    thread.daemon = True
    thread.start()
    logger.info("Background refresh thread started (5-min interval)")

# ============= SCRAPING FUNCTIONS =============
def fetch_with_retry(url: str, max_retries: int = 3, use_proxy: bool = True) -> Optional[requests.Response]:
    """Fetch URL with retry logic and IP rotation"""
    
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0'
    ]
    
    for attempt in range(max_retries):
        try:
            headers = {
                'User-Agent': random.choice(user_agents),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Cache-Control': 'max-age=0'
            }
            
            # Try with proxy first
            if use_proxy and attempt > 0:  # Use proxy after first direct attempt fails
                proxy = proxy_rotator.get_next_proxy()
                if proxy:
                    logger.info(f"Attempt {attempt + 1} with proxy: {proxy.get('http')}")
                    try:
                        response = requests.get(url, headers=headers, proxies=proxy, timeout=15)
                        if response.status_code == 200 and len(response.text) > 500:
                            return response
                        proxy_rotator.mark_failed(proxy)
                    except:
                        proxy_rotator.mark_failed(proxy)
            
            # Direct request (no proxy)
            logger.info(f"Attempt {attempt + 1} direct request")
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200 and len(response.text) > 500:
                return response
            
            # Rate limiting detected, wait longer
            if response.status_code in [429, 403]:
                wait_time = random.uniform(5, 10) * (attempt + 1)
                logger.warning(f"Rate limited, waiting {wait_time}s")
                time.sleep(wait_time)
            else:
                time.sleep(random.uniform(2, 4))
            
        except Exception as e:
            logger.error(f"Attempt {attempt + 1} failed: {e}")
            time.sleep(random.uniform(3, 6))
    
    return None

# ============= CALENDAR PARSERS =============

def scrape_trading_economics(date_from: str, date_to: str, country: Optional[str] = None) -> List[CalendarEvent]:
    """Scrape Trading Economics calendar"""
    events = []
    try:
        url = f"https://tradingeconomics.com/calendar?c=&d1={date_from}&d2={date_to}"
        if country:
            url += f"&c={country.upper()}"
        
        logger.info(f"Scraping Trading Economics: {url}")
        response = fetch_with_retry(url)
        
        if not response:
            return events
        
        soup = BeautifulSoup(response.text, 'html.parser')
        table = soup.find('table', {'id': 'calendar'}) or soup.find('table', {'class': 'table'})
        
        if not table:
            logger.warning("TradingEconomics: Calendar table not found")
            return events
        
        current_date_str = date_from # Fallback
        rows = table.find_all('tr')
        
        for row in rows:
            # Check for date header (often colspan=3 or hidden-xs)
            # TE format: "Sunday December 07 2025" or similar
            date_header = row.find('th', {'colspan': True}) or row.find('td', {'colspan': True})
            
            if date_header:
                header_text = date_header.get_text(strip=True)
                # Try to parse date
                try:
                    # Remove any extra text 
                    clean_text = header_text.replace(',', '').split('   ')[0].strip()
                    # Parse "Friday December 12 2025"
                    parsed_dt = datetime.strptime(clean_text, '%A %B %d %Y')
                    current_date_str = parsed_dt.strftime('%Y-%m-%d')
                    continue
                except ValueError:
                    # Ignore if not a date (some headers are just "Actual" etc)
                    pass
            
            # Use recursive=False to avoid finding nested table cells
            cells = row.find_all('td', recursive=False)
            
            if len(cells) >= 4:
                # Extract metadata
                category = row.get('data-category', '')
                event_id = row.get('data-id', '') # Important for uniqueness
                
                # Try to map category to impact
                calculated_impact = "Low"
                cat_lower = category.lower()
                if any(x in cat_lower for x in ['rate', 'inflation', 'gdp', 'payroll', 'unemployment']):
                    calculated_impact = "High"
                elif any(x in cat_lower for x in ['balance', 'spending', 'sales', 'confidence', 'pmi']):
                     calculated_impact = "Medium"

                # Extract IDs for frontend event lookup
                # Construct unique ID: Source-Country-Date-EventName(hash)
                # Or use the data-id from TE if available
                
                te_event_id = f"TradingEconomics-{event_id}" if event_id else f"TE-{current_date_str}-{cells[2].get_text(strip=True)}"

                event = CalendarEvent(
                    source="TradingEconomics",
                    date=current_date_str,
                    time=cells[0].get_text(strip=True),
                    country=cells[1].get_text(strip=True),
                    event=cells[2].get_text(strip=True),
                    actual=cells[3].get_text(strip=True) if len(cells) > 3 else None,
                    previous=cells[4].get_text(strip=True) if len(cells) > 4 else None,
                    consensus=cells[5].get_text(strip=True) if len(cells) > 5 else None,
                    forecast=cells[6].get_text(strip=True) if len(cells) > 6 else None,
                    category=category,
                    impact=calculated_impact,
                    description=f"{category}",
                    event_id=te_event_id # Add ID for frontend lookup
                )
                events.append(event)
        
        logger.info(f"TradingEconomics: Found {len(events)} events")
    except Exception as e:
        logger.error(f"TradingEconomics error: {e}")
    
    return events

def scrape_investing_com(date_from: str, date_to: str, country: Optional[str] = None) -> List[CalendarEvent]:
    """Scrape Investing.com calendar"""
    events = []
    try:
        # Convert date format from YYYY-MM-DD to DD/MM/YYYY
        from_dt = datetime.strptime(date_from, '%Y-%m-%d')
        to_dt = datetime.strptime(date_to, '%Y-%m-%d')
        
        url = f"https://www.investing.com/economic-calendar/"
        
        logger.info(f"Scraping Investing.com: {url}")
        response = fetch_with_retry(url)
        
        if not response:
            return events
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Investing.com uses dynamic loading, try to find the calendar table
        calendar_rows = soup.find_all('tr', {'class': 'js-event-item'})
        
        for row in calendar_rows:
            try:
                event = CalendarEvent(
                    source="Investing.com",
                    date=row.get('data-event-datetime', date_from).split()[0],
                    time=row.find('td', {'class': 'time'}).get_text(strip=True) if row.find('td', {'class': 'time'}) else "N/A",
                    country=row.find('td', {'class': 'flagCur'}).get_text(strip=True) if row.find('td', {'class': 'flagCur'}) else "N/A",
                    event=row.find('td', {'class': 'event'}).get_text(strip=True) if row.find('td', {'class': 'event'}) else "N/A",
                    actual=row.find('td', {'id': 'eventActual_'}).get_text(strip=True) if row.find('td', {'id': lambda x: x and 'eventActual' in x}) else None,
                    forecast=row.find('td', {'id': 'eventForecast_'}).get_text(strip=True) if row.find('td', {'id': lambda x: x and 'eventForecast' in x}) else None,
                    previous=row.find('td', {'id': 'eventPrevious_'}).get_text(strip=True) if row.find('td', {'id': lambda x: x and 'eventPrevious' in x}) else None,
                    impact=row.get('data-event-importance', 'N/A'),
                )
                events.append(event)
            except Exception as e:
                continue
        
        logger.info(f"Investing.com: Found {len(events)} events")
    except Exception as e:
        logger.error(f"Investing.com error: {e}")
    
    return events

def scrape_forexfactory(date_from: str, date_to: str) -> List[CalendarEvent]:
    """Scrape ForexFactory calendar"""
    events = []
    try:
        # ForexFactory uses format: month.day.year
        from_dt = datetime.strptime(date_from, '%Y-%m-%d')
        url = f"https://www.forexfactory.com/calendar?week={from_dt.strftime('%b%d.%Y').lower()}"
        
        logger.info(f"Scraping ForexFactory: {url}")
        response = fetch_with_retry(url)
        
        if not response:
            return events
        
        soup = BeautifulSoup(response.text, 'html.parser')
        calendar_rows = soup.find_all('tr', {'class': 'calendar__row'})
        
        current_date = None
        for row in calendar_rows:
            try:
                # Check for date
                date_cell = row.find('td', {'class': 'calendar__cell calendar__date'})
                if date_cell:
                    date_span = date_cell.find('span')
                    if date_span:
                        current_date = date_span.get_text(strip=True)
                
                time_cell = row.find('td', {'class': 'calendar__cell calendar__time'})
                currency_cell = row.find('td', {'class': 'calendar__cell calendar__currency'})
                impact_cell = row.find('td', {'class': 'calendar__cell calendar__impact'})
                event_cell = row.find('td', {'class': 'calendar__cell calendar__event'})
                actual_cell = row.find('td', {'class': 'calendar__cell calendar__actual'})
                forecast_cell = row.find('td', {'class': 'calendar__cell calendar__forecast'})
                previous_cell = row.find('td', {'class': 'calendar__cell calendar__previous'})
                
                if event_cell:
                    event = CalendarEvent(
                        source="ForexFactory",
                        date=current_date or date_from,
                        time=time_cell.get_text(strip=True) if time_cell else "N/A",
                        country=currency_cell.get_text(strip=True) if currency_cell else "N/A",
                        currency=currency_cell.get_text(strip=True) if currency_cell else None,
                        event=event_cell.get_text(strip=True),
                        impact=impact_cell.get('class', [''])[1] if impact_cell else None,
                        actual=actual_cell.get_text(strip=True) if actual_cell else None,
                        forecast=forecast_cell.get_text(strip=True) if forecast_cell else None,
                        previous=previous_cell.get_text(strip=True) if previous_cell else None,
                    )
                    events.append(event)
            except Exception as e:
                continue
        
        logger.info(f"ForexFactory: Found {len(events)} events")
    except Exception as e:
        logger.error(f"ForexFactory error: {e}")
    
    return events

def scrape_fxstreet(date_from: str, date_to: str) -> List[CalendarEvent]:
    """Scrape FXStreet calendar"""
    events = []
    try:
        url = "https://www.fxstreet.com/economic-calendar"
        
        logger.info(f"Scraping FXStreet: {url}")
        response = fetch_with_retry(url)
        
        if not response:
            return events
        
        soup = BeautifulSoup(response.text, 'html.parser')
        event_rows = soup.find_all('div', {'class': 'fxs_c_economicCalendar_item'})
        
        for row in event_rows:
            try:
                event = CalendarEvent(
                    source="FXStreet",
                    date=date_from,
                    time=row.find('span', {'class': 'fxs_c_economicCalendar_dateTime_time'}).get_text(strip=True) if row.find('span', {'class': 'fxs_c_economicCalendar_dateTime_time'}) else "N/A",
                    country=row.find('span', {'class': 'fxs_c_economicCalendar_country'}).get_text(strip=True) if row.find('span', {'class': 'fxs_c_economicCalendar_country'}) else "N/A",
                    event=row.find('span', {'class': 'fxs_c_economicCalendar_event_title'}).get_text(strip=True) if row.find('span', {'class': 'fxs_c_economicCalendar_event_title'}) else "N/A",
                    impact=row.find('span', {'class': 'fxs_c_economicCalendar_volatility'}).get('class', [])[-1] if row.find('span', {'class': 'fxs_c_economicCalendar_volatility'}) else None,
                    actual=row.find('span', {'class': 'fxs_c_economicCalendar_actual'}).get_text(strip=True) if row.find('span', {'class': 'fxs_c_economicCalendar_actual'}) else None,
                    forecast=row.find('span', {'class': 'fxs_c_economicCalendar_consensus'}).get_text(strip=True) if row.find('span', {'class': 'fxs_c_economicCalendar_consensus'}) else None,
                    previous=row.find('span', {'class': 'fxs_c_economicCalendar_previous'}).get_text(strip=True) if row.find('span', {'class': 'fxs_c_economicCalendar_previous'}) else None,
                )
                events.append(event)
            except Exception as e:
                continue
        
        logger.info(f"FXStreet: Found {len(events)} events")
    except Exception as e:
        logger.error(f"FXStreet error: {e}")
    
    return events

def scrape_marketwatch(date_from: str, date_to: str) -> List[CalendarEvent]:
    """Scrape MarketWatch calendar"""
    events = []
    try:
        url = "https://www.marketwatch.com/economy-politics/calendar"
        
        logger.info(f"Scraping MarketWatch: {url}")
        response = fetch_with_retry(url)
        
        if not response:
            return events
        
        soup = BeautifulSoup(response.text, 'html.parser')
        event_rows = soup.find_all('tr', {'class': 'element--intraday'}) or soup.find_all('tr')
        
        for row in event_rows:
            try:
                cells = row.find_all('td')
                if len(cells) >= 3:
                    event = CalendarEvent(
                        source="MarketWatch",
                        date=date_from,
                        time=cells[0].get_text(strip=True),
                        country="US",
                        event=cells[1].get_text(strip=True),
                        actual=cells[2].get_text(strip=True) if len(cells) > 2 else None,
                        consensus=cells[3].get_text(strip=True) if len(cells) > 3 else None,
                        previous=cells[4].get_text(strip=True) if len(cells) > 4 else None,
                    )
                    events.append(event)
            except Exception as e:
                continue
        
        logger.info(f"MarketWatch: Found {len(events)} events")
    except Exception as e:
        logger.error(f"MarketWatch error: {e}")
    
    return events

def scrape_dailyfx(date_from: str, date_to: str) -> List[CalendarEvent]:
    """Scrape DailyFX calendar"""
    events = []
    try:
        url = "https://www.dailyfx.com/economic-calendar"
        
        logger.info(f"Scraping DailyFX: {url}")
        response = fetch_with_retry(url)
        
        if not response:
            return events
        
        soup = BeautifulSoup(response.text, 'html.parser')
        # Add DailyFX specific parsing logic here
        
        logger.info(f"DailyFX: Found {len(events)} events")
    except Exception as e:
        logger.error(f"DailyFX error: {e}")
    
    return events

# ============= API ENDPOINTS =============

@app.get("/")
def read_root():
    return {
        "message": "Free Forex Economic Calendar API",
        "version": "2.0",
        "features": [
            "Multiple calendar sources with auto-fallback",
            "Free proxy rotation",
            "Full date navigation",
            "No paid services required"
        ],
        "endpoints": {
            "/calendar": "Get economic calendar (all sources)",
            "/calendar/date/{date}": "Get calendar for specific date",
            "/calendar/range": "Get calendar for date range",
            "/calendar/sources": "List all available sources",
            "/proxy/status": "Check proxy status"
        },
        "sources": [
            "TradingEconomics",
            "Investing.com",
            "ForexFactory",
            "FXStreet",
            "MarketWatch",
            "DailyFX"
        ]
    }

@app.get("/calendar", response_model=List[CalendarEvent])
async def get_calendar(
    date: Optional[str] = Query(None, description="Specific date (YYYY-MM-DD)"),
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    period: Optional[str] = Query("today", description="today, tomorrow, week, month"),
    country: Optional[str] = Query(None, description="Country code filter"),
    sources: Optional[str] = Query("all", description="Comma-separated sources or 'all'")
):
    """Get economic calendar from multiple sources with fallback"""
    
    # Calculate date range
    today = datetime.now()
    
    # Determine effective date range
    effective_from = None
    effective_to = None

    if date:
        try:
            effective_from = effective_to = date
            datetime.strptime(date, '%Y-%m-%d')  # Validate format
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    elif date_from and date_to:
        try:
            datetime.strptime(date_from, '%Y-%m-%d')
            datetime.strptime(date_to, '%Y-%m-%d')
            effective_from = date_from
            effective_to = date_to
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_from/date_to format. Use YYYY-MM-DD")
    else:
        if period == "today":
            effective_from = effective_to = today.strftime('%Y-%m-%d')
        elif period == "tomorrow":
            tomorrow = today + timedelta(days=1)
            effective_from = effective_to = tomorrow.strftime('%Y-%m-%d')
        elif period == "week":
            effective_from = today.strftime('%Y-%m-%d')
            effective_to = (today + timedelta(days=7)).strftime('%Y-%m-%d')
        elif period == "month":
            effective_from = today.strftime('%Y-%m-%d')
            effective_to = (today + timedelta(days=30)).strftime('%Y-%m-%d')
        else:
            effective_from = effective_to = today.strftime('%Y-%m-%d')
    
    # Check cache - ALWAYS return cached data instantly (never block on scraping)
    cache_key = f"{effective_from}_{effective_to}_{country}_{sources}"
    
    # Try exact match first
    if cache_key in CACHE:
        timestamp, cached_data = CACHE[cache_key]
        if time.time() - timestamp < CACHE_TTL:
            logger.info(f"Serving cached data for {cache_key}")
            return cached_data
    
    # Try to find ANY cached data that overlaps with the requested range
    for key, (timestamp, cached_data) in CACHE.items():
        if time.time() - timestamp < CACHE_TTL and cached_data:
            # Filter events to match requested date range
            filtered = [
                e for e in cached_data 
                if effective_from <= e.date <= effective_to
            ]
            if filtered:
                logger.info(f"Serving partial cached data ({len(filtered)} events) for {cache_key}")
                return filtered
    
    # No cache available - Force Scrape
    logger.info(f"No cache for {cache_key}, scraping live...")
    
    try:
        # Scrape live (blocking)
        live_data = scrape_trading_economics(effective_from, effective_to, country)
        if not live_data:
             # Fallback to other sources
             live_data.extend(scrape_investing_com(effective_from, effective_to, country))
        
        if live_data:
            # Cache the result
            CACHE[cache_key] = (time.time(), live_data)
            save_cache()
            return live_data
    except Exception as e:
        logger.error(f"Live scrape failed: {e}")
        
    return []

@app.get("/calendar/date/{target_date}", response_model=List[CalendarEvent])
async def get_calendar_by_date(
    target_date: str,
    country: Optional[str] = Query(None, description="Country code filter")
):
    """Get calendar for a specific date (YYYY-MM-DD)"""
    try:
        datetime.strptime(target_date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    
    return await get_calendar(date=target_date, country=country, sources="all")

@app.get("/calendar/range", response_model=List[CalendarEvent])
async def get_calendar_range(
    start_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
    end_date: str = Query(..., description="End date (YYYY-MM-DD)"),
    country: Optional[str] = Query(None, description="Country code filter")
):
    """Get calendar for a date range"""
    try:
        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        end_dt = datetime.strptime(end_date, '%Y-%m-%d')
        
        if end_dt < start_dt:
            raise HTTPException(status_code=400, detail="End date must be after start date")
        
        if (end_dt - start_dt).days > 90:
            raise HTTPException(status_code=400, detail="Date range cannot exceed 90 days")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    
    all_events = []
    
    # Scrape all sources for the date range
    all_events.extend(scrape_trading_economics(start_date, end_date, country))
    all_events.extend(scrape_investing_com(start_date, end_date, country))
    all_events.extend(scrape_forexfactory(start_date, end_date))
    all_events.extend(scrape_fxstreet(start_date, end_date))
    all_events.extend(scrape_marketwatch(start_date, end_date))
    
    return all_events

@app.get("/calendar/navigate")
async def navigate_calendar(
    year: int = Query(..., description="Year"),
    month: int = Query(..., description="Month (1-12)"),
    country: Optional[str] = Query(None, description="Country code filter")
):
    """Navigate calendar by year and month"""
    try:
        if month < 1 or month > 12:
            raise HTTPException(status_code=400, detail="Month must be between 1 and 12")
        
        # Get first and last day of month
        first_day = datetime(year, month, 1)
        if month == 12:
            last_day = datetime(year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = datetime(year, month + 1, 1) - timedelta(days=1)
        
        start_date = first_day.strftime('%Y-%m-%d')
        end_date = last_day.strftime('%Y-%m-%d')
        
        events = await get_calendar_range(start_date, end_date, country)
        
        # Group by date
        calendar_data = {}
        for event in events:
            event_date = event.date
            if event_date not in calendar_data:
                calendar_data[event_date] = []
            calendar_data[event_date].append(event.dict())
        
        return {
            "year": year,
            "month": month,
            "total_events": len(events),
            "calendar": calendar_data
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/calendar/sources")
async def get_sources():
    """List all available calendar sources"""
    return {
        "sources": [
            {
                "name": "TradingEconomics",
                "url": "https://tradingeconomics.com/calendar",
                "features": ["Date range", "Country filter", "High reliability"]
            },
            {
                "name": "Investing.com",
                "url": "https://www.investing.com/economic-calendar/",
                "features": ["Real-time updates", "Multiple countries", "Impact ratings"]
            },
            {
                "name": "ForexFactory",
                "url": "https://www.forexfactory.com/calendar",
                "features": ["Trader favorite", "Detailed events", "Historical data"]
            },
            {
                "name": "FXStreet",
                "url": "https://www.fxstreet.com/economic-calendar",
                "features": ["Professional grade", "Volatility indicators"]
            },
            {
                "name": "MarketWatch",
                "url": "https://www.marketwatch.com/economy-politics/calendar",
                "features": ["US-focused", "Reliable data"]
            },
            {
                "name": "DailyFX",
                "url": "https://www.dailyfx.com/economic-calendar",
                "features": ["Forex specific", "Trading insights"]
            }
        ]
    }

@app.get("/proxy/status")
async def proxy_status():
    """Check proxy rotation status"""
    proxies = proxy_rotator.get_proxies()
    return {
        "active_proxies": len(proxies),
        "failed_proxies": len(proxy_rotator.failed_proxies),
        "last_refresh": proxy_rotator.last_fetch_time,
        "status": "healthy" if proxies else "no_proxies"
    }

@app.get("/dates/calendar/{year}/{month}")
async def get_month_calendar(year: int, month: int):
    """Get calendar view for a specific month"""
    import calendar as cal
    
    try:
        # Generate calendar for the month
        month_cal = cal.monthcalendar(year, month)
        month_name = cal.month_name[month]
        
        # Get all events for this month
        first_day = datetime(year, month, 1)
        if month == 12:
            last_day = datetime(year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = datetime(year, month + 1, 1) - timedelta(days=1)
        
        return {
            "year": year,
            "month": month,
            "month_name": month_name,
            "calendar": month_cal,
            "start_date": first_day.strftime('%Y-%m-%d'),
            "end_date": last_day.strftime('%Y-%m-%d'),
            "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
