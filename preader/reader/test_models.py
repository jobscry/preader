from django.contrib.auth.models import User
from django.test import TestCase
from django.utils.http import http_date
from django.utils.timezone import now, make_naive

from datetime import datetime, timedelta
import requests_mock

from reader.models import (
    Feed,
    FeedLog,
    MAX_ERRORS,
    Entry,
    feed_datetime,
    MAX_FEEDS,
    SimpleBufferObject,
    MAX_BULK_CREATE,
    shorten_string,
    feed_datetime,
    parse_http_date
)


class SimpleBufferObjectTest(TestCase):

    def test_simpleBufferObject_init(self):
        buffer = SimpleBufferObject(Entry, 1)

        self.assertEqual(buffer.max, 1)

        buffer = SimpleBufferObject(Entry)
        self.assertEqual(buffer.max, MAX_BULK_CREATE)

    def test_simpleBufferObject_add(self):
        buffer = SimpleBufferObject(Feed, 2)
        f = Feed(
            title='feed1',
            feed_url='http://example.com/feed1'
        )
        buffer.add(f)
        self.assertEquals(buffer.count, 1)

    def test_simpleBufferObject_purge(self):
        buffer = SimpleBufferObject(Feed, 2)
        f = Feed(
            title='feed1',
            feed_url='http://example.com/feed1'
        )
        buffer.add(f)
        before_count = Feed.objects.count()
        buffer.purge()
        self.assertEqual(Feed.objects.count(), before_count + 1)
        self.assertEqual(buffer.count, 0)
        self.assertEqual(buffer.buffer, list())

    def test_simpleBufferObject_auto_purge(self):
        buffer = SimpleBufferObject(Feed, 1)
        f = Feed(
            title='feed1',
            feed_url='http://example.com/feed1'
        )
        buffer.add(f)
        self.assertEqual(buffer.count, 0)

    def test_simpleBufferObject_context(self):
        before_count = Feed.objects.count()
        with SimpleBufferObject(Feed, 1) as buffer:
            f = Feed(
                title='feed1',
                feed_url='http://example.com/feed1'
            )
            buffer.add(f)
        self.assertEqual(Feed.objects.count(), before_count + 1)


class ModelUtilsTest(TestCase):
    def test_shortenString(self):
        test_string = '1234567890'
        self.assertEqual(
            shorten_string(
                test_string,
                20
            ),
            test_string
        )
        self.assertEqual(
            shorten_string(
                test_string,
                10
            ),
            '1234567...'
        )

    def test_feed_datetime(self):
        self.assertIsNone(feed_datetime(timetuple=None, allow_none=True, default=None))
        self.assertIsNotNone(feed_datetime(timetuple=None, allow_none=False, default=None))

        test_date = now()
        self.assertEqual(feed_datetime(timetuple=None, allow_none=False, default=test_date), test_date)

        self.assertAlmostEqual(feed_datetime(timetuple=test_date.timetuple()), test_date, delta=timedelta(seconds=1))

    def parse_http_date(self):
        dt = datetime(year=1994, month=11, day=6, hour=8, minute=49, second=37)
        self.assertEqual(dt, parse_http_date('Sun, 06 Nov 1994 08:49:37 GMT'))

        self.assertEqual(dt, parse_http_date(None, dt))


