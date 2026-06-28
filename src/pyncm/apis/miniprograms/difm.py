from __future__ import annotations

from .. import weapi


def getCurrentPlayingTrackList(channelId=101, limit=10, source=0) -> dict:
    """get current playing tracks for a difm channel (mobile).

    Args:
        channelId: channel id. defaults to 101.
        limit: count. defaults to 10.
        source: unknown. defaults to 0.

    Returns:
        dict
    """
    return weapi(
        '/api/dj/difm/playing/tracks/list',
        {
            'limit': str(limit),
            'source': str(source),
            'channelId': str(channelId),
        },
    )


def getChannelCollection(source=0) -> dict:
    """get all difm channel info (mobile).

    Args:
        source: unknown. defaults to 0.

    Returns:
        dict
    """
    return weapi('/api/dj/difm/all/style/channel/v2', {'sources': '[%s]' % source})


def getChannelSubscriptionCollection(source=0) -> dict:
    """get all subscribed difm channel info (mobile).

    Args:
        source: unknown. defaults to 0.

    Returns:
        dict
    """
    return weapi('/api/dj/difm/subscribe/channels/get/v2', {'sources': '[%s]' % source})


def setChannelSubscribiton(id, set_subsubscribe=True) -> dict:
    """subscribe/unsubscribe difm channel (mobile).

    Args:
        id: channel id.
        set_subsubscribe: subscribe or unsubscribe. defaults to true.

    Returns:
        dict
    """
    url = (
        '/api/dj/difm/channel/subscribe'
        if set_subsubscribe
        else '/api/dj/difm/channel/unsubscribe'
    )
    return weapi(url, {'id': str(id)})
