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

def switch_to_mock():
    backup_existing()
    # Restore mock if it was backed up, else generate
    mock_files = ["crm_customers.csv", "sales_transactions.csv", "support_logs.csv", "products.csv"]
    restored = False
    if os.path.exists("data/mock_backup") and all(os.path.exists(os.path.join("data/mock_backup", f)) for f in mock_files):
        for f in mock_files:
            shutil.move(os.path.join("data/mock_backup", f), os.path.join("data", f))
        restored = True
        print("Restored Retail Mock dataset from backup.")
    else:
        sys.path.append("data")
        from generate_mock_data import generate_data
        generate_data()
        print("Generated fresh Retail Mock dataset.")

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
        sys.path.append("data")
        from generate_olist_data import generate_olist
        generate_olist()
        print("Generated fresh Olist dataset.")

def switch_to_fuzzy_factory():
    backup_existing()
    ff_files = ["orders.csv", "order_items.csv", "order_item_refunds.csv", "website_pageviews.csv", "website_sessions.csv", "products.csv"]
    for f in ff_files:
        src = os.path.join("data/maven_fuzzy_factory", f)
        if os.path.exists(src):
            shutil.move(src, os.path.join("data", f))
    print("Switched back to Maven Fuzzy Factory dataset successfully.")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = sys.argv[1].lower()
        if target == "fuzzy" or target == "ff":
            switch_to_fuzzy_factory()
        elif target == "olist":
            switch_to_olist()
        elif target == "mock":
            switch_to_mock()
        else:
            print("Unknown target. Use 'fuzzy', 'olist', or 'mock'.")
    else:
        switch_to_mock()
