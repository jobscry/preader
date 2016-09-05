from django.conf import settings
from .forms import URLForm

NUM_COLS = getattr(settings, 'NUM_COLS', 3)


def layout(request):
    return {
        'layout_cols': NUM_COLS
    }

def new_url_form(request):
    if request.user.is_authenticated():
        return {
            'new_url_form': URLForm()
        }
    return {
        'new_url_form': None
    }
