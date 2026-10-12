"""Isolated native PDF rendering probe (test dependency, never in the image).

Keep the renderer in a separate process: Python 3.13's unittest/Django teardown
can crash inside MuPDF's native finalizer when mixed with the app runtime.
Subprocess failures still fail the tests; no errors or exit codes are suppressed.
"""
import json
import sys

import pymupdf


def probe(path, image_path=None):
    pages = []
    with pymupdf.open(path) as document:
        for page in document:
            image = page.get_pixmap()
            if image_path and not pages:
                image.save(image_path)
            pages.append({'width': page.rect.width, 'height': page.rect.height,
                          'text': page.get_text(), 'words': page.get_text('words'),
                          'fonts': page.get_fonts(),
                          'noto_embedded': any('NotoSans' in font[3] and len(document.extract_font(font[0])[3]) > 0 for font in page.get_fonts()),
                          'pixels': image.width * image.height})
    return pages


if __name__ == '__main__':
    print(json.dumps(probe(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)))
