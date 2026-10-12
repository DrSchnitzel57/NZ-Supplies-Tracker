from django.core.paginator import Paginator


def paginate(request, queryset, per_page=50):
    page = Paginator(queryset, per_page).get_page(request.GET.get('page'))
    filters = request.GET.copy()
    filters.pop('page', None)
    return page, {'page_obj': page, 'page_query': filters.urlencode()}
