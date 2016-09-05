from django.contrib.auth.models import User
from django.core.urlresolvers import reverse
from django.test import Client, TestCase

from .models import Feed


class ReaderViewsTests(TestCase):
    def setUp(self):
        self.c = Client()
        User.objects.create_user('tester', 'tester@example.com', 'tester')
        Feed.objects.create(feed_url='http://example.com/feed/')

    def test_FeedListView(self):
        # test FeedListView, ensure user's subribed are listed
        u = User.objects.get(pk=1)
        f = Feed.objects.get(pk=1)
        res = self.c.get(reverse('feeds:feed-list'))
        self.assertEqual(res.status_code, 302)
        self.assertTrue(
            self.c.login(username='tester', password='tester')
        )
        res = self.c.get(reverse('feeds:feed-list'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(0, len(res.context['object_list']))
        f.subscribe(u)
        res = self.c.get(reverse('feeds:feed-list'))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(1, len(res.context['object_list']))