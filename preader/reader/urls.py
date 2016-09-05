from django.conf.urls import url
from . import views

urlpatterns = [
    url(r'(?P<feed_id>[0-9]+)/$', views.EntryListView.as_view(), name='entry-list'),
    url(r'add/url/$', views.URLFormView.as_view(), name='add-url'),
    url(r'subscribe/$', views.SubscriptionFormView.as_view(), name='subscribe'),

    url(r'feeds/$', views.FeedListView.as_view(), name='feed-list'),

    #url(r'', views.home, name='feed-home'),

#    url(r'subscribe/$', AddFeedView.as_view(), name='add-feed'),
]
