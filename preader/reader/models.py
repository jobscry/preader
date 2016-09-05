from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q
from django.utils.http import http_date
from django.utils.timezone import (
    now,
    make_aware,
    make_naive,
    get_current_timezone
)
from django_bleach.models import BleachField
from bs4 import BeautifulSoup
from model_utils import Choices
from model_utils.managers import QueryManager
from model_utils.models import TimeStampedModel
from datetime import datetime, timedelta
from time import mktime
from urllib.parse import urljoin
import hashlib
import bleach
import requests
from speedparser import speedparser
import email.utils as eut
import re


HEADERS = {
    'User-Agent': getattr(settings, 'USER_AGENT', 'PReader 0.1')
}
FEED_TYPES = (
    'application/atom+xml',
    'application/rss+xml',
    'text/xml'
)

BLEACH_TAGS = ['a', 'p', 'img', 'strong', 'em']

BLEACH_ATTRS = {
    '*': ['class'],
    'a': ['href', 'rel'],
    'img': ['src', 'alt'],
}

CURRENT_TZ = get_current_timezone()
MAX_ERRORS = getattr(settings, 'MAX_ERRORS', 5)
REQ_MAX_REDIRECTS = getattr(settings, 'MAX_REDIRECTS', 3)
REQ_TIMEOUT = getattr(settings, 'TIMEOUT', 5.0)

MAX_FEEDS = getattr(settings, 'MAX_FEEDS', 5)
MAX_BULK_CREATE = getattr(settings, 'MAX_BULK_CREATE', 100)

# stolen from http://code.activestate.com/recipes/363841-detect-character-encoding-in-an-xml-file/
xmlDec = r"""
    ^<\?xml             # w/o BOM, xmldecl starts with <?xml at the first byte
    .+?                 # some chars (version info), matched minimal
    encoding=           # encoding attribute begins
    ["']                # attribute start delimiter
     [^"']+              # every character not delimiter (not overly exact!)
    ["']                # attribute end delimiter
    .*?                 # some chars optionally (standalone decl or whitespace)
    \?>                 # xmldecl end
    """

XML_DECLARATION = re.compile(xmlDec, re.I | re.X)

alphanum = re.compile(r'[\W_]+')


class SimpleBufferObject(object):
    def __init__(self, model, max_items=None):
        if max_items is None:
            self.max = MAX_BULK_CREATE
        else:
            self.max = max_items

        self.buffer = list()
        self.count = 0
        self.model = model

    def __enter__(self):
        return self

    def add(self, item):
        self.buffer.append(item)
        self.count += 1
        if self.count >= self.max:
            self.purge()

    def purge(self):
        self.model.objects.bulk_create(self.buffer)
        del self.buffer
        self.buffer = list()
        self.count = 0

    def __exit__(self, *args, **kwargs):
        self.purge()


