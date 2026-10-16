[English Version](README.md)

# 南方音乐

> 一款精工打造的 Windows 网易云音乐桌面工作站：精准音频控制、实时歌词、可视化工具与深度桌面联动，全部汇聚于一个毫不妥协的播放器。

> 能用是及格，值得用才是作品。真正的功夫，都在看不见的时间里。

## 友情链接

- [LINUX DO](https://linux.do) - 新的理想型社区

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/Adreno5/SouthsideMusic)

Southside Music 是一款以自研音频引擎为核心的 Windows 专用网易云音乐第三方桌面客户端。它将响度均衡、变速变调、逐字歌词与桌面歌词、手动歌词对轴、频谱与节拍可视化、本地收藏、发现与重新发现、歌曲评论、歌曲与歌词动画导出、Onerad 助手、Windows 媒体联动和 SouthsideClient WebSocket 桥接整合在同一个桌面应用中。

> 想了解项目一路走来的过程，可以阅读 [SouthsideMusic Story](SouthsideMusic_Story.md)。

---

## Star History

<a href="https://www.star-history.com/?repos=Adreno5%2FSouthsideMusic&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=Adreno5/SouthsideMusic&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=Adreno5/SouthsideMusic&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=Adreno5/SouthsideMusic&type=date&legend=top-left" />
 </picture>
</a>

---

## 目录

