import requests
import json

base_url = "http://localhost:8005"

def test_endpoint(name, url):
    print(f"\n--- Testing {name} ---")
    try:
        r = requests.get(url)
        print(f"Status: {r.status_code}")
        data = r.json()
        print(f"Success: {data.get('success')}")
        if name == 'calendar':
            items = data.get('events', [])
        else:
            items = data.get('news', [])
        
        print(f"Count: {len(items)}")
        if items:
            print("First item keys:", items[0].keys())
            print("First item sample:", json.dumps(items[0], indent=2)[:200])
        else:
            print("Response text sample:", r.text[:200])
    except Exception as e:
        print(f"Error: {e}")

test_endpoint("calendar", f"{base_url}/api/calendar")
test_endpoint("news", f"{base_url}/api/news?force_refresh=true")
