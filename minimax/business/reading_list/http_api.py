"""Small WSGI HTTP interface for the reading list."""
from __future__ import annotations
import json
from urllib.parse import parse_qs
from business.reading_list.service import ReadingList


def make_app(reading_list: ReadingList):
    def app(environ, start_response):
        if environ.get("REQUEST_METHOD") != "GET" or environ.get("PATH_INFO") != "/books":
            status, payload = "404 Not Found", {"error": "not found"}
        else:
            params = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
            try:
                if any(len(value) != 1 for value in params.values()):
                    raise ValueError("duplicate query argument")
                if any(key not in {"status", "min_rating"} for key in params):
                    raise ValueError("unsupported query argument")
                raw_rating = params.get("min_rating", [None])[0]
                if raw_rating is not None and (not raw_rating.isdecimal() or raw_rating.startswith("0") and raw_rating != "0"):
                    raise ValueError("invalid min_rating query")
                rating = int(raw_rating) if raw_rating is not None else None
                books = reading_list.list_books(status=params.get("status", [None])[0], min_rating=rating)
                status, payload = "200 OK", [{"id": b.id, "title": b.title, "status": b.status, "rating": b.rating} for b in books]
            except ValueError as error:
                status, payload = "400 Bad Request", {"error": str(error)}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        start_response(status, [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(data)))])
        return [data]
    return app
