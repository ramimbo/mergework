import unittest
from scripts.template_text_smoke import scan_template

class TestTemplateTextSmoke(unittest.TestCase):
    def test_scan_template(self):
        # Create a test template with a raw Jinja placeholder
        with open('test_template.html', 'w') as f:
            f.write('{{ query }}')

        # Scan the template
        scan_template('test_template.html')

        # Remove the test template
        os.remove('test_template.html')

if __name__ == '__main__':
    unittest.main()