from pydantic import BaseModel


class PantryItem(BaseModel):
    # Firestore document IDs are strings, not integers.
    id: str
    name: str
    quantity: float
    unit: str
    category: str
    expiry_date: str | None = None


class AddItemRequest(BaseModel):
    name: str
    quantity: float
    unit: str
    category: str
    expiry_date: str | None = None


class UpdateQuantityRequest(BaseModel):
    quantity: float
