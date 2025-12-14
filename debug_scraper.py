import requests
from bs4 import BeautifulSoup
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def debug_scrape():
    url = "https://tradingeconomics.com/calendar"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        table = soup.find('table', {'id': 'calendar'})
        
        if not table:
            print("Table not found")
            return

        rows = table.find_all('tr')
        print(f"Found {len(rows)} rows")
        
        found_stars = False
        with open('debug_output.txt', 'w', encoding='utf-8') as f:
            for i, row in enumerate(rows):
                # Search for anything looking like a star or sentiment
                if "sentiment" in str(row) or "star" in str(row) or "high" in str(row):
                     f.write(f"--- Row {i} (Found Candidate) ---\n")
                     f.write(row.prettify())
                     found_stars = True
                     if i > 50: break # Don't dump too many
        
        if not found_stars:
             print("No stars/sentiment found in first 100 rows")
        else:
             print("Found candidate rows with stars/sentiment")
                
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    debug_scrape()
