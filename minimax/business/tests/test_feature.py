import unittest
from business.reading_list.service import Book, ReadingList


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.listing = ReadingList([
            Book("a", "Algorithms", "finished", 5),
            Book("b", "Database", "reading", 4),
            Book("c", "Networking", "finished", 2),
            Book("d", "Systems", "unread", 0),
        ])

    def test_status_filter(self):
        self.assertEqual([b.id for b in self.listing.list_books(status="finished")], ["a", "c"])

    def test_rating_filter(self):
        self.assertEqual([b.id for b in self.listing.list_books(min_rating=4)], ["a", "b"])

    def test_combined_filters(self):
        self.assertEqual([b.id for b in self.listing.list_books(status="finished", min_rating=3)], ["a"])

    def test_empty_result(self):
        self.assertEqual(self.listing.list_books(status="unread", min_rating=5), [])

    def test_http_filters_and_bad_params(self):
        from business.reading_list.http_api import make_app
        app = make_app(self.listing)
        cases = [("status=finished&min_rating=4", "200 OK", b'"id": "a"'),
                 ("min_rating=bad", "400 Bad Request", b'"error"'),
                 ("status=finished&status=unread", "400 Bad Request", b'"error"')]
        for query, expected_status, expected_body in cases:
            statuses = []
            body = b"".join(app({"REQUEST_METHOD": "GET", "PATH_INFO": "/books", "QUERY_STRING": query},
                                lambda status, headers: statuses.append(status)))
            self.assertEqual(statuses[0], expected_status)
            self.assertIn(expected_body, body)

    def test_invalid_status_and_rating(self):
        for status in ("archived", ""):
            with self.assertRaises(ValueError):
                self.listing.list_books(status=status)
        for rating in (-1, 6, 2.5, True):
            with self.assertRaises(ValueError):
                self.listing.list_books(min_rating=rating)


if __name__ == "__main__":
    unittest.main()
