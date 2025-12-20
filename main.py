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
try:
    from swiftshadow.classes import ProxyInterface
except ImportError:
    try:
        # Fallback for older versions where it was named 'Proxy'
        from swiftshadow.classes import Proxy as ProxyInterface
    except ImportError:
        # If classes nested structure fails, try top-level (some libraries export to __init__)
        try:
            from swiftshadow import ProxyInterface
        except ImportError:
            from swiftshadow import Proxy as ProxyInterface
from scrapy import Selector
import logging
import asyncio
import os
from threading import Thread
import nest_asyncio

# Apply nest_asyncio to allow swiftshadow to call asyncio.run() 
# while uvicorn's event loop is already running.
nest_asyncio.apply()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Free Forex Economic Calendar API")

# ============= PROXY MANAGEMENT (SwiftShadow) =============
# Thanks to nest_asyncio, we can now initialize this normally.
# It will still call asyncio.run() internally, which will now work.
try:
    proxy_manager = ProxyInterface(protocol="http", autoRotate=True)
except Exception as e:
    logger.error(f"Failed to initialize ProxyInterface: {e}")
    proxy_manager = None

def get_proxy_manager():
    return proxy_manager

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

class NewsStory(BaseModel):
    id: str
    title: str
    content: Optional[str] = None
    link: str
    date: str # ISO string
    source: str = "ForexFactory"
    impact: str = "Low" # Low, Medium, High (Hot)
    is_hot: bool = False

# ============= DATA MODELS =============
# ... (models remain the same)

