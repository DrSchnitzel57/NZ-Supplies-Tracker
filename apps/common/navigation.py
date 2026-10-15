"""Only accept same-section list return URLs, never arbitrary redirects."""
from urllib.parse import urlsplit


def list_return(request, section):
    value = request.GET.get('return_to', '')
    try:
        parsed = urlsplit(value)
    except ValueError:
        return section
    if (len(value) <= 2000 and not parsed.scheme and not parsed.netloc
            and parsed.path == section and '\\' not in value and not parsed.fragment):
        return value
    return section
