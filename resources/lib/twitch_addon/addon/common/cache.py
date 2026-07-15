"""
    tknorris shared module

    Copyright (C) 2016 tknorris
    Copyright (C) 2016-2018 Twitch-on-Kodi

    Modified by Twitch-on-Kodi/plugin.video.twitch Dec. 12, 2016

    SPDX-License-Identifier: GPL-3.0-only
    See LICENSES/GPL-3.0-only for more information.
"""

import functools
import hashlib
import inspect
import os
import pickle
import shutil
import tempfile
import time

from . import kodi, log_utils

cache_path = kodi.translate_path('special://temp/%s/cache/' % kodi.get_id())
try:
    if not os.path.exists(cache_path):
        os.makedirs(cache_path)
except Exception as e:
    log_utils.log('Failed to create cache: %s: %s' % (cache_path, e), log_utils.LOGWARNING)

cache_enabled = kodi.get_setting('use_cache') == 'true'
_CACHE_MARKER = 'twitch-cache-v1'


def make_cache_path():
    try:
        if not os.path.exists(cache_path):
            os.makedirs(cache_path)
    except Exception as e:
        log_utils.log('Failed to create cache: %s: %s' % (cache_path, e), log_utils.LOGWARNING)


def reset_cache():
    try:
        shutil.rmtree(cache_path)
        make_cache_path()
        return True
    except Exception as e:
        log_utils.log('Failed to Reset Cache: %s' % (e), log_utils.LOGWARNING)
        return False


def invalidate_cache_for_function(func_name_pattern):
    try:
        if not os.path.exists(cache_path):
            return True

        count = 0
        for filename in os.listdir(cache_path):
            filepath = os.path.join(cache_path, filename)
            if not os.path.isfile(filepath) or filename.startswith('.cache-'):
                continue
            try:
                with open(filepath, 'rb') as f:
                    entry = pickle.load(f)
            except Exception:
                continue
            if (_is_cache_entry(entry)
                    and func_name_pattern in entry[1]):
                os.remove(filepath)
                count += 1

        log_utils.log('Invalidated %d matching cache entries' % count, log_utils.LOGDEBUG)
        return True
    except Exception as e:
        log_utils.log('Failed to invalidate cache: %s' % (e), log_utils.LOGWARNING)
        return False


def _get_func(name, args=None, kwargs=None, cache_limit=1):
    if not cache_enabled or cache_limit <= 0: return False, None
    now = time.time()
    max_age = now - (cache_limit * 60 * 60)
    if args is None: args = []
    if kwargs is None: kwargs = {}
    full_path = os.path.join(cache_path, _get_filename(name, args, kwargs))
    if os.path.exists(full_path):
        try:
            mtime = os.path.getmtime(full_path)
            if mtime >= max_age:
                with open(full_path, 'rb') as f:
                    entry = pickle.load(f)
                if not _is_cache_entry(entry) or entry[1] != name:
                    raise ValueError('Invalid cache entry')
                return True, entry[2]
        except Exception:
            try:
                os.remove(full_path)
            except OSError:
                pass
            log_utils.log('Discarded unreadable cache entry', log_utils.LOGWARNING)

    return False, None


def _save_func(name, args=None, kwargs=None, result=None):
    temporary_path = None
    try:
        if args is None: args = []
        if kwargs is None: kwargs = {}
        make_cache_path()
        pickled_result = pickle.dumps(
            (_CACHE_MARKER, name, result), protocol=pickle.HIGHEST_PROTOCOL
        )
        full_path = os.path.join(cache_path, _get_filename(name, args, kwargs))
        descriptor, temporary_path = tempfile.mkstemp(
            prefix='.cache-', dir=cache_path
        )
        with os.fdopen(descriptor, 'wb') as f:
            f.write(pickled_result)
        os.replace(temporary_path, full_path)
        temporary_path = None
    except Exception as e:
        log_utils.log('Failure during cache write: %s' % (e), log_utils.LOGWARNING)
    finally:
        if temporary_path:
            try:
                os.remove(temporary_path)
            except OSError:
                pass