@app.get("/debug/fetch")
def debug_fetch(url: str = "https://tradingeconomics.com/calendar"):
    """Debug endpoint to test external connectivity and status codes"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        return {
            "url": url,
            "status_code": response.status_code,
            "length": len(response.text),
            "sample": response.text[:500] if response.status_code == 200 else "",
            "is_blocked": "access denied" in response.text.lower() or "forbidden" in response.text.lower()
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/debug/calendar")
def debug_calendar(date_from: str = "2025-12-15", date_to: str = "2025-12-21"):
    """Run scraper directly and return results"""
    try:
        events = scrape_trading_economics(date_from, date_to)
        return {
            "count": len(events),
            "events_sample": events[:5] if events else []
        }
    except Exception as e:
        return {"error": str(e)}

# Caching disabled per user request

@app.on_event("startup")
async def startup_event():
    # No background threads per user request for LIVE-ONLY data
    logger.info("Server started in LIVE-ONLY mode with SwiftShadow rotation")

# ============= SCRAPING FUNCTIONS =============
def fetch_with_retry(url: str, max_retries: int = 5, use_proxy: bool = True) -> Optional[requests.Response]:
    """Fetch URL with retry logic using SwiftShadow IP rotation"""
    
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
            
            # SwiftShadow Rotation
            manager = get_proxy_manager()
            if not manager:
                logger.error("Failed to initialize swiftshadow proxy manager")
                return None
            
            proxy_str = ""
            try:
                if hasattr(manager, 'get'):
                    # SwiftShadow v2.x (Modern)
                    proxy_obj = manager.get()
                    proxy_str = proxy_obj.as_string()
                elif hasattr(manager, 'proxy'):
                    # SwiftShadow v1.x (Legacy)
                    proxy_str = manager.proxy
                    if hasattr(manager, 'rotate'):
                        manager.rotate()
                else:
                    logger.error(f"Unknown proxy manager type: {type(manager)}")
                    return None
            except Exception as e:
                logger.error(f"Error getting proxy from manager: {e}")
                return None
                
            proxies = {"http": proxy_str, "https": proxy_str}
            logger.info(f"Attempt {attempt + 1} with SwiftShadow proxy: {proxy_str}")
            
            try:
                # Always use proxy as requested by user to avoid Render IP blacklist
                response = requests.get(url, headers=headers, proxies=proxies, timeout=20)
                
                if response.status_code == 200 and len(response.text) > 500:
                    return response
                
                if response.status_code in [403, 429]:
                    logger.warning(f"Blocked or Rate Limited (Status {response.status_code}). Rotating...")
                else:
                    logger.warning(f"Request failed with status {response.status_code}")
                    
            except Exception as e:
                logger.error(f"Proxy request error: {e}")
            
            # Wait briefly between retries
            time.sleep(random.uniform(1, 3))
            
        except Exception as e:
            logger.error(f"Fetch internal error: {e}")
            time.sleep(2)
    
    return None

# ============= CALENDAR PARSERS =============

def scrape_trading_economics(date_from: str, date_to: str, country: Optional[str] = None) -> List[CalendarEvent]:
    """Scrape Trading Economics calendar using Scrapy Selector"""
    events = []
    try:
        url = f"https://tradingeconomics.com/calendar?c=&d1={date_from}&d2={date_to}"
        if country:
            url += f"&c={country.upper()}"
        
        logger.info(f"Scraping Trading Economics: {url}")
        response = fetch_with_retry(url)
        
        if not response:
            logger.error("TradingEconomics: Fetch failed (None response)")
            return events
        
        logger.info(f"TradingEconomics: Status {response.status_code}, Length {len(response.text)}")
        if response.status_code != 200:
            logger.error(f"TradingEconomics: Non-200 status code: {response.status_code}")
            return events

        if "access denied" in response.text.lower() or "forbidden" in response.text.lower():
            logger.error("TradingEconomics: Access denied/Forbidden detected in response body")
            return events

        sel = Selector(text=response.text)
        
        # TradingEconomics rows are usually <tr> with class 'table-row' or similar
        # Or they are inside a table with ID 'calendar'
        rows = sel.css('table#calendar tr, table.table tr')
        
        current_date_str = date_from
        today_dt = datetime.now()

        for row in rows:
            # Check for date header
            # Usually <th colspan=".." class="table-header"> or similar
            date_text = row.css('th[colspan]::text, td[colspan] b::text, td[colspan]::text').get()
            if date_text:
                header_text = date_text.strip()
                if header_text:
                    # Parse date (logic remain same or improved)
                    clean_text = header_text.replace(',', '').strip()
                    if "Today" in clean_text:
                        current_date_str = today_dt.strftime('%Y-%m-%d')
                        continue
                    elif "Tomorrow" in clean_text:
                        current_date_str = (today_dt + timedelta(days=1)).strftime('%Y-%m-%d')
                        continue
                    
                    parsed_dt = None
                    for fmt in ('%A %B %d %Y', '%B %d %Y', '%A %B %d', '%Y-%m-%d'):
                        try:
                            parsed_dt = datetime.strptime(clean_text, fmt)
                            if fmt == '%A %B %d':
                                parsed_dt = parsed_dt.replace(year=today_dt.year)
                            break
                        except ValueError:
                            if len(clean_text.split()) > 4:
                                clean_short = " ".join(clean_text.split()[:4])
                                try:
                                    parsed_dt = datetime.strptime(clean_short, '%A %B %d %Y')
                                    break
                                except ValueError: pass
                            continue
                    
                    if parsed_dt:
                        current_date_str = parsed_dt.strftime('%Y-%m-%d')
                    continue

            # Data rows usually have <td> cells
            cells = row.css('td')
            if len(cells) >= 4:
                # Extract event details
                # Format: Time, Country, Event, Actual, Previous, Consensus, Forecast
                time_val = cells[0].css('::text').get(default="").strip()
                country_val = cells[1].css('::text').get(default="").strip()
                event_val = cells[2].css('a::text, ::text').get(default="").strip()
                
                # Uniqueness
                event_id = row.attrib.get('data-id', '')
                te_event_id = f"TradingEconomics-{event_id}" if event_id else f"TE-{current_date_str}-{event_val}"

                # Impact calculation (logic remain same)
                category = row.attrib.get('data-category', '')
                calculated_impact = "Low"
                cat_lower = (category + " " + event_val).lower()
                if any(x in cat_lower for x in ['rate', 'inflation', 'gdp', 'payroll', 'unemployment', 'fed', 'fomc', 'ecb', 'boe']):
                    calculated_impact = "High"
                elif any(x in cat_lower for x in ['balance', 'spending', 'sales', 'confidence', 'pmi', 'manufacturing']):
                     calculated_impact = "Medium"

                event = CalendarEvent(
                    source="TradingEconomics",
                    date=current_date_str,
                    time=time_val,
                    country=country_val,
                    event=event_val,
                    actual=cells[3].css('::text').get(default="").strip(),
                    previous=cells[4].css('::text').get(default="").strip() if len(cells) > 4 else None,
                    consensus=cells[5].css('::text').get(default="").strip() if len(cells) > 5 else None,
                    forecast=cells[6].css('::text').get(default="").strip() if len(cells) > 6 else None,
                    impact=calculated_impact,
                    event_id=te_event_id
                )
                events.append(event)
                
        logger.info(f"TradingEconomics: Found {len(events)} events")
    except Exception as e:
        logger.error(f"TradingEconomics error: {e}")
    
    return events

def scrape_investing_com(date_from: str, date_to: str, country: Optional[str] = None) -> List[CalendarEvent]:
    """Scrape Investing.com calendar using Scrapy Selector"""
    events = []
    try:
        url = "https://www.investing.com/economic-calendar/"
        logger.info(f"Scraping Investing.com: {url}")
        response = fetch_with_retry(url)
        
        if not response:
            return events
        
        sel = Selector(text=response.text)
        
        # Investing.com uses rows with class 'js-event-item' or inside '#economicCalendar_table'
        rows = sel.css('tr.js-event-item')
        
        for row in rows:
            try:
                # Extract event data using CSS selectors
                dt_raw = row.attrib.get('data-event-datetime', date_from)
                event_date = dt_raw.split()[0] if dt_raw else date_from
                
                # Impact is often data-event-importance or stars
                impact_val = row.attrib.get('data-event-importance', 'N/A')
                
                event = CalendarEvent(
                    source="Investing.com",
                    date=event_date,
                    time=row.css('td.time::text').get(default="N/A").strip(),
                    country=row.css('td.flagCur::text').get(default="N/A").strip(),
                    event=row.css('td.event a::text, td.event::text').get(default="N/A").strip(),
                    actual=row.css('td[id^="eventActual_"]::text').get(default="").strip(),
                    forecast=row.css('td[id^="eventForecast_"]::text').get(default="").strip(),
                    previous=row.css('td[id^="eventPrevious_"]::text').get(default="").strip(),
                    impact=impact_val,
                    event_id=f"Investing-{row.attrib.get('id', 'N/A')}"
                )
                events.append(event)
            except Exception as e:
                continue
        
        logger.info(f"Investing.com: Found {len(events)} events")
    except Exception as e:
        logger.error(f"Investing.com error: {e}")
    
    return events

def scrape_forexfactory(date_from: str, date_to: str) -> List[CalendarEvent]:
    """Scrape ForexFactory calendar using Scrapy Selector"""
    events = []
    try:
        from_dt = datetime.strptime(date_from, '%Y-%m-%d')
        url = f"https://www.forexfactory.com/calendar?week={from_dt.strftime('%b%d.%Y').lower()}"
        
        logger.info(f"Scraping ForexFactory: {url}")
        response = fetch_with_retry(url)
        
        if not response:
            return events
        
        sel = Selector(text=response.text)
        rows = sel.css('tr.calendar__row')
        
        current_date_str = date_from
        for row in rows:
            try:
                # Update current date if present
                date_val = row.css('td.calendar__date span::text').get()
                if date_val:
                    # FF dates are like "Oct 23" - we may need to handle year
                    current_date_str = date_val.strip()
                
                event_title = row.css('td.calendar__event span::text, td.calendar__event::text').get()
                if not event_title:
                    continue
                
                # Impact is often a class on the impact cell: calendar__impact-icon--high etc.
                impact_class = row.css('td.calendar__impact span::attr(class)').get(default="")
                impact = "Low"
                if "high" in impact_class.lower(): impact = "High"
                elif "medium" in impact_class.lower(): impact = "Medium"

                event = CalendarEvent(
                    source="ForexFactory",
                    date=current_date_str,
                    time=row.css('td.calendar__time::text').get(default="N/A").strip(),
                    country=row.css('td.calendar__currency::text').get(default="N/A").strip(),
                    currency=row.css('td.calendar__currency::text').get(default="N/A").strip(),
                    event=event_title.strip(),
                    impact=impact,
                    actual=row.css('td.calendar__actual::text').get(default="").strip(),
                    forecast=row.css('td.calendar__forecast::text').get(default="").strip(),
                    previous=row.css('td.calendar__previous::text').get(default="").strip(),
                    event_id=f"FF-{current_date_str}-{event_title.strip()}"
                )
                events.append(event)
            except Exception as e:
                continue
        
        logger.info(f"ForexFactory: Found {len(events)} events")
    except Exception as e:
        logger.error(f"ForexFactory error: {e}")
    
    return events

def scrape_fxstreet(date_from: str, date_to: str) -> List[CalendarEvent]:
    """Scrape FXStreet calendar using Scrapy Selector"""
    events = []
    try:
        url = "https://www.fxstreet.com/economic-calendar"
        logger.info(f"Scraping FXStreet: {url}")
        response = fetch_with_retry(url)
        
        if not response:
            return events
        
        sel = Selector(text=response.text)
        # FXStreet items are often 'div.fxs_c_economicCalendar_item'
        rows = sel.css('div.fxs_c_economicCalendar_item')
        
        for row in rows:
            try:
                event = CalendarEvent(
                    source="FXStreet",
                    date=date_from,
                    time=row.css('span.fxs_c_economicCalendar_dateTime_time::text').get(default="N/A").strip(),
                    country=row.css('span.fxs_c_economicCalendar_country::text').get(default="N/A").strip(),
                    event=row.css('span.fxs_c_economicCalendar_event_title::text').get(default="N/A").strip(),
                    impact=row.css('span.fxs_c_economicCalendar_volatility::attr(class)').get(default="").split()[-1],
                    actual=row.css('span.fxs_c_economicCalendar_actual::text').get(default="").strip(),
                    forecast=row.css('span.fxs_c_economicCalendar_consensus::text').get(default="").strip(),
                    previous=row.css('span.fxs_c_economicCalendar_previous::text').get(default="").strip(),
                    event_id=f"FXStreet-{row.css('span.fxs_c_economicCalendar_event_title::text').get(default='N/A').strip()}"
                )
                events.append(event)
            except Exception as e:
                continue
        
        logger.info(f"FXStreet: Found {len(events)} events")
    except Exception as e:
        logger.error(f"FXStreet error: {e}")
    
    return events

def scrape_forexfactory_news() -> Tuple[List[NewsStory], List[NewsStory]]:
    """Scrape Latest and Hot news from ForexFactory"""
    latest_news = []
    hot_news = []
    
    try:
        url = "https://www.forexfactory.com/news"
        logger.info(f"Scraping ForexFactory News: {url}")
        
        response = fetch_with_retry(url)
        if not response:
            return [], []
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 1. Scrape Latest News (Main feed)
        news_items = soup.find_all('li', {'class': 'news__list_item'})
        
        for item in news_items:
            try:
                story_div = item.find('div', {'class': 'news__story'})
                if not story_div: continue
                
                title_tag = story_div.find('a', {'class': 'news__title'})
                if not title_tag: continue
                
                title = title_tag.get_text(strip=True)
                link = title_tag.get('href', '')
                if link and not link.startswith('http'):
                    link = f"https://www.forexfactory.com{link}"
                    
                date_str = datetime.now().isoformat()
                
                impact = "Low"
                if "impact-high" in str(item): impact = "High"
                elif "impact-medium" in str(item): impact = "Medium"
                
                story_id = f"ff-news-{hash(title)}"
                
                story = NewsStory(
                    id=story_id,
                    title=title,
                    link=link,
                    date=date_str,
                    source="ForexFactory",
                    impact=impact,
                    is_hot=False
                )
                latest_news.append(story)
            except Exception as e:
                continue
                
        # 2. Scrape 'Hottest Stories' (Sidebar)
        sidebars = soup.find_all('div', {'class': 'sidebar__widget'})
        for widget in sidebars:
            header = widget.find('h3', {'class': 'sidebar__title'})
            if header and 'Hottest Stories' in header.get_text():
                hot_items = widget.find_all('li')
                for item in hot_items:
                    try:
                        link_tag = item.find('a')
                        if not link_tag: continue
                        
                        title = link_tag.get_text(strip=True)
                        link = link_tag.get('href', '')
                        if link and not link.startswith('http'):
                            link = f"https://www.forexfactory.com{link}"
                        
                        story_id = f"ff-hot-{hash(title)}"
                        
                        story = NewsStory(
                            id=story_id,
                            title=title,
                            link=link,
                            date=datetime.now().isoformat(),
                            source="ForexFactory",
                            impact="High",
                            is_hot=True
                        )
                        hot_news.append(story)
                    except:
                        continue
                        
        logger.info(f"ForexFactory News: Found {len(latest_news)} latest, {len(hot_news)} hot")
        
    except Exception as e:
        logger.error(f"ForexFactory News Error: {e}")
        
    return latest_news, hot_news

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
    
    # LIVE ONLY: Caching disabled per user request
    logger.info(f"LIVE-ONLY mode: Scraping fresh data for {effective_from} to {effective_to}...")
    
    try:
        # Scrape live (blocking)
        live_data = scrape_trading_economics(effective_from, effective_to, country)
        if not live_data:
             # Fallback to other sources
             logger.info("TradingEconomics empty, trying Investing.com...")
             live_data.extend(scrape_investing_com(effective_from, effective_to, country))
        
        if live_data:
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

@app.get("/news", response_model=List[NewsStory])
async def get_news(
    type: str = Query("latest", description="News type: 'latest' or 'hot'")
):
    """Get live news from ForexFactory (LIVE ONLY)"""
    latest, hot = scrape_forexfactory_news()
    if type.lower() == "hot":
        return hot
    return latest

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
    """Check SwiftShadow proxy rotation status"""
    try:
        manager = get_proxy_manager()
        if not manager:
            return {"status": "error", "error": "Manager not initialized"}
            
        proxy_str = ""
        if hasattr(manager, 'get'):
            proxy_str = manager.get().as_string()
        elif hasattr(manager, 'proxy'):
            proxy_str = manager.proxy
        
        return {
            "status": "healthy",
            "provider": "SwiftShadow",
            "current_proxy": proxy_str,
            "auto_rotate": True,
            "version": "1.x" if hasattr(manager, 'proxy') and not hasattr(manager, 'get') else "2.x"
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
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
