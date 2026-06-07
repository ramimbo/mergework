import re
import os
from jinja2 import Environment, FileSystemLoader

def scan_template(template_path):
    """Scan a Jinja template for high-risk text artifacts."""
    env = Environment(loader=FileSystemLoader(os.path.dirname(template_path)))
    template = env.get_template(os.path.basename(template_path))
    source = template.render()

    # Check for raw visible Jinja placeholders
    if re.search(r'{{\s*[\w_]+\s*}}', source):
        print(f"Warning: Raw Jinja placeholder found in {template_path}")

    # Check for mojibake/replacement-character sequences
    if re.search(r'[?]', source):
        print(f"Warning: Mojibake/replacement-character sequence found in {template_path}")

    # Check for typographic quote usage around dynamic query/status notices
    if re.search(r'“|”', source):
        print(f"Warning: Typographic quote usage found in {template_path}")

def main():
    """Run the template text smoke check."""
    template_dir = 'app/templates'
    for root, dirs, files in os.walk(template_dir):
        for file in files:
            if file.endswith('.html'):
                template_path = os.path.join(root, file)
                scan_template(template_path)

if __name__ == '__main__':
    main()