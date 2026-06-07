import json

import pyncm
import pyncm.apis

with open('config.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

pyncm.WriteLoginInfo(data['login_status'])
pyncm.SetCurrentSession(pyncm.LoadSessionFromString(data['session']))

with pyncm.GetCurrentSession():
    print('=== Test 1: SetLikeTrack (for 我喜欢的音乐 specialType=5) ===')
    try:
        result = pyncm.apis.track.SetLikeTrack(2632426028, like=True)
        print(
            f'Result: {json.dumps(result, indent=2, ensure_ascii=False) if result else "None"}'
        )
    except Exception as e:
        print(f'Error: {type(e).__name__}: {e}')

    print('\n=== Test 2: SetManipulatePlaylistTracks with list param ===')
    try:
        result = pyncm.apis.playlist.SetManipulatePlaylistTracks(
            ['2632426028'], 13331336677, op='add'
        )
        print(
            f'Result: {json.dumps(result, indent=2, ensure_ascii=False) if result else "None"}'
        )
    except Exception as e:
        print(f'Error: {type(e).__name__}: {e}')

    # Also try with a non-special playlist
    print('\n=== Get playlists again to find a regular one ===')
    playlists = pyncm.apis.user.GetUserPlaylists(pyncm.GetCurrentSession().uid)[
        'playlist'
    ]
    for pl in playlists:
        print(
            f'  id={pl["id"]}, name={pl["name"]}, specialType={pl.get("specialType")}'
        )
