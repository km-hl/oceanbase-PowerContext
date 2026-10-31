"""Reading-list service before SPEC-001 filtering was implemented."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Book:
    id: str
    title: str
    status: str = "unread"
    rating: int = 0

class ReadingList:
    def __init__(self, books=None):
        self._books = list(books or [])

    def add(self, book):
        if book.status not in {"unread", "reading", "finished"}:
            raise ValueError("invalid book status")
        if type(book.rating) is not int or not 0 <= book.rating <= 5:
            raise ValueError("rating must be an integer from 0 to 5")
        if any(item.id == book.id for item in self._books):
            raise ValueError("duplicate book id")
        self._books.append(book)

    def list_books(self):
        return list(self._books)
