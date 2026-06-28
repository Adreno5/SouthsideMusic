[English Version](README.md)

# Southside Music

> 能用是及格，值得用才是作品。真正的功夫，都在看不见的时间里。

# 友情链接

- [LINUX DO](https://linux.do) 新的理想型社区

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/Adreno5/SouthsideMusic)

Windows 专用的网易云音乐第三方桌面客户端。支持流媒体播放、逐字歌词、响度均衡、桌面歌词、本地收藏、歌曲导出、Onerad 助手和 SouthsideClient 联动。

> 开发历程见 [SouthsideMusic Story](SouthsideMusic_Story.md)。

---

## 目录

- [它是什么](#它是什么)
- [功能一览](#功能一览)
- [安装](#安装)
- [初次使用](#初次使用)
- [界面说明](#界面说明)
- [高级功能](#高级功能)
- [小贴士](#小贴士)
- [开发](#开发)

---

## 它是什么

Southside Music 是一个 Windows 桌面音乐播放器。登录网易云音乐账号后，可以搜索歌曲和歌单，浏览云端歌单，播放每日推荐，并维护自己的本地收藏夹。

它有独立的音频引擎，支持响度均衡、播放速度、播放音调、立体声 Haas 增强、混响、交叉淡化、静音结尾跳过、预加载、FFT 频谱、逐字歌词、翻译歌词和桌面歌词悬浮窗。

简单来说：它把你网易云账号里的歌，用一种更纯粹、更专注的方式播放出来，同时把进阶控制留在手边。

---

## 功能一览

**播放**

- 网易云全曲库歌曲搜索和歌单搜索
- 首页展示每日推荐歌曲和推荐歌单
- 响度均衡，让不同歌曲接近相同感知音量
- 0.1x 到 3.0x 变速播放
- -12 到 +12 半音的音调调整
- 可选立体声 Haas 增强和混响
- 相邻歌曲交叉淡化
- 自动跳过结尾静音，可调整阈值和检测剩余时间
- 当前歌曲和下一首预加载，改善拖动进度和切歌体验
- 音频输出设备选择

**歌词和视觉**

- 支持 LRC 和 YRC 歌词
- 有 YRC 逐字时间数据时自动逐字高亮
- 有翻译歌词时可开关翻译显示
- 桌面歌词置顶悬浮窗，支持重置位置
- SouthsideMusic 内置实时 FFT 频谱可视化
- 当前歌曲封面参与背景色混合
- 支持明暗主题、英文和简体中文界面

**管理**

- 应用本地收藏夹
- 侧边栏显示网易云云端歌单
- 在应用内创建本地文件夹和云端歌单
- 将歌曲添加到本地文件夹或云端歌单
- 文件夹内支持批量选择、添加到播放列表、添加到文件夹、移除
- “库”页面汇总所有本地收藏歌曲
- 在首页、库、收藏夹、搜索结果中将歌曲插入当前播放之后
- 导出歌曲为音频文件，并写入封面、歌词、专辑、歌手和元数据

**助手和客户端联动**

- Onerad 助手侧栏，支持流式输出和工具调用确认
- 支持 OpenAI 兼容 Chat Completions、OpenAI Responses 和 Anthropic 服务配置
- API Key 使用 Windows 用户数据保护加密保存
- SouthsideClient WebSocket 桥接，端口为 `15489`
- 向 SouthsideClient 发送歌词、封面、播放位置、播放状态和 FFT 数据
- 接收 SouthsideClient 的基础播放控制：暂停/继续、跳转、下一首、上一首

**自动更新和诊断**

- 从应用内检查 GitHub Releases 并启动更新流程
- 启动时检查 FFmpeg、Python 运行时、音频输出、网络和 OpenGL
- 缺少 FFmpeg 时可自动下载
- 未捕获异常会弹出带 traceback 的错误窗口
- `F3` 可切换调试覆盖层

---

## 安装

从 [Releases 页面](https://github.com/Adreno5/SouthsideMusic/releases) 的最新版本下载 `SouthsideMusic_win64_setup.exe`，运行安装即可。

启动时应用会检查 FFmpeg、Python 运行时、音频输出、网络访问和 OpenGL。如果缺少 FFmpeg，依赖检查窗口可以自动下载安装。

> SouthsideMusic 仅支持 Windows。

---

## 初次使用

### 1. 登录

打开软件后，在侧边栏账号区域或首页账号区域登录网易云：

- **Cell Phone / 手机号** - 输入手机号，获取验证码后填入
- **QR Code / 二维码** - 用网易云音乐 App 扫描二维码，然后确认已扫码

匿名会话可以启动应用，但大多数有用的云端功能都需要登录真实网易云账号。

### 2. 搜索

点击标题栏搜索框，输入关键词并回车。在搜索页面可以用搜索类型选择器切换 **Songs** 和 **Playlists**。

歌曲结果可以直接播放，也可以添加到本地或云端文件夹。歌单结果会以云端歌单卡片显示。

### 3. 使用首页

登录后首页会展示每日推荐歌曲和推荐歌单。点击歌曲播放，点击封面可插入到当前歌曲之后。

### 4. 浏览文件夹和歌单

左侧栏包含：

- **Daily Recommend / 每日推荐**
- **Local / 本地** 收藏夹
- **Cloud / 云端** 网易云歌单
- **Refresh / 刷新**、**Library / 库**、**Settings / 设置** 和 **Add folder / 添加文件夹**

点击文件夹或歌单会在收藏页面打开。用 **Replace Playlist / 替换播放列表** 可以把它设为当前队列，用 **Add to Playlist / 添加到播放列表** 可以追加到队列。

### 5. 管理本地收藏

在 Local 区域使用 **Add folder / 添加文件夹** 创建本地文件夹。右键或使用歌曲卡片操作可以添加歌曲、导出歌曲、移除歌曲，也可以调整本地文件夹内歌曲顺序。

---

## 界面说明

### 首页

首页显示当前登录用户，并加载每日推荐歌曲和推荐歌单。

### 搜索

搜索支持 **Songs / 歌曲** 和 **Playlists / 歌单**。向下滚动会继续加载结果。

### 库

库页面把所有本地收藏夹中的歌曲汇总到一个视图，封面和详情会懒加载。

### 收藏夹

收藏夹页面显示当前选中的本地文件夹或云端歌单。支持替换当前播放列表、追加到播放列表、批量选择、添加到文件夹、添加到播放列表、移除，以及本地歌曲排序。

### 播放区

点击底部播放栏会展开播放面板。面板包含：

- 专辑封面、歌曲名和歌手
- 滚动歌词，有逐字数据时逐字高亮
- 有翻译歌词时显示翻译开关

底部控制栏常驻显示封面、当前歌词、进度、FFT 频谱、上一首/播放/下一首和播放列表按钮。拖动顶部进度线可以跳转播放位置。

### 播放列表面板

点击底部控制栏的播放列表按钮，会打开右侧播放列表面板。可以播放队列中的歌曲、调整顺序、导出、重复单项、移除单项或清空队列。

### Onerad

点击标题栏聊天按钮可以打开 Onerad 侧栏。先在设置中配置模型服务。Onerad 可以回答应用相关问题，也可以在明确确认后执行支持的应用操作，例如搜索、打开文件夹或修改设置。

### 设置

设置按可折叠分组组织：

| 分组 | 主要选项 |
| --- | --- |
| 应用 | 语言、下载并发线程数 |
| 播放 | 播放顺序、立体声、Haas 延迟、混响、智能跳过、交叉淡化、速度、音调、跳过阈值、跳过检测剩余时间、输出设备 |
| LLM | 提供商、API 格式、API Key、Base URL、模型映射 |
| 窗口 | 背景混合比例 |
| 歌词 | 歌词平滑系数、加速度平滑系数 |
| 桌面歌词 | 启用桌面歌词、重置位置 |
| FFT | 启用频谱、FFT 平滑、SouthsideMusic 侧 FFT 系数、SouthsideClient 侧 FFT 系数 |
| 响度 | 目标 LUFS、参考值 |
| 连接 | SouthsideClient 连接状态、已发送/已接收大小、延迟、连接/断开 |

---

## 高级功能

### 逐字歌词

当网易云返回 YRC 逐字时间数据时，歌词会使用逐字高亮。否则回退到 LRC 行级歌词。

### 翻译歌词

如果当前歌曲有翻译歌词，展开播放面板会显示翻译开关。

### 桌面歌词

在设置中启用桌面歌词后，会出现置顶悬浮歌词窗口。窗口位置会保存；拖到屏幕顶部可以吸附，也可以在设置中使用 **Reset Position / 重置位置**。

### 响度均衡

Target LUFS 控制感知播放响度。数值越低越安静，默认目标为 `-16`。

### 交叉淡化和智能跳过

交叉淡化会混合相邻歌曲的衔接。智能跳过会在歌曲末尾按配置的阈值和剩余时间窗口跳过静音。

### 歌曲导出

右键歌曲并选择 **Export / 导出**。支持 `.mp3`、`.m4a`、`.flac`、`.wav`、`.ogg`、`.opus` 等扩展名。导出时会尽量写入封面、歌词、专辑、歌手和曲目信息。

### SouthsideClient 桥接

SouthsideMusic 会在 `15489` 端口启动 WebSocket 服务，用于连接 SouthsideClient。桥接会发送播放状态、歌词、进度、封面/信息和 FFT 数据，也接收基础播放控制。

---

## 小贴士

- **空格键** 暂停 / 继续播放
- **F3** 切换调试覆盖层
- 点击底部播放栏中进度线下方区域可展开/收起播放面板
- 歌曲准备好后，可以拖动底部进度线跳转播放位置
- 在多个页面中点击歌曲卡片封面，可把歌曲插入当前播放之后
- 用 **Library / 库** 可以一次看到所有本地收藏歌曲
- 在设置的 **Language** 中可以立即切换英文和简体中文
- 如果感觉音量不统一，可以在设置里调整 **Target LUFS**，并按提示重启生效

---

## 开发

### 环境要求

- Windows
- 项目环境要求 Python `>=3.13`
- `uv`
- 初次搭建和下载依赖需要网络连接

`setup_workspace.py` 当前会准备 Python 3.14.2 嵌入式运行时、free-threaded worker Python（`3.14t`）和只安装 Nuitka 的干净构建 venv。

### 初次搭建

```bash
git clone https://github.com/Adreno5/SouthsideMusic.git
cd SouthsideMusic
python setup_workspace.py
```

`setup_workspace.py` 会检查环境，安装或验证 `uv`，执行 `uv sync`，准备嵌入式 Python，安装嵌入式依赖，准备构建 venv，验证运行时，并在确认后安装 Inno Setup。

### 从源码运行

```bash
uv run src/main.py
```

如果环境已经激活，也可以运行：

```bash
python src/main.py
```

### 验证

```bash
python -m py_compile src/main.py
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
```

目前没有正式测试套件。`src/test.py` 是手动 API 探索脚本，不是 pytest 测试。

### 构建

```bash
build.bat
```

`build.bat` 会删除旧输出，用 Nuitka 构建 `launcher.py`，复制嵌入式 Python、free-threaded Python、`src`、字体、图标、图片和运行时元数据，重新生成图标，然后在存在 `ISCC.exe` 时运行 Inno Setup。

构建产物：

```text
build.result\
├── raw\          可直接运行的便携目录
└── installer\    已安装 Inno Setup 时生成安装器
```

如果没有找到 Inno Setup，`build.result\raw\` 中仍会保留便携版文件。

### 技术栈

| 层级 | 技术 |
| --- | --- |
| 界面 | PySide6 + PySide6-Fluent-Widgets |
| 窗口 | qframelesswindow + hPyT |
| 音频 | pydub + sounddevice |
| 数学/DSP | NumPy + SciPy |
| 元数据 | mutagen |
| API | 内置 `pyncm` 网易云音乐客户端 |
| 网络 | requests + Tornado WebSocket server |
| 助手 | OpenAI SDK + Anthropic SDK |
| 打包 | Nuitka + Inno Setup |
| 字体 | HarmonyOS Sans SC |

### 配置和数据

- 持久化应用设置保存在 `config.json`
- 本地收藏数据和运行时缓存位于 `data/`
- 旧版 `config.pkl` 会迁移并删除
- LLM API Key 使用 Windows `CryptProtectData` 加密
- 运行时会清理 `ffcache*` 等缓存文件

### 许可

PolyForm Noncommercial License 1.0.0 - 详见 [LICENSE](../LICENSE)。

本软件仅供个人学习、研究和私人娱乐使用，禁止任何商业用途。通过本软件导出的音乐文件由用户自行负责，请勿传播或倒卖导出的音频文件。开发者不承担任何因不当使用产生的责任。
