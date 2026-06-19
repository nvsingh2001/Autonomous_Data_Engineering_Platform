import csv
import os
import random
from datetime import datetime, timedelta

def generate_olist():
    os.makedirs("data", exist_ok=True)
    random.seed(101)
    
    # 1. Customers
    cust_ids = [f"c_{i:03d}" for i in range(1, 51)]
    cust_unique_ids = [f"u_uniq_{i:03d}" for i in range(1, 51)]
    cities = ["sao paulo", "rio de janeiro", "belo horizonte", "porto alegre", "curitiba", "salvador"]
    states = ["SP", "RJ", "MG", "RS", "PR", "BA"]
    
    customers = []
    for i in range(50):
        zip_prefix = str(random.randint(10000, 99999))
        city_idx = random.randint(0, len(cities)-1)
        customers.append([cust_ids[i], cust_unique_ids[i], zip_prefix, cities[city_idx], states[city_idx]])
        
    with open("data/olist_customers_dataset.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["customer_id", "customer_unique_id", "customer_zip_code_prefix", "customer_city", "customer_state"])
        writer.writerows(customers)
    print("Generated olist_customers_dataset.csv")

    # 2. Products
    prod_ids = [f"p_{i:03d}" for i in range(1, 16)]
    categories = ["perfumaria", "automotivo", "esporte_lazer", "bebes", "utilidades_domesticas", "informatica_acessorios"]
    products = []
    for i in range(15):
        category = categories[i % len(categories)]
        name_len = random.randint(30, 60)
        desc_len = random.randint(100, 1000)
        photos = random.randint(1, 5)
        weight = random.randint(100, 5000)
        products.append([prod_ids[i], category, name_len, desc_len, photos, weight, 20, 20, 20])
        
    with open("data/olist_products_dataset.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["product_id", "product_category_name", "product_name_lenght", "product_description_lenght", "product_photos_qty", "product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm"])
        writer.writerows(products)
    print("Generated olist_products_dataset.csv")

    # 3. Orders
    order_ids = [f"o_{i:03d}" for i in range(1, 101)]
    statuses = ["delivered", "delivered", "delivered", "shipped", "invoiced", "processing"]
    orders = []
    base_date = datetime(2018, 1, 1)
    
    for i in range(100):
        order_id = order_ids[i]
        cust_id = random.choice(cust_ids)
        status = random.choice(statuses)
        p_time = base_date + timedelta(minutes=random.randint(1, 525600))
        approved = p_time + timedelta(minutes=random.randint(5, 60))
        carrier = approved + timedelta(days=random.randint(1, 2)) if status == "delivered" else ""
        delivered = carrier + timedelta(days=random.randint(2, 5)) if status == "delivered" else ""
        estimated = p_time + timedelta(days=10)
        
        orders.append([
            order_id,
            cust_id,
            status,
            p_time.strftime("%Y-%m-%d %H:%M:%S"),
            approved.strftime("%Y-%m-%d %H:%M:%S") if approved else "",
            carrier.strftime("%Y-%m-%d %H:%M:%S") if isinstance(carrier, datetime) else "",
            delivered.strftime("%Y-%m-%d %H:%M:%S") if isinstance(delivered, datetime) else "",
            estimated.strftime("%Y-%m-%d %H:%M:%S")
        ])
        
    with open("data/olist_orders_dataset.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["order_id", "customer_id", "order_status", "order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date", "order_delivered_customer_date", "order_estimated_delivery_date"])
        writer.writerows(orders)
    print("Generated olist_orders_dataset.csv")

    # 4. Order Items
    order_items = []
    item_seq = 1
    for o_id in order_ids:
        num_items = random.choices([1, 2, 3], weights=[0.8, 0.15, 0.05])[0]
        for seq in range(1, num_items + 1):
            p_id = random.choice(prod_ids)
            seller_id = f"s_{random.randint(1, 5):03d}"
            ship_limit = datetime.strptime(orders[int(o_id.split("_")[1])-1][3], "%Y-%m-%d %H:%M:%S") + timedelta(days=5)
            price = round(random.uniform(10.0, 350.0), 2)
            freight = round(random.uniform(5.0, 45.0), 2)
            
            order_items.append([o_id, seq, p_id, seller_id, ship_limit.strftime("%Y-%m-%d %H:%M:%S"), price, freight])
            
    with open("data/olist_order_items_dataset.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["order_id", "order_item_id", "product_id", "seller_id", "shipping_limit_date", "price", "freight_value"])
        writer.writerows(order_items)
    print("Generated olist_order_items_dataset.csv")

    # 5. Payments
    payments = []
    pay_methods = ["credit_card", "boleto", "voucher", "debit_card"]
    for o_id in order_ids:
        # Sum order item prices and freight values to match payment value
        items_val = sum(x[5] + x[6] for x in order_items if x[0] == o_id)
        if items_val == 0:
            continue
        
        num_payments = random.choices([1, 2], weights=[0.9, 0.1])[0]
        if num_payments == 1:
            payments.append([o_id, 1, random.choice(pay_methods), random.randint(1, 12), round(items_val, 2)])
        else:
            v1 = round(items_val * 0.4, 2)
            v2 = round(items_val - v1, 2)
            payments.append([o_id, 1, "voucher", 1, v1])
            payments.append([o_id, 2, random.choice(pay_methods), random.randint(1, 12), v2])
            
    with open("data/olist_order_payments_dataset.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["order_id", "payment_sequential", "payment_type", "payment_installments", "payment_value"])
        writer.writerows(payments)
    print("Generated olist_order_payments_dataset.csv")

    # 6. Reviews
    reviews = []
    for i, o_id in enumerate(order_ids):
        # Create reviews for 80% of orders
        if random.random() > 0.2:
            review_id = f"r_{i+1:03d}"
            score = random.choices([5, 4, 3, 2, 1], weights=[0.6, 0.2, 0.1, 0.05, 0.05])[0]
            title = f"Review Title {i}"
            msg = f"Review Comment Message {i}"
            p_time_str = orders[int(o_id.split("_")[1])-1][3]
            p_time = datetime.strptime(p_time_str, "%Y-%m-%d %H:%M:%S")
            rev_date = p_time + timedelta(days=random.randint(5, 12))
            ans_time = rev_date + timedelta(days=random.randint(1, 3))
            
            reviews.append([
                review_id,
                o_id,
                score,
                title if score < 4 else "",
                msg if score < 4 else "",
                rev_date.strftime("%Y-%m-%d"),
                ans_time.strftime("%Y-%m-%d %H:%M:%S")
            ])
            
    with open("data/olist_order_reviews_dataset.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["review_id", "order_id", "review_score", "review_comment_title", "review_comment_message", "review_creation_date", "review_answer_timestamp"])
        writer.writerows(reviews)
    print("Generated olist_order_reviews_dataset.csv")

if __name__ == "__main__":
    generate_olist()
