import requests
import time
import feedparser
from bs4 import BeautifulSoup

# 1. Substitute this with your actual Discord, Slack, or local server endpoint
WEBHOOK_URL = "https://your-server-endpoint-or-chat-app.com"

# Using global, free business feeds that do not require account keys
BUSINESS_FEEDS = [
    {"source": "BBC Business", "url": "http://feeds.bbci.co.uk/news/business/rss.xml"},
    {"source": "MarketWatch Top Stories", "url": "https://feeds.marketwatch.com/marketwatch/topstories"},
    {"source": "CNBC Business", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"}
]

# Track processed stories across loops to block duplicate alerts
seen_stories = set()

def clean_html_to_pure_text(html_content):
    """Strips all HTML tags, inline scripts, adverts, and tracking pixels."""
    if not html_content:
        return ""
    # Parse HTML and extract textual strings only
    soup = BeautifulSoup(html_content, "html.parser")
    
    # Strip common promo or ad block elements if embedded in descriptions
    for element in soup(["script", "style", "a", "img"]):
        element.decompose()
        
    text = soup.get_text(separator=" ")
    # Clean up whitespace gaps left behind by stripped links
    return " ".join(text.split()).strip()

def monitor_world_business_news():
    print("Scanning global financial channels for updates...")
    
    for feed in BUSINESS_FEEDS:
        try:
            # Parse the open XML payload
            parsed_feed = feedparser.parse(feed["url"])
            
            # Look at the 5 most recent entries per network
            for entry in parsed_feed.entries[:5]:
                title = clean_html_to_pure_text(entry.get("title", ""))
                summary = clean_html_to_pure_text(entry.get("summary", ""))
                guid = entry.get("id", entry.get("link", title))
                
                if guid not in seen_stories:
                    seen_stories.add(guid)
                    
                    # Construct a payload consisting strictly of plain text data
                    payload = {
                        "text": f"💼 *{feed['source']}* \n"
                                f"**Title:** {title}\n"
                                f"**Summary:** {summary}\n"
                                f"---"
                    }
                    
                    # Push out the data block
                    response = requests.post(WEBHOOK_URL, json=payload, timeout=10)
                    if response.status_code in [200, 204]:
                        print(f"Successfully pushed text headline: {title[:40]}...")
                        
        except Exception as e:
            print(f"Skipping feed {feed['source']} due to data read error: {e}")

if __name__ == "__main__":
    print("Initiating keyless text-only world business alert bot...")
    while True:
        monitor_world_business_news()
        # Global channels update roughly every 10-15 minutes
        time.sleep(600) 
