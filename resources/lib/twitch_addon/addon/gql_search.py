# -*- coding: utf-8 -*-
"""
    Website search via Twitch's GQL backend.

    Adapted from anxdpanic/plugin.video.twitch PR #706.

    Results are adapted to the existing Helix search shape so routes and
    converters remain unchanged. Returns None on failure so callers can fall
    back to Helix.

    SPDX-License-Identifier: GPL-3.0-only
    See LICENSES/GPL-3.0-only for more information.
"""

import requests

from . import utils
from .common import log_utils
from .constants import Keys


GQL_URL = 'https://gql.twitch.tv/gql'
TIMEOUT = 15

_CHANNEL_QUERY = (
    'query Search($q: String!) {'
    '  searchFor(userQuery: $q, platform: "web", target: {index: CHANNEL}) {'
    '    channels { edges { item { ... on User {'
    '      id login displayName'
    '      broadcastSettings { language title }'
    '      profileImageURL(width: 300)'
    '      stream { id viewersCount previewImageURL game { id name displayName } }'
    '    } } } }'
    '  }'
    '}'
)

_GAME_QUERY = (
    'query Search($q: String!) {'
    '  searchFor(userQuery: $q, platform: "web", target: {index: GAME}) {'
    '    games { edges { item { ... on Game {'
    '      id name displayName boxArtURL(width: 285, height: 380)'
    '    } } } }'
    '  }'
    '}'
)


def _post(query, search_query):
    body = [{
        'operationName': 'Search',
        'query': query,
        'variables': {'q': search_query},
    }]
    response = requests.post(
        GQL_URL,
        json=body,
        headers={'Client-ID': utils.get_private_client_id()},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    envelope = response.json()
    if isinstance(envelope, list):
        if len(envelope) != 1:
            return None
        envelope = envelope[0]
    if not isinstance(envelope, dict) or envelope.get('errors'):
        return None
    data = envelope.get('data')
    if not isinstance(data, dict):
        return None
    search_for = data.get('searchFor')
    if not isinstance(search_for, dict):
        return None
    return search_for


def _channel_item(item):
    if not isinstance(item, dict):
        return None
    if not item.get('id') or not item.get('login') or not item.get('displayName'):
        return None

    settings = item.get('broadcastSettings')
    if not isinstance(settings, dict):
        settings = {}
    stream = item.get('stream')
    if not isinstance(stream, dict):
        stream = {}
    game = stream.get('game')
    if not isinstance(game, dict):
        game = {}
    profile = item.get('profileImageURL') or ''

    return {
        Keys.ID: item['id'],
        Keys.BROADCASTER_LOGIN: item['login'],
        Keys.DISPLAY_NAME: item['displayName'],
        Keys.BROADCASTER_LANGUAGE: settings.get('language') or '',
        Keys.TITLE: settings.get('title') or '',
        Keys.OFFLINE_IMAGE_URL: profile,
        Keys.THUMBNAIL_URL: stream.get('previewImageURL') or profile,
        Keys.VIEWER_COUNT: stream.get('viewersCount') or 0,
        Keys.GAME_NAME: game.get('name') or game.get('displayName') or '',
        Keys.GAME_ID: game.get('id') or '',
    }


def _game_item(item):
    if not isinstance(item, dict):
        return None
    name = item.get('name') or item.get('displayName')
    if not item.get('id') or not name:
        return None
    return {
        Keys.ID: item['id'],
        Keys.NAME: name,
        Keys.BOX_ART_URL: item.get('boxArtURL') or '',
    }


def _get_edges(search_for, container_name):
    container = search_for.get(container_name)
    if not isinstance(container, dict):
        return None
    edges = container.get('edges')
    if not isinstance(edges, list) or not edges:
        return None
    return edges


def search(search_query, kind):
    """Return Helix-shaped search data, or None to request Helix fallback."""
    try:
        if kind == 'games':
            search_for = _post(_GAME_QUERY, search_query)
            container_name = 'games'
            adapter = _game_item
        elif kind in ('channels', 'streams'):
            search_for = _post(_CHANNEL_QUERY, search_query)
            container_name = 'channels'
            adapter = _channel_item
        else:
            return None

        if search_for is None:
            return None
        edges = _get_edges(search_for, container_name)
        if edges is None:
            return None

        items = []
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            source = edge.get('item')
            if kind == 'streams' and (
                    not isinstance(source, dict)
                    or not isinstance(source.get('stream'), dict)
                    or not source.get('stream')):
                continue
            item = adapter(source)
            if item is not None:
                items.append(item)
        if not items:
            return None
        return {Keys.DATA: items}
    except Exception as error:
        log_utils.log(
            'gql_search failed: %s' % error.__class__.__name__,
            log_utils.LOGWARNING,
        )
        return None
