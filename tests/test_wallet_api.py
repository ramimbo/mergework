import unittest
from app import app

class TestWalletAPI(unittest.TestCase):
    def test_wallet_api(self):
        # Test wallet API
        with app.test_client() as client:
            response = client.get('/wallets')
            self.assertEqual(response.status_code, 200)

            # Test search query
            response = client.get('/wallets?q=example')
            self.assertEqual(response.status_code, 200)
            self.assertIn('example', response.data.decode('utf-8'))

if __name__ == '__main__':
    unittest.main()