class Feed(TimeStampedModel):
    CHECK_FREQUENCY_CHOICES = Choices(
        (1, 'h', 'Every Hour'),
        (12, 'th', 'Every 12 Hours'),
        (24, 'd', 'Every Day')
    )
    title = models.CharField(max_length=255)  # required
    description = BleachField(blank=True)
    icon = models.URLField(blank=True)
    site_url = models.URLField(blank=True)
    feed_url = models.URLField(unique=True)  # required
    disabled = models.BooleanField(db_index=True, default=False)

    last_checked = models.DateTimeField(null=True, blank=True)
    next_checked = models.DateTimeField(null=True, blank=True)
    check_frequency = models.PositiveSmallIntegerField(
        choices=CHECK_FREQUENCY_CHOICES, default=CHECK_FREQUENCY_CHOICES.h)
    error_count = models.PositiveSmallIntegerField(default=0)

    etag = models.CharField(max_length=255, blank=True)
    last_modified = models.DateTimeField(null=True, blank=True)

    subscriptions = models.ManyToManyField(User)

    objects = models.Manager()
    active = QueryManager(disabled=False, has_subscribers=True)
    has_subscribers = models.BooleanField(default=False)

    has_new_entries = models.BooleanField(default=False, db_index=True)

    def __str__(self):  # pragma: no cover
        return self.title

    class Meta:
        ordering = ('-modified', '-created')

    def subscribe(self, user):
        self.subscriptions.add(user)
        UserEntry.subscribe_users(user, self)
        if not self.has_subscribers:
            self.has_subscribers = True
            self.save()

    def unsubscribe(self, user):
        self.subscriptions.remove(user)
        UserEntry.unsubscribe_users(user, self)
        if self.subscriptions.count() < 1:
            self.has_subscribers = False
            self.save()

    def is_subscribed(self, user):
        return self.subscriptions.filter(pk=user.pk).exists()

    def increment_error_count(self):
        self.error_count += 1
        if self.error_count >= MAX_ERRORS:
            self.disabled = True

    def reset_error_count(self):
        self.error_count = 0
        self.disabled = False

    @staticmethod
    def get_feeds_from_url(url):
        """
        From URL, check if URL is a feed or if URL has feeds.
        Returns a list of Feed objects with feeds from URL
        """
        feeds = []
        # check if URL is already in database
        existing = Feed.objects.filter(feed_url=url)
        if existing:
            return [existing.first(), ]
        # not in database, check the URL via GET request
        req = requests.get(
            url,
            headers=HEADERS,
            allow_redirects=True
        )
        if req.status_code == requests.codes.ok:
            req.encoding = 'utf-8'
            # sometimes content types have extra text, get rid of ';'
            content_type = req.headers.get('content-type', None)
            if ';' in content_type:
                content_type = content_type.split(';')[0]
            # is this URL a feed?
            if content_type in FEED_TYPES:
                feed, created = Feed.objects.get_or_create(feed_url=req.url, defaults={'title': 'no title yet'})
                return [feed, ]
            # no feed, check for feeds in head

            html = BeautifulSoup(req.text, 'lxml')
            if html.head is not None:
                for feed_type in FEED_TYPES:
                    feed_count = 0
                    for link in html.head.find_all(type=feed_type):
                        feed_url = urljoin(req.url, link.get('href'))
                        feed, created = Feed.objects.get_or_create(
                            feed_url=feed_url,
                            defaults={'title': 'no title yet'}
                        )
                        feeds.append(feed)
                        feed_count += 1
                        if feed_count >= MAX_FEEDS:
                            break
                return feeds
        return feeds

    @staticmethod
    def update_feeds(num=10):

        with SimpleBufferObject(Entry) as new_entry_buffer:
            current_time = now()

            # get all active feeds with subscribers that have not been checked or need to be checked based
            # on "next_checked"
            feeds = Feed.active.filter(Q(next_checked=None) | Q(next_checked__lte=current_time))[:num]

            for feed in feeds:
                # update last checked to current time
                feed.last_checked = now()
                # set "next_checked" based on "check_frequency"
                feed.next_checked = feed.last_checked + timedelta(hours=feed.check_frequency)

                # create new FeedLog object
                log = FeedLog(feed=feed)
                notes = []

                # load conditional GET headers from feed object
                headers = HEADERS
                if feed.etag and feed.etag != '':
                    headers['If-None-Match'] = feed.etag
                if feed.last_modified:
                    last_modified = make_naive(feed.last_modified)
                    headers['If-Modified-Since'] = http_date(last_modified.timestamp())

                try:
                    req = requests.get(feed.feed_url, headers=headers, allow_redirects=True)

                    log.status_code = req.status_code
                    log.headers = ', '.join("{!s}={!r}".format(key, val) for (key, val) in headers.items())
                    log.headers += "--\n"
                    log.headers += ', '.join("{!s}={!r}".format(key, val) for (key, val) in req.headers.items())

                    notes.append('updating {0}'.format(feed))

                    # update feed URL if redirected or altered
                    if (req.url != feed.feed_url) and (req.history[-1].status_code == 301):
                        # if updated feed URL already exists, something is wrong
                        if Feed.objects.filter(feed_url=req.url).exists():
                            feed.disabled = True
                            notes.append(
                                'Feed URL does not match response, \
                                but new feed already exists with {0}.'.format(req.url)
                            )
                        else:
                            notes.append('Updating feed url from {0} to {1}.'.format(feed.feed_url, req.url))
                            feed.feed_url = req.url

                    if req.status_code == requests.codes.not_modified:
                        notes.append('not modified')

                    elif req.status_code == requests.codes.ok:
                        notes.append('status OK, parsing')

                        # update conditional GET data
                        feed.etag = alphanum.sub('', req.headers.get('etag', ''))
                        feed.last_modified = parse_http_date(
                            req.headers.get('last-modified', None), default=feed.last_checked)

                        # must remove encoding declaration from feed or lxml will pitch a fit
                        text = XML_DECLARATION.sub('', req.text, 1)
                        parsed = speedparser.parse(text, encoding=req.encoding)

                        # bozo feed
                        if parsed.bozo == 1:
                            notes.append('bozo feed')
                            notes.append(parsed.bozo_tb)
                            feed.increment_error_count()
                        else:
                            # update feed meta data, reset error count
                            feed.reset_error_count()
                            feed.title = parsed.feed.get('title', feed.title)
                            feed.title = shorten_string(feed.title)
                            feed.description = parsed.feed.get('description', parsed.feed.get('subtitle', None))
                            # icon/logo are not working in speedparser
                            # feed.icon = parsed.feed.get('logo', feed.icon)

                            # get latest existing entry for feed
                            try:
                                latest_entry = feed.entry_set.latest()
                            except Entry.DoesNotExist:
                                latest_entry = None

                            for count, entry in enumerate(parsed.entries):
                                published = feed_datetime(
                                    entry.get('published_parsed', entry.get('updated_parsed', None)),
                                    default=feed.last_checked
                                )

                                # only proceed if entry is newer than last
                                # entry for feed
                                if latest_entry is None or published > latest_entry.published:

                                    # entry ID is a hash of the link or entry id
                                    entry_id = hashlib.sha1(entry.get('id', entry.link).encode('utf-8')).hexdigest()
                                    author = bleach.clean(
                                        entry.get('author', 'no author'), strip=True, strip_comments=True)
                                    author = shorten_string(author)

                                    content = None
                                    content_items = entry.get('content', None)
                                    if content_items is None:
                                        content = entry.get('summary', 'No summary.')
                                    else:
                                        for c in content_items:
                                            if c.get('type', None) in ('text', 'html', 'xhtml', None):
                                                if content is None:
                                                    content = c.get('value', '')
                                                else:
                                                    content += c.get('value', '')
                                        content = bleach.clean(
                                            content, tags=BLEACH_TAGS, attributes=BLEACH_ATTRS, strip=True,
                                            strip_comments=True)

                                    title = bleach.clean(
                                        entry.get('title', 'no title'), strip=True, strip_comments=True)
                                    title = shorten_string(title)

                                    new_entry_buffer.add(
                                        Entry(
                                            feed=feed,
                                            entry_id=entry_id,
                                            link=entry.get('link', ''),
                                            title=title,
                                            author=author,
                                            content=content,
                                            published=published,
                                            updated=feed_datetime(entry.get('updated_parsed', None),
                                                                  default=feed.last_checked)
                                        )
                                    )
                                    log.entries += 1
                                else:
                                    break

                            if log.entries > 0:
                                feed.has_new_feeds = True
                    else:
                        notes.append('error: {0}'.format(req.status_code))
                        feed.increment_error_count()

                except requests.exceptions.Timeout:  # pragma: no cover
                    log.notes = 'timeout error'
                    feed.increment_error_count()
                except requests.exceptions.ConnectionError:  # pragma: no cover
                    log.notes = 'connection error'
                    feed.increment_error_count()
                except requests.exceptions.HTTPError:  # pragma: no cover
                    log.notes = 'HTTP error'
                    feed.increment_error_count()
                except requests.exceptions.TooManyRedirects:  # pragma: no cover
                    log.notes = 'too many redirects'
                    feed.increment_error_count()

                log.notes = '\n'.join(notes)
                duration = now() - feed.last_checked
                log.duration = duration.microseconds
                feed.save()
                log.save()


