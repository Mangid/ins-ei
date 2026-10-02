from app import app, DB_PATH
from inventory import router as inventory_router, configure_inventory, init_inventory_db

# Inventory is deliberately isolated from the customer portal. It only references
# customers optionally when an issue is assigned to a customer.
init_inventory_db(DB_PATH)
configure_inventory(DB_PATH)
app.include_router(inventory_router)
