"""Reading-list business feature used for SDD change verification."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Book:
    id: str
    title: str
    status: str = "unread"
    rating: int = 0


class ReadingList:
    def __init__(self, books: list[Book] | None = None):
        self._books = list(books or [])

    def add(self, book: Book) -> None:
        if book.status not in {"unread", "reading", "finished"}:
            raise ValueError("invalid book status")
        if type(book.rating) is not int or not 0 <= book.rating <= 5:
            raise ValueError("rating must be an integer from 0 to 5")
        if any(item.id == book.id for item in self._books):
            raise ValueError("duplicate book id")
        self._books.append(book)

    def list_books(self, *, status: str | None = None, min_rating: int | None = None) -> list[Book]:
        if status is not None and status not in {"unread", "reading", "finished"}:
            raise ValueError("invalid status filter")
        if min_rating is not None and (type(min_rating) is not int or not 0 <= min_rating <= 5):
            raise ValueError("min_rating must be an integer from 0 to 5")
        return [book for book in self._books
                if (status is None or book.status == status)
                and (min_rating is None or book.rating >= min_rating)]