def shorten_string(string, max_len=255, end='...'):
    if len(string) >= max_len:
        reduce = max_len - len(end)
        return string[:reduce] + end
    return string


def feed_datetime(timetuple, allow_none=False, default=None):
    """
    Feed Datetime

    Utility for getting python datetime from entries.  Converts a timetuple (if not None) to a timezone
    aware python datetime object.

    :param timetuple: timetuple if timetuple exists in entry element
    :param allow_none: should None be returned if now datetime object exists?  If allow_none is true and no timetuple
    or default exists, return the current datetime
    :param default: if timetuple is none, use this value
    :return: a timezone aware python datetime object
    """
    if timetuple is None:
        if default is None:
            if allow_none:
                return None
            return now()
        return default
    r = datetime.fromtimestamp(mktime(timetuple))
    return make_aware(r, CURRENT_TZ)


def parse_http_date(http_date_str, default=None):
    """
    Parse HTTP Date

    Parses an RFC1123 date string and returns a datetime object
    Example:  Sun, 06 Nov 1994 08:49:37 GMT

    :param http_date_str:
    :param default: Python Datetime to return if http_date_str is None
    :return:
    """
    if not http_date_str:
        return default
    return datetime.fromtimestamp(mktime(eut.parsedate(http_date_str)))


class Entry(models.Model):
    feed = models.ForeignKey(Feed)
    entry_id = models.CharField(max_length=40)
    link = models.URLField(max_length=2083)
    title = models.CharField(max_length=255)
    author = models.CharField(max_length=255, blank=True)
    content = BleachField()
    updated = models.DateTimeField(blank=True)
    published = models.DateTimeField(db_index=True)

    added_to_subscribers = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ('-published', '-updated')
        get_latest_by = 'published'
        verbose_name_plural = 'entries'

    def __str__(self):  # pragma: no cover
        return '{0}: {1}'.format(self.feed, self.entry_id)


