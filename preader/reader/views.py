from django.core import serializers
from django.core.urlresolvers import reverse
from django.core.exceptions import PermissionDenied
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotAllowed, HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from braces.views import LoginRequiredMixin
from vanilla import FormView, ListView
from .forms import URLForm, NewSubscriptionForm
from .models import Feed, Entry, UserEntry


@login_required
def entry_actions(request, feed_id, entry_id, action='read'):
    entry = get_object_or_404(Entry.objects.select_related('feed'), pk=entry_id, feed__pk=feed_id)
    if not entry.feed.is_subscribed(request.user):
        raise Http404

    entry_status, created = UserEntry.objects.get_or_create(
        user=request.user, feed=entry.feed, entry=entry, defaults={'status': UserEntry.READ})

    if action == 'read':
        if not created:
            entry_status.status = UserEntry.READ
    elif action == 'clear':
        pass


class JSONSerializedQueryset(LoginRequiredMixin, ListView):
    fields = None

    def get(self, request, *args, **kwargs):
        return HttpResponse(
            serializers.serialize('json', self.get_queryset(), fields=self.fields),
            content_type='application/json'
        )


class FeedListView(JSONSerializedQueryset):
    model = Feed
    fields = (
        'title',
        'description',
        'id'
    )

    def get_queryset(self):
        return Feed.active.filter(subscriptions=self.request.user)


class EntryListView(LoginRequiredMixin, ListView):
    model = Entry
    feed = None
    fields = (
        'feed',
        'id',
        'title',
        'link',
        'content',
        'updated',
        'published'
    )

    def _get_feed(self):
        if self.feed is None:
            self.feed = get_object_or_404(
                Feed.active.all(),
                pk=self.kwargs['feed_id']
            )
        return self.feed

    def get_context_data(self, **kwargs):
        context = super(EntryListView, self).get_context_data(**kwargs)
        context['feed'] = self._get_feed()
        return context

    def get_queryset(self):
        return Entry.objects.select_related('feed').filter(
            feed=self._get_feed()).only(*self.fields)


class URLFormView(LoginRequiredMixin, FormView):
    form_class = URLForm

    def get(self, request, *args, **kwargs):
        return HttpResponseNotAllowed('Not allowed.')

    def form_valid(self, form):
        feeds = Feed.get_feeds_from_url(form.cleaned_data['url'])
        if not feeds:
            raise PermissionError
            messages.error(self.request, 'No feed urls found.')
            return redirect(reverse('feeds:feed-list'))

        self.request.session['feed_id_list'] = [feed.id for feed in feeds]
        return redirect(reverse('feeds:subscribe'))

    def form_invalid(self, form):
        messages.error(self.request, 'Please enter a valid URL.')
        return redirect(reverse('feeds:feed-list'))


class SubscriptionFormView(LoginRequiredMixin, FormView):
    template_name = 'reader/subscribe.html'

    def get_form(self, data=None, files=None, **kwargs):
        feed_id_list = self.request.session.get('feed_id_list', None)
        if not feed_id_list:
            raise PermissionDenied
        if self.request.POST:
            return NewSubscriptionForm(self.request.POST, feed_id_list=feed_id_list)
        else:
            return NewSubscriptionForm(feed_id_list=feed_id_list)

    def form_valid(self, form):
        feeds = Feed.objects.filter(id__in=form.cleaned_data['feeds'])

        if not feeds:
            messages.error(self.request, 'No feed urls found.')

        for feed in feeds:
            already_subscribed = False
            if feed.is_subscribed(self.request.user):
                already_subscribed = True

            if already_subscribed:
                messages.warning(self.request, 'Already subscribed to ' + feed.feed_url)
            else:
                feed.subscribe(self.request.user)
                messages.success(self.request, 'Subscribed to ' + feed.feed_url)

        del self.request.session['feed_id_list']
        return redirect(reverse('feeds:feed-list'))

    def get_context_data(self, **kwargs):
        context = super(SubscriptionFormView, self).get_context_data(**kwargs)
        return context

