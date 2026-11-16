from __future__ import annotations

import json
from typing import Any

from harness.markdown_cases import MARKDOWN_CASES


def toolScenario(
    calls: list[dict[str, Any]], text: str = 'Harness task complete.'
) -> dict[str, Any]:
    names = list(dict.fromkeys(call['name'] for call in calls))
    return {
        'rounds': [
            {
                'tools': [
                    {'name': 'get_tool_usage', 'arguments': {'tool_name': name}}
                    for name in names
                    if name != 'get_confirm'
                ]
            },
            *[{'tools': [call]} for call in calls],
            {'text': text},
        ],
        'chunk_size': 16,
    }


def call(tool_name: str, /, **arguments: Any) -> dict[str, Any]:
    return {'name': tool_name, 'arguments': arguments}


def scenarioFor(case_id: str) -> dict[str, Any]:
    if case_id in ('llm_explain_code', 'llm_responses_tools'):
        return toolScenario(
            [
                call('read', path='src/core/app_context.py', limit=90),
                call('grep', path='src', pattern='addScheduledTask'),
            ],
            'AppContext schedules Qt work through addScheduledTask.',
        )
    if case_id == 'llm_search':
        return toolScenario([
            call('search_cloud', query='Harness'),
            call('continue_search_cloud'),
        ])
    if case_id == 'llm_no_results':
        return toolScenario(
            [call('search_cloud', query='no-results')], 'No songs found.'
        )
    if case_id == 'llm_organize':
        return toolScenario([
            call('get_folders'),
            call('get_folder_songs', folder='local:0:Harness Library', limit=6),
            call('create_favorite_folder', name='Harness Mix', kind='local'),
            call('favorite_song', song='song:90001', folder='local:new:Harness Mix'),
            call('favorite_song', song='song:90002', folder='local:new:Harness Mix'),
        ])
    if case_id in ('llm_confirm', 'llm_cancel'):
        name = 'Harness Approved' if case_id == 'llm_confirm' else 'Harness Cancelled'
        pending = [call('create_favorite_folder', name=name, kind='local')]
        return toolScenario(
            [
                call('get_tool_usage', tool_name='create_favorite_folder'),
                call('get_confirm', plan='Create ' + name, tools=json.dumps(pending)),
            ],
            'Please confirm this plan.',
        )
    if case_id == 'llm_lyrics':
        return toolScenario([
            call('get_current_song'),
            call('get_current_lyrics'),
            call(
                'set_current_lyrics',
                lyric='[00:00.500]Harness edited\n[00:02.000]Second line',
                translated_lyric='[00:00.500]测试歌词\n[00:02.000]第二行',
                yrc_lyric='[500,1500](500,1500,0)Harness edited',
            ),
            call('set_translation_enabled', enabled=True),
            call('get_current_lyrics'),
        ])
    if case_id == 'llm_usage_guard':
        return {
            'rounds': [
                {'tools': [call('read', path='src/main.py')]},
                {'text': 'Usage must be loaded first.'},
            ]
        }
    if case_id == 'llm_unknown_tool':
        return {
            'rounds': [
                {'tools': [call('missing_harness_tool')]},
                {'text': 'Unknown tool handled.'},
            ]
        }
    if case_id == 'llm_read_boundary':
        return toolScenario([call('read', path='../config.json')])
    if case_id == 'llm_http_error':
        return {'rounds': [{'status': 401}]}
    if case_id == 'llm_disconnect':
        return {'rounds': [{'text': 'Interrupted response ' * 4, 'disconnect': True}]}
    if case_id == 'llm_timeout':
        return {'rounds': [{'text': 'Late', 'delay': 5} for _ in range(3)]}
    if case_id == 'llm_stop':
        return {
            'rounds': [{'text': 'Streaming text. ' * 100, 'delay': 0.03}],
            'chunk_size': 8,
        }
    if case_id == 'llm_history':
        return {
            'rounds': [
                {'text': 'First answer.'},
                {'text': 'Second answer.'},
                {'text': 'Edited answer.'},
            ]
        }
    if case_id.startswith('llm_'):
        return {
            'rounds': [
                {
                    'text': '# Harness\n\n**Streaming** 中文 $x^2$.\n\n```python\nprint(1)\n```'
                }
            ],
            'chunk_size': 7,
        }
    return {'rounds': []}


CASES: list[dict[str, str]] = (
    [
        {'id': case_id, 'group': 'core', 'description': description}
        for case_id, description in [
            ('boot', '真实入口、依赖检查、窗口和隔离收藏'),
            ('playback', '合成音频解码、PCM、暂停恢复、切歌和进度'),
            ('queue', '队列重排、当前歌曲索引与清空确认'),
            ('search_ui', '搜索输入、分页、无结果和歌单搜索'),
            ('favorites_ui', '收藏列表过滤与批量选择'),
            ('lyrics', 'LRC、翻译、YRC 和桌面歌词'),
            ('lyric_editor', '键盘打轴、保存并读取歌词缓存'),
            ('settings', '设置控件变更及配置持久化'),
            ('effects', '音量、速度、音高、均衡器与处理 worker'),
            ('export_original', '原始歌曲导出与标签'),
            ('export_effects', '真实音效导出与标签'),
            ('export_video', '真实歌词视频导出与 FFprobe'),
            ('smtc', '媒体信息、播放状态和原生控制回调'),
            ('bridge', '真实 WebSocket 状态和双向控制'),
            ('llm_chat', 'Chat Completions SSE 到真实聊天控件'),
            ('llm_responses', 'Responses SSE 到真实聊天控件'),
            ('llm_responses_tools', 'Responses 多轮工具执行与真实源代码'),
            ('llm_anthropic', 'Anthropic Messages SSE 到真实聊天控件'),
            ('llm_explain_code', 'AI 读取和搜索真实源代码'),
            ('llm_search', 'AI 搜歌、分页与句柄'),
            ('llm_no_results', 'AI 搜索无结果'),
            ('llm_organize', 'AI 整理歌单并验证落盘'),
            ('llm_confirm', 'AI 计划与用户确认后的执行'),
            ('llm_cancel', '取消 AI 计划并验证没有写入'),
            ('llm_lyrics', 'AI 设置歌词、逐字歌词与翻译'),
            ('llm_usage_guard', '工具使用说明前置检查'),
            ('llm_unknown_tool', '未知工具错误返回'),
            ('llm_read_boundary', '源代码读取边界'),
            ('llm_http_error', 'HTTP 401 错误和 UI 恢复'),
            ('llm_disconnect', '连接中断和 UI 恢复'),
            ('llm_timeout', '请求超时和 UI 恢复'),
            ('llm_stop', '停止流式输出并隔离迟到数据'),
            ('llm_history', '多轮历史、编辑重发和清空'),
        ]
    ]
    + [
        {
            'id': 'md_' + case['id'],
            'group': 'core',
            'description': 'Markdown: ' + case['id'],
        }
        for case in MARKDOWN_CASES
    ]
    + [
        {
            'id': 'online_search',
            'group': 'online',
            'description': '真实网易云歌曲与歌单搜索',
        },
        {'id': 'native_audio', 'group': 'native', 'description': '真实声卡输出'},
        {'id': 'native_smtc', 'group': 'native', 'description': '真实 Windows SMTC'},
        {'id': 'whisper', 'group': 'whisper', 'description': '本地模型与真实音频转录'},
    ]
)
