from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
import os
from dotenv import load_dotenv

load_dotenv()
import json
import logging
from perplexity_client import Perplexity
from analyst_prompt import get_analysis_prompt
from calendar_prompt import get_calendar_prompt
from news_prompt import get_news_prompt

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Forex AI Analysis API - Powered by Perplexity")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Perplexity client with Claude Sonnet 4.5
PERPLEXITY_TOKEN = os.getenv("PERPLEXITY_TOKEN", "eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..NG6caEamxjKf-axb.9IPgCRmzfySvR72_Xsc1M22A7VcuzxHNGU59Aoc-hNNMkWh-PBoyFDFHe-7K3wrT8Az-mF7HiUIOQISgPQxtCjMWGyUQeLI8iFdfV3svXCJ2w0fqSu6gNhLAU3NfY1qYie1_s0lyvcX0an3imz5O1SOhvfH5nrN4_-AZ2LuIpAyrGLjExJwcf69tE5vY0mkLCw3cIo5ILlB_j1pND04VBxWyWEkbW4rmceMYLA9jNODxbIpP4JuqGIeGtERPYjGC6A.mzEcwTxaKBwBb4my3R6osw")

# Global Perplexity client - will be initialized on first use
perplexity_client = None

@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    global perplexity_client
    try:
        logger.info("Initializing Perplexity client on startup...")
        perplexity_client = Perplexity(token=PERPLEXITY_TOKEN)
        logger.info("✓ Perplexity client initialized successfully")
    except Exception as e:
        logger.error(f"✗ Failed to initialize Perplexity client: {e}", exc_info=True)
        logger.warning("Server will start but AI features may not work")

# Cache storage
cache = {
    "calendar": {"data": None, "timestamp": None},
    "news": {"data": None, "timestamp": None},
    "analysis": {"data": None, "timestamp": None}
}
CACHE_DURATION = 4 * 60 * 60  # 4 hours in seconds

# ============= DATA MODELS =============

class CalendarEvent(BaseModel):
    date: str
    time: str
    country: str
    event: str
    currency: str
    impact: str
    actual: Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None

class NewsStory(BaseModel):
    title: str
    summary: str
    source: str
    date: str
    impact: str
    affected_pairs: List[str] = []

class MarketAnalysis(BaseModel):
    sentiment: str  # BULLISH, BEARISH, NEUTRAL
    confidence: int
    top_pairs: List[str]
    risk_factors: List[str]
    opportunities: List[str]
    reasoning: str

class AnalysisRequest(BaseModel):
    include_calendar: bool = True
    include_news: bool = True
    custom_query: Optional[str] = None

# ============= HELPER FUNCTIONS =============

def get_perplexity_client():
    """Get Perplexity client instance"""
    if perplexity_client is None:
        raise HTTPException(status_code=503, detail="AI service not initialized. Please restart the server.")
    return perplexity_client

def is_cache_valid(cache_key: str) -> bool:
    """Check if cache is still valid"""
    try:
        if cache[cache_key]["data"] is None or cache[cache_key]["timestamp"] is None:
            return False
        
        elapsed = datetime.now().timestamp() - cache[cache_key]["timestamp"]
        return elapsed < CACHE_DURATION
    except Exception as e:
        logger.error(f"Cache validation error: {e}")
        return False

def extract_json_from_text(text: str) -> Optional[Any]:
    """Helper to extract JSON from AI response, handling markdown blocks and cleaning common issues"""
    if not text:
        return None
        
    # Log the response to a debug file
    try:
        with open("last_ai_response.txt", "w", encoding="utf-8") as f:
            f.write(text)
    except:
        pass

    try:
        # 1. Try direct parse
        return json.loads(text.strip())
    except:
        try:
            import re
            # 2. Look for markdown code blocks (```json ... ```)
            json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
            if json_match:
                content = json_match.group(1).strip()
                try:
                    return json.loads(content)
                except:
                    # Try cleaning trailing commas or other common issues
                    content = re.sub(r',\s*([\]}])', r'\1', content)
                    return json.loads(content)
            
            # 3. Look for anything between [ and ] or { and }
            # For arrays:
            array_match = re.search(r'(\[[\s\S]*\])', text)
            if array_match:
                try:
                    return json.loads(array_match.group(1).strip())
                except:
                    pass
                    
            # For objects:
            obj_match = re.search(r'(\{[\s\S]*\})', text)
            if obj_match:
                try:
                    return json.loads(obj_match.group(1).strip())
                except:
                    pass
        except:
            pass
    return None

