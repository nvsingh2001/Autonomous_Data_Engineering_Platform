import os
import shutil
import sys

def backup_existing():
    # 1. Backup Fuzzy Factory files
    ff_files = ["orders.csv", "order_items.csv", "order_item_refunds.csv", "website_pageviews.csv", "website_sessions.csv"]
    has_ff = False
    for f in ff_files:
        if os.path.exists(os.path.join("data", f)):
            has_ff = True
            break
    if os.path.exists("data/products.csv"):
        with open("data/products.csv", "r", encoding="utf-8") as f:
            header = f.readline()
        if "product_name" in header and "created_at" in header and "category" not in header:
            has_ff = True
            
    if has_ff:
        os.makedirs("data/maven_fuzzy_factory", exist_ok=True)
        for f in ff_files + ["products.csv"]:
            src = os.path.join("data", f)
            if os.path.exists(src):
                dest = os.path.join("data/maven_fuzzy_factory", f)
                if os.path.exists(dest):
                    os.remove(dest)
                shutil.move(src, dest)
        print("Backed up Maven Fuzzy Factory dataset to data/maven_fuzzy_factory")

    # 2. Backup Retail Mock files
    mock_files = ["crm_customers.csv", "sales_transactions.csv", "support_logs.csv"]
    has_mock = False
    for f in mock_files:
        if os.path.exists(os.path.join("data", f)):
            has_mock = True
            break
    if os.path.exists("data/products.csv"):
        with open("data/products.csv", "r", encoding="utf-8") as f:
            header = f.readline()
        if "product_id" in header and "product_name" in header and "category" in header and "stock" in header:
            has_mock = True
            
    if has_mock:
        os.makedirs("data/mock_backup", exist_ok=True)
        for f in mock_files + ["products.csv"]:
            src = os.path.join("data", f)
            if os.path.exists(src):
                dest = os.path.join("data/mock_backup", f)
                if os.path.exists(dest):
                    os.remove(dest)
                shutil.move(src, dest)
        print("Backed up Retail Mock dataset to data/mock_backup")

    # 3. Backup Olist files
    olist_files = [
        "olist_customers_dataset.csv", 
        "olist_orders_dataset.csv", 
        "olist_order_items_dataset.csv", 
        "olist_products_dataset.csv", 
        "olist_order_payments_dataset.csv", 
        "olist_order_reviews_dataset.csv"
    ]
    has_olist = False
    for f in olist_files:
        if os.path.exists(os.path.join("data", f)):
            has_olist = True
            break
    if has_olist:
        os.makedirs("data/olist_backup", exist_ok=True)
        for f in olist_files:
            src = os.path.join("data", f)
            if os.path.exists(src):
                dest = os.path.join("data/olist_backup", f)
                if os.path.exists(dest):
                    os.remove(dest)
                shutil.move(src, dest)
        print("Backed up Olist dataset to data/olist_backup")

    # 4. Backup Amazon files
    amazon_files = [
        "Amazon Sale Report.csv",
        "Cloud Warehouse Compersion Chart.csv",
        "Expense IIGF.csv",
        "International sale Report.csv",
        "May-2022.csv",
        "P  L March 2021.csv",
        "Sale Report.csv"
    ]
    has_amazon = False
    for f in amazon_files:
        if os.path.exists(os.path.join("data", f)):
            has_amazon = True
            break
    if has_amazon:
        os.makedirs("data/amazon_backup", exist_ok=True)
        for f in amazon_files:
            src = os.path.join("data", f)
            if os.path.exists(src):
                dest = os.path.join("data/amazon_backup", f)
                if os.path.exists(dest):
                    os.remove(dest)
                shutil.move(src, dest)
        print("Backed up Amazon dataset to data/amazon_backup")

def switch_to_mock():
    backup_existing()
    mock_files = ["crm_customers.csv", "sales_transactions.csv", "support_logs.csv", "products.csv"]
    if os.path.exists("data/mock_backup") and all(os.path.exists(os.path.join("data/mock_backup", f)) for f in mock_files):
        for f in mock_files:
            shutil.move(os.path.join("data/mock_backup", f), os.path.join("data", f))
        print("Restored Retail Mock dataset from backup.")
    else:
        print("Error: Mock dataset backup not found and generator script was removed.")

def switch_to_olist():
    backup_existing()
    olist_files = [
        "olist_customers_dataset.csv", 
        "olist_orders_dataset.csv", 
        "olist_order_items_dataset.csv", 
        "olist_products_dataset.csv", 
        "olist_order_payments_dataset.csv", 
        "olist_order_reviews_dataset.csv"
    ]
    if os.path.exists("data/olist_backup") and all(os.path.exists(os.path.join("data/olist_backup", f)) for f in olist_files):
        for f in olist_files:
            shutil.move(os.path.join("data/olist_backup", f), os.path.join("data", f))
        print("Restored Olist dataset from backup.")
    else:
        print("Error: Olist dataset backup not found and generator script was removed.")

def switch_to_fuzzy_factory():
    backup_existing()
    ff_files = ["orders.csv", "order_items.csv", "order_item_refunds.csv", "website_pageviews.csv", "website_sessions.csv", "products.csv"]
    for f in ff_files:
        src = os.path.join("data/maven_fuzzy_factory", f)
        if os.path.exists(src):
            shutil.move(src, os.path.join("data", f))
    print("Switched back to Maven Fuzzy Factory dataset successfully.")

def switch_to_amazon():
    backup_existing()
    amazon_files = [
        "Amazon Sale Report.csv",
        "Cloud Warehouse Compersion Chart.csv",
        "Expense IIGF.csv",
        "International sale Report.csv",
        "May-2022.csv",
        "P  L March 2021.csv",
        "Sale Report.csv"
    ]
    if os.path.exists("data/amazon_backup") and any(os.path.exists(os.path.join("data/amazon_backup", f)) for f in amazon_files):
        for f in amazon_files:
            src = os.path.join("data/amazon_backup", f)
            if os.path.exists(src):
                shutil.move(src, os.path.join("data", f))
        print("Switched to Amazon dataset successfully.")
    else:
        print("Error: Amazon dataset backup not found in data/amazon_backup.")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = sys.argv[1].lower()
        if target == "fuzzy" or target == "ff":
            switch_to_fuzzy_factory()
        elif target == "olist":
            switch_to_olist()
        elif target == "mock":
            switch_to_mock()
        elif target == "amazon":
            switch_to_amazon()
        else:
            print("Unknown target. Use 'fuzzy', 'olist', 'mock', or 'amazon'.")
    else:
        switch_to_mock()
