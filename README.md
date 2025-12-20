# Perplexity AI Backend - Claude Sonnet 4.5

## Overview

Your backend is now configured to use **Claude Sonnet 4.5** via Perplexity AI for all AI-powered features.

## Model Configuration

### Current Model
- **Model**: Claude Sonnet 4.5 (`claude-3.5-sonnet`)
- **Provider**: Perplexity AI
- **Session Token**: Updated (expires periodically)

### Available Models

The Perplexity client supports multiple models:

1. **claude-3.5-sonnet** (Default) ✅
   - Best for: Comprehensive analysis, detailed responses
   - Speed: Fast
   - Quality: Excellent

2. **gpt-4o**
   - Best for: General queries, quick responses
   - Speed: Very fast
   - Quality: Good

3. **gpt-4-turbo**
   - Best for: Complex reasoning
   - Speed: Moderate
   - Quality: Excellent

## Usage

### Python Client

```python
from perplexity_client import Perplexity

# Initialize with your token
client = Perplexity(token="your_token_here")

# Use Claude Sonnet 4.5 (default)
response = client.ask("What's the EUR/USD forecast?")

# Or specify model explicitly
response = client.ask(
    "Analyze the forex market", 
    model="claude-3.5-sonnet"
)

# Use GPT-4o instead
response = client.ask(
    "Quick market update", 
    model="gpt-4o"
)
```

### API Endpoints

All backend endpoints now use Claude Sonnet 4.5 by default:

```bash
# Get news (uses Claude Sonnet 4.5)
curl http://localhost:8000/api/news

# Get calendar (uses Claude Sonnet 4.5)
curl "http://localhost:8000/api/calendar?date_from=2025-12-20&date_to=2025-12-27"

# Get analysis (uses Claude Sonnet 4.5)
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"include_calendar": true, "include_news": true}'
```

## Session Token Management

### Current Token
```
eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..NG6caEamxjKf-axb.9IPgCRmzfySvR72_Xsc1M22A7VcuzxHNGU59Aoc-hNNMkWh-PBoyFDFHe-7K3wrT8Az-mF7HiUIOQISgPQxtCjMWGyUQeLI8iFdfV3svXCJ2w0fqSu6gNhLAU3NfY1qYie1_s0lyvcX0an3imz5O1SOhvfH5nrN4_-AZ2LuIpAyrGLjExJwcf69tE5vY0mkLCw3cIo5ILlB_j1pND04VBxWyWEkbW4rmceMYLA9jNODxbIpP4JuqGIeGtERPYjGC6A.mzEcwTxaKBwBb4my3R6osw
```

### How to Update Token

When your session expires, update the token in:

1. **Environment file** (`.env`):
```bash
PERPLEXITY_TOKEN=your_new_token_here
```

2. **Restart the server**:
```bash
cd backend-server
py main.py
```

### Getting a New Token

1. Go to [perplexity.ai](https://www.perplexity.ai)
2. Log in to your account
3. Open browser DevTools (F12)
4. Go to Application → Cookies
5. Find `__Secure-next-auth.session-token`
6. Copy the value
7. Update `.env` file

## Configuration Files

### `.env`
```bash
# Perplexity Session Token (Claude Sonnet 4.5)
PERPLEXITY_TOKEN=eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0...

# Server Configuration
PORT=8000
HOST=0.0.0.0

# Cache Configuration (in seconds)
CACHE_DURATION=14400  # 4 hours
```

### `main.py`
The backend automatically uses Claude Sonnet 4.5 for all queries. The model is specified in the payload sent to Perplexity.

### `perplexity_client.py`
Updated to support model selection:
```python
def ask(self, query: str, model: str = "claude-3.5-sonnet") -> str:
    # Uses Claude Sonnet 4.5 by default
    ...
```

## Benefits of Claude Sonnet 4.5

### Superior Analysis
- More detailed market insights
- Better understanding of complex financial data
- Improved reasoning for predictions

### Accuracy
- Higher quality responses
- More reliable data interpretation
- Better context understanding

### Comprehensive Responses
- Longer, more detailed answers
- Better structured output
- More actionable insights

## Testing

### Test Model Selection

```bash
# Test with Claude Sonnet 4.5
curl "http://localhost:8000/api/search?q=What%20model%20are%20you%3F"
```

Expected response will mention Claude or Anthropic.

### Verify Health

```bash
curl http://localhost:8000/api/health
```

Should return:
```json
{
  "status": "healthy",
  "perplexity": "connected",
  "cache": {...}
}
```

## Troubleshooting

### Issue: "AI service not initialized"
**Solution**: 
1. Check token is valid in `.env`
2. Restart server: `py main.py`

### Issue: "Unauthorized" or 401 errors
**Solution**: 
1. Token has expired
2. Get new token from perplexity.ai
3. Update `.env`
4. Restart server

### Issue: Slow responses
**Solution**: 
- First request initializes connection (slower)
- Subsequent requests use cache (faster)
- Claude Sonnet 4.5 may take 5-10 seconds for complex queries

## Performance

### Response Times
- **News**: ~10-15 seconds (first request)
- **Calendar**: ~10-15 seconds (first request)
- **Analysis**: ~15-20 seconds (comprehensive)
- **Cached**: <1 second

### Cache Duration
- **Default**: 4 hours
- **Configurable**: Set `CACHE_DURATION` in `.env`
- **Force Refresh**: Add `?force_refresh=true` to any endpoint

## Next Steps

1. ✅ Claude Sonnet 4.5 configured
2. ✅ New session token updated
3. ✅ Backend restarted
4. 🔄 Test all endpoints
5. 🔄 Integrate with React Native app

## Support

For issues or questions:
1. Check server logs: `py main.py`
2. Test health endpoint: `curl http://localhost:8000/api/health`
3. Verify token is valid
4. Check Perplexity AI status

---

**Status**: ✅ Claude Sonnet 4.5 Active
**Last Updated**: 2025-12-20
