import csv
import os
import random
from datetime import datetime, timedelta

def generate_data():
    os.makedirs("data", exist_ok=True)
    random.seed(42)
    
    customers = []
    countries = ["USA", "Canada", "UK", "Germany", "France", "India", "Australia"]
    phone_formats = [
        lambda p: f"+1-{p[:3]}-{p[3:6]}-{p[6:]}",
        lambda p: f"({p[:3]}) {p[3:6]}-{p[6:]}",
        lambda p: p,
        lambda p: f"{p[:3]}.{p[3:6]}.{p[6:]}"
    ]
    
    for i in range(1, 1001):
        cust_id = f"CUST_{i:04d}"
        name = f"Customer Name {i}"
        email = f"customer_{i}@example.com" if random.random() > 0.08 else ""
        raw_phone = "".join(str(random.randint(0, 9)) for _ in range(10))
        phone = random.choice(phone_formats)(raw_phone)
        base_date = datetime(2025, 1, 1) + timedelta(days=random.randint(0, 365))
        date_format = random.choice([
            lambda d: d.strftime("%Y-%m-%d"),
            lambda d: d.strftime("%m/%d/%Y"),
            lambda d: d.strftime("%d-%b-%Y"),
            lambda d: d.strftime("%Y/%m/%d")
        ])
        sign_up_date = date_format(base_date)
        country = random.choice(countries) if random.random() > 0.02 else ""
        customers.append([cust_id, name, email, phone, sign_up_date, country])
    
    dup_customers = random.sample(customers, 30)
    customers.extend(dup_customers)
    
    with open("data/crm_customers.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["customer_id", "name", "email", "phone", "sign_up_date", "country"])
        writer.writerows(customers)
        
    print(f"Generated data/crm_customers.csv with {len(customers)} rows.")
    
    products = []
    categories = ["Electronics", "Clothing", "Home & Kitchen", "Books", "Sports", "Beauty"]
    for i in range(1, 101):
        prod_id = f"PROD_{i:03d}"
        name = f" Product {i}  " if i % 10 == 0 else f"Product {i}"
        category = random.choice(categories) if i % 15 != 0 else ""
        if i == 13:
            price = 0.0
        elif i == 42:
            price = -15.5
        else:
            price = round(random.uniform(5.0, 500.0), 2)
        stock = random.randint(0, 1000)
        products.append([prod_id, name, category, price, stock])
        
    with open("data/products.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["product_id", "product_name", "category", "price", "stock"])
        writer.writerows(products)
        
    print(f"Generated data/products.csv with {len(products)} rows.")
    
    transactions = []
    cust_ids = [c[0] for c in customers]
    prod_ids = [p[0] for p in products]
    
    for i in range(1, 5001):
        tx_id = f"TX_{i:06d}"
        cust_id = random.choice(cust_ids) if random.random() > 0.02 else "CUST_9999"
        prod_id = random.choice(prod_ids) if random.random() > 0.02 else "PROD_999"
        if i % 200 == 0:
            quantity = -1
        elif i % 350 == 0:
            quantity = 0
        else:
            quantity = random.randint(1, 10)
        unit_price = round(random.uniform(5.0, 500.0), 2)
        base_date = datetime(2025, 1, 1) + timedelta(days=random.randint(0, 365))
        tx_date = base_date.strftime("%Y-%m-%d %H:%M:%S") if random.random() > 0.1 else base_date.strftime("%Y/%m/%d")
        transactions.append([tx_id, cust_id, prod_id, quantity, unit_price, tx_date])
        
    dup_txs = random.sample(transactions, 20)
    transactions.extend(dup_txs)
    
    with open("data/sales_transactions.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["transaction_id", "customer_id", "product_id", "quantity", "unit_price", "transaction_date"])
        writer.writerows(transactions)
        
    print(f"Generated data/sales_transactions.csv with {len(transactions)} rows.")
    
    support_logs = []
    ticket_types = ["Refund Request", "Technical Issue", "Delivery Query", "General Inquiry", "Account Problem"]
    statuses = ["resolved", "RESOLVED", "Closed", "closed", "open", "Open", "pending", "Pending"]
    
    for i in range(1, 1201):
        log_id = f"LOG_{i:05d}"
        cust_id = random.choice(cust_ids) if random.random() > 0.01 else "CUST_8888"
        ticket_type = random.choice(ticket_types)
        rating = random.randint(1, 5) if random.random() > 0.20 else ""
        status = random.choice(statuses)
        base_date = datetime(2025, 1, 1) + timedelta(days=random.randint(0, 365))
        log_date = base_date.strftime("%Y-%m-%d")
        support_logs.append([log_id, cust_id, ticket_type, rating, status, log_date])
        
    with open("data/support_logs.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["log_id", "customer_id", "ticket_type", "rating", "status", "log_date"])
        writer.writerows(support_logs)
        
    print(f"Generated data/support_logs.csv with {len(support_logs)} rows.")

if __name__ == "__main__":
    generate_data()