class ReaderModelsTest(TestCase):

    def setUp(self):
        Feed.objects.get_or_create(
            title='test feed 01',
            feed_url='http://example.com/feedtest/',
        )
        User.objects.create(username='tester', email='tester@example.com')

    def _test_subscribe_setup(self):
        return Feed.objects.get(pk=1), User.objects.get(pk=1)

    def test_feed_datetime_none(self):
        # test feed_datetime, ensure None values are handled right
        time_hack_tuple = None
        self.assertEqual(None, feed_datetime(time_hack_tuple, True))
        self.assertTrue(isinstance(
            feed_datetime(time_hack_tuple),
            datetime
        ))

    def test_feed_datetime_not_none(self):
        # test feed_datetime, ensure timetuple returns datetime
        time_hack_tuple = now().timetuple()  # timezone aware
        self.assertTrue(isinstance(
            feed_datetime(time_hack_tuple),
            datetime
        ))

    def test_subscribe_subcribed(self):
        # subscribed
        f, u = self._test_subscribe_setup()
        f.subscribe(u)
        self.assertTrue(f.subscriptions.filter(pk=1).exists())

    def test_unsubscribe(self):
        # subscribe then unsubcribe
        f, u = self._test_subscribe_setup()
        f.subscribe(u)
        f.unsubscribe(u)
        self.assertFalse(f.subscriptions.filter(pk=1).exists())

    def test_is_usubscribed_not_subcribed(self):
        # not subscribed
        f, u = self._test_subscribe_setup()
        self.assertFalse(f.is_subscribed(u))

    def test_increment_error_count_increment(self):
        # get current error count, increment
        f = Feed.objects.get(pk=1)
        error_count = f.error_count
        f.increment_error_count()
        f.save()
        f = Feed.objects.get(pk=1)
        self.assertEqual(error_count + 1, f.error_count)

    def test_increment_error_count_max_errors(self):
        # set error count to MAX_ERRORS - 1, increment should also disable
        f = Feed.objects.get(pk=1)
        f.error_count = MAX_ERRORS - 1
        f.save()
        f.increment_error_count()
        f.save()
        f = Feed.objects.get(pk=1)
        self.assertTrue(f.disabled)

    def test_reset_error_count(self):
        # increment error count
        f = Feed.objects.get(pk=1)
        f.increment_error_count()
        f.save()
        f = Feed.objects.get(pk=1)
        f.reset_error_count()
        self.assertEqual(f.error_count, 0)

    def test_reset_error_count_max_errors(self):
        # increment error count with disabling, reset should enable
        f = Feed.objects.get(pk=1)
        f.error_count = MAX_ERRORS
        f.increment_error_count()
        f.save()
        f = Feed.objects.get(pk=1)
        f.reset_error_count()
        f.save()
        f = Feed.objects.get(pk=1)
        self.assertFalse(f.disabled)

    def test_get_feeds_from_url_existing(self):
        # test get_feeds_from_url from existing feed, should return feed w/out
        # requesting
        feed = Feed.objects.create(
            site_url='http://example.com',
            feed_url='http://example.com/feed/'
        )
        self.assertListEqual(
            Feed.get_feeds_from_url('http://example.com/feed/'),
            [feed, ]
        )

    def test_get_feeds_from_url_from_HTML_page(self):
        # test get_feeds_from_url from HTML page, should return rel link for feed
        url = 'http://example.com/feed/'
        self.assertEqual(Feed.objects.filter(feed_url=url).count(), 0)
        with requests_mock.Mocker() as mock:
            mock.get(
                'http://example.com/',
                text="""
<!DOCTYPE html>
<html>
    <head>
        <link rel="alternate" type="application/atom+xml" href="%s">
    </head>
    <body>
    </body>
</html>
                """ % url,
                status_code=200,
                headers={
                    'content-type': 'text/html'
                }
            )
            self.assertListEqual(
                Feed.get_feeds_from_url('http://example.com/'),
                [Feed.objects.get(feed_url=url), ],
            )

    def test_get_feeds_from_url_from_HTML_page_hit_max(self):
        # test get_feeds_from_url from HTML page, number of feeds in page should hit default max
        start = Feed.objects.count()
        feed_urls = ''
        for x in range(MAX_FEEDS + 1):
            feed_urls += '<link rel="alternate" type="application/atom+xml" href="http://example.com/feed%s">' % x
        with requests_mock.Mocker() as mock:
            mock.get(
                'http://example.com/',
                text="""
<!DOCTYPE html>
<html>
    <head>
        %s
    </head>
    <body>
    </body>
</html>
                """ % feed_urls,
                status_code=200,
                headers={
                    'content-type': 'text/html'
                }
            )
            Feed.get_feeds_from_url('http://example.com/')
            self.assertEqual(Feed.objects.count() - start, MAX_FEEDS)

    def test_get_feeds_from_url_from_feed(self):
        # test get_feeds_from_url from feed, should return input URL
        url = 'http://example.com/feed/'
        self.assertEqual(Feed.objects.filter(feed_url=url).count(), 0)
        with requests_mock.Mocker() as mock:
            mock.get(
                url,
                text="""
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
    <title>Example Feed</title>
    <link href="http://example.org/feed/" rel="self" />
    <id>urn:uuid:60a76c80-d399-11d9-b91C-0003939e0af6</id>
    <updated>2003-12-13T18:30:02Z</updated>

    <entry>
        <title>Atom-Powered Robots Run Amok</title>
        <link href="http://example.org/2003/12/13/atom03" />
        <link rel="alternate" type="text/html"
            href="http://example.org/2003/12/13/atom03.html"/>
        <link rel="edit" href="http://example.org/2003/12/13/atom03/edit"/>
        <id>urn:uuid:1225c695-cfb8-4ebb-aaaa-80da344efa6a</id>
        <updated>2003-12-13T18:30:02Z</updated>
        <summary>Some text.</summary>
        <content type="xhtml">
            <div xmlns="http://www.w3.org/1999/xhtml">
                <p>This is the entry content.</p>
            </div>
        </content>
        <author>
            <name>John Doe</name>
            <email>johndoe@example.com</email>
        </author>
    </entry>
</feed>
                """,
                status_code=200,
                headers={
                    'content-type': 'application/atom+xml;weird'
                }
            )
            self.assertListEqual(
                Feed.get_feeds_from_url('http://example.com/feed/'),
                [Feed.objects.get(feed_url=url), ]
            )

    def test_get_feeds_from_url_HTTP_error(self):
        # testing get_feeds_from_url with HTTP error, should return None
        with requests_mock.Mocker() as mock:
            mock.get(
                'http://example.com/feed/',
                text='',
                status_code=400
            )
            self.assertEqual(
                [],
                Feed.get_feeds_from_url('http://example.com/feed/')
            )

    def test_update_feeds_last_checked(self):
        # test update_feeds, ensure last_checked and next_checked are updated
        time_hack_high = now() + timedelta(seconds=1)
        time_hack_low = time_hack_high + timedelta(seconds=-2)
        f, u = self._test_subscribe_setup()
        f.subscribe(u)

        with requests_mock.Mocker() as mock:
            mock.get(
                'http://example.com/feedtest/',
                text="",
                status_code=304,
                headers={
                    'content_type': 'application/atom+xml'
                }
            )
            Feed.update_feeds()
            f = Feed.objects.get(pk=1)
            self.assertLess(time_hack_low, f.last_checked)
            self.assertGreater(time_hack_high, f.last_checked)

            self.assertLess(
                time_hack_low + timedelta(hours=f.check_frequency),
                f.next_checked
            )
            self.assertGreater(
                time_hack_high + timedelta(hours=f.check_frequency),
                f.next_checked
            )

    def test_update_feeds_conditional_get(self):
        # test update_feeds, ensure conditional GET is used when available
        time_hack = now()
        f, u = self._test_subscribe_setup()
        f.subscribe(u)
        f.etag = 'test'
        f.last_modified = time_hack
        f.save()
        with requests_mock.Mocker() as mock:
            mock.get(
                f.feed_url,
                text='',
                status_code=304,
                headers={
                    'content_type': 'application/atom+xml'
                }
            )
            Feed.update_feeds()
            history = mock.request_history[0]
            self.assertEqual(
                'test', history.headers.get('If-None-Match', None)
            )
            self.assertEqual(
                http_date(time_hack.timestamp()),
                history.headers.get('If-Modified-Since', None)
            )

    def test_update_feeds_changed_url(self):
        # test update_feeds, ensure redirect to URL of existing feed causes updating feed to be disabled
        test_url = 'http://example.com/feed22/'
        f, u = self._test_subscribe_setup()
        f.subscribe(u)
        with requests_mock.Mocker() as mock:
            mock.get(
                f.feed_url,
                text='',
                status_code=301,
                headers={
                    'location': test_url,
                    'content-type': 'text/plain'
                }
            )
            mock.get(
                test_url,
                text='',
                status_code=200,
                headers={
                    'content_type': 'application/atom+xml'
                }
            )
            Feed.update_feeds()
            f = Feed.objects.get(id=f.id)
            self.assertEqual(test_url, f.feed_url)

    def test_update_feeds_changed_url_existing(self):
        # test update_feeds, ensure redirect to URL of existing feed causes updating feed to be disabled
        test_url = 'http://example.com/feed22/'
        test_feed = Feed.objects.create(feed_url=test_url)
        f, u = self._test_subscribe_setup()
        f.subscribe(u)
        test_feed.subscribe(u)
        test_feed.save()
        f.save()  # force update for inclusion in feed_update
        with requests_mock.Mocker() as mock:
            mock.get(
                test_url,
                text='',
                status_code=200,
                headers={
                    'content_type': 'application/atom+xml'
                }
            )
            mock.get(
                f.feed_url,
                text='',
                status_code=301,
                headers={
                    'location': test_url,
                    'content-type': 'text/plain'
                }
            )
            Feed.update_feeds()
            f = Feed.objects.get(id=f.id)
            self.assertTrue(f.disabled)

    def test_update_feeds_feed_meta(self):
        # test update_feeds, ensure feed meta data is updated this includes reseting error count and updating feed
        # title, description
        f, u = self._test_subscribe_setup()
        f.subscribe(u)
        f.increment_error_count()
        f.save()
        with requests_mock.Mocker() as mock:
            mock.get(
                f.feed_url,
                text="""
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">

   <title>Example Feed</title>
   <link href="http://example.org/"/>
   <logo>http://example.com/icon.jpg</logo>
   <subtitle>this is a feed</subtitle>
   <updated>2003-12-13T18:30:02Z</updated>
   <id>urn:uuid:60a76c80-d399-11d9-b93C-0003939e0af6</id>

   <entry>
     <title>Atom-Powered Robots Run Amok</title>
     <link href="http://example.org/2003/12/13/atom03"/>
     <id>urn:uuid:1225c695-cfb8-4ebb-aaaa-80da344efa6a</id>
     <updated>2003-12-13T18:30:02Z</updated>
     <summary>Some text.</summary>
   </entry>

</feed>
                    """,
                status_code=200,
                headers={
                    'content_type': 'application/atom+xml'
                }
            )
            Feed.update_feeds()
            f = Feed.objects.get(pk=1)
            self.assertEqual(0, f.error_count)
            self.assertEqual('Example Feed', f.title)
            self.assertEqual('this is a feed', f.description)

    def test_updates_feeds_no_summary_multiple_content(self):
        # test update_feeds where entry has no "summary" field and uses "content" instead
        # test update_feeds, ensure feed's entries are added correctly
        f, u = self._test_subscribe_setup()
        f.subscribe(u)
        time_hack_formatted = http_date(now().timestamp())
        with requests_mock.Mocker() as mock:
            mock.get(
                f.feed_url,
                text="""
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">

  <title>Example Feed</title>
  <link href="http://example.org/"/>
  <updated>{0}</updated>
  <id>urn:uuid:60a76c80-d399-11d9-b93C-0003939e0af6</id>

  <entry>
    <title>Atom-Powered Robots Run Amok</title>
    <link href="http://example.org/2003/12/13/atom03"/>
    <id>urn:uuid:1225c695-cfb8-4ebb-aaaa-80da344efa6a</id>
    <updated>{0}</updated>
    <content>Some text 1.</content>
    <content type="text/html">Some text 2.</content>
    <author>
       <name>John Doe</name>
       <email>john@example.com</email
    </author>
  </entry>

</feed>
                    """.format(time_hack_formatted),
                status_code=200,
                headers={
                    'content_type': 'application/atom+xml'
                }
            )
            Feed.update_feeds()
            e = f.entry_set.first()
            self.assertEqual('<p>Some text 1.</p><p>Some text 2.</p>', e.content)

    def test_updates_feeds_no_summary_no_content(self):
        # test update_feeds where entry has no "summary" field and uses "content" instead
        # test update_feeds, ensure feed's entries are added correctly
        f, u = self._test_subscribe_setup()
        f.subscribe(u)
        time_hack_formatted = http_date(now().timestamp())
        with requests_mock.Mocker() as mock:
            mock.get(
                f.feed_url,
                text="""
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">

  <title>Example Feed</title>
  <link href="http://example.org/"/>
  <updated>{0}</updated>
  <id>urn:uuid:60a76c80-d399-11d9-b93C-0003939e0af6</id>

  <entry>
    <title>Atom-Powered Robots Run Amok</title>
    <link href="http://example.org/2003/12/13/atom03"/>
    <id>urn:uuid:1225c695-cfb8-4ebb-aaaa-80da344efa6a</id>
    <updated>{0}</updated>
    <author>
       <name>John Doe</name>
       <email>john@example.com</email
    </author>
  </entry>

</feed>
                    """.format(time_hack_formatted),
                status_code=200,
                headers={
                    'content_type': 'application/atom+xml'
                }
            )
            Feed.update_feeds()
            e = f.entry_set.first()
            self.assertEqual('No summary.', e.content)

    def test_update_feeds_no_new_entries(self):
        f, u = self._test_subscribe_setup()
        f.subscribe(u)
        time_hack = now()
        time_hack_formatted = http_date(time_hack.timestamp())

        Entry.objects.create(
            feed=f,
            entry_id='urn:uuid:1225c695-cfb8-4ebb-aaaa-80da344efa6a',
            link='http://example.org/2003/12/13/atom03',
            title='Atom-Powered Robots Run Amok',
            content='Some text.',
            updated=time_hack,
            published=time_hack
        )

        prev_date = now() - timedelta(days=1)
        time_hack_formatted2 = http_date(prev_date.timestamp())
        with requests_mock.Mocker() as mock:
            mock.get(
                f.feed_url,
                text="""
<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">

  <title>Example Feed</title>
  <link href="http://example.org/"/>
  <updated>{0}</updated>
  <id>urn:uuid:60a76c80-d399-11d9-b93C-0003939e0af6</id>

  <entry>
    <title>Atom-Powered Robots Run Amok</title>
    <link href="http://example.org/2003/12/13/atom03"/>
    <id>urn:uuid:1225c695-cfb8-4ebb-aaaa-80da344efa6a</id>
    <updated>{1}</updated>
    <summary>Some text.</summary>
    <author>
       <name>John Doe</name>
       <email>john@example.com</email
    </author>
  </entry>

</feed>
                    """.format(time_hack_formatted, time_hack_formatted2),
                status_code=200,
                headers={
                    'content_type': 'application/atom+xml'
                }
            )
            Feed.update_feeds()
            self.assertEqual(1, f.entry_set.count())

    def test_update_feeds_HTTP_error(self):
        # test update_feeds, ensure HTTP error is logged, increments
        # feed error count
        f, u = self._test_subscribe_setup()
        f.subscribe(u)
        with requests_mock.Mocker() as mock:
            mock.get(
                f.feed_url,
                text="",
                status_code=500,
                headers={
                    'content_type': 'application/atom+xml'
                }
            )
            Feed.update_feeds()
            f = Feed.objects.get(pk=1)
            self.assertEqual(1, f.error_count)