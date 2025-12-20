import requests
from scrapy import Selector
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_ff():
    url = "https://www.forexfactory.com/calendar"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    logger.info(f"Testing FF: {url}")
    response = requests.get(url, headers=headers)
    
    sel = Selector(text=response.text)
    rows = sel.css('tr.calendar__row')
    logger.info(f"Total rows found: {len(rows)}")
    
    for i, row in enumerate(rows[:5]):
        cells = row.css('td')
        logger.info(f"Row {i}: Total cells {len(cells)}")
        for j, cell in enumerate(cells):
            text = cell.xpath('string(.)').get(default="").strip()
            logger.info(f"  Cell {j}: '{text}' | HTML: {cell.get()[:60]}")

if __name__ == "__main__":
    test_ff()
