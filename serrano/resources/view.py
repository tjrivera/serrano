import functools
import logging
from datetime import datetime
from django.http import HttpResponse
from django.conf.urls import patterns, url
from django.core.urlresolvers import reverse
from django.views.decorators.cache import never_cache
from restlib2.http import codes
from preserialize.serialize import serialize
from avocado.models import DataView
from avocado.conf import settings
from avocado.events import usage
from serrano.forms import ViewForm
from .base import DataResource, RevisionResource
from . import templates

log = logging.getLogger(__name__)

def view_posthook(instance, data, request):
    uri = request.build_absolute_uri
    data['_links'] = {
        'self': {
            'href': uri(reverse('serrano:views:single', args=[instance.pk])),
        }
    }
    return data


class ViewBase(DataResource):
    cache_max_age = 0
    private_cache = True

    model = DataView
    template = templates.View

    def prepare(self, request, instance, template=None):
        if template is None:
            template = self.template
        posthook = functools.partial(view_posthook, request=request)
        return serialize(instance, posthook=posthook, **template)

    def get_queryset(self, request, **kwargs):
        "Constructs a QuerySet for this user or session."

        if hasattr(request, 'user') and request.user.is_authenticated():
            kwargs['user'] = request.user
        elif request.session.session_key:
            kwargs['session_key'] = request.session.session_key
        else:
            # The only case where kwargs is empty is for non-authenticated
            # cookieless agents.. e.g. bots, most non-browser clients since
            # no session exists yet for the agent.
            return self.model.objects.none()

        return self.model.objects.filter(**kwargs)

    def get_default(self, request):
        default = self.model.objects.get_default_template()

        if not default:
            log.warning('No default template for view objects')
            return

        form = ViewForm(request, {'json': default.json, 'session': True})

        if form.is_valid():
            instance = form.save()
            return instance

        log.error('Error creating default view', extra=dict(form.errors))


class ViewsResource(ViewBase):
    "Resource of views"
    def get(self, request):
        queryset = self.get_queryset(request)

        # Only create a default is a session exists
        if request.session.session_key:
            queryset = list(queryset)

            if not len(queryset):
                default = self.get_default(request)
                if default:
                    queryset.append(default)

        return self.prepare(request, queryset)

    def post(self, request):
        form = ViewForm(request, request.data)

        if form.is_valid():
            instance = form.save()
            usage.log('create', instance=instance, request=request)
            response = self.render(request, self.prepare(request, instance),
                status=codes.created)
        else:
            response = self.render(request, dict(form.errors),
                status=codes.unprocessable_entity)
        return response


class ViewResource(ViewBase):
    "Resource for accessing a single view"
    def get_object(self, request, pk=None, session=None, **kwargs):
        if not pk and not session:
            raise ValueError('A pk or session must used for the lookup')

        queryset = self.get_queryset(request, **kwargs)

        try:
            if pk:
                return queryset.get(pk=pk)
            else:
                return queryset.get(session=True)
        except self.model.DoesNotExist:
            pass

    def is_not_found(self, request, response, **kwargs):
        instance = self.get_object(request, **kwargs)
        if instance is None:
            return True
        request.instance = instance

    def get(self, request, **kwargs):
        usage.log('read', instance=request.instance, request=request)
        self.model.objects.filter(pk=request.instance.pk).update(
                accessed = datetime.now())
        return self.prepare(request, request.instance)

    def put(self, request, **kwargs):
        instance = request.instance
        form = ViewForm(request, request.data, instance=instance)

        if form.is_valid():
            instance = form.save()
            usage.log('update', instance=instance, request=request)
            response = self.render(request, self.prepare(request, instance))
        else:
            response = self.render(request, dict(form.errors),
                status=codes.unprocessable_entity)
        return response

    def delete(self, request, **kwargs):
        if request.instance.session:
            return HttpResponse(status=codes.bad_request)
        request.instance.delete()
        usage.log('delete', instance=instance, request=request)
        return HttpResponse(status=codes.no_content)


class ViewsRevisionsResource(RevisionResource):
    """
    Resource for getting all revisions across all views for entity making
    the request.
    """
    object_model = DataView
    object_model_template = templates.View
    object_model_base_uri = 'serrano:views'


class ViewRevisionsResource(RevisionResource):
    """
    Resource for retrieving all revisions for a specific view.
    """
    object_model = DataView
    object_model_template = templates.View
    object_model_base_uri = 'serrano:views'

    def get(self, request, **kwargs):
        query_kwargs = {'object_id': int(kwargs['pk'])}

        params = self.get_params(request)
        queryset = self.get_queryset(request, **query_kwargs)

        return self.prepare(request, queryset, embed=params['embed'])


class ViewRevisionResource(RevisionResource):
    """
    Resource for retrieving a specific revision for a specific view.
    """
    object_model = DataView
    object_model_template = templates.View
    object_model_base_uri = 'serrano:views'

    def get_object(self, request, object_pk=None, revision_pk=None, **kwargs):
        if not object_pk:
            raise ValueError('An object model id must be supplied for the lookup')
        if not revision_pk:
            raise ValueError('A Revision id must be supplied for the lookup')

        queryset = self.get_queryset(request, **kwargs)

        try:
            return queryset.get(pk=revision_pk, object_id=object_pk)
        except self.model.DoesNotExist:
            pass

    def is_not_found(self, request, response, **kwargs):
        instance = self.get_object(request, **kwargs)
        if instance is None:
            return True
        request.instance = instance

    def get(self, request, **kwargs):
        return self.prepare(request, request.instance)


class RevisionViewResource(ViewBase):
    """
    Resource for retrieving a view as it existed at a specific revision.
    """

    def get(self, request, **kwargs):
        pass


single_resource = never_cache(ViewResource())
active_resource = never_cache(ViewsResource())
revisions_resource = never_cache(ViewsRevisionsResource())
revisions_for_object_resource = never_cache(ViewRevisionsResource())
revision_for_object_resource = never_cache(ViewRevisionResource())
object_at_revision_resource = never_cache(RevisionViewResource())

# Resource endpoints
urlpatterns = patterns('',
    url(r'^$', active_resource, name='active'),

    # Endpoints for specific views
    url(r'^(?P<pk>\d+)/$', single_resource, name='single'),
    url(r'^session/$', single_resource, {'session': True}, name='session'),

    # Revision related endpoints
    url(r'^revisions/$', revisions_resource, name='revisions'),
    url(r'^(?P<pk>\d+)/revisions/$', revisions_for_object_resource,
        name='revisions_for_object'),
    url(r'^(?P<object_pk>\d+)/revisions/(?P<revision_pk>\d+)/$',
        revision_for_object_resource, name='revision_for_object'),
    url(r'^(?P<object_pk>\d+)/revisions/(?P<revision_pk>\d+)/object/$',
        object_at_revision_resource, name='object_at_revision'),
)
