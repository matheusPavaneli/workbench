import unittest

from shop.users import display_name


class HiddenBN4(unittest.TestCase):
    def test_padded_parts_are_trimmed_and_joined_by_one_space(self):
        self.assertEqual("Ada Lovelace", display_name({"first_name": " Ada ", "last_name": " Lovelace "}))

    def test_a_first_name_alone_has_no_trailing_space(self):
        self.assertEqual("Ada", display_name({"first_name": "Ada"}))

    def test_a_last_name_alone_has_no_leading_space(self):
        self.assertEqual("Hopper", display_name({"last_name": "Hopper"}))

    def test_blank_parts_fall_back_to_the_email(self):
        self.assertEqual("a@x.test", display_name({"first_name": "  ", "last_name": "", "email": "a@x.test"}))
