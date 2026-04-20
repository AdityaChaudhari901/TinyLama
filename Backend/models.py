"""Typed domain models for the product catalog."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class ProductMetadata:
    brand: str = ""
    category: str = ""
    price: str = ""
    rating: str = ""
    description: str = ""
    features: list[str] | str = field(default_factory=list)
    availability: str = "In Stock"

    def to_dict(self) -> dict:
        return {
            "brand":        self.brand,
            "category":     self.category,
            "price":        self.price,
            "rating":       self.rating,
            "description":  self.description,
            "features":     self.features,
            "availability": self.availability,
        }

    @staticmethod
    def from_row(row: dict) -> "ProductMetadata":
        return ProductMetadata(
            brand=row.get("brand", ""),
            category=row.get("category", ""),
            price=row.get("price", ""),
            rating=row.get("rating", ""),
            description=row.get("description", ""),
            features=row.get("features", []),
            availability=row.get("availability", "In Stock"),
        )


@dataclass
class Product:
    id: str
    title: str
    text: str
    embedding: list[float]
    metadata: ProductMetadata
    source: str = "boltic"

    def to_search_result(self, pid_reverse: dict[str, str]) -> dict:
        return {
            "id":         self.id,
            "product_id": pid_reverse.get(self.id, self.id),
            "title":      self.title,
            "details":    self.text,
            "metadata":   self.metadata.to_dict(),
            "source":     self.source,
        }

    def to_recommendation(self, score: float, rank: int, source_category: str) -> dict:
        return {
            "product_id":    self.id,
            "title":         self.title,
            "score":         round(score, 4),
            "rank":          rank,
            "same_category": self.metadata.category.lower().strip() == source_category,
            "metadata":      self.metadata.to_dict(),
        }