- [项目简介](#项目简介)
- [功能亮点](#功能亮点)
- [安装](#安装)
- [快速开始](#快速开始)
- [功能指南](#功能指南)
- [快捷键与使用技巧](#快捷键与使用技巧)
- [文档](#文档)
- [开发](#开发)
- [许可](#许可)

---

## 项目简介

Southside Music 面向希望在桌面上使用网易云音乐、同时保留播放控制权的用户。登录后可以搜索曲库、浏览云端歌单、收听每日推荐，也可以用本地收藏夹重新组织自己的音乐。

播放器使用独立音频管线，而不是把听歌体验交给网页容器。响度、均衡器、速度、音调、立体声宽度、混响、歌曲衔接、静音检测、预加载、歌词和可视化都由应用直接处理。

繁重的工作被移出界面线程：解码、交叉淡化渲染、响度分析和图片解码都在 free-threaded worker 进程中执行，PCM 音频缓冲落在磁盘而不是内存。安装包自带运行时，运行时不依赖系统 Python。

Southside Music 是独立、非商业项目，并非网易云音乐官方客户端。

---

## 功能亮点

### 音频

- 可调整目标 LUFS 的响度均衡
- `0.1x` 到 `3.0x` 变速播放，并通过时间伸缩保持音调
- `-12` 到 `+12` 半音的独立变调
- 图形化均衡器曲线编辑器，支持实时频谱反馈
- 可选立体声 Haas 加宽和混响
- 智能交叉淡化，支持自适应时长、过渡曲线、节拍与调性匹配、自动增益控制
- 可选歌词交接，让上一首的歌词延续到下一首，而不是闪黑
- 结尾静音检测，可调整阈值和检测时间窗口
- 播放中即时切换音质，从标准音质到无损、Hi-Res 和超清母带
- 预加载当前歌曲和下一首，改善拖动进度与切歌体验
- 分片并发流式下载，线程数可调
- 自由选择音频输出设备

### 歌词与视觉

- 支持 LRC 行级时间和 YRC 逐字时间
- 有 YRC 数据时自动逐字高亮
- 有翻译数据时可即时开关翻译歌词
- 手动歌词编辑器：粘贴自己的歌词，跟着音乐敲空格对轴，补齐网易云没有时间轴的歌曲
- 桌面歌词置顶悬浮窗，支持保存位置、顶部吸附和位置重置
- 实时 FFT 频谱可视化和节拍检测驱动的视觉脉冲
- 使用当前封面参与主题背景色混合，强度可调
- 导出歌词动画，可设置格式、码率、帧率、对齐、行数、背景、动画、翻译和音频
- 支持明暗主题、英文和简体中文界面

### 曲库与导出

- 网易云歌曲和歌单搜索，滚动增量加载
- 首页心动模式、私人漫游、私人雷达和相似歌曲入口
- 每日推荐歌曲和用户云端歌单与本地收藏夹共用一个侧边栏
- 批量选择、替换播放列表、追加队列、移除和本地排序
- 导出歌曲时写入封面、歌词、专辑、歌手和曲目元数据
- 可以把当前播放效果渲染进导出文件，也可以保留原始音频
- 在搜索、收藏和播放页面把歌曲插入当前播放之后
- 歌曲评论、楼中楼回复、头像加载、分页浏览与评论发布
- “重新发现收藏”页面，把很久没听的收藏重新翻出来

### 助手与联动

- Onerad 助手侧栏，支持流式输出、Markdown 与公式渲染和明确的工具调用确认
- 支持 OpenAI 兼容 Chat Completions、OpenAI Responses 和 Anthropic 服务，可配置多个服务商与模型映射
- API Key 使用 Windows 数据保护 API 加密
- Windows SMTC 联动：播放信息出现在系统媒体浮出控件中，并响应媒体按键
- SouthsideClient WebSocket 桥接端口为 `15489`
- 向 SouthsideClient 发送歌词、封面、歌曲信息、播放进度、状态和 FFT
- 接收 SouthsideClient 的暂停/继续、跳转、下一首和上一首控制

### 可靠性

- 在应用内检查 GitHub Releases 并处理原地更新
- 启动时检查 FFmpeg、Python 运行时、音频输出、网络和 OpenGL
- 缺少 FFmpeg 时提供引导下载
- 首次启动的 bootstrap 会自动修复内置运行时中缺失的依赖包
- 自动清理过旧或超过空间上限的可重新下载缓存
- 未捕获异常通过弹窗显示 traceback 详情，配置损坏时回退到默认值启动
- 可选调试模式，`F3` 调试覆盖层、`F4` 性能覆盖层和启动耗时报告
- 长时间无操作后自动削弱动画以节省资源
- 单实例启动器，重复启动会被拒绝

---

## 安装

1. 打开 [Releases 页面](https://github.com/Adreno5/SouthsideMusic/releases)。
2. 下载最新的 `SouthsideMusic_<版本号>_win64_setup.exe`。
3. 运行安装程序，然后启动 Southside Music。

安装程序会写入自己的应用目录，因此需要管理员权限。内置 Python 运行时、字体和界面资源会一并安装，升级时 `data\` 目录中的用户数据不会被覆盖。

首次启动时会执行依赖检查。如果缺少 FFmpeg，依赖窗口可以自动下载并安装；缺失的 Python 依赖包会由内置 bootstrap 自动补齐。

> Southside Music 目前仅支持 Windows。

---

## 快速开始

### 1. 登录

在首页或侧边栏的账号区域选择登录方式：

- **Cell Phone / 手机号** - 输入手机号和验证码。
- **QR Code / 二维码** - 使用网易云音乐 App 扫码并确认登录。
- **Cookie** - 从浏览器 DevTools 复制 `MUSIC_U` Cookie 值并粘贴（F12 → Application → Cookies → music.163.com）。

匿名会话可以启动应用，但每日推荐、云端歌单和大多数账号功能需要登录网易云音乐。

### 2. 查找音乐

点击标题栏搜索框，输入关键词并按回车。在搜索页面切换 **Songs / 歌曲** 和 **Playlists / 歌单**，向下滚动会继续加载结果。

### 3. 组织播放队列

点击歌曲会立即播放。在支持的歌曲卡片上点击封面，可以把歌曲插入当前播放之后。打开文件夹或歌单后，可以替换当前队列，也可以追加到队列末尾。

### 4. 管理收藏

在 Local / 本地区域使用 **Add folder / 添加文件夹** 创建应用管理的收藏夹。歌曲操作支持添加、移除、导出和排序；云端歌单则独立显示在 Cloud / 云端区域，每日推荐位于本地收藏夹上方。

---

## 功能指南

### 播放区

点击底部播放栏可以打开完整播放页面，其中包含封面、歌曲信息、同步歌词、翻译控制、音质切换、评论入口、歌词编辑和歌词动画导出。紧凑控制栏始终保留播放进度、当前歌词、FFT、上一首/播放/下一首和队列入口。

歌曲准备好后可拖动进度线跳转。打开队列面板后，可以播放、排序、导出、单曲循环、移除或清空队列中的歌曲。

### 交接与交叉淡化

交叉淡化会分析相邻歌曲，并在当前歌曲接近结尾时准备过渡。高级设置提供过渡强度、曲线、最大时长、BPM 窗口、节拍匹配、调性匹配和自动增益控制。开启歌词交接后，歌词会跨过衔接继续滚动。如果需要保留精确的曲目边界，可以关闭交叉淡化。

### 歌词、歌词编辑与歌词动画

YRC 歌词使用逐字高亮；没有 YRC 时间数据时自动回退到 LRC 行级显示。网易云提供翻译数据时，可以同时显示翻译歌词。

歌词编辑器针对当前歌曲打开，分两步：先粘贴或改写歌词，再跟着音乐敲空格对轴。结果会保存在该歌曲上，之后歌词页面和桌面歌词都会使用这份时间轴。

播放页面的导出按钮可以把同步歌词渲染为 `.mp4`、`.av1`、`.mkv` 或 `.webm`。导出选项包括码率、帧率、显示行数、逐字时间、翻译、对齐方式、纯色背景、滚动动画，以及是否包含原始音频。

### 桌面歌词

在设置中启用桌面歌词后，会打开置顶悬浮歌词窗口。窗口位置会跨启动保存；拖到屏幕顶部可以吸附，也可以在设置中使用 **Reset Position / 重置位置**。

### 收藏与歌曲导出

收藏页面既可以显示本地文件夹，也可以显示网易云云端歌单。本地文件夹支持排序和移除；两个视图都可以替换队列、追加队列，并在可用时执行批量操作。

歌曲导出支持 `.mp3`、`.m4a`、`.flac`、`.wav`、`.ogg` 和 `.opus`。Southside Music 会把可用的封面、歌词、专辑、歌手和曲目信息写入导出文件，导出时可以选择保留原始音频，也可以把当前播放效果渲染进去。

### 重新发现收藏

首页顶部提供独立的“重新发现收藏”横向薄卡片，与下方四个播放模式区分开，点击后进入独立页面，浏览全部本地收藏。收藏夹和云端歌单页只保留“重新发现”按钮，从该入口打开时只使用当前列表。“换个顺序”偏向低播放次数和较久没有听过的歌曲，“很少听到”优先播放次数最少的歌曲，“很久没听”找回以前播放过的收藏。最近 24 小时听过的歌曲和当前歌曲会尽量排在后面。

页面每批显示 40 首，向下滚动自动追加，直到当前收藏全部显示；已显示的歌曲保持顺序，不会在加载时重复加入。歌曲行展示封面、歌手、播放次数与上次听歌时间。“换个排序”重新排列，“播放全部”使用完整结果作为播放队列，也可以点击某首开始播放，点击封面加入当前歌曲之后。返回按钮回到原来的首页或收藏夹页，收藏夹本身的排序不受影响。

旧播放次数会立即参与挑选。听歌时间从本次更新起记录在 `data/listening_history.json`：实际播放满 30 秒记一次，短歌听满一半；暂停和快速跳过不记听歌时间。旧歌曲没有时间记录时，会明确显示“上次听歌时间未记录”。

### 评论

播放页面的评论按钮会打开当前歌曲的评论页面，支持楼中楼回复、头像和分页浏览，登录后可以直接在页面上发布评论。

### Onerad

先在设置中配置模型服务，然后点击标题栏聊天按钮。Onerad 支持流式回答，也可以请求执行搜索、整理收藏夹和歌单、控制播放、修改设置或管理模型服务等应用操作。会影响应用的工具调用需要用户确认，回答以 Markdown 渲染并支持公式。

### SouthsideClient

Southside Music 会在本机 `15489` 端口启动 WebSocket 服务。连接会向 SouthsideClient 发送播放状态、歌曲数据、歌词、封面、进度和 FFT 数据，并接收基础播放控制。设置页面会显示连接状态、流量、延迟以及连接/断开按钮。

具体的数据包结构见 [Music bridge protocol / 音乐桥接协议](music_bridge_protocol.md)。

### 设置

设置页面默认保持精简。启用 **Advanced Settings / 高级设置** 后，会显示完整的音频、FFT、LLM、存储和歌曲衔接参数；下表中标注为“仅高级”的分组只在该模式下出现。

| 分组 | 主要选项 | 仅高级 |
| --- | --- | --- |
| 应用 | 语言、SMTC 服务、AFK 时长、调试模式 | 部分 |
| 窗口 | 封面颜色背景强度 | 否 |
| 播放 | 播放顺序、输出设备、速度、音调、智能跳过 | 部分 |
| 交接 | 交叉淡化强度、曲线、时长、BPM 窗口、节拍、调性、增益匹配、歌词交接 | 否 |
| 播放效果 | 立体声宽度、混响、均衡器曲线 | 部分 |
| 桌面歌词 | 显示开关、吸附方式、位置重置 | 否 |
| 缓存存储 | 自动清理、缓存时限、空间上限、下载并发数 | 部分 |
| 响度 | 目标 LUFS 与参考值 | 是 |
| 动画 | 歌词动画类型与平滑参数 | 是 |
| 歌词 | 行间距 | 否 |
| FFT | 频谱、平滑、缓冲、大小和客户端缩放 | 是 |
| 节拍检测 | 节拍检测与视觉闪烁 | 是 |
| LLM | 服务商、API 格式、密钥、Base URL 和模型 | 是 |
| 连接 | SouthsideClient 状态、流量、延迟和控制 | 是 |

---

## 快捷键与使用技巧

- 按 **空格键** 暂停或继续播放。
- 开启高级设置，再开启 **应用 > 调试模式**，即可按 **F3** 打开调试覆盖层、按 **F4** 打开性能与内存覆盖层。调试模式默认关闭；开启后，每次启动都会将各阶段耗时以 PNG 和 JSON 保存到 `data/debug/launch`。
- 点击底部播放栏中进度线下方的区域，展开或收起播放页面。
- 歌曲加载完成后，拖动进度线可以跳转播放位置。
- 在支持的列表中点击歌曲封面，把歌曲插入当前播放之后。
- 在播放页面使用音质按钮切换当前歌曲的流媒体音质。
- 搜索框收起时可以直接拖动它移动窗口，点击后才会展开输入。
- 在设置中修改 **Language**，立即切换英文和简体中文。
- 如果不同歌曲的感知音量不一致，可以调整 **Target LUFS**。
- 如果听歌时动画过早变弱，可以增大 **AFK Timeout / AFK 时长**。

---

## 文档

- [Music bridge protocol](music_bridge_protocol.md) - Southside Music 与 SouthsideClient 之间的数据包约定
- [NetEase PC API notes](netease-pc-api.md) - 网易云音乐 PC 客户端接口协议记录，用于对齐内置 `ncm` 客户端
- [隐私政策](privacy_policy.md) 与 [服务条款](terms_of_service.md)
- [SouthsideMusic Story](SouthsideMusic_Story.md) - 项目是怎么走到今天的
- [Licenses](licenses) - 随应用分发的第三方许可文本

---

## 开发

### 环境要求

- Windows
- Python `>=3.13`（工作区以 `3.14` 校验，打包后的应用运行在 Python `3.14.7` 与 free-threaded `3.14t` worker 上）
- [`uv`](https://docs.astral.sh/uv/)
- 初次搭建环境和下载依赖时需要网络连接

### 准备工作区

```bash
git clone https://github.com/Adreno5/SouthsideMusic.git
cd SouthsideMusic
python setup_workspace.py
```

搭建脚本会同步 `uv` 环境，准备 Python 3.14.7 嵌入式运行时，安装 free-threaded `3.14t` worker 环境，创建独立的 Nuitka 构建环境，验证运行时，并按需安装 Inno Setup。

### 从源码运行

```bash
uv run src/main.py
```

如果已经激活项目环境，也可以运行：

```bash
python src/main.py
```

### 验证

```bash
python -m py_compile src/main.py
uv run python -m compileall -q src tests scripts beat_test.py
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
uv run python -m unittest discover -s tests
```

`tests/` 是真实测试目录，基于 `unittest`，不使用 pytest。可以用上面的命令跑全部测试，也可以直接运行单个文件，例如 `uv run python tests/test_lyric_formats.py`。

CI（`.github/workflows/ci.yml`）会在装有 FFmpeg 的 Windows 上执行编译、lint、格式检查和测试。`src/test.py` 是手动 API 探索脚本，不属于测试套件。

### 构建

```bat
build.bat
```

构建脚本会使用 Nuitka 编译 `launcher.py`，组装嵌入式运行时和应用资源，重新生成图标，并在存在 `ISCC.exe` 时调用 Inno Setup。

```text
build.result\
├── raw\          可直接运行的便携目录
└── installer\    已安装 Inno Setup 时生成带版本号的安装器
```

如果没有 Inno Setup，便携构建仍会保留在 `build.result\raw\`。正式发布由 `.github/workflows/` 中的 GitHub Actions 工作流完成，发布说明从 `release-notes/<tag>.md` 读取。

### 项目结构

```text
bootstrap.py        首次启动时为内置 Python 运行时补齐依赖
launcher.py         Nuitka 入口：单实例检查后启动应用
build.bat           Windows 构建与打包流程
setup_workspace.py  工作区搭建脚本
src/
  main.py          应用入口和启动生命周期
  core/            音频引擎、配置、模型、歌词、主题、LLM 和桥接服务端
  services/        事件总线和更新检查
  views/           页面、卡片、面板、窗口和控件
  ncm/             内置网易云音乐 API 客户端
tests/             基于 unittest 的测试目录
scripts/           图标生成、音频实验和逆向分析辅助脚本
docs/              中英文项目文档
data/              运行时缓存、本地收藏和听歌记录
fonts/             内置 HarmonyOS Sans SC 字体
icons/, images/    应用界面资源
release-notes/     发布工作流读取的发布说明
config.json        持久化用户配置
```

### 技术栈

| 层级 | 技术 |
| --- | --- |
| 界面 | PySide6 + PySide6-Fluent-Widgets |
| 窗口 | qframelesswindow + hPyT |
| 音频与 DSP | sounddevice + pydub + NumPy + SciPy |
| 元数据 | mutagen |
| 网易云 API | 内置 `ncm` 客户端 |
| 网络 | requests + Tornado WebSocket server |
| 助手 | OpenAI SDK + Anthropic SDK + markdown-it-py + ziamath |
| Windows 联动 | winrt（SMTC）+ pywin32（DPAPI）+ psutil |
| 打包 | Nuitka + Inno Setup |

### 配置与数据

- 持久化设置保存在 `config.json`。
- 本地收藏、播放次数、听歌记录和运行时缓存保存在 `data/` 下。
- 旧版 `config.pkl` 数据会迁移并删除。
- LLM API Key 使用 `CryptProtectData` 为当前 Windows 用户加密。
- 可重新下载的缓存可以按文件年龄和总占用空间自动清理。
- 调试模式下，启动耗时和性能报告会写入 `data/debug/`。

---

## 许可

Southside Music 使用 [PolyForm Noncommercial License 1.0.0](../LICENSE)。

本软件仅供个人学习、研究和私人娱乐使用，禁止商业用途。通过应用导出的音乐由用户自行负责，不得传播或倒卖导出的音频文件。