class UserEntry(models.Model):
    UNREAD = 'u'
    READ = 'r'
    SAVED = 's'
    STATUS = (
        (UNREAD, 'Unread'),
        (READ, 'Read'),
        (SAVED, 'Saved'),
    )
    user = models.ForeignKey(User)
    feed = models.ForeignKey(Feed)
    entry = models.ForeignKey(Entry)
    status = models.CharField(max_length=1, choices=STATUS, default=UNREAD)

    objects = models.Manager()
    read = QueryManager(status=READ)
    saved = QueryManager(status=SAVED)
    unread = QueryManager(status=UNREAD)

    class Meta:
        verbose_name = 'User Entry'
        verbose_name_plural = 'User Entries'

    @staticmethod
    def update_subscriptions():
        """
        Add New Entries

        For all feeds with new_entries flag, get all subscribers and feeds with added_to_subscribers flag false.
        For each subscriber, add to bulk create buffer a new UserEntry object.
        Update flags on entries and feeds.

        """
        with SimpleBufferObject(UserEntry) as user_entry_object_buffer:
            feeds = Feed.active.filter(has_new_entries=True)
            for feed in feeds:
                entries = feed.entry_set.filter(added_to_subscribers=False).only('id')
                if entries:
                    subscribers = feed.subscriptions.all()
                    for subscriber in subscribers:
                        for entry in entries:
                            user_entry_object_buffer.add(
                                UserEntry(user=subscriber, feed=feed, entry=entry)
                            )
                    entries.update(added_to_subscribers=True)
                feed.has_new_entries = False
                feed.save()

    @staticmethod
    def subscribe_users(users, feed):
        if not hasattr(users, '__iter__'):
            users = (users, )

        user_entry_object_buffer = SimpleBufferObject(UserEntry)
        for user in users:
            entries = feed.entry_set.filter(added_to_subscribers=True).only('id')
            if entries:
                for entry in entries:
                    user_entry_object_buffer.add(
                        UserEntry(user=user, feed=feed, entry=entry)
                    )
        user_entry_object_buffer.purge()

    @staticmethod
    def unsubscribe_users(users, feed):
        if not hasattr(users, '__iter__'):
            users = (users, )

        UserEntry.objects.filter(user__in=users, feed=feed).delete()


class FeedLog(models.Model):
    feed = models.ForeignKey(Feed, editable=False)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    headers = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    duration = models.PositiveIntegerField()
    datetime = models.DateTimeField(auto_now_add=True)
    entries = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = 'Feed Log'
        verbose_name_plural = 'Feed Logs'

    def __str__(self):  # pragma: no cover
        return '{0} ({1}) on {2}'.format(self.feed, self.status_code, self.datetime)
