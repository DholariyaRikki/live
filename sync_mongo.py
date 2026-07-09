import os
import sys
import logging
from datetime import datetime
import requests
from pymongo import MongoClient
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('live.sync')

# Load environment variables
load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://admin:admin123@cluster0.abcde.mongodb.net/?retryWrites=true&w=majority")
DB_NAME = os.getenv("MONGO_DB_NAME", "agriflow_live")
COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME", "daily_prices")

FILTERS_URL = "https://api.agmarknet.gov.in/v1/daily-price-arrival/filters"
REPORT_URL = "https://api.agmarknet.gov.in/v1/prices-and-arrivals/market-report/daily"
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def get_gujarat_market_ids():
    """Queries the AGMARKNET filters metadata and returns Gujarat market IDs."""
    logger.info("Fetching Gujarat market list from filters API...")
    try:
        response = requests.get(FILTERS_URL, headers=HEADERS, timeout=60)
        if response.status_code != 200:
            logger.error(f"Failed to fetch filters: HTTP {response.status_code}")
            logger.error(f"Response Content: {response.text[:200]}")
            return []
            
        data = response.json()
        market_data = data.get("data", {}).get("market_data", [])
        guj_market_ids = [m.get("id") for m in market_data if m.get("state_id") == 11 and m.get("id")]
        
        logger.info(f"Successfully retrieved {len(guj_market_ids)} Gujarat market IDs.")
        return guj_market_ids
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Network error while connecting to filters API: {req_err}")
        logger.error("TIP: If running in GitHub Actions, the API gateway might be blocking GitHub Actions cloud IPs. Try running manually or using a proxy.")
        return []
    except Exception as e:
        logger.error(f"Error parsing market list: {e}")
        return []

def fetch_daily_report(date_str, market_ids):
    """Fetches the full daily price report for the given date and markets."""
    if not market_ids:
        logger.error("No market IDs provided. Skipping fetch.")
        return None
        
    logger.info(f"Fetching daily report for {date_str}...")
    payload = {
        "date": date_str,
        "State": [11],
        "title": "Market-wise Commodity Report",
        "marketIds": market_ids,
        "includeExcel": False,
        "stateIds": [11]
    }
    
    try:
        response = requests.post(REPORT_URL, json=payload, headers=HEADERS, timeout=90)
        if response.status_code != 200:
            logger.error(f"Report API returned HTTP {response.status_code}")
            return None
            
        res_json = response.json()
        if not res_json.get("success", True):
            logger.error(f"Report API failed: {res_json.get('message')}")
            return None
            
        return res_json
    except Exception as e:
        logger.error(f"Error calling daily report API: {e}")
        return None

def store_in_mongodb(report_data, date_str):
    """Stores the raw report payload inside MongoDB."""
    if not report_data:
        logger.error("No data to store in MongoDB.")
        return False
        
    # Check if using default placeholder string
    if "abcde" in MONGO_URI or not MONGO_URI:
        import json
        output_path = os.path.join(os.path.dirname(__file__), "output.json")
        logger.info(f"Using default placeholder URI. Running in Local Test Mode. Saving today's payload to {output_path}...")
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(report_data, f, indent=4)
            logger.info("Local Test Mode completed successfully! Today's API data was fetched and saved locally.")
            return True
        except Exception as err:
            logger.error(f"Failed to write local file: {err}")
            return False
            
    try:
        logger.info(f"Connecting to MongoDB...")
        client = MongoClient(MONGO_URI)
        db = client[DB_NAME]
        collection = db[COLLECTION_NAME]
        
        # Prepare document
        document = {
            "date": date_str,
            "synced_at": datetime.utcnow(),
            "payload": report_data
        }
        
        # Update if exists for this date, or insert new
        result = collection.update_one(
            {"date": date_str},
            {"$set": document},
            upsert=True
        )
        
        logger.info(f"Successfully updated/inserted today's data in MongoDB. Match: {result.matched_count}, Upserted ID: {result.upserted_id}")
        client.close()
        return True
    except Exception as e:
        logger.error(f"MongoDB storage failure: {e}")
        return False

def main():
    # Today's date YYYY-MM-DD
    today_str = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"Starting standalone live MongoDB synchronizer for {today_str}...")
    
    market_ids = get_gujarat_market_ids()
    if not market_ids:
        logger.error("Could not load markets. Sync failed.")
        sys.exit(1)
        
    report = fetch_daily_report(today_str, market_ids)
    if not report:
        logger.error("Could not fetch today's report data. Sync failed.")
        sys.exit(1)
        
    success = store_in_mongodb(report, today_str)
    if success:
        logger.info("Live MongoDB sync completed successfully!")
    else:
        logger.error("MongoDB storage failed.")
        sys.exit(1)

if __name__ == "__main__":
    main()
