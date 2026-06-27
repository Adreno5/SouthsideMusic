from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, cast
from weakref import WeakSet

import shiboken6

from core.config import cfg

Language = Literal['en_US', 'zh_CN']
LANGUAGES: tuple[Language, ...] = ('en_US', 'zh_CN')


class _TextWidget(Protocol):
    def setText(self, text: str) -> None: ...


@dataclass(frozen=True)
class BoundText:
    key: str
    kwargs: dict[str, Any]


_bound_widgets: WeakSet[object] = WeakSet()


def _isValidWidget(widget: object) -> bool:
    try:
        return shiboken6.isValid(widget)
    except TypeError:
        return True


TRANSLATIONS: dict[str, list[str]] = {'dependences_window.audio_output_checking': ['Audio Output: Checking', '音频输出：检查中'],
 'dependences_window.available': ['available', '可用'],
 'dependences_window.count_device_s': ['{count} device(s)', '{count} 个设备'],
 'dependences_window.dependences_checking': ['Dependences Checking', '依赖检查'],
 'dependences_window.download_ffmpeg_automatically': ['Download FFmpeg Automatically',
                                                      '自动下载 FFmpeg'],
 'dependences_window.failed': ['Failed', '失败'],
 'dependences_window.ffmpeg_checking': ['FFmpeg: Checking', 'FFmpeg：检查中'],
 'dependences_window.ffmpeg_checking_2': ['FFmpeg: Checking...', 'FFmpeg：检查中...'],
 'dependences_window.ffmpeg_download_failed': ['FFmpeg: Download failed',
                                               'FFmpeg：下载失败'],
 'dependences_window.ffmpeg_downloading': ['FFmpeg: Downloading...', 'FFmpeg：下载中...'],
 'dependences_window.ffmpeg_downloading_percent': ['FFmpeg: Downloading... '
                                                   '({percent:.2f}%)',
                                                   'FFmpeg：下载中...（{percent:.2f}%）'],
 'dependences_window.ffmpeg_extracting': ['FFmpeg: Extracting...', 'FFmpeg：解压中...'],
 'dependences_window.ffmpeg_extraction_failed': ['FFmpeg: Extraction failed',
                                                 'FFmpeg：解压失败'],
 'dependences_window.name_status_detail': ['{name}: {status} ({detail})',
                                           '{name}：{status}（{detail}）'],
 'dependences_window.network_checking': ['Network: Checking', '网络：检查中'],
 'dependences_window.no_output_device': ['no output device', '没有输出设备'],
 'dependences_window.no_valid_context': ['no valid context', '没有可用上下文'],
 'dependences_window.ok': ['OK', '正常'],
 'dependences_window.opengl_checking': ['OpenGL: Checking', 'OpenGL：检查中'],
 'dependences_window.python_runtime_checking': ['Python Runtime: Checking',
                                                'Python 运行时：检查中'],
 'desktop_lyrics.building_settings_panel': ['  Building settings panel...',
                                            '  正在构建设置面板...'],
 'desktop_lyrics.creating_desktop_lyrics_viewer': ['  Creating desktop lyrics '
                                                   'viewer...',
                                                   '  正在创建桌面歌词查看器...'],
 'desktop_lyrics.desktop_lyrics': ['Desktop Lyrics', '桌面歌词'],
 'desktop_lyrics.enable_desktop_lyrics': ['Enable Desktop Lyrics', '启用桌面歌词'],
 'desktop_lyrics.initializing_desktop_lyrics_page': ['Initializing desktop lyrics '
                                                     'page...',
                                                     '正在初始化桌面歌词页面...'],
 'desktop_lyrics.reset_position': ['Reset Position', '重置位置'],
 'dialogs.i_scanned': ['I scanned', '我已扫码'],
 'dialogs.login_anomaly_risk_control': ['Login anomaly risk control', '登录异常风控'],
 'dialogs.login_via_qr_code': ['Login via QRCode', '二维码登录'],
 'dialogs.qr_code_expired_or_not_exist': ['QRCode expired or not exist', '二维码已过期或不存在'],
 'dialogs.use_your_cloudmusic_app_to_scan_the_qr_code_and_click_i_scanned_button': ['use '
                                                                                    'your '
                                                                                    'CloudMusic '
                                                                                    'app '
                                                                                    'to '
                                                                                    'scan '
                                                                                    'the '
                                                                                    'QRCode '
                                                                                    'and '
                                                                                    'click '
                                                                                    "'I "
                                                                                    "scanned' "
                                                                                    'button',
                                                                                    '使用网易云音乐 '
                                                                                    'App '
                                                                                    '扫描二维码，然后点击“我已扫码”按钮'],
 'error_popup.copy_details_above_and_paste_it_to_the_issue_page_below': ['Copy details '
                                                                         'above and '
                                                                         'paste it to '
                                                                         'the issue '
                                                                         'page below',
                                                                         '复制上方详情并粘贴到下面的问题页面'],
 'error_popup.describe_the_error_you_encountered_in_the_title_and_paste_the_details_': ['Describe '
                                                                                        'the '
                                                                                        'error '
                                                                                        'you '
                                                                                        'encountered '
                                                                                        'in '
                                                                                        'the '
                                                                                        'title, '
                                                                                        'and '
                                                                                        'paste '
                                                                                        'the '
                                                                                        'details '
                                                                                        'into '
                                                                                        'the '
                                                                                        'description',
                                                                                        '请在标题中描述遇到的错误，并将详情粘贴到描述中'],
 'error_popup.details': ['Details:', '详情：'],
 'error_popup.oops_something_went_wrong': ['Oops! Something went wrong', '糟糕！发生了一些错误'],
 'error_popup.report_this_problem': ['Report this Problem', '报告此问题'],
 'error_popup.southside_music_encountered_some_errors': ['SouthsideMusic encountered '
                                                         'some errors',
                                                         'SouthsideMusic 遇到了一些错误'],
 'error_popup.tip': ['tip', '提示'],
 'events_services.are_you_sure_to_remove_folder': ['Are you sure to remove folder '
                                                   "'{folder_name}'?",
                                                   "确定要移除文件夹 '{folder_name}' 吗？"],
 'events_services.cancel': ['Cancel', '取消'],
 'events_services.enter_new_name_of_your_folder': ['enter new name of your folder',
                                                   '输入文件夹的新名称'],
 'events_services.folder_added_to_cloud': ['Folder {folder_name} was added to cloud',
                                           '文件夹 {folder_name} 已添加到云端'],
 'events_services.folder_added_to_local': ['Folder {folder_name} was added to local',
                                           '文件夹 {folder_name} 已添加到本地'],
 'events_services.folder_renamed_to': ['Folder {folder_name} was renamed to {new_name}',
                                       '文件夹 {folder_name} 已重命名为 {new_name}'],
 'events_services.imported_successfully': ['Imported successfully', '导入成功'],
 'events_services.my_folder': ['my folder', '我的文件夹'],
 'events_services.rename_folder': ['Rename Folder', '重命名文件夹'],
 'events_services.renamed_successfully': ['Renamed successfully', '重命名成功'],
 'events_services.remove': ['Remove', '移除'],
 'events_services.remove_folder': ['Remove Folder', '移除文件夹'],
 'favorites_page.add_to_folder': ['Add to Folder', '添加到文件夹'],
 'favorites_page.add_to_playlist': ['Add to Playlist', '添加到播放列表'],
 'favorites_page.added_added_count_selected_songs_to_folder_name': ['Added '
                                                                    '{added_count} '
                                                                    'selected songs to '
                                                                    '{folder_name}',
                                                                    '已将 {added_count} '
                                                                    '首选中歌曲添加到 '
                                                                    '{folder_name}'],
 'favorites_page.added_added_count_selected_songs_to_playlist': ['Added {added_count} '
                                                                 'selected songs to '
                                                                 'playlist',
                                                                 '已将 {added_count} '
                                                                 '首选中歌曲添加到播放列表'],
 'favorites_page.added_added_count_songs_from_favorites_to_playlist': ['Added '
                                                                       '{added_count} '
                                                                       'songs from '
                                                                       'favorites to '
                                                                       'playlist',
                                                                       '已将收藏中的 '
                                                                       '{added_count} '
                                                                       '首歌曲添加到播放列表'],
 'favorites_page.added_count_selected_songs_to_folder_name': ['Added {count} selected '
                                                              'songs to {folder_name}',
                                                              '已将 {count} 首选中歌曲添加到 '
                                                              '{folder_name}'],
 'favorites_page.are_you_sure_you_want_to_delete_count_selected_songs_from_folder_name': ['Are '
                                                                                          'you '
                                                                                          'sure '
                                                                                          'you '
                                                                                          'want '
                                                                                          'to '
                                                                                          'delete '
                                                                                          '{count} '
                                                                                          'selected '
                                                                                          'songs '
                                                                                          'from '
                                                                                          "'{folder_name}'?",
                                                                                          '确定要从 '
                                                                                          "'{folder_name}' "
                                                                                          '中删除 '
                                                                                          '{count} '
                                                                                          '首选中歌曲吗？'],
 'favorites_page.are_you_sure_you_want_to_delete_song_song_name_from_cloud_folder_folde': ['Are '
                                                                                           'you '
                                                                                           'sure '
                                                                                           'you '
                                                                                           'want '
                                                                                           'to '
                                                                                           'delete '
                                                                                           'song '
                                                                                           '{song_name} '
                                                                                           'from '
                                                                                           'cloud '
                                                                                           'folder '
                                                                                           "'{folder_name}'?",
                                                                                           '确定要从云端文件夹 '
                                                                                           "'{folder_name}' "
                                                                                           '中删除歌曲 '
                                                                                           '{song_name} '
                                                                                           '吗？'],
 'favorites_page.are_you_sure_you_want_to_delete_song_song_name_from_favorites': ['Are '
                                                                                  'you '
                                                                                  'sure '
                                                                                  'you '
                                                                                  'want '
                                                                                  'to '
                                                                                  'delete '
                                                                                  'song '
                                                                                  '{song_name} '
                                                                                  'from '
                                                                                  'favorites?',
                                                                                  '确定要从收藏中删除歌曲 '
                                                                                  '{song_name} '
                                                                                  '吗？'],
 'favorites_page.clear': ['Clear', '清除'],
 'favorites_page.confirm_delete': ['Confirm Delete', '确认删除'],
 'favorites_page.create_new_folder': ['Create New Folder', '新建文件夹'],
 'favorites_page.deleted_count_selected_songs': ['Deleted {count} selected songs',
                                                 '已删除 {count} 首选中歌曲'],
 'favorites_page.enter_name_of_your_new_folder': ['enter name of your new folder',
                                                  '输入新文件夹名称'],
 'favorites_page.initializing_favorites_page': ['Initializing favorites page...',
                                                '正在初始化收藏页面...'],
 'favorites_page.multiple_selection': ['Multiple selection', '多选'],
 'favorites_page.my_folder': ['my folder', '我的文件夹'],
 'favorites_page.none': ['None', '无'],
 'favorites_page.playlist_replaced': ['Playlist replaced', '播放列表已替换'],
 'favorites_page.playlist_replaced_with_folder_name': ['Playlist replaced with '
                                                       '{folder_name}',
                                                       '播放列表已替换为 {folder_name}'],
 'favorites_page.please_re_login_to_perform_this_action': ['Please re-login to perform '
                                                           'this action',
                                                           '请重新登录后再执行此操作'],
 'favorites_page.remove': ['Remove', '移除'],
 'favorites_page.replace_playlist': ['Replace Playlist', '替换播放列表'],
 'favorites_page.select_all': ['Select All', '全选'],
 'favorites_page.session_expired': ['Session expired', '会话已过期'],
 'favorites_page.song_deleted': ['Song deleted', '歌曲已删除'],
 'favorites_page.song_song_name_deleted': ['Song {song_name} deleted',
                                           '歌曲 {song_name} 已删除'],
 'favorites_page.song_song_name_removed_from_cloud_folder': ['Song {song_name} removed '
                                                             'from cloud folder',
                                                             '歌曲 {song_name} '
                                                             '已从云端文件夹移除'],
 'favorites_page.songs_added': ['Songs added', '歌曲已添加'],
 'favorites_page.songs_deleted': ['Songs deleted', '歌曲已删除'],
 'favorites_page.delete': ['Delete', '删除'],
 'favorites_page.cancel': ['Cancel', '取消'],
 'folder_card.add_to_cloud': ['Add to Cloud', '添加到云端'],
 'folder_card.add_to_local': ['Add to Local', '添加到本地'],
 'folder_card.remove': ['Remove', '移除'],
 'folder_card.rename': ['Rename', '重命名'],
 'language.en_US': ['English', '英文'],
 'language.zh_CN': ['Simplified Chinese', '简体中文'],
 'launch_window.launching': ['Launching...', '启动中...'],
 'main_window.add_folder': ['Add folder', '添加文件夹'],
 'main_window.add_new_folder': ['Add New Folder', '新建文件夹'],
 'main_window.enter_name_of_your_new_folder': ['enter name of your new folder',
                                               '输入新文件夹名称'],
 'main_window.local': ['Local', '本地'],
 'main_window.cloud': ['Cloud', '云端'],
 'main_window.daily_recommend': ['Daily Recommend', '每日推荐'],
 'main_window.my_folder': ['my folder', '我的文件夹'],
 'main_window.refresh': ['Refresh', '刷新'],
 'main_window.search_failed': ['Search failed', '搜索失败'],
 'main_window.settings': ['Settings', '设置'],
 'main_window.llm_ask_onerad': ['Ask Onerad', '问 Onerad'],
 'main_window.llm_cancel': ['Cancel', '取消'],
 'main_window.llm_clear_chat': ['Clear chat', '清空聊天'],
 'main_window.llm_confirm_execute': ['Confirm', '确认执行'],
 'main_window.llm_confirm_then_execute': ['Confirm to execute these actions.',
                                          '确认后执行这些操作。'],
 'main_window.llm_copy_chat': ['Copy chat', '复制聊天'],
 'main_window.llm_done': ['Done.', '完成。'],
 'main_window.llm_needs_confirmation': ['Needs confirmation', '需要确认'],
 'main_window.llm_no_pending_tools_parsed': ['No pending tools were parsed',
                                             '没有解析到待执行工具'],
 'main_window.llm_no_tools_executed': ['No tools executed.', '没有执行工具。'],
 'main_window.llm_send': ['Send', '发送'],
 'main_window.llm_stop': ['Stop', '停止'],
 'main_window.llm_tools_failed': ['Failed: {error}', '执行失败：{error}'],
 'main_window.llm_tools_prefix': ['Tools: ', '工具: '],
 'main_window.southside_client_connection': ['SouthsideClient connection',
                                             'SouthsideClient 连接'],
 'main_window.southside_music_was_been_disconnected_from_southsidclient': ['SouthsideMusic '
                                                                           'was been '
                                                                           'disconnected '
                                                                           'from '
                                                                           'SouthsidClient',
                                                                           'SouthsideMusic '
                                                                           '已与 '
                                                                           'SouthsideClient '
                                                                           '断开连接'],
 'main_window.southside_music_was_connected_to_southsidclient': ['SouthsideMusic was '
                                                                 'connected to '
                                                                 'SouthsidClient',
                                                                 'SouthsideMusic 已连接到 '
                                                                 'SouthsideClient'],
 'main_window.the_keyword_is_empty': ['the keyword is empty!', '关键词为空！'],
 'main_window.char_outputed_suffix': ['chars', '字'],
 'main_window.tool_calls_suffix': ['tool calls', '工具调用'],
 'playlist_page.are_you_sure_you_want_to_remove_all_songs_from_playlist': ['Are you '
                                                                           'sure you '
                                                                           'want to '
                                                                           'remove all '
                                                                           'songs from '
                                                                           'playlist?',
                                                                           '确定要移除播放列表中的所有歌曲吗？'],
 'playlist_page.confirm_delete': ['Confirm Delete', '确认删除'],
 'playlist_page.initializing_sidebar': ['Initializing sidebar...', '正在初始化侧边栏...'],
 'playlist_page.remove_all': ['Remove All', '全部移除'],
 'playlist_page.removed': ['Removed', '已移除'],
 'playlist_page.removed_all_songs': ['Removed all songs', '已移除所有歌曲'],
 'playing_manager.failed_to_download_missing_cached_files': ['Failed to download '
                                                             'missing cached files.',
                                                             '下载缺失的缓存文件失败。'],
 'playing_manager.failed_to_download_song_assets': ['Failed to download song assets.',
                                                    '下载歌曲资源失败。'],
 'playing_manager.failed_to_start_streaming_playback': ['Failed to start streaming '
                                                        'playback.',
                                                        '启动流式播放失败。'],
 'playing_manager.first_song_in_playlist': ['This song is the first song in the '
                                            'playlist.',
                                            '这已经是播放列表中的第一首歌。'],
 'playing_manager.last_song_in_playlist': ['This song is the last song in the '
                                           'playlist.',
                                           '这已经是播放列表中的最后一首歌。'],
 'playing_manager.playback_failed': ['Playback failed', '播放失败'],
 'playing_manager.warning': ['Warning', '警告'],
 'update.failed_try_again_later': ['Failed to update. Please try again later.',
                                   '更新失败，请稍后重试。'],
 'update.skip': ['Skip', '跳过'],
 'update.update': ['Update', '更新'],
 'update.update_available': ['Update Available', '发现更新'],
 'update.update_complete': ['Update Complete', '更新完成'],
 'update.update_completed_restart': ['Update completed. Click OK to restart.',
                                     '更新已完成，点击 OK 重启。'],
 'update.update_failed': ['Update Failed', '更新失败'],
 'update.version_available': ['{tag_name} is available! Do you want to update now?',
                              '{tag_name} 可用，要现在更新吗？'],
 'search_page.search_type.playlists': ['Playlists', '歌单'],
 'search_page.search_type.songs': ['Songs', '单曲'],
 'main_window.anonymous_user': ['Click me to Login', '点我登录'],
 'main_window.cell_phone': ['Cell Phone', '手机号'],
 'main_window.choose_method_to_log_into_an_account': ['choose method to log into an '
                                                      'account',
                                                      '选择账号登录方式'],
 'main_window.enter_the_verification_code': ['enter the verification code', '输入验证码'],
 'main_window.enter_your_cell_phone_number': ['enter your cell phone number', '输入手机号'],
 'main_window.logged_in_via_method_method': ['logged in via method {method}',
                                             '已通过 {method} 登录'],
 'main_window.login': ['Login', '登录'],
 'main_window.login_successful': ['Login successful', '登录成功'],
 'main_window.qr_code': ['QR Code', '二维码'],
 'main_window.verification_code_sent': ['Verification Code Sent', '验证码已发送'],
 'main_window.logout': ['Log out', '登出账号'],
 'main_window.logout_successful': ['Logged out successfully', '已成功登出账号'],
 'main_window.home': ['Home', '首页'],
 'main_window.library': ['Library', '库'],
 'home_page.title': ['Home', '首页'],
 'home_page.recommend_folders': ['Recommend Folders', '推荐歌单'],
 'home_page.recommend_songs': ['Recommend Songs', '每日推荐'],
 'home_page.welcome_back': ['Welcome back,', '欢迎回来，'],
 'library_page.title': ['Library', '库'],
 'library_page.number_prefix': ['You have', '你一共有'],
 'library_page.number_suffix': ['songs in total', '首歌'],
 'setting_page.acceleration_smooth_factor': ['Acceleration Smooth Factor', '加速度平滑系数'],
 'setting_page.adjust_the_right_channel_delay_of_stereo_haas_effect': ['adjust the '
                                                                       'right-channel '
                                                                       'delay of '
                                                                       'stereo Haas '
                                                                       'effect',
                                                                       '调整立体声 Haas '
                                                                       '效果的右声道延迟'],
 'setting_page.adjust_the_strength_of_the_reverb_effect': ['adjust the strength of the '
                                                           'reverb effect',
                                                           '调整混响效果强度'],
 'setting_page.app': ['App', '应用'],
 'setting_page.change_the_display_language_immediately': ['change the display language '
                                                          'immediately',
                                                          '立即切换显示语言'],
 'setting_page.changed_output_device_to_device': ['changed output device to {device}',
                                                  '已将输出设备切换为 {device}'],
 'setting_page.connected': ['Connected', '已连接'],
 'setting_page.connection': ['Connection', '连接'],
 'setting_page.connection_status_span_style_color_color_status_span': ['Connection '
                                                                       'Status: <span '
                                                                       "style='color: "
                                                                       "{color};'>{status}</span>",
                                                                       '连接状态：<span '
                                                                       "style='color: "
                                                                       "{color};'>{status}</span>"],
 'setting_page.current_volume': ['Current Volume', '当前音量'],
 'setting_page.current_volume_db_value': ['Current volume(db): {value}',
                                          '当前音量(db)：{value}'],
 'setting_page.desktop_lyrics': ['Desktop Lyrics', '桌面歌词'],
 'setting_page.device_changed': ['Device changed', '设备已切换'],
 'setting_page.disconnect': ['Disconnect', '断开连接'],
 'setting_page.disconnected': ['Disconnected', '未连接'],
 'setting_page.enable_desktop_lyrics': ['Enable Desktop Lyrics', '启用桌面歌词'],
 'setting_page.enable_fft_driven_visual_effects': ['enable FFT-driven visual effects',
                                                   '启用 FFT 驱动的视觉效果'],
 'setting_page.enable_frequency_graphics': ['Enable Frequency Graphics', '启用频谱图形'],
 'setting_page.enable_crossfade': ['Enable Crossfade', '启用交叉淡化'],
 'setting_page.enable_crossfade_effect': ['blend the end of the current song into the '
                                          'next preloaded song',
                                          '将当前歌曲结尾与下一首预加载歌曲混合播放'],
 'setting_page.enable_reverb': ['Enable Reverb', '启用混响'],
 'setting_page.enable_reverb_effect': ['enable reverb effect', '启用混响效果'],
 'setting_page.enable_stereo': ['Enable Stereo', '启用立体声'],
 'setting_page.enable_stereo_effect': ['enable stereo effect', '启用立体声效果'],
 'setting_page.fft': ['FFT', 'FFT'],
 'setting_page.fft_filtering_window_size': ['FFT Filtering Window size', 'FFT 滤波窗口大小'],
 'setting_page.fft_smoothing_factor': ['FFT Smoothing Factor', 'FFT 平滑系数'],
 'setting_page.floating_lyrics_window_controls': ['Floating lyrics window controls.',
                                                  '悬浮歌词窗口控制。'],
 'setting_page.frequency_graphics': ['Frequency Graphics', '频谱图形'],
 'setting_page.frequency_visualization_tuning_for_local_and_client_output': ['Frequency '
                                                                             'visualization '
                                                                             'tuning '
                                                                             'for '
                                                                             'local '
                                                                             'and '
                                                                             'client '
                                                                             'output.',
                                                                             '本地和客户端输出的频谱可视化调节。'],
 'setting_page.language': ['Language', 'Language(语言)'],
 'setting_page.language_and_application_behavior': ['Language and application '
                                                    'behavior.',
                                                    '语言和应用行为。'],
 'setting_page.larger_value_make_color_of_backgound_nearly_to_image_of_playing_song': ['larger '
                                                                                       'value '
                                                                                       'make '
                                                                                       'color '
                                                                                       'of '
                                                                                       'backgound '
                                                                                       'nearly '
                                                                                       'to '
                                                                                       'image '
                                                                                       'of '
                                                                                       'playing '
                                                                                       'song',
                                                                                       '数值越大，背景颜色越接近正在播放歌曲的封面'],
 'setting_page.larger_value_means_a_more_sudden_change': ['larger value means a more '
                                                          'sudden change',
                                                          '数值越大变化越突然'],
 'setting_page.larger_value_means_more_intense_changing_only_on_southside_client_side': ['larger '
                                                                                         'value '
                                                                                         'means '
                                                                                         'more '
                                                                                         'intense '
                                                                                         'changing(only '
                                                                                         'on '
                                                                                         'SouthsideClient '
                                                                                         'side)',
                                                                                         '数值越大变化越强（仅 '
                                                                                         'SouthsideClient '
                                                                                         '侧）'],
 'setting_page.larger_value_means_more_intense_changing_only_on_southside_music_side': ['larger '
                                                                                        'value '
                                                                                        'means '
                                                                                        'more '
                                                                                        'intense '
                                                                                        'changing(only '
                                                                                        'on '
                                                                                        'SouthsideMusic '
                                                                                        'side)',
                                                                                        '数值越大变化越强（仅 '
                                                                                        'SouthsideMusic '
                                                                                        '侧）'],
 'setting_page.larger_value_means_more_smoothing': ['larger value means more smoothing',
                                                    '数值越大越平滑'],
 'setting_page.live_playback_volume_in_db': ['live playback volume in db',
                                             '实时播放音量(db)'],
 'setting_page.loudness': ['Loudness', '响度'],
 'setting_page.lyrics': ['Lyrics', '歌词'],
 'setting_page.lyrics_smooth_factor': ['Lyrics Smooth Factor', '歌词平滑系数'],
 'setting_page.move_the_desktop_lyrics_window_back_to_the_origin': ['move the desktop '
                                                                    'lyrics window '
                                                                    'back to the '
                                                                    'origin',
                                                                    '将桌面歌词窗口移回初始位置'],
 'setting_page.need_restart': ['Need Restart', '需要重启'],
 'setting_page.output_device': ['Output Device', '输出设备'],
 'setting_page.play_method.play_in_order': ['Play in order', '顺序播放'],
 'setting_page.play_method.repeat_list': ['Repeat list', '列表循环'],
 'setting_page.play_method.repeat_one': ['Repeat one', '单曲循环'],
 'setting_page.play_method.shuffle': ['Shuffle', '随机播放'],
 'setting_page.play_order': ['Play order', '播放顺序'],
 'setting_page.pitch_shift_in_semitones': ['pitch shift in semitones', '按半音调整音调'],
 'setting_page.playback_order_stereo_output_speed_and_skip_behavior': ['Playback '
                                                                       'order, stereo '
                                                                       'output, speed '
                                                                       'and skip '
                                                                       'behavior.',
                                                                       '播放顺序、立体声输出、速度和跳过行为。'],
 'setting_page.crossfade_time': ['Crossfade Time', '交叉淡化时长'],
 'setting_page.crossfade_time_description': ['seconds used for mixing two adjacent '
                                             'songs',
                                             '两首相邻歌曲混合播放的秒数'],
 'setting_page.crossfade_strength': ['Crossfade Strength', '交叉淡化强度'],
 'setting_page.crossfade_strength_description': ['larger value makes the transition '
                                                 'start earlier and blend more '
                                                 'strongly',
                                                 '数值越大，过渡越早开始且混合感越强'],
 'setting_page.playback_pitch': ['Playback Pitch', '播放音调'],
 'setting_page.playback_speed': ['Playback Speed', '播放速度'],
 'setting_page.playing': ['Playing', '播放'],
 'setting_page.llm': ['LLM', 'LLM'],
 'setting_page.llm_provider_model_and_authentication': ['OpenAI-compatible provider, '
                                                        'model and authentication.',
                                                        'OpenAI 兼容服务、模型和认证配置。'],
 'setting_page.llm_base_url': ['Base URL', 'Base URL'],
 'setting_page.openai_compatible_base_url': ['OpenAI-compatible API base URL',
                                             'OpenAI 兼容 API Base URL'],
 'setting_page.llm_api_key': ['Api Key', 'Api Key'],
 'setting_page.llm_api_key_stored_encrypted': ['stored encrypted in config.json',
                                               '加密存储在 config.json 中'],
 'setting_page.llm_model': ['Model', 'Model'],
 'setting_page.select_model_after_refreshing_models': ['select a model after '
                                                       'refreshing the model list',
                                                       '刷新模型列表后选择模型'],
 'setting_page.refresh_models': ['Refresh Models', '刷新模型'],
 'setting_page.llm_refresh_models': ['Refresh model list', '刷新模型列表'],
 'setting_page.fetch_models_from_the_configured_base_url': ['fetch models from the '
                                                            'configured Base URL',
                                                            '从当前 Base URL 获取模型列表'],
 'setting_page.llm_models_refresh_failed': ['Failed to fetch models', '获取模型列表失败'],
 'setting_page.llm_base_url_required': ['Base URL is required', 'Base URL 不能为空'],
 'setting_page.llm_models_refreshed': ['Models refreshed', '模型已刷新'],
 'setting_page.loaded_model_count': ['Loaded {count} model(s)', '已加载 {count} 个模型'],
 'setting_page.add_provider': ['Add Provider', '添加提供商'],
 'setting_page.provider_model_count': ['{count} Model(s)', '{count} 个模型'],
 'setting_page.edit': ['Edit', '编辑'],
 'setting_page.delete': ['Delete', '删除'],
 'setting_page.provider_name': ['Provider Name', '供应商名称'],
 'setting_page.api_format': ['API Format', 'API 格式'],
 'setting_page.fetch_models': ['Fetch Models', '获取模型列表'],
 'setting_page.model_id': ['Model ID', 'Model ID'],
 'setting_page.display_name': ['Display Name', '显示名称'],
 'setting_page.add_model_mapping': ['Add', '添加'],
 'setting_page.cancel': ['Cancel', '取消'],
 'setting_page.add': ['Add', '添加'],
 'setting_page.save': ['Save', '保存'],
 'setting_page.provider_name_required': ['Provider name is required', '供应商名称不能为空'],
 'setting_page.provider_name_duplicated': ['Provider name already exists', '供应商名称已存在'],
 'setting_page.model_mapping_required': ['Model ID and display name are required',
                                         'Model ID 和显示名称不能为空'],
 'setting_page.model_mapping_duplicated': ['Model ID or display name is duplicated',
                                           'Model ID 或显示名称重复'],
 'setting_page.range_60_quietest_0_loudest_recommend_16_18_youtube_14_lufs_netflix_27': ['Range: '
                                                                                         '-60(quietest)~0(loudest)\n'
                                                                                         'Recommend: '
                                                                                         '-16~-18\n'
                                                                                         'Youtube: '
                                                                                         '-14 '
                                                                                         'LUFS\n'
                                                                                         'Netflix: '
                                                                                         '-27 '
                                                                                         'LUFS\n'
                                                                                         'TikTok '
                                                                                         '/ '
                                                                                         'Instagram '
                                                                                         'Reels: '
                                                                                         '-13 '
                                                                                         'LUFS\n'
                                                                                         'Apple '
                                                                                         'Music '
                                                                                         '(Video): '
                                                                                         '-16 '
                                                                                         'LUFS\n'
                                                                                         'Spotify '
                                                                                         '(Video): '
                                                                                         '-14 '
                                                                                         'LUFS '
                                                                                         '/ '
                                                                                         '-16 '
                                                                                         'LUFS',
                                                                                         '范围：-60（最安静）~0（最响）\n'
                                                                                         '推荐：-16~-18\n'
                                                                                         'YouTube：-14 '
                                                                                         'LUFS\n'
                                                                                         'Netflix：-27 '
                                                                                         'LUFS\n'
                                                                                         'TikTok '
                                                                                         '/ '
                                                                                         'Instagram '
                                                                                         'Reels：-13 '
                                                                                         'LUFS\n'
                                                                                         'Apple '
                                                                                         'Music（视频）：-16 '
                                                                                         'LUFS\n'
                                                                                         'Spotify（视频）：-14 '
                                                                                         'LUFS '
                                                                                         '/ '
                                                                                         '-16 '
                                                                                         'LUFS'],
 'setting_page.reference': ['Reference', '参考'],
 'setting_page.remain_time_to_skip': ['Remain time to Skip', '跳过检测剩余时间'],
 'setting_page.reset_position': ['Reset Position', '重置位置'],
 'setting_page.restart_the_application_to_apply_the_new_lufs': ['Restart the '
                                                                'application to apply '
                                                                'the new LUFS',
                                                                '重启应用以应用新的 LUFS'],
 'setting_page.restart_to_apply_loudness_changes': ['restart to apply loudness changes',
                                                    '重启后应用响度变化'],
 'setting_page.reverb_intensity': ['Reverb Intensity', '混响强度'],
 'setting_page.show_lyrics_in_a_floating_always_on_top_window': ['show lyrics in a '
                                                                 'floating '
                                                                 'always-on-top window',
                                                                 '在置顶悬浮窗口中显示歌词'],
 'setting_page.skip_the_no_sound_section_when_song_ends': ['Skip the no sound section '
                                                           'when song ends',
                                                           '歌曲结尾时跳过无声片段'],
 'setting_page.skip_threshold': ['Skip Threshold', '跳过阈值'],
 'setting_page.smaller_value_means_a_more_bounce_effect': ['smaller value means a more '
                                                           'bounce effect',
                                                           '数值越小弹性效果越明显'],
 'setting_page.smart_skip': ['Smart Skip', '智能跳过'],
 'setting_page.smoothing_controls_for_the_main_lyrics_animation': ['Smoothing controls '
                                                                   'for the main '
                                                                   'lyrics animation.',
                                                                   '主歌词动画的平滑控制。'],
 'setting_page.southside_client_side_fft_multiple_factor': ['SouthsideClient side FFT '
                                                            'Multiple Factor',
                                                            'SouthsideClient 侧 FFT '
                                                            '放大系数'],
 'setting_page.southside_client_websocket_status_and_controls': ['SouthsideClient '
                                                                 'websocket status and '
                                                                 'controls.',
                                                                 'SouthsideClient '
                                                                 'WebSocket 状态和控制。'],
 'setting_page.southside_music_side_fft_multiple_factor': ['SouthsideMusic side FFT '
                                                           'Multiple Factor',
                                                           'SouthsideMusic 侧 FFT 放大系数'],
 'setting_page.speed_of_playing': ['speed of playing', '播放速度'],
 'setting_page.start_detecting_volume_during_the_remaining_specified_seconds': ['start '
                                                                                'detecting '
                                                                                'volume '
                                                                                'during '
                                                                                'the '
                                                                                'remaining '
                                                                                'specified '
                                                                                'seconds',
                                                                                '在剩余指定秒数内开始检测音量'],
 'setting_page.stereo_haas_index_ms': ['Stereo Haas Index (ms)', '立体声 Haas 延迟(ms)'],
 'setting_page.target_lufs': ['Target LUFS', '目标 LUFS'],
 'setting_page.target_lufs_value': ['Target LUFS: {value}', '目标 LUFS'],
 'setting_page.target_volume_normalization_for_playback': ['Target volume '
                                                           'normalization for '
                                                           'playback.',
                                                           '播放目标响度标准化。'],
 'setting_page.the_device_to_output_audio': ['the device to output audio', '音频输出设备'],
 'setting_page.the_order_of_play': ['the order of play', '播放顺序'],
 'setting_page.the_threshold_of_the_skip': ['the threshold of the skip', '跳过检测阈值'],
 'setting_page.theme_sensitive_background_mixing': ['Theme-sensitive background '
                                                    'mixing.',
                                                    '随主题变化的背景混合。'],
 'setting_page.try_connect': ['Try connect', '尝试连接'],
 'setting_page.sent_size': ['Sent', '已发送'],
 'setting_page.received_size': ['Received', '已接收'],
 'setting_page.latency': ['Latency', '延迟'],
 'setting_page.window': ['Window', '窗口'],
 'setting_page.window_background_mix_ratio': ['Window Background Mix Ratio',
                                              '窗口背景混合比例'],
 'setting_page.download_concurrent_threads': ['Download Concurrent Threads', '下载并发线程数'],
 'setting_page.download_concurrent_threads_description': ['the number of threads that '
                                                          'launch when download(larger '
                                                          'is NOT better)',
                                                          '下载时启动的线程数量(并不是越大越好)'],
 'song_card.add_to': ['Add to ...', '添加到...'],
 'song_card.add_to_folder': ['Add to Folder', '添加到文件夹'],
 'song_card.added': ['Added', '已添加'],
 'song_card.added_song_name_to_cloud_playlist_folder_name': ['Added {song_name} to '
                                                             'cloud playlist '
                                                             "'{folder_name}'",
                                                             '已将 {song_name} 添加到云端歌单 '
                                                             "'{folder_name}'"],
 'song_card.added_song_name_to_folder_name': ["Added {song_name} to '{folder_name}'",
                                              "已将 {song_name} 添加到 '{folder_name}'"],
 'song_card.already_saved': ['Already saved', '已保存'],
 'song_card.cloud': ['Cloud', '云端'],
 'song_card.create_new_folder': ['Create New Folder...', '新建文件夹...'],
 'song_card.create_new_folder_2': ['Create New Folder', '新建文件夹'],
 'song_card.export': ['Export', '导出'],
 'song_card.export_song': ['Export song', '导出歌曲'],
 'song_card.exported_song_song_name': ['Exported song {song_name}',
                                       '已导出歌曲 {song_name}'],
 'song_card.failed_to_load': ['Failed to load', '加载失败'],
 'song_card.favorited': ['Favorited', '已收藏'],
 'song_card.folder_folder_name_may_have_been_removed': ["Folder '{folder_name}' may "
                                                        'have been removed',
                                                        "文件夹 '{folder_name}' 可能已被删除"],
 'song_card.folder_not_found': ['Folder not found', '未找到文件夹'],
 'song_card.loading': ['Loading...', '加载中...'],
 'song_card.local': ['Local', '本地'],
 'song_card.my_first_folder': ['My first folder', '我的第一个文件夹'],
 'song_card.please_re_login_to_perform_this_action': ['Please re-login to perform this '
                                                      'action',
                                                      '请重新登录后再执行此操作'],
 'song_card.remove': ['Remove', '移除'],
 'song_card.repeat': ['Repeat', '重复'],
 'song_card.session_expired': ['Session expired', '会话已过期'],
 'song_card.song_files_mp3_m4a_flac_wav_ogg_opus': ['Song Files (*.mp3, *.m4a, *.flac, '
                                                    '*.wav, *.ogg, *.opus)',
                                                    '歌曲文件 (*.mp3, *.m4a, *.flac, '
                                                    '*.wav, *.ogg, *.opus)'],
 'song_card.song_song_name_has_been_added_to_folder_name': ['Song {song_name} has been '
                                                            'added to {folder_name}',
                                                            '歌曲 {song_name} 已添加到 '
                                                            '{folder_name}'],
 'song_card.this_song_is_already_in_all_folders': ['This song is already in all '
                                                   'folders',
                                                   '这首歌已在所有文件夹中'],
 'playing_controller.crossfading_tip': ['Crossfading', '正在交叉淡化']}

