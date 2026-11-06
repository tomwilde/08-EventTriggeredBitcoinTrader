import requests
import time

# Configuration
WEBHOOK_URL = "https://your-server-endpoint-or-chat-app.com"
ALERT_PRICE_USD = 80000.0  # Set your threshold

def check_bitcoin_price():
    try:
        # Fetching live aggregated price data
        response = requests.get("https://coingecko.com").json()
        current_price = response["bitcoin"]["usd"]
        
        if current_price >= ALERT_PRICE_USD:
            payload = {
                "text": f"🚨 Bitcoin Alert! Current price is ${current_price:,.2f}, which has crossed your target of ${ALERT_PRICE_USD:,.2f}."
            }
            requests.post(WEBHOOK_URL, json=payload)
            print(f"Alert fired! Price: ${current_price}")
            
    except Exception as e:
        print(f"Error fetching data: {e}")

# Run loop (e.g., check every 60 seconds)
if __name__ == "__main__":
    while True:
        check_bitcoin_price()
        time.sleep(60)
