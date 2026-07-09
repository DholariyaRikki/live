import os
import sys
import time
import json
import logging
from datetime import date
import requests
from pymongo import MongoClient

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("sync_mongo")

MONGO_URI = os.getenv("MONGO_URI") or "mongodb+srv://admin:admin123@cluster0.abcde.mongodb.net/?retryWrites=true&w=majority"
DB_NAME = os.getenv("MONGO_DB_NAME") or "agriflow_live"
COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME") or "daily_prices"

FILTERS_URL = "https://api.agmarknet.gov.in/v1/daily-price-arrival/filters"
REPORT_URL = "https://api.agmarknet.gov.in/v1/prices-and-arrivals/market-report/daily"
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

def fetch_gujarat_markets():
    """Fetches all Gujarat market IDs from the AGMARKNET filters API."""
    logger.info("Fetching Gujarat market IDs from filters API...")
    try:
        response = requests.get(FILTERS_URL, headers=HEADERS, timeout=60)
        if response.status_code == 200:
            res_json = response.json()
            data = res_json.get('data', {})
            markets = data.get('market_data', [])
            guj_market_ids = [m.get('id') for m in markets if m.get('state_id') == 11]
            logger.info(f"Found {len(guj_market_ids)} Gujarat markets.")
            return guj_market_ids
        else:
            logger.error(f"Filters API returned status {response.status_code}")
    except Exception as e:
        logger.error(f"Failed to fetch filters: {e}")
    return []

def fetch_gujarat_data(date_str, market_ids):
    """Fetches daily report for Gujarat (State ID 11)."""
    if not market_ids:
        logger.error("No market IDs provided. Cannot fetch data.")
        return None
        
    payload = {
        "date": date_str,
        "State": [11],
        "title": "Market-wise Commodity Report",
        "marketIds": market_ids,
        "includeExcel": False,
        "stateIds": [11]
    }
    
    logger.info(f"Fetching report for {date_str} from AGMARKNET API directly...")
    
    for attempt in range(3):
        try:
            response = requests.post(REPORT_URL, json=payload, headers=HEADERS, timeout=60)
            if response.status_code == 200:
                res_json = response.json()
                if res_json.get('success', True):
                    return res_json
                else:
                    logger.error(f"API returned failure: {res_json.get('message')}")
            else:
                logger.error(f"API returned status {response.status_code}")
        except Exception as e:
            logger.error(f"Attempt {attempt+1} failed: {e}")
        time.sleep(5)
        
    return None

def store_in_mongodb(report_data, date_str):
    """Stores the raw report payload inside MongoDB."""
    if not report_data:
        logger.error("No data to store in MongoDB.")
        return False
        
    if "abcde" in MONGO_URI or not MONGO_URI:
        logger.warning("Using dummy MONGO_URI. Please set the real MONGO_URI environment variable.")
        return False

    try:
        logger.info(f"Connecting to MongoDB...")
        client = MongoClient(MONGO_URI)
        db = client[DB_NAME]
        collection = db[COLLECTION_NAME]
        
        result = collection.update_one(
            {"date": date_str}, 
            {"$set": {"date": date_str, "payload": report_data}}, 
            upsert=True
        )
        logger.info(f"Successfully updated/inserted today's data in MongoDB. Match: {result.matched_count}, Upserted ID: {result.upserted_id}")
        client.close()
        return True
    except Exception as e:
        logger.error(f"MongoDB storage failure: {e}")
        return False

def main():
    today_str = date.today().strftime("%Y-%m-%d")
    logger.info(f"Starting standalone live MongoDB synchronizer for {today_str}...")
    
    market_ids = fetch_gujarat_markets()
    report = fetch_gujarat_data(today_str, market_ids)
    if report:
        success = store_in_mongodb(report, today_str)
        if success:
            logger.info("Live MongoDB sync completed successfully!")
        else:
            logger.error("MongoDB storage failed.")
    else:
        logger.error("Failed to fetch report from AGMARKNET API.")

if __name__ == "__main__":
    main()
