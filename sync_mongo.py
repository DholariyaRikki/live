import os
import time
import logging
from datetime import date, datetime

import requests
from pymongo import MongoClient, UpdateOne

# ----------------------------------------------------
# Logging
# ----------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger("AgriFlow")

# ----------------------------------------------------
# Mongo Config
# ----------------------------------------------------

MONGO_URI = os.getenv("MONGO_URI")

DB_NAME = "agriflow_live"

COLLECTION_NAME = "daily_prices"

# ----------------------------------------------------
# API
# ----------------------------------------------------

FILTER_URL = "https://api.agmarknet.gov.in/v1/daily-price-arrival/filters"

REPORT_URL = "https://api.agmarknet.gov.in/v1/prices-and-arrivals/market-report/daily"

HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0"
}


# ----------------------------------------------------
# Mongo
# ----------------------------------------------------

client = MongoClient(MONGO_URI)

db = client[DB_NAME]

collection = db[COLLECTION_NAME]

# Unique Index
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


# ----------------------------------------------------
# Get Gujarat Markets
# ----------------------------------------------------

def fetch_market_ids():

    logger.info("Fetching Gujarat Market IDs...")

    r = requests.get(FILTER_URL, headers=HEADERS)

    data = r.json()

    markets = data["data"]["market_data"]

    ids = []

    for market in markets:

        if market["state_id"] == 11:
            ids.append(market["id"])

    logger.info(f"Found {len(ids)} markets")

    return ids


# ----------------------------------------------------
# Fetch Report
# ----------------------------------------------------

def fetch_report(today, market_ids):

    payload = {

        "date": today,

        "State": [11],

        "stateIds": [11],

        "marketIds": market_ids,

        "includeExcel": False,

        "title": "Market-wise Commodity Report"

    }

    for i in range(3):

        try:

            r = requests.post(

                REPORT_URL,

                json=payload,

                headers=HEADERS,

                timeout=60

            )

            if r.status_code == 200:

                logger.info("Report Downloaded")

                return r.json()

        except Exception as e:

            logger.error(e)

            time.sleep(5)

    return None


# ----------------------------------------------------
# Convert JSON to Mongo Documents
# ----------------------------------------------------

def prepare_documents(report, report_date):

    docs = []

    states = report["data"]["states"]

    for state in states:

        state_name = state["stateName"]

        state_id = state["stateId"]

        for market in state["markets"]:

            market_id = market["marketId"]

            market_name = market["marketName"]

            commodities = market.get("commodities", [])

            for commodity in commodities:

                if "commodityId" not in commodity:
                    continue

                commodity_id = commodity["commodityId"]

                commodity_name = commodity["commodityName"]

                total_arrivals = commodity.get("total_arrivals", 0)

                for row in commodity.get("data", []):

                    docs.append({

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

                        "unit_arrival": row.get("unitOfArrivals"),

                        "minimum_price": row.get("minimumPrice"),

                        "maximum_price": row.get("maximumPrice"),

                        "modal_price": row.get("modalPrice"),

                        "unit_price": row.get("unitOfPrice"),

                        "created_at": datetime.utcnow(),

                        "updated_at": datetime.utcnow()

                    })

    return docs


# ----------------------------------------------------
# Save Mongo
# ----------------------------------------------------

def save_documents(documents):

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

                    "$set": {

                        **doc,

                        "updated_at": datetime.utcnow()

                    },

                    "$setOnInsert": {

                        "created_at": datetime.utcnow()

                    }

                },

                upsert=True

            )

        )

    if operations:

        result = collection.bulk_write(operations)

        logger.info("Inserted : %s", result.upserted_count)

        logger.info("Updated  : %s", result.modified_count)


# ----------------------------------------------------
# Main
# ----------------------------------------------------

def main():

    today = date.today().strftime("%Y-%m-%d")

    logger.info(f"Sync Started : {today}")

    market_ids = fetch_market_ids()

    report = fetch_report(today, market_ids)

    if report is None:

        logger.error("Failed to fetch report")

        return

    documents = prepare_documents(report, today)

    logger.info(f"Prepared {len(documents)} documents")

    save_documents(documents)

    logger.info("Mongo Sync Completed")


if __name__ == "__main__":

    main()