def language() -> Language:
    if cfg.language in LANGUAGES:
        return cfg.language
    return 'en_US'


def setLanguage(value: Language) -> None:
    cfg.language = value
    refreshBoundTexts()


def tr(key: str, **kwargs: Any) -> str:
    values = TRANSLATIONS.get(key)
    if values is None:
        text = key
    else:
        lang_index = LANGUAGES.index(language())
        text = values[lang_index] if len(values) > lang_index else None
        if text is None:
            text = values[0] if values else key
    if kwargs:
        return text.format(**kwargs)
    return text


def bindText(widget: object, key: str, **kwargs: Any) -> None:
    if not hasattr(widget, 'setText'):
        return
    setattr(widget, '_southside_text_binding', BoundText(key, kwargs))
    cast(_TextWidget, widget).setText(tr(key, **kwargs))
    _bound_widgets.add(widget)


def setBoundText(widget: object, key: str, **kwargs: Any) -> None:
    bindText(widget, key, **kwargs)


def refreshBoundTexts() -> None:
    for widget in list(_bound_widgets):
        if not _isValidWidget(widget):
            _bound_widgets.discard(widget)
            continue
        binding = getattr(widget, '_southside_text_binding', None)
        if binding is None or not hasattr(widget, 'setText'):
            continue
        cast(_TextWidget, widget).setText(tr(binding.key, **binding.kwargs))
