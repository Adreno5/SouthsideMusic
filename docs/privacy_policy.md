# Southside Music - Privacy Policy

Last updated: 2026-10-01

[简体中文](#南方音乐--隐私政策)

## 1. Summary

Southside Music is a local desktop application. The developer does **not** operate any server
that collects your data, and the Application contains **no analytics, tracking, or telemetry**
that reports to the developer. Data the Application handles stays on your device unless you
directly connect to a third-party service (such as NetEase CloudMusic or an LLM provider you
configure).

## 2. Scope

This policy explains what Southside Music stores locally, what leaves your device over the
network, and the choices available to you. It applies to the Application itself, not to the
third-party services it connects to.

## 3. Data Stored on Your Device

The Application stores the following on your own computer. The developer has no access to it.

- **`config.json`** - application settings, UI preferences, your LLM provider configuration
  (including encrypted API keys), and cached login/session state returned by NetEase.
- **`data/` directory** - runtime caches, including downloaded music, cover images, lyrics,
  crossfade analysis data, and temporary files.
- **`favorites.json`** - your locally saved favorites and playlists.
- **`update.json`** - the last known release information used for update checks.
- **Log files** - diagnostic logs written locally for troubleshooting.

## 4. Sensitive Data Handling

- **NetEase login** - your `MUSIC_U` cookie and related session data are stored locally in
  `config.json` so you stay logged in.
- **LLM API keys** - keys you enter for Onerad assistant providers are encrypted with the
  Windows Data Protection API (DPAPI) before being written to `config.json`. They are decrypted
  only in memory on your device when needed.
- Neither the developer nor anyone else receives these secrets. You are responsible for the
  physical and account security of your own machine.

## 5. What Leaves Your Device

The Application contacts the following endpoints. No analytics or advertising data is sent to
the developer.

- **NetEase CloudMusic services** - to log in, search the catalog, stream audio, fetch lyrics
  and comments, and read cloud playlists. Your account credentials and requests are handled by
  NetEase under its own privacy policy.
- **GitHub** (`api.github.com`, `codeload.github.com`) - to check for and download updates from
  the project's public Releases page.
- **Your configured LLM provider** - when you use the Onerad assistant, the prompts and any
  application context you choose to send (for example the current song, lyrics, or your
  question) are transmitted to the provider you configured (OpenAI-compatible, OpenAI
  Responses, or Anthropic), under that provider's policy.
- **FFmpeg download source** - only when you choose to download the missing FFmpeg dependency.
- **Local WebSocket bridge** (`localhost:15489`) - this is a loopback connection on your own
  machine. Playback state, lyrics, cover, progress, and FFT data are streamed to a locally
  connected SouthsideClient and never relayed by Southside Music to any remote server.

## 6. No Analytics or Advertising

Southside Music does not embed analytics SDKs, advertising SDKs, crash-reporting services, or
any mechanism that sends usage data to the developer. Internal timers and state emission used
during playback are local-only and are not transmitted off your device unless a connected
third-party service requires them for a function you use.

## 7. Data Retention and Deletion

Because all data is stored locally, you control it entirely:

- Delete individual items through the Application, or remove cache folders under `data/`.
- Delete `config.json` to reset settings and sign-out state.
- Delete `favorites.json` to remove local favorites.
- Uninstalling the Application and deleting its data folders removes all locally stored data.

The developer holds no copy of your data to delete.

## 8. Children's Privacy

The Application is not directed at children and is not intended for use by anyone below the
minimum age required to hold a NetEase CloudMusic account in their jurisdiction. Do not use the
Application if you are not old enough to consent to these services.

## 9. Security

The Application relies on Windows DPAPI for API-key protection and on your operating system for
file and account security. No method of storage or transmission is perfectly secure. You should
keep your system updated and protect your user account.

## 10. International Transfers

Data sent to third-party services (NetEase, GitHub, or an LLM provider you choose) may be
processed in countries other than your own, according to those services' policies. Southside
Music itself performs no cross-border transfer of your data.

## 11. Your Choices

- You may use the Application in anonymous mode without logging in, with reduced features.
- You may decline to configure any LLM provider.
- You may close the local bridge by not using SouthsideClient or by controlling which
  applications can connect to port `15489`.
- You may delete any locally stored data at any time.

## 12. Third-Party Links and Services

This policy does not cover NetEase, GitHub, LLM providers, or any other third-party service.
Review their privacy policies before use.

## 13. Changes to This Policy

This policy may be revised from time to time. The updated version will be published in the
project repository with a revised "Last updated" date.

## 14. Contact

Questions about this policy can be raised through the project's public issue tracker at
<https://github.com/Adreno5/SouthsideMusic>.

---

# 南方音乐 - 隐私政策

最后更新：2026-10-01

## 1. 概要

南方音乐（Southside Music）是一款本地桌面应用。开发者**不运营任何收集你数据的服务器**，
本应用也**不含任何向开发者上报的分析、追踪或遥测**功能。除你主动连接第三方服务（如网易云
音乐或你自行配置的 LLM 服务商）外，本应用处理的数据都保留在你的设备上。

## 2. 适用范围

本政策说明南方音乐在本地存储哪些数据、哪些数据会经网络离开你的设备，以及你拥有的选择。
本政策仅适用于本应用本身，不适用于它所连接的第三方服务。

## 3. 存储在你设备上的数据

本应用将以下内容存储在你自己的电脑上，开发者无法访问。

- **`config.json`** - 应用设置、界面偏好、你的 LLM 服务商配置（含加密后的 API Key），
  以及网易返回的登录/会话缓存状态。
- **`data/` 目录** - 运行时缓存，包括已下载的音乐、封面图片、歌词、交叉淡化分析数据和
  临时文件。
- **`favorites.json`** - 你在本地保存的收藏和歌单。
- **`update.json`** - 用于更新检查的上次已知发行版信息。
- **日志文件** - 为排查问题而写入本地的诊断日志。

## 4. 敏感数据处理

- **网易登录** - 你的 `MUSIC_U` Cookie 及相关会话数据存储在本地 `config.json` 中，以便
  保持登录状态。
- **LLM API Key** - 你为 Onerad 助手服务商输入的 Key，在写入 `config.json` 前会使用
  Windows 数据保护 API（DPAPI）加密，仅在需要时于你设备的内存中解密。
- 开发者或其他任何人都无法获取这些机密。你有责任保障自己机器的物理与账号安全。

## 5. 离开你设备的数据

本应用会连接以下端点。不会向开发者发送任何分析或广告数据。

- **网易云音乐服务** - 用于登录、搜索曲库、流式播放、获取歌词与评论、读取云端歌单。
  你的账号凭据和请求由网易按其自身隐私政策处理。
- **GitHub**（`api.github.com`、`codeload.github.com`）- 用于从项目公开的 Releases 页面
  检查并下载更新。
- **你配置的 LLM 服务商** - 使用 Onerad 助手时，提示词以及你选择发送的应用上下文（例如
  当前歌曲、歌词或你的问题）会发送到你配置的服务商（OpenAI 兼容、OpenAI Responses 或
  Anthropic），并受该服务商政策约束。
- **FFmpeg 下载源** - 仅在你选择下载缺失的 FFmpeg 依赖时访问。
- **本地 WebSocket 桥接**（`localhost:15489`）- 这是你自己机器上的回环连接。播放状态、
  歌词、封面、进度和 FFT 数据仅流式发送给本地连接的 SouthsideClient，南方音乐绝不会将其
  转发到任何远程服务器。

## 6. 无分析与广告

南方音乐不内嵌分析 SDK、广告 SDK、崩溃上报服务，也不含任何向开发者发送使用数据的机制。
播放期间使用的内部计时器和状态上报仅在本地进行，除非你使用的某项功能需要所连接的第三方
服务，否则不会离开你的设备。

## 7. 数据保留与删除

由于所有数据都存储在本地，你完全掌控它们：

- 通过应用内操作删除单项数据，或移除 `data/` 下的缓存文件夹。
- 删除 `config.json` 可重置设置和登录状态。
- 删除 `favorites.json` 可移除本地收藏。
- 卸载本应用并删除其数据文件夹，即可移除全部本地存储数据。

开发者不持有你的任何数据副本，因此无需替你删除。

## 8. 儿童隐私

本应用并非面向儿童，也不适用于低于其所在司法辖区持有网易云音乐账号最低年龄的任何人。
如果你未达到同意使用这些服务的年龄，请勿使用本应用。

## 9. 安全

本应用依赖 Windows DPAPI 保护 API Key，并依赖你的操作系统保障文件与账号安全。没有任何
存储或传输方式是绝对安全的。你应当保持系统更新并保护好自己的用户账号。

## 10. 国际传输

发送给第三方服务（网易、GitHub 或你选择的 LLM 服务商）的数据，可能按这些服务的政策在你
所在国家/地区之外处理。南方音乐本身不对你的数据进行跨境传输。

## 11. 你的选择

- 你可以使用匿名模式而不登录，部分功能会受限。
- 你可以选择不配置任何 LLM 服务商。
- 你可以不使用 SouthsideClient，或控制哪些应用能连接 `15489` 端口，从而关闭本地桥接。
- 你可以随时删除任何本地存储的数据。

## 12. 第三方链接与服务

本政策不涵盖网易、GitHub、LLM 服务商或任何其他第三方服务。使用前请查阅它们的隐私政策。

## 13. 本政策的变更

本政策可能不时修订。更新后的版本将发布在项目仓库中，并标注新的"最后更新"日期。

## 14. 联系方式

如对本政策有疑问，可通过项目公开的问题追踪页面提出：
<https://github.com/Adreno5/SouthsideMusic>。
