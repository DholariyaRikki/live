import os
import time
import logging
from datetime import date, datetime

import requests
from pymongo import MongoClient, UpdateOne

# --------------------------------------------------
# Logging
# --------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger("AgriFlow")

# --------------------------------------------------
# Configuration
# --------------------------------------------------

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("MONGO_DB_NAME", "agriflow_live")
COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME", "daily_prices")

FILTER_URL = "https://api.agmarknet.gov.in/v1/daily-price-arrival/filters"
REPORT_URL = "https://api.agmarknet.gov.in/v1/prices-and-arrivals/market-report/daily"

HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0"
}

# --------------------------------------------------
# MongoDB
# --------------------------------------------------

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]

collection.create_index(
    [
        ("date", 1),
        ("market_id", 1),
        ("commodity_id", 1),
        ("variety", 1),
        ("grade", 1)
    ],
    unique=True
)

# --------------------------------------------------
# Fetch Gujarat Markets
# --------------------------------------------------

def fetch_market_ids():

    logger.info("Fetching Gujarat Market IDs...")

    r = requests.get(FILTER_URL, headers=HEADERS, timeout=60)
    r.raise_for_status()

    response = r.json()

    markets = response.get("data", {}).get("market_data", [])

    ids = [
        market["id"]
        for market in markets
        if market.get("state_id") == 11
    ]

    logger.info(f"Found {len(ids)} markets")

    return ids

# --------------------------------------------------
# Fetch Report
# --------------------------------------------------

def fetch_report(today, market_ids):

    payload = {
        "date": today,
        "State": [11],
        "stateIds": [11],
        "marketIds": market_ids,
        "includeExcel": False,
        "title": "Market-wise Commodity Report"
    }

    for attempt in range(3):

        try:

            r = requests.post(
                REPORT_URL,
                json=payload,
                headers=HEADERS,
                timeout=120
            )

            r.raise_for_status()

            report = r.json()

            logger.info("Report Downloaded")

            return report

        except Exception as e:

            logger.error(f"Attempt {attempt+1}: {e}")

            time.sleep(5)

    return None

# --------------------------------------------------
# Detect states
# --------------------------------------------------

def get_states(report):

    if not report:
        return []

    if "data" in report and isinstance(report["data"], dict):
        return report["data"].get("states", [])

    return report.get("states", [])

# --------------------------------------------------
# Convert JSON to Mongo Documents
# --------------------------------------------------

def prepare_documents(report, report_date):

    documents = []

    states = get_states(report)

    if not states:
        logger.error("No states found in API response.")
        logger.info(report.keys())
        return documents

    for state in states:

        state_id = state.get("stateId")
        state_name = state.get("stateName")

        for market in state.get("markets", []):

            market_id = market.get("marketId")
            market_name = market.get("marketName")

            for commodity in market.get("commodities", []):

                if "commodityId" not in commodity:
                    continue

                commodity_id = commodity.get("commodityId")
                commodity_name = commodity.get("commodityName")

                total_arrivals = commodity.get("total_arrivals", 0)

                for row in commodity.get("data", []):

                    documents.append({

                        "date": report_date,

                        "state_id": state_id,
                        "state_name": state_name,

                        "market_id": market_id,
                        "market_name": market_name,

                        "commodity_id": commodity_id,
                        "commodity_name": commodity_name,

                        "variety": row.get("variety"),
                        "grade": row.get("grade"),

                        "arrival": row.get("arrivals"),
                        "total_arrivals": total_arrivals,

                        "minimum_price": row.get("minimumPrice"),
                        "maximum_price": row.get("maximumPrice"),
                        "modal_price": row.get("modalPrice"),

                        "unit_arrival": row.get("unitOfArrivals"),
                        "unit_price": row.get("unitOfPrice"),

                        "updated_at": datetime.utcnow()
                    })

    return documents

# --------------------------------------------------
# Save to MongoDB
# --------------------------------------------------

def save_documents(documents):

    if not documents:
        logger.warning("No documents to save.")
        return

    operations = []

    for doc in documents:

        operations.append(

            UpdateOne(

                {
                    "date": doc["date"],
                    "market_id": doc["market_id"],
                    "commodity_id": doc["commodity_id"],
                    "variety": doc["variety"],
                    "grade": doc["grade"]
                },

                {
                    "$set": doc,
                    "$setOnInsert": {
                        "created_at": datetime.utcnow()
                    }
                },

                upsert=True

            )

        )

    result = collection.bulk_write(operations)

    logger.info(f"Inserted : {result.upserted_count}")
    logger.info(f"Modified : {result.modified_count}")

# --------------------------------------------------
# Main
# --------------------------------------------------

def main():

    today = date.today().strftime("%Y-%m-%d")

    logger.info(f"Sync Started : {today}")

    market_ids = fetch_market_ids()

    if not market_ids:
        logger.error("No market IDs found.")
        return

    report = fetch_report(today, market_ids)

    if not report:
        logger.error("Report download failed.")
        return

    logger.info(f"Response Keys: {list(report.keys())}")

    documents = prepare_documents(report, today)

    logger.info(f"Prepared {len(documents)} documents")

    save_documents(documents)

    logger.info("Sync Completed Successfully")

# --------------------------------------------------

if __name__ == "__main__":
    main()