def parse_calendar_response(response: str) -> List[Dict]:
    """Parse Perplexity response into calendar events using JSON extraction"""
    data = extract_json_from_text(response)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Handle cases where AI returns {"events": [...]}
        return data.get("events", [])
    
    # Fallback to very basic parsing if JSON fails
    logger.warning("JSON parsing failed for calendar, using fallback")
    events = []
    lines = response.split('\n')
    for line in lines:
        line = line.strip()
        if any(kw in line.lower() for kw in ['event', 'release', 'data']) and ':' in line:
            events.append({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "time": "TBD",
                "country": "Various",
                "event": line.split(':')[-1].strip(),
                "currency": "USD",
                "impact": "Medium"
            })
    return events

def parse_news_response(response: str) -> List[Dict]:
    """Parse Perplexity response into news stories using JSON extraction"""
    data = extract_json_from_text(response)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("news", data.get("stories", []))
    
    # Fallback
    logger.warning("JSON parsing failed for news, using fallback")
    return []

def parse_analysis_response(response: str) -> Dict:
    """Parse Perplexity response into market analysis using JSON extraction"""
    data = extract_json_from_text(response)
    if isinstance(data, dict):
        return data
    
    # Fallback to original logic if no JSON
    sentiment = "NEUTRAL"
    if any(word in response.lower() for word in ['bullish', 'positive', 'upward', 'rally']):
        sentiment = "BULLISH"
    elif any(word in response.lower() for word in ['bearish', 'negative', 'downward', 'decline']):
        sentiment = "BEARISH"
    
    return {
        "sentiment": sentiment,
        "confidence": 75,
        "top_pairs": ["EUR/USD", "GBP/USD", "USD/JPY"],
        "risk_factors": ["Market uncertainty"],
        "reasoning": response
    }

# ============= API ENDPOINTS =============

@app.get("/")
async def root():
    """API root endpoint"""
    return {
        "name": "Forex AI Analysis API",
        "version": "2.0.0",
        "powered_by": "Perplexity AI + Claude Sonnet 4.5",
        "model": "claude-3.5-sonnet",
        "endpoints": {
            "calendar": "/api/calendar",
            "news": "/api/news",
            "analysis": "/api/analyze",
            "fundamental_analysis": "/api/fundamental-analysis",
            "search": "/api/search",
            "health": "/api/health"
        },
        "status": "operational",
        "features": {
            "real_time_data": True,
            "web_search": "60-80 searches per analysis",
            "caching": "4 hours",
            "fundamental_only": "No technical analysis"
        }
    }

@app.get("/api/calendar")
async def get_calendar(
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    force_refresh: bool = Query(False, description="Force refresh cache")
):
    """
    Get economic calendar events using Perplexity AI
    """
    try:
        # Check cache
        if not force_refresh and is_cache_valid("calendar"):
            logger.info("Returning cached calendar data")
            return JSONResponse(content=cache["calendar"]["data"])
        
        # Set default date range if not provided
        if not date_from:
            date_from = datetime.now().strftime("%Y-%m-%d")
        if not date_to:
            date_to = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        
        # Query Perplexity
        client = get_perplexity_client()
        query = get_calendar_prompt(date_from, date_to)
        
        logger.info(f"Querying Perplexity/Claude for calendar: {date_from} to {date_to}")
        response = client.ask(query)
        logger.info(f"Received response ({len(response)} chars)")
        
        # Parse response
        events = parse_calendar_response(response)
        
        # Update cache
        result = {
            "success": True,
            "date_from": date_from,
            "date_to": date_to,
            "events": events,
            "count": len(events),
            "source": "Perplexity AI",
            "cached_at": datetime.now().isoformat()
        }
        
        cache["calendar"]["data"] = result
        cache["calendar"]["timestamp"] = datetime.now().timestamp()
        
        return JSONResponse(content=result)
        
    except Exception as e:
        logger.error(f"Calendar endpoint error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/news")
async def get_news(
    force_refresh: bool = Query(False, description="Force refresh cache")
):
    """
    Get latest forex and financial news using Perplexity AI
    """
    try:
        # Check cache
        if not force_refresh and is_cache_valid("news"):
            logger.info("Returning cached news data")
            return JSONResponse(content=cache["news"]["data"])
        
        # Query Perplexity
        client = get_perplexity_client()
        query = get_news_prompt()
        
        logger.info("Querying Perplexity/Claude for latest news")
        response = client.ask(query)
        logger.info(f"Received response ({len(response)} chars)")
        
        # Parse response
        news_stories = parse_news_response(response)
        
        # Update cache
        result = {
            "success": True,
            "news": news_stories,
            "count": len(news_stories),
            "source": "Perplexity AI",
            "cached_at": datetime.now().isoformat()
        }
        
        cache["news"]["data"] = result
        cache["news"]["timestamp"] = datetime.now().timestamp()
        
        return JSONResponse(content=result)
        
    except Exception as e:
        logger.error(f"News endpoint error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/analyze")
