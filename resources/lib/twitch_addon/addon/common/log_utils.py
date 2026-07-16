"""
    tknorris shared module

    Copyright (C) 2016 tknorris
    Copyright (C) 2016-2018 Twitch-on-Kodi

    Modified by Twitch-on-Kodi/plugin.video.twitch Dec. 12, 2016

    SPDX-License-Identifier: GPL-3.0-only
    See LICENSES/GPL-3.0-only for more information.
"""

import re
import time

from . import kodi

from xbmc import LOGDEBUG, LOGERROR, LOGFATAL, LOGINFO, LOGNONE, LOGWARNING  # @UnusedImport

LOGNOTICE = LOGINFO

_AUTHORIZATION_PATTERN = re.compile(
    r"(authorization['\"]?\s*[:=]\s*['\"]?)"
    r"(?:(?:bearer|oauth)(?:\s+|\+))?[^'\"\s,}&|]+",
    re.IGNORECASE,
)
_PROXY_CREDENTIALS_PATTERN = re.compile(r'(https?://)[^\s]+@', re.IGNORECASE)
_ENCODED_PROXY_CREDENTIALS_PATTERN = re.compile(
    r'(https?%3A%2F%2F)[^&|\s]+%40', re.IGNORECASE
)
_SIGNED_QUERY_PATTERN = re.compile(
    r'([?&|](?:token|sig|signature)=)[^&#|\s\'\"]+', re.IGNORECASE
)


def _redact_sensitive_values(msg):
    msg = '%s' % msg
    msg = _AUTHORIZATION_PATTERN.sub(r'\1[REDACTED]', msg)
    msg = _PROXY_CREDENTIALS_PATTERN.sub(r'\1[REDACTED]@', msg)
    msg = _ENCODED_PROXY_CREDENTIALS_PATTERN.sub(r'\1[REDACTED]%40', msg)
    return _SIGNED_QUERY_PATTERN.sub(r'\1[REDACTED]', msg)


def log(msg, level=LOGDEBUG):
    try:
        msg = _redact_sensitive_values(msg)
        if kodi.is_unicode(msg):
            msg = '%s (ENCODED)' % msg.encode('utf-8')
        kodi.__log('%s: %s' % (kodi.get_name(), msg), level)
    except Exception as e:
        try:
            kodi.__log('Logging Failure: %s' % e, LOGERROR)
        except:
            pass  # just give up


def trace(method):
    #  @trace decorator
    def method_trace_on(*args, **kwargs):
        start = time.time()
        result = method(*args, **kwargs)
        end = time.time()
        log('{name!r} time: {time:2.4f}s args: |{args!r}| kwargs: |{kwargs!r}|'.format(name=method.__name__, time=end - start, args=args, kwargs=kwargs), LOGDEBUG)
        return result

    def method_trace_off(*args, **kwargs):
        return method(*args, **kwargs)

    if __is_debugging():
        return method_trace_on
    else:
        return method_trace_off


def __is_debugging():
    command = {'jsonrpc': '2.0', 'id': 1, 'method': 'Settings.getSettings', 'params': {'filter': {'section': 'system', 'category': 'logging'}}}
    js_data = kodi.execute_jsonrpc(command)
    if 'result' in js_data and 'settings' in js_data['result']:
        for item in js_data['result']['settings']:
            if item['id'] == 'debug.showloginfo':
                return item['value']

    return False
