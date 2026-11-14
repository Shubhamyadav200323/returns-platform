"""Invented vocabularies for synthetic master data. No external or personal data is used:
names are random first/last-name combinations and emails use the reserved example.com domain."""

FIRST_NAMES = (
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Ayaan", "Krishna", "Ishaan",
    "Ananya", "Diya", "Saanvi", "Aadhya", "Pari", "Anika", "Navya", "Myra", "Sara", "Ira",
    "Rohan", "Kabir", "Dev", "Neel", "Om", "Meera", "Tara", "Kavya", "Riya", "Zoya",
    "Farhan", "Imran", "Joseph", "Maria", "Priya", "Rahul", "Sneha", "Vikram", "Pooja", "Harpreet",
)
LAST_NAMES = (
    "Sharma", "Verma", "Gupta", "Iyer", "Nair", "Reddy", "Patel", "Shah", "Mehta", "Joshi",
    "Khan", "Singh", "Das", "Bose", "Menon", "Pillai", "Rao", "Kulkarni", "Desai", "Chopra",
    "Fernandes", "D'Souza", "Banerjee", "Mukherjee", "Agarwal", "Malhotra", "Kapoor", "Yadav", "Mishra", "Thomas",
)
CITIES = (
    ("Bengaluru", 14), ("Mumbai", 14), ("Delhi", 13), ("Hyderabad", 9), ("Chennai", 8), ("Pune", 8),
    ("Kolkata", 7), ("Ahmedabad", 5), ("Jaipur", 4), ("Lucknow", 4), ("Kochi", 3), ("Indore", 3),
    ("Chandigarh", 3), ("Bhubaneswar", 2), ("Guwahati", 2), ("Coimbatore", 1),
)
SEGMENTS = (("NEW", 30), ("REGULAR", 45), ("LOYAL", 20), ("VIP", 5))

BRANDS = ("Aurora", "Nimbus", "Kestrel", "Vega", "Lumen", "Orchid", "Terra", "Zephyr", "Saffron", "Indigo",
          "Monsoon", "Peak", "Cobalt", "Juniper", "Sable")

# category -> (catalogue weight, sales weight, return propensity, price range in rupees, nouns)
CATEGORY_PROFILE = {
    "ELECTRONICS":  (12, 14, 1.3, (799, 74_999), ("Wireless Earbuds", "Smartwatch", "Bluetooth Speaker", "Power Bank", "Tablet", "Action Camera", "Keyboard")),
    "APPLIANCES":   (6, 5, 1.1, (1_499, 49_999), ("Air Fryer", "Mixer Grinder", "Vacuum Cleaner", "Water Purifier", "Induction Cooktop")),
    "APPAREL":      (22, 24, 2.4, (299, 5_999), ("Cotton Kurta", "Denim Jacket", "Linen Shirt", "Running Tee", "Saree", "Chinos", "Hoodie")),
    "FOOTWEAR":     (9, 10, 2.1, (499, 8_999), ("Running Shoes", "Sneakers", "Sandals", "Loafers", "Formal Shoes")),
    "HOME_KITCHEN": (14, 13, 0.9, (199, 9_999), ("Cookware Set", "Bedsheet", "Dinner Set", "Table Lamp", "Storage Jars", "Curtains")),
    "BOOKS":        (10, 9, 0.4, (149, 1_999), ("Novel", "Cookbook", "Biography", "Exam Guide", "Comic Anthology")),
    "TOYS":         (8, 7, 0.9, (249, 6_999), ("Building Blocks", "Board Game", "RC Car", "Puzzle", "Plush Toy")),
    "BEAUTY":       (9, 9, 0.35, (149, 3_999), ("Face Serum", "Lipstick", "Sunscreen", "Hair Oil", "Perfume")),
    "GROCERY":      (7, 7, 0.2, (49, 1_499), ("Basmati Rice", "Cold-Pressed Oil", "Assorted Dry Fruits", "Green Tea", "Masala Box")),
    "GIFT_CARDS":   (3, 2, 0.15, (500, 10_000), ("Gift Card",)),
}

REASON_WEIGHTS = {
    "default":     {"DEFECTIVE": 14, "WRONG_ITEM": 7, "NOT_AS_DESCRIBED": 14, "ARRIVED_DAMAGED": 9,
                    "SIZE_FIT": 0, "CHANGED_MIND": 38, "BETTER_PRICE": 18},
    "ELECTRONICS": {"DEFECTIVE": 32, "WRONG_ITEM": 7, "NOT_AS_DESCRIBED": 14, "ARRIVED_DAMAGED": 12,
                    "SIZE_FIT": 0, "CHANGED_MIND": 22, "BETTER_PRICE": 13},
    "APPLIANCES":  {"DEFECTIVE": 30, "WRONG_ITEM": 6, "NOT_AS_DESCRIBED": 14, "ARRIVED_DAMAGED": 16,
                    "SIZE_FIT": 0, "CHANGED_MIND": 22, "BETTER_PRICE": 12},
    "APPAREL":     {"DEFECTIVE": 6, "WRONG_ITEM": 5, "NOT_AS_DESCRIBED": 13, "ARRIVED_DAMAGED": 4,
                    "SIZE_FIT": 46, "CHANGED_MIND": 21, "BETTER_PRICE": 5},
    "FOOTWEAR":    {"DEFECTIVE": 8, "WRONG_ITEM": 5, "NOT_AS_DESCRIBED": 10, "ARRIVED_DAMAGED": 4,
                    "SIZE_FIT": 50, "CHANGED_MIND": 18, "BETTER_PRICE": 5},
}

OVERRIDE_REASONS = ("Courier delay verified", "Customer was travelling - goodwill", "VIP goodwill",
                    "Delivery date disputed", "Defect surfaced after window")

INSPECTION_NOTES = {
    "A_NEW": ("Sealed, tags intact", "Unopened"),
    "B_LIKE_NEW": ("Opened, no wear", "Packaging slightly torn"),
    "C_USED": ("Visible use", "Minor scuffs", "Washed / worn"),
    "D_DAMAGED": ("Cracked casing", "Torn fabric", "Crushed box and item"),
    "E_DEFECTIVE": ("Fails power-on test", "Zip broken", "Stitching defect confirmed"),
}
