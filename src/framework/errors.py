"""Exceptions raised by Framework."""

from __future__ import annotations


class FrameworkError(Exception):
    """Base class for every error Framework raises."""


class ProjectNotFoundError(FrameworkError):
    """No Framework project (a folder with settings.yml or config.yml) was found."""


class SettingsError(FrameworkError):
    """A settings file is missing, cannot be parsed, or holds an invalid value."""


class DataNotFoundError(FrameworkError):
    """A data catalog name or file path does not resolve to a data file."""


class UnsupportedFormatError(FrameworkError):
    """A file format Framework cannot read or write from Python."""


class IntegrityError(FrameworkError):
    """A locked data file no longer matches the hash recorded in framework.db."""


class FrameworkWarning(UserWarning):
    """Base class for warnings Framework issues."""


class DataChangedWarning(FrameworkWarning):
    """A data file changed since Framework last recorded its hash: an unlocked file read
    after it changed, or locked data that ``data_save()`` replaced with new contents."""


class CacheWarning(FrameworkWarning):
    """A cache entry could not be used, so it was treated as a miss."""