def _get_filename(name, args, kwargs):
    call = pickle.dumps(
        _canonicalize((args, kwargs)), protocol=pickle.HIGHEST_PROTOCOL
    )
    return (hashlib.md5(name.encode('utf-8')).hexdigest()
            + hashlib.md5(call).hexdigest())


def _canonicalize(value):
    if isinstance(value, dict):
        items = [(_canonicalize(key), _canonicalize(item))
                 for key, item in value.items()]
        items.sort(key=lambda item: pickle.dumps(
            item[0], protocol=pickle.HIGHEST_PROTOCOL
        ))
        return 'dict', tuple(items)
    if isinstance(value, list):
        return 'list', tuple(_canonicalize(item) for item in value)
    if isinstance(value, tuple):
        return 'tuple', tuple(_canonicalize(item) for item in value)
    if isinstance(value, (set, frozenset)):
        items = [_canonicalize(item) for item in value]
        items.sort(key=lambda item: pickle.dumps(
            item, protocol=pickle.HIGHEST_PROTOCOL
        ))
        return type(value).__name__, tuple(items)
    return 'value', value


def _get_call_arguments(func, args, kwargs, skip_first=False):
    bound = inspect.signature(func).bind(*args, **kwargs)
    bound.apply_defaults()
    arguments = tuple(bound.arguments.items())
    if skip_first:
        arguments = arguments[1:]
    return arguments, {}


def _is_cache_entry(entry):
    return (isinstance(entry, tuple) and len(entry) == 3
            and entry[0] == _CACHE_MARKER and isinstance(entry[1], str))


def cache_method(cache_limit, persist=True):
    def wrap(func):
        @functools.wraps(func)
        def memoizer(*args, **kwargs):
            if not persist:
                return func(*args, **kwargs)
            if args:
                klass = args[0]
                full_name = '%s.%s.%s' % (klass.__module__, klass.__class__.__name__, func.__name__)
            else:
                full_name = func.__name__
            cache_args, cache_kwargs = _get_call_arguments(
                func, args, kwargs, skip_first=True
            )
            in_cache, result = _get_func(full_name, cache_args, cache_kwargs, cache_limit=cache_limit)
            if in_cache:
                # log_utils.log('Using method cache for: |%s|%s|%s| -> |%d|' % (full_name, args, kwargs, len(pickle.dumps(result))), log_utils.LOGDEBUG)
                log_utils.log('Using method cache for: |%s| -> |%d|' % (full_name, len(pickle.dumps(result))), log_utils.LOGDEBUG)
                return result
            else:
                # log_utils.log('Calling cached method: |%s|%s|%s|' % (full_name, args, kwargs), log_utils.LOGDEBUG)
                log_utils.log('Calling cached method: |%s|' % (full_name), log_utils.LOGDEBUG)
                result = func(*args, **kwargs)
                if cache_enabled and cache_limit > 0:
                    _save_func(full_name, cache_args, cache_kwargs, result)
                return result

        return memoizer

    return wrap


# do not use this with instance methods the self parameter will cause args to never match
def cache_function(cache_limit, persist=True):
    def wrap(func):
        @functools.wraps(func)
        def memoizer(*args, **kwargs):
            if not persist:
                return func(*args, **kwargs)
            name = func.__name__
            cache_args, cache_kwargs = _get_call_arguments(func, args, kwargs)
            in_cache, result = _get_func(name, cache_args, cache_kwargs, cache_limit=cache_limit)
            if in_cache:
                # log_utils.log('Using function cache for: |%s|%s|%s| -> |%d|' % (name, args, kwargs, len(pickle.dumps(result))), log_utils.LOGDEBUG)
                log_utils.log('Using function cache for: |%s| -> |%d|' % (name, len(pickle.dumps(result))), log_utils.LOGDEBUG)
                return result
            else:
                # log_utils.log('Calling cached function: |%s|%s|%s|' % (name, args, kwargs), log_utils.LOGDEBUG)
                log_utils.log('Calling cached function: |%s|' % (name), log_utils.LOGDEBUG)
                result = func(*args, **kwargs)
                if cache_enabled and cache_limit > 0:
                    _save_func(name, cache_args, cache_kwargs, result)
                return result

        return memoizer

    return wrap
