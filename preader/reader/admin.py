from django.contrib import admin
from django.utils.timezone import now
from .models import Feed, Entry, FeedLog, UserEntry


class FeedLogAdmin(admin.ModelAdmin):
    list_display = (
        'feed',
        'status_code',
        'datetime',
        'entries',
        'duration',
    )
    date_hierarchy = 'datetime'

admin.site.register(FeedLog, FeedLogAdmin)


def force_update_next(modeladmin, request, queryset):
    current_time = now()
    queryset.update(
        next_checked=current_time,
        etag='',
        last_modified=None
    )
force_update_next.short_description = 'Force Feed Update on Next Scan'


def clear_errors(modeladmin, request, queryset):
    queryset.update(error_count=0)
clear_errors.short_description = 'Reset Error Count'


def disable_feeds(modeladmin, request, queryset):
    queryset.update(disabled=True)
disable_feeds.short_description = 'Disable Feeds'


def enable_feeds(modeladmin, request, queryset):
    queryset.update(disabled=False)
enable_feeds.short_description = 'Enable Feeds'


class FeedAdmin(admin.ModelAdmin):
    list_display = (
        'title',
        'disabled',
        'has_subscribers',
        'has_new_entries',
        'last_checked',
        'next_checked',
        'check_frequency',
        'error_count'
    )
    list_filter = ('disabled', 'has_new_entries', 'check_frequency')
    actions = [
        force_update_next,
        clear_errors,
        disable_feeds,
        enable_feeds
    ]

admin.site.register(Feed, FeedAdmin)


class EntryAdmin(admin.ModelAdmin):
    list_display = (
        'feed',
        'title',
        'added_to_subscribers',
        'updated',
        'published'
    )
    list_filter = ('feed', 'added_to_subscribers')

admin.site.register(Entry, EntryAdmin)


class UserEntryAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'feed',
        'entry',
        'status'
    )
    list_filter = ('status', )

admin.site.register(UserEntry, UserEntryAdmin)