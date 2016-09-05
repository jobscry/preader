from django import forms
from .models import Feed


class URLForm(forms.Form):
    url = forms.URLField(label='URL', max_length=255)


class NewSubscriptionForm(forms.Form):
    feeds = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple, label='URLs')

    def __init__(self, *args, **kwargs):
        feed_id_list = kwargs.pop('feed_id_list')
        super(NewSubscriptionForm, self).__init__(*args, **kwargs)
        self.fields['feeds'] = forms.MultipleChoiceField(
            choices=Feed.objects.filter(id__in=feed_id_list).values_list(
                'id', 'feed_url'), widget=forms.CheckboxSelectMultiple, label='URLs'
        )


class NewFeedForm(forms.ModelForm):
    class Meta:
        model = Feed
        fields = ('feed_url', )
