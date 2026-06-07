import unittest
from app import app

class TestActivity(unittest.TestCase):
    def test_activity(self):
        # Test activity page
        with app.test_client() as client:
            response = client.get('/activity')
            self.assertEqual(response.status_code, 200)

            # Test search query
            response = client.get('/activity?q=example')
            self.assertEqual(response.status_code, 200)
            self.assertIn('example', response.data.decode('utf-8'))

if __name__ == '__main__':
    unittest.main()