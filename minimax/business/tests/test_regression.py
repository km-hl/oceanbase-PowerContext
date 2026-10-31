import unittest
from business.reading_list.service import Book, ReadingList


class RegressionTests(unittest.TestCase):
    def test_insertion_order_without_filter(self):
        listing = ReadingList([Book("z", "Zero"), Book("a", "Alpha")])
        self.assertEqual([b.id for b in listing.list_books()], ["z", "a"])

    def test_duplicate_ids_rejected(self):
        listing = ReadingList([Book("a", "Alpha")])
        with self.assertRaises(ValueError):
            listing.add(Book("a", "Second"))

    def test_add_validation(self):
        listing = ReadingList()
        for book in (Book("x", "X", "invalid"), Book("y", "Y", rating=6)):
            with self.assertRaises(ValueError):
                listing.add(book)



if __name__ == "__main__":
    unittest.main()
