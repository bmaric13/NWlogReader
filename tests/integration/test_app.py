```python
import unittest
from app import main

class TestApp(unittest.TestCase):
    def test_app_start(self):
        main()

if __name__ == '__main__':
    unittest.main()
```