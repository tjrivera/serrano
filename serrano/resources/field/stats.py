from django.core.urlresolvers import reverse
from .base import FieldBase


class FieldStats(FieldBase):
    "Field Stats Resource"

    def get(self, request, pk):
        uri = request.build_absolute_uri
        instance = request.instance

        if instance.simple_type == 'number':
            stats = instance.max().min().avg()
        else:
            stats = instance.count(distinct=True)

        if stats is None:
            resp = {}
        else:
            resp = next(iter(stats))

        resp['_links'] = {
            'self': {
                'href': uri(reverse('serrano:field-stats', args=[instance.pk])),
            },
            'parent': {
                'href': uri(reverse('serrano:field', args=[instance.pk])),
            },
        }

        return resp