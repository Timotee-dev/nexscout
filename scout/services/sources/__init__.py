from .base import REGISTRY, JobSource, RawJob, SourceError, register  # noqa: F401

# Importing registers each source. Add new modules here.
# Order here is the order they're fetched and shown.
from . import remoteok, remotive, weworkremotely, jobicy, himalayas, hackernews  # noqa: F401,E402,I001


def get_sources():
    return dict(REGISTRY)
