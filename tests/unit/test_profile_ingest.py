```python
import unittest
from profile_ingest import ingest_profile

class TestProfileIngest(unittest.TestCase):
    def test_valid_input(self):
        data = {'name': 'John Doe', 'email': 'johndoe@example.com'}
        ingest_profile(data)

    def test_invalid_input(self):
        data = 'invalid input'
        with self.assertRaises(ValueError):
            ingest_profile(data)

if __name__ == '__main__':
    unittest.main()
```