async def analyze_market(
    request: AnalysisRequest,
    force_refresh: bool = Query(False, description="Force refresh cache")
):
    """
    Get comprehensive market analysis using Perplexity AI
    """
    try:
        # Check cache (only if no custom query)
        if not force_refresh and not request.custom_query and is_cache_valid("analysis"):
            logger.info("Returning cached analysis data")
            return JSONResponse(content=cache["analysis"]["data"])
        
        # Build context
        context_parts = []
        
        if request.include_calendar:
            calendar_data = await get_calendar(force_refresh=False)
            context_parts.append(f"Economic Calendar: {json.dumps(calendar_data)[:500]}")
        
        if request.include_news:
            news_data = await get_news(force_refresh=False)
            context_parts.append(f"Latest News: {json.dumps(news_data)[:500]}")
        
        context = "\n\n".join(context_parts)
        
        # Build query using the premium high-fidelity analyst prompt
        current_date = datetime.now().strftime("%Y-%m-%d")
        current_time = datetime.now().strftime("%H:%M")
        
        if request.custom_query:
            query = f"{request.custom_query}\n\nContext:\n{context}"
        else:
            query = get_analysis_prompt(current_date, current_time)
        
        # Query Perplexity
        client = get_perplexity_client()
        logger.info(f"Querying Perplexity for structured market analysis (JSON-only)")
        response = client.ask(query)
        
        # Parse response
        analysis = parse_analysis_response(response)
        
        # Update cache (only if not custom query)
        result = {
            "success": True,
            "analysis": analysis,
            "source": "Perplexity AI",
            "generated_at": datetime.now().isoformat()
        }
        
        if not request.custom_query:
            cache["analysis"]["data"] = result
            cache["analysis"]["timestamp"] = datetime.now().timestamp()
        
        return JSONResponse(content=result)
        
    except Exception as e:
        logger.error(f"Analysis endpoint error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/search")
async def search(
    q: str = Query(..., description="Search query"),
):
    """
    General search endpoint for any forex/financial question
    """
    try:
        client = get_perplexity_client()
        logger.info(f"Search query: {q}")
        response = client.ask(q)
        
        return JSONResponse(content={
            "success": True,
            "query": q,
            "response": response,
            "source": "Perplexity AI",
            "timestamp": datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Search endpoint error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    try:
        client = get_perplexity_client()
        return {
            "status": "healthy",
            "perplexity": "connected",
            "model": "claude-3.5-sonnet",
            "cache": {
                "calendar": is_cache_valid("calendar"),
                "news": is_cache_valid("news"),
                "analysis": is_cache_valid("analysis")
            }
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }

@app.post("/api/fundamental-analysis")
async def get_fundamental_analysis(
    force_refresh: bool = Query(False, description="Force refresh cache")
):
    """
    Comprehensive fundamental market analysis using AI
    
    This endpoint performs 60-80 web searches to gather:
    - Economic calendar data
    - Fed policy context
    - Leading indicators
    - Intermarket signals
    - Historical patterns
    - Sector fundamentals
    
    Returns probability-weighted market predictions based on pure fundamentals.
    """
    try:
        from analyst_prompt import get_analysis_prompt
        from calendar_prompt import get_calendar_prompt
        from news_prompt import get_news_prompt
        from pydantic import BaseModel, Field
        from datetime import datetime
        
        # Check cache
        cache_key = "fundamental_analysis"
        if cache_key not in cache:
            cache[cache_key] = {"data": None, "timestamp": None}
        
        if not force_refresh and is_cache_valid(cache_key):
            logger.info("Returning cached fundamental analysis")
            return JSONResponse(content=cache[cache_key]["data"])
        
        # Get current date/time
        now = datetime.now()
        current_date = now.strftime("%Y-%m-%d")
        current_time = now.strftime("%H:%M:%S")
        
        # Generate analysis prompt
        analysis_prompt = get_analysis_prompt(current_date, current_time)
        
        # Query Perplexity with Claude Sonnet 4.5
        client = get_perplexity_client()
        logger.info("Starting comprehensive fundamental analysis...")
        logger.info("This will perform 60-80 web searches and may take 2-3 minutes...")
        
        # Use Claude Sonnet 4.5 for superior analysis
        response = client.ask(analysis_prompt, model="claude-3.5-sonnet")
        
        # Parse and structure the response
        result = {
            "success": True,
            "analysis": response,
            "model": "claude-3.5-sonnet",
            "date": current_date,
            "time": current_time,
            "source": "Perplexity AI + Claude Sonnet 4.5",
            "generated_at": datetime.now().isoformat(),
            "cache_duration": "4 hours"
        }
        
        # Update cache
        cache[cache_key]["data"] = result
        cache[cache_key]["timestamp"] = datetime.now().timestamp()
        
        logger.info("✓ Fundamental analysis completed successfully")
        return JSONResponse(content=result)
        
    except Exception as e:
        logger.error(f"Fundamental analysis endpoint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8006))
    uvicorn.run(app, host="0.0.0.0", port=port)
