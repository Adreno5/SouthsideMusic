# Music / Legacy 本地音乐协议 v2

Music 提供 `ws://localhost:15489/`，Legacy 连接。消息为 JSON 文本帧，使用 `option` 区分用途。

Legacy 每次连接发送 `{"option":"bridge_hello","protocol_version":2}`。Music 随即补发歌曲、播放状态、歌词布局、卡片当前歌词和播放列表。未握手的客户端继续使用 v1 的 `cover` / `update_lyric`；新版 Legacy 也能接收旧版 Music。

| option | 方向 | 内容 |
| --- | --- | --- |
| `bridge_hello` | Legacy → Music | `protocol_version`，当前支持 1、2 |
| `main_menu_song` | Music → Legacy | `song_id`、`song_name`、`artists`、`image`（Base64 PNG，允许空字符串） |
| `main_menu_playback` | Music → Legacy | `song_id`、`is_playing`、`position`、`duration`、`ratio` |
| `main_menu_lyric` | Music → Legacy | `song_id`、`index`、`text`、`translation`、`has_yrc`、`yrc_clip_ratio` |
| `lyric_layout` | Music → Legacy | 仅 `layout`，结构见下文 |
| `play_state` / `play_position` | Music → Legacy | 保留给播放控制条及旧版本客户端 |
| `update_fft` / `enable_fft` / `disable_fft` | Music → Legacy | 保留原有频谱协议 |
| `playlist_update` / `playlist_control` | 双向 | 保留原有播放列表协议 |
| `music_control` | Legacy → Music | 保留 `toggle`、`seek`、`next`、`previous` 命令 |
| `ws_ping` / `ws_pong` | 双向 | 保留原有延迟测量协议 |

所有时间使用秒；比例范围为 `[0, 1]`。卡片通过 `song_id` 关联三类消息，丢弃与当前歌曲不符的歌词和状态。卡片当前歌词根据播放位置选取，独立于 Music 歌词页面的滚动、可见行和游戏 HUD 的布局。

## 歌词布局

```json
{
  "option": "lyric_layout",
  "layout": {
    "schema": "southside_lyric_layout_v2",
    "ready": true,
    "position": 12.5,
    "use_yrc": true,
    "translation_enabled": true,
    "current_index": 3,
    "canvas_width": 480,
    "canvas_height": 300,
    "center_y": 150,
    "primary_font_size_px": 18.6667,
    "translation_font_size_px": 13.3333,
    "lines": [{
      "index": 3,
      "time": 12,
      "text": "Current lyric",
      "is_current": true,
      "is_metadata": false,
      "x": 0,
      "baseline_y_from_center": 5,
      "top_y_from_center": -13,
      "bottom_y_from_center": 9,
      "primary_color": {"r": 255, "g": 255, "b": 255, "a": 255},
      "yrc_base_color": {"r": 255, "g": 255, "b": 255, "a": 120},
      "has_yrc": true,
      "yrc_clip_ratio": 0.4,
      "yrc_clip_width": 42,
      "translation": "Translation",
      "translation_x": 0,
      "translation_baseline_y_from_center": 23,
      "translation_color": {"r": 255, "g": 255, "b": 255, "a": 153}
    }]
  }
}
```

坐标与字体大小均为 Music 的 Qt 逻辑像素，颜色分量为 `0..255` 整数。`x` 与 `translation_x` 分别是正文、翻译的左侧坐标；各个 Y 偏移都相对于 `center_y`。行数组保留 Music 的可见行顺序和实际基线、颜色、横向滚动及已平滑的逐字裁剪宽度。Music 绘制与序列化共用同一个行布局函数，Legacy 不重新生成五行布局或压缩行距。

无歌词时仍发送完整画布和字体信息，设置 `ready:false`、`current_index:-1`、`lines:[]`，明确清空游戏歌词；同时用空 `main_menu_lyric.text` 清空卡片歌词。`has_yrc:false` 时忽略逐字裁剪。无封面时仍传送歌曲名称和歌手。

## Legacy 设置

`SouthsideMusicHud` 模块提供卡片、封面、歌手、歌词、翻译、进度条开关，以及卡片最大宽度、高度、垂直位置、背景透明度和歌词字号。默认保持原有卡片尺寸（最大宽度 420、高度 88），进度条默认关闭。关闭整个模块会关闭连接并停止重连。

歌词 HUD 的 `Visual Height Scale` 范围为 `0.25..2`，默认为 `1`。它只对 Legacy 最终渲染做 Y 轴变换，包含字形、翻译、基线、逐字裁剪和 HUD 边界；不改变 X 轴，不修改收到的布局，不回传到 Music，不影响播放时间和卡片。HUD 原有 `Scale` 仍控制整体缩放。

`Text Align Mode` 控制正文和翻译的 Left / Center / Right 对齐。每段文本按自身宽度相对于歌词画布对齐，再叠加 Music 传来的横向偏移；逐字高亮从对齐后的正文左边缘开始裁剪。垂直布局和视觉高度缩放不受对齐模式影响。

## 验证

Music：`.venv/Scripts/python.exe -m unittest discover -s tests -p test_music_bridge.py -v`，覆盖渲染与传输一致、逐字裁剪、翻译、空布局、独立卡片、v1 兼容和暂停状态。

Legacy：`.\gradlew.bat compileJava --rerun-tasks`。实际联调时先启动 Music，再启动 Legacy；检查滚动 Music 歌词、切歌、暂停、翻译开关、Y 轴缩放及断线重连。
