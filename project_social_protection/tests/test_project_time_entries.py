from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from project_social_protection.models import validate_binary_completion


class BinaryCompletionValidatorTest(SimpleTestCase):
    def test_absent_is_valid(self):
        self.assertIsNone(validate_binary_completion(0))

    def test_present_is_valid(self):
        self.assertIsNone(validate_binary_completion(100))

    def test_intermediate_values_are_invalid(self):
        for value in (-1, 1, 25, 50, 99, 101):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    validate_binary_completion(value)
