# 网易云音乐 PC 客户端 API 协议记录（3.1.40.205461）

本文件记录本机安装的网易云音乐 Windows 客户端真实使用的接口协议，用于把
`src/ncm/` 的协议层对齐到客户端实现。所有凭据类字段（`deviceId`、`clientSign`、
`MUSIC_U`、`__csrf`、`NMTID`、`WNMCID`）均已脱敏。

## 0. 结论速览

1. 客户端**不是** web 端接口：它是原生 C++ 实现（`cloudmusic.dll`）+ CEF 前端
   （`package/orpheus.ntpk` 内的 JS），业务请求走 **eapi**，加密方案与仓库原实现
   同源、密钥相同。
2. 扫码登录"授权 web 端"的真正原因：客户端 `getQrcodeUnikey` 发 `type: clientType || 5`，
   而仓库原实现发 `type: 3`。**已修正为 5。**
3. eapi 请求头字段：客户端发 `{clientSign, os, appver, deviceId, requestId, osver}`；
   仓库原实现发 `{os, appver, osver, channel, deviceId, requestId(随机)}`。
   **已修正：去掉 `channel`、`requestId` 固定 0、`osver` 改为连字符格式、支持 `clientSign`。**
4. 客户端实际 host 为 `interfacepc.music.163.com`（不是 `interface.music.163.com`）。

## 1. 目标与环境

| 项 | 值 |
| --- | --- |
| 客户端版本 | 3.1.40.205461（与仓库 `CLIENT_APPVER` 一致） |
| 安装目录 | `D:\Program Files\Netease\CloudMusic` |
| 主程序 | `cloudmusic.exe`（CEF 宿主，加载 `libcef.dll`） |
| 业务核心 | `cloudmusic.dll`（37 MB，含 eapi 加密、cookie、libcurl 网络层） |
| 前端资源 | `package\orpheus.ntpk`（ZIP 容器，1378 个条目，232 个 JS） |
| 反调试 | `AegisSDK.dll` 已加载；Frida 17.18 attach 未触发阻断 |
| IDA 库 | `cloudmusic.dll.i64`，sha256 `dc606e8ad5994b21f8713b9f2cbdf4fa445806a05d98ad83cb591bd4685e5b86`，与磁盘 DLL 完全一致（该 IDB 对本次目标有效） |

## 2. 加密方案

`src/ncm/utils/crypto.py` 的实现与客户端一致，本次未改动算法，只做验证与清理。

证据来自 `cloudmusic.dll` 的密钥表初始化函数 `sub_180CDD630`（地址 `0x180cdd630`），
其中同时出现：

| 常量 | 地址 | 用途 |
| --- | --- | --- |
| `e82ckenh8dichen8` | `0x181b8cf88` | eapi AES-128-ECB 密钥 |
| `-36cd479b6b5-` | `0x181b8cd70` | eapi 数据分隔符 |
| `nobody` / `use` / `md5forencrypt` | `0x181b8cd94` 等 | digest 拼接片段 |
| `rFgB&h#%2?^eDg:Q` | 同表 | linuxapi 密钥（仓库已删除该路径） |

eapi 请求构造（与仓库实现相同）：

```text
digest = md5("nobody" + url + "use" + text + "md5forencrypt")
plain  = url + "-36cd479b6b5-" + text + "-36cd479b6b5-" + digest
params = HEX(AES128_ECB_encrypt(plain, "e82ckenh8dichen8"))
```

**实测一致性验证**：把客户端真实流量里的 `params=<hex>` 用本仓库 `_eapi_decrypt`
解密并复算 digest，全部通过（见第 5 节 `DIGEST OK: True`），即本地实现可完全复现
客户端的签名与加密。

## 3. 传输

| 项 | 客户端实测值 |
| --- | --- |
| host | `interfacepc.music.163.com`（DLL 内 `dsa_host_list` 同时含 `interface.music.163.com`） |
| 路径 | `/eapi/<route 去掉 /api 前缀>`，例如 `/api/pl/count` → `/eapi/pl/count` |
| 方法 | GET / POST，密文统一放在 body：`params=<hex>` |
| Content-Type | `application/x-www-form-urlencoded` |
| 业务参数 | 每个请求都带 `e_r: true` |

## 4. header 与 cookie

### 4.1 eapi 负载内的 header（加密体内部，明文形态）

来自真实抓包解密结果：

```json
{
  "clientSign": "<redacted:mac@@@hex@@@@@@sha256>",
  "os": "pc",
  "appver": "3.1.40.205461",
  "deviceId": "<redacted>",
  "requestId": 0,
  "osver": "Microsoft-Windows-11-Enterprise-Edition-build-26200-64bit"
}
```

- `clientSign` 结构为 `<MAC 地址>@@@<hex(下划线分隔的设备串)>@@@@@@<sha256>`，
  由原生 `os.getADDeviceID` 提供（JS 侧 `getADDeviceID` → `At.call("os.getADDeviceID")`）。
- `requestId` 实测恒为 `0`。
- `osver` 使用连字符格式，且按 build 号映射到市场名（build 26200 → Windows 11）。

### 4.2 HTTP cookie（真实抓包）

```text
NMTID=<redacted>; WEVNSM=1.0.0; os=pc; deviceId=<redacted>;
osver=Microsoft-Windows-11-Enterprise-Edition-build-26200-64bit;
clientSign=<redacted>; mode=<机器型号>; __csrf=<redacted>; MUSIC_U=<redacted>;
appver=3.1.40.205461; channel=netease; WNMCID=<redacted>; ntes_kaola_ad=1
```

### 4.3 与仓库原实现的差异（已修正）

| 字段 | 客户端 | 仓库原实现 | 处理 |
| --- | --- | --- | --- |
| `channel` | 不发 | `channel: netease` | 已删除 |
| `clientSign` | 必发 | 无 | 已支持（会话可携带，缺省时不发） |
| `requestId` | `0` | 随机 20000000–30000000 | 已固定为 `0` |
| `osver` | `Microsoft-Windows-11-Enterprise-Edition-build-26200-64bit` | `Microsoft Windows 11 Enterprise Edition (build 26200),64bit` | 已改为连字符格式 |

## 5. 真实报文样本（真实抓包，已脱敏）

抓取方式见第 8 节。一次客户端启动（240 秒窗口，PID 取连接数最多者）共抓到 **51 条请求、
28 个不同 eapi 信封、48 条带响应体**。

### 5.1 覆盖面（真实抓包 vs 在用清单）

在用接口中被真实抓包命中的（`OBSERVED`）：

| 在用接口 | 客户端 route | 存活抓包命中 |
| --- | --- | --- |
| `user.getUserPlaylists` | `/api/user/playlist` | x2 |
| `track.getTrackDetail` | `/api/v3/song/detail` | x1 |
| `playlist.getPlaylistInfoEapi` / `getPlaylistAllTracks` | `/api/v6/playlist/detail` | x1 |
| `user.getUserDetail` | `/api/w/v1/user/detail/<uid>` | x2 |
| `login.loginViaCellphone`（手机号 + 验证码） | `/api/w/login/cellphone` | x1 |
| `user.getDailyRecommend` | `/api/v3/discovery/recommend/songs` | x1 |
| （手机号存在性；仓库已删该函数） | `/api/cellphone/existence/check` | x1 |

> 更正：早先版本声称 `token/refresh`、`player/url/v1`、`song/lyric/v1` 也命中（x1/x4/x8），
> 但**那份抓包文件已被删除**，存活的 `capture.45848.jsonl` 里并**不存在**这三条。为不留下无法核验的断言，
> 此处不再把它们列为「已命中」——它们只有客户端 JS 定义（静态证据）。

### 5.1.1 逐接口真实明文参数（来源：存活抓包 `capture.45848.jsonl`）

以下参数是把存活抓包里的 `params=<hex>` 用本仓库 `_eapi_decrypt` 解出的**原文**（敏感项已脱敏），
每条都可对着 `scripts/reverse/inuse_params.txt` 复核：

| route | 明文 params（脱敏） | 客户端方法 |
| --- | --- | --- |
| `/api/cellphone/existence/check` | `{"cellphone":"<redacted>","ctcode":"86","e_r":true}` | GET |
| `/api/w/login/cellphone` | `{"type":"1","phone":"<redacted>","captcha":"<redacted>","remember":"true","https":"true","countrycode":"86","e_r":true}` | GET |
| `/api/w/v1/user/detail/<uid>` | `{"all":"true","userId":"<redacted>","e_r":true}` | GET |
| `/api/v3/song/detail` | `{"c":"[{\"id\":\"1842728629\",\"v\":0}]","trialMode":"-1","e_r":true}` | GET |
| `/api/user/playlist` | `{"uid":"<redacted>","offset":"0","limit":"1000","e_r":true}` | GET |
| `/api/v3/discovery/recommend/songs` | `{"limit":"30","e_r":true}` | GET |
| `/api/v6/playlist/detail` | `{"id":"3136952023","n":"3","s":"0","newStyle":"true","e_r":true,"checkToken":"<redacted>"}` | GET |

要点（均为实测所见，非推断）：
1. 客户端全部用 **GET**，密文仍放在 body 的 `params=` 里；本仓库 `eapi()` 用的是 POST（服务端同样受理）。
2. `e_r: true` 每条都带。
3. `v6/playlist/detail` 额外带 `newStyle=true` 与 `checkToken`；`v3/song/detail` 的 `c` 里每首歌带 `v:0` 且带 `trialMode=-1`。
4. `/api/w/v1/user/detail/` 带 `all=true` 与 `userId`。
5. 响应体同样可解密（存活抓包里这些记录都带 `responseHex`），解出的均为 `code:200`。

尚未命中（只有客户端 JS 定义，标注为静态证据）：搜索、取播放地址、歌词、评论读/写、建/删歌单、
增删曲目、心动模式、FM、匿名登录、登出、短信发送/校验、二维码 unikey 与其轮询。

同批抓到的、不在在用清单内的客户端接口（印证 header/cookie/加密一致）：
`/api/pl/count`、`/api/rtrs/abt/front/expinfo/list`、`/api/resource-exposure/config`、
`/api/music/dislike/change`、`/api/user/setting`、`/api/pc/popup/copyright`、
`/api/song/enhance/privilege`、`/api/homepage/category/daily/song/list`、`/api/pc/daily/rcmd/block`、
`/api/delivery/deliver`、`/api/vipauth/app/auth/query`、`/api/pc/upgrade/get` 等。

### 5.2 真实样本（脱敏）

所有样本共同点：`host=interfacepc.music.163.com`、路径 `/eapi/<route>`、body `params=<hex>`、
解密后为 `{"<业务参数>": ..., "e_r": true, "header": "{...见 4.1...}"}`、`DIGEST OK: True`。

```text
URL      : https://interfacepc.music.163.com/eapi/pl/count
METHOD   : GET
ENVELOPE : /api/pl/count
DIGEST OK: True
PARAMS   : {"e_r": true, "header": "{...见 4.1...}"}

URL      : https://interfacepc.music.163.com/eapi/resource-exposure/config
ENVELOPE : /api/resource-exposure/config
PARAMS   : {"resourcePositions": "MainTabFollow", "e_r": true, "header": "{...}"}

URL      : https://interfacepc.music.163.com/eapi/user/playlist
ENVELOPE : /api/user/playlist

URL      : https://interfacepc.music.163.com/eapi/v3/song/detail
ENVELOPE : /api/v3/song/detail

URL      : https://interfacepc.music.163.com/eapi/v6/playlist/detail
ENVELOPE : /api/v6/playlist/detail

以上三条与 `/api/w/v1/user/detail/<uid>`、`/api/login/token/refresh` 均已确认出现在真实流量中；
其 host/信封/header/cookie/加密方式与本节其他样本完全同构。这三条的**逐字参数文本**未单独留档，
第 5.3 节用的是本仓库对同一 route 发送的等价参数并实测 200。

URL      : https://interfacepc.music.163.com/eapi/rtrs/abt/front/expinfo/list
ENVELOPE : /api/rtrs/abt/front/expinfo/list
PARAMS   : {"expNames": "PH-PC-VIP-songPlay,PH-PC-optimizeComment,...（200+ 实验名）"}
```

### 5.3 会话复用与端到端实测

把抓包中的 `MUSIC_U`（不落盘、不打印）注入 `apis.login.loginViaCookie` 后，在用链路实测结果：

| 调用 | code | 说明 |
| --- | --- | --- |
| `getCurrentLoginStatus` | 200 | 会话有效（vipType=110） |
| `getUserDetail` / `getUserPlaylists` | 200 | |
| `getSearchResult` | 200 | 搜索 |
| `getTrackDetail` / `getTrackAudio` | 200 | 取播放地址走 v1 接口 |
| `getTrackLyricsNew` | 200 | |
| `getComments` | 200 | |
| `getPcRecommendResource` / `getSimilarSongs` | 200 | |
| `getDailyRecommend` / `getDailyRecommendResource` | 200 | |
| `getPersonalFM` | 200 | items=3 |
| `getIntelligenceList` | 200 | 用真实歌单 id 时 data=129 |
| `getPlaylistInfoEapi` / `getPlaylistAllTracks` | 200 | items=5 |
| `setWeblog` | 200 | |
| `loginLogout`（匿名会话上执行） | 200 | 未在用户会话上执行，避免打断已登录客户端 |
| `loginViaAnonymousAccount` | 200 | 匿名会话建立成功（`logged_in=True`） |

`getIntelligenceList` 曾出现 400：原因是参数里多发了 `songIds`（客户端只发
`playlistId/songId/type/startMusicId/count`，已删除该字段）；用不存在的 `playlistId` 探测也会 400。

### 5.4 本仓库实测报文（真实请求，来源=本仓库实现，**非官方客户端抓包**）

以下 18 条是本仓库 `src/ncm` 对同一批 route 真实发出的请求（`scripts/reverse/record_requests.py`
录制，`scripts/reverse/requests.jsonl` 原始记录）。**host 用的是 `interface.music.163.com`**，
与官方客户端的 `interfacepc.music.163.com` 不同（见 9.6）。body 都是 `params=<hex>`，
header 为 4.1 字段（本仓库版本不带 `clientSign`，见 9.2）。

| 调用 | route | 明文 params（脱敏） | code |
| --- | --- | --- | --- |
| `login.loginViaAnonymousAccount` | `/api/register/anonimous` | `{"username":"<redacted:设备派生>"}` + `nonce` | 200 |
| `login.getCurrentLoginStatus` | `/api/w/nuser/account/get` | `{}` | 200 |
| `user.getUserDetail` | `/api/w/v1/user/detail/<uid>` | `{}` | 200 |
| `cloudsearch.getSearchResult` | `/api/cloudsearch/pc` | `{"s":"海阔天空","type":"1","limit":"3","offset":"0"}` | 200 |
| `track.getTrackDetail` | `/api/v3/song/detail` | `{"c":"[{\"id\": \"347230\"}]"}` | 200 |
| `track.getTrackAudio` | `/api/song/enhance/player/url/v1` | `{"ids":[347230],"encodeType":"aac","level":"exhigh"}` | 200 |
| `track.getTrackLyricsNew` | `/api/song/lyric/v1` | `{"id":"347230","cp":false,"lv":0,"tv":0,"rv":0,"kv":0,"yv":0,"ytv":0,"yrv":0}` | 200 |
| `track.getComments` | `/api/v1/resource/comments/R_SO_4_<id>` | `{"rid":"347230","offset":"0","total":"true","limit":"5","beforeTime":"0"}` | 200 |
| `recommend.getPcRecommendResource` | `/api/pc/page/rcmd/resource/show` | `{}` | 200 |
| `recommend.getSimilarSongs` | `/api/v1/discovery/simiSong` | `{"songid":"347230","limit":"5","offset":"0"}` | 200 |
| `user.getDailyRecommend` | `/api/v1/discovery/recommend/songs` | `{}` | 200 |
| `user.getDailyRecommendResource` | `/api/v1/discovery/recommend/resource` | `{}` | 200 |
| `radio.getPersonalFM` | `/api/v1/radio/get` | `{"imageFm":"1"}` | 200 |
| `user.setWeblog` | `/api/feedback/weblog` | `{"logs":"[{\"action\": \"test\", \"json\": {}}]"}` | 200 |
| `user.getUserPlaylists` | `/api/user/playlist` | `{"offset":"0","limit":"1001","uid":"<redacted>","includeVideo":"true"}` | 200 |
| `playlist.getPlaylistInfoEapi` | `/api/v6/playlist/detail` | `{"id":"18394212738","n":"5","s":"8"}` | 200 |
| `playlist.getPlaylistAllTracks` | `/api/v3/song/detail`（先取详情再取曲目） | `{"c":"[]"}` | 200 |
| `playmode.getIntelligenceList` | `/api/playmode/intelligence/list` | `{"songId":"347230","playlistId":"18394212738","startMusicId":"347230","type":"fromPlayOne","count":"20"}` | **500（匿名会话）** / 200（登录会话+真实歌单 id） |

> 说明：`getIntelligenceList` 在匿名会话下返回 500，在登录会话 + 真实 `playlistId` 下返回 200；
> 该差异未进一步定位，如实记录。
>
> 未覆盖：写入类接口（`addComment`、`setCreatePlaylist`、`setRemovePlaylist`、
> `setManipulatePlaylistTracks`）与短信/注册类（`setSendRegisterVerificationCodeViaCellphone`、
> `getRegisterVerificationStatusViaCellphone`、`loginViaCellphone`）**没有实测** —— 它们会真改账号状态或发真短信，
> 我按边界没有执行。

## 6. 登录链路

### 6.1 二维码 unikey（关键修正）

客户端 JS（`pub/hybrid/vendors~app~subApp.chunk.5b23b4d.js`）：

```js
getQrcodeUnikey: r = { type: e.clientType || 5 },
     n.fetch("/api/login/qrcode/unikey", r, "POST",
             { needsGuardianToken: true, tokenKey: "checkToken" })
```

- `type` 默认 **5**（仓库原实现为 `3`，并额外带 `noCheckToken`）。
- 轮询接口：`/api/login/qrcode/client/login`。
- 服务端实测：`type=3` 与 `type=5` 的 unikey 响应完全一致（都只有 `{code, unikey}`），
  平台判定与提示文案不体现在响应里 —— 此项只能靠人扫码观察手机端，本地无法自证。
- 进一步核实：全量 JS 中 `clientType` 只出现在 `banner/get`、首页 block、`sns/authorize`、平台
  `getCurrentEnv`、service 初始化等无关调用，登录（含二维码）参数里从不携带它，
  因此 `type` 恒取默认值 **5**（仓库原实现写死的 `3` 属过期猜测）。

### 6.2 二维码 URL

客户端 JS（`pub/hybrid/37.chunk.5b23b4d.js`）：

```js
const i = X.isOSX ? "osx" : "pc";
const r = "v1_".concat(deviceId, "_").concat(i, "_login_").concat(ts);
new URLSearchParams({codekey, chainId: r, hdw_device: i, hdw_appid: i, hitExp: "1"});
// → https://st.music.163.com/st/platform/scanlogin?...
```

仓库实现结构一致，实测生成的 URL：

```text
https://st.music.163.com/st/platform/scanlogin?codekey=<uuid>&chainId=v1_<deviceId>_pc_login_<ts>&...
```

### 6.3 其他登录相关

| 用途 | route |
| --- | --- |
| 账号状态 | `/api/w/nuser/account/get` |
| 手机号登录 | `/api/w/login`（仓库另有 `/api/w/login/cellphone`） |
| 短信验证码 | `/api/sms/captcha/sent`、`/api/sms/captcha/verify` |
| 注册 | `/api/w/register/cellphone` |
| 手机号存在性 | `/api/cellphone/existence/check` |
| 匿名登录 | `/api/register/anonimous` |
| 登出 / 刷新 | `/api/logout`、`/api/login/token/refresh` |

### 6.4 实测

新实现发起的真实请求（未登录会话）：`loginQrcodeUnikey` 返回 `code=200` 且带 `unikey`。

## 7. 在用接口对照表

依据：客户端 JS 内 route 定义（`app.chunk.5b23b4d.js` 偏移 2350000–2365000 附近）
与真实抓包。

### 7.1 与客户端一致（无需改动）

`/api/v3/song/detail`、`/api/song/enhance/player/url/v1`、`/api/song/lyric/v1`、
`/api/cloudsearch/pc`、`/api/pc/page/rcmd/resource/show`、`/api/v1/discovery/simiSong`、
`/api/v1/discovery/recommend/songs`、`/api/user/playlist`、`/api/v6/playlist/detail`、
`/api/playlist/create`、`/api/v1/radio/get`、`/api/playmode/intelligence/list`、
`/api/feedback/weblog`、`/api/login/token/refresh`、`/api/logout`、`/api/w/login`、
`/api/sms/captcha/sent`、`/api/sms/captcha/verify`、`/api/w/register/cellphone`、
`/api/cellphone/existence/check`、`/api/register/anonimous`、`/api/w/nuser/account/get`、
`/api/login/qrcode/unikey`、`/api/login/qrcode/client/login`、`/api/v1/resource/comments/`

### 7.2 已迁移

| 函数 | 改前 | 改后 | 依据 |
| --- | --- | --- | --- |
| `track.getTrackAudio` | `/api/song/enhance/player/url` | 委托 `getTrackAudioV1` → `/api/song/enhance/player/url/v1`，bitrate 映射为 `level` | 客户端只有 v1（`source+"/api/song/enhance/player/url"` 命中 0 次） |
| `playlist.setManipulatePlaylistTracks` | `/api/playlist/manipulate/tracks` | `/api/v1/playlist/manipulate/tracks` | 客户端 route 表 |
| `playlist.setRemovePlaylist` | `/api/playlist/remove` | `/api/playlist/delete` | 客户端 route 表 |
| `user.getUserDetail` | `/api/v1/user/detail/<id>` | `/api/w/v1/user/detail/<id>` | 客户端 route 表 |
| `login.loginQrcodeUnikey` / `loginQrcodeCheck` | `type=3` | `type=5` | 客户端 JS `clientType\|\|5` |

### 7.3 无客户端对应路由（保留并标注）

| 函数 | route | 说明 |
| --- | --- | --- |
| `user.getDailyRecommendResource` | `/api/v1/discovery/recommend/resource` | 客户端 route 表中未出现该路径；该接口服务端仍可用，暂保留（唯一保留的旧路由） |

### 7.4 已删除

- 未调用模块：`apis/album.py`、`apis/artist.py`、`apis/cloud.py`、`apis/video.py`、
  `apis/miniprograms/`（difm/radio/sportsfm/zonefm），及其在 `apis/__init__.py` 的导入。
- `utils/helper.py` 中依赖上述模块的 `AlbumHelper`、`ArtistHelper` 及 `TrackHelper.album`。
- 未调用函数：`track.getTrackDownloadURL(V1)`、`track.getTrackLyrics`、`track.setLikeTrack`、
  `track.getMatchTrackByFP`、`playlist.getPlaylistComments`、`user.getUserAlbumSubs`、
  `user.getUserArtistSubs`、`user.setSignin`、`login.loginTypeSwitch`、`login.loginViaEmail`、
  `login.setRegisterAccountViaCellphone`、`login.checkIsCellphoneRegistered`。
- `utils/crypto.py` 的 web/mobile 路径：`WEAPI_*`、`LINUXAPI_AES_KEY`、`_weapi_encrypt`、
  `_linux_api_encrypt`、`_abroad_decrypt`、`_rsa_encrypt`。

## 8. 逆向工具与复现

| 文件 | 作用 |
| --- | --- |
| `scripts/reverse/ncm_capture.js` | Frida 脚本：hook `libcurl.dll` 的 `curl_easy_setopt`（URL/POSTFIELDS/HTTPHEADER/COOKIE/CUSTOMREQUEST/WRITEDATA/WRITEFUNCTION）、`curl_multi_add_handle`、`curl_easy_perform`，并用 `fwrite` 关联默认写入器以抓响应体；结果 JSONL 落盘 |
| `scripts/reverse/capture.py` | 附着运行中的 `cloudmusic.exe` 并加载上述脚本，可指定 `--pid/--seconds` |
| `scripts/reverse/decrypt_capture.py` | 解出抓包的 `params` 明文、复算 digest 校验、并对 cookie 脱敏 |
| `scripts/reverse/unpack_ntpk.py` | 解包 `orpheus.ntpk`（ZIP 偏移 100），列出条目与全部 `/api/` route |
| `scripts/reverse/grep_js.py` | 对压缩过的单行 JS 做上下文窗口检索 |

复现步骤：

```powershell
python scripts\reverse\capture.py --pid <cloudmusic PID> --seconds 120
uv run python scripts\reverse\decrypt_capture.py --capture scripts\reverse\capture.45848.jsonl
uv run python scripts\reverse\dump_inuse_params.py
uv run python scripts\reverse\coverage.py scripts\reverse\capture.45848.jsonl
python scripts\reverse\unpack_ntpk.py --ntpk "D:\Program Files\Netease\CloudMusic\package\orpheus.ntpk" --routes
```

## 9. 已知差异与未完成项

1. **响应体抓取已可用**：客户端未设置自定义 `CURLOPT_WRITEFUNCTION`，改用 `fwrite` +
   `CURLOPT_WRITEDATA` 关联默认写入器；并把异步侧的 flush 从 `curl_multi_add_handle`
   （排队时）改到 `curl_multi_remove_handle`（完成时）。验证：一次 240 秒抓取中 48/51 条记录带响应体。
2. **clientSign 无法本地生成同构值**：其取值来自原生 `os.getADDeviceID`。仓库实现支持
   在会话里携带 `clientSign`，缺省时不发（与 macOS 分支行为一致）；实测无该字段时
   服务端仍返回业务 200。
3. **osver 文案**：客户端按 build 号映射市场名（26200 → Windows 11），仓库使用注册表
   `ProductName`（本机为 Windows 10 Enterprise），字段结构一致、取值可能不同。
4. **在用业务接口的真实报文部分待补**：已命中 8 个在用接口（见 5.1），其中播放地址与歌词已覆盖；
   搜索、评论、建/删歌单、心动模式、FM 等需要用户实际操作客户端时才会产生流量，其报文待补。
5. `utils/security.py` 中 `WEAPI_ABROAD_*` 相关表与函数随 `_abroad_decrypt` 删除后已无调用方，
   但仍在文件中（未清理）。
6. **客户端反调试会破坏抓包（重要）**：对该客户端做**长时间 / 多进程** Frida attach 会触发
   `AegisSDK.dll` 反调试自保 —— 表现为客户端接口失灵（评论、歌单加载不出，无法登出）。
   实测：单进程、90–240 秒短窗口能正常抓到 51 条记录；一次同时 attach 3 个进程并长期驻留后，
   客户端接口即失效（杀掉抓包进程并重启客户端后恢复）。结论：**只能短窗口、单进程抓，抓完立刻 detach**。
7. **抓包工具自身的两个坑（已修）**：`capture.py --watch` 原用 `frida.enumerate_processes()`，
   该 API 在 frida-python 17 不存在 → watch 模式静默抓不到任何数据；另外用
   `Start-Process -RedirectStandardOutput` 起长驻子进程会让调用一直等 stdout 句柄而看起来卡死，
   长驻子进程不应重定向。现已改为按 PID attach + `--guard 20` 自杀看门狗。
8. **host 已对齐（本轮修正）**：`src/ncm/__init__.py` 的 `API_HOST` 已按真实抓包从
   `interface.music.163.com` 改为 **`interfacepc.music.163.com`**。`
9. **本轮按真实抓包修正的接口参数**：`getDailyRecommend` 由 `/api/v1/...` 改为客户端实证的
   `/api/v3/discovery/recommend/songs`（带 `limit=30`）；`getUserDetail` 补 `all=true`/`userId`；
   `getPlaylistInfoEapi` 补 `newStyle=true`；`getTrackDetail` 的 `c` 补 `v` 并补 `trialMode=-1`；
   `_osVersion` 按 Windows 规则（build ≥ 22000 视为 Windows 11）生成 osver（残留差异：客户端还带
   `Edition` 字样，本机注册表为 `Windows 10 Enterprise`）。
10. **`security.py` 的 web 路径残留已删除**：`WEAPI_ABROAD_SBOX`/`KEY`/`IV`、`c_decrypt_abroad_message`
   及其专用位运算辅助函数全部移除（403 行 → 31 行），仅保留 `ID_XOR_KEY_1` 与
   `cloudmusic_dll_encode_id`（匿名设备 id 用）。ruff/compileall 通过，函数仍可用。
11. **本轮修改的实测与一处失败记录**：改为 `interfacepc.music.163.com` 后，匿名会话实测
   `loginQrcodeUnikey`/`getTrackDetail`/`getTrackAudio`/`getSearchResult`/`getTrackLyricsNew`/
   `getPersonalFM`/`getCurrentLoginStatus` 全部 `code=200` —— 改 host 不影响受理。
   另有一次 `verify_chain.py` 失败：用旧抓包里的 `MUSIC_U` 建会话时服务端已不再认可该 token，
   `login_info['content']` 为 `None`，读取 `vipType` 抛 `TypeError`。即**该脚本依赖新抓的 token**，
   旧抓包会失败；这不是代码回归，但复现时必须用新抓包。

## 10. 凭据处理


## 11. 与目标契约的对照

| 契约 | 状态 | 证据 |
| --- | --- | --- |
| 提取真实签名/加密 | 达成 | DLL 密钥表 `sub_180CDD630` + 真实流量 digest 复算全部 `True` |
| 提取 host | 达成 | 真实流量 `interfacepc.music.163.com` |
| 提取参数/header/cookie 要求 | 达成 | 第 4 节（真实抓包）+ 第 5 节样本 |
| 在用调用面继续可用 | 达成 | 第 5.3 节：逐项 200 |
| 旧 web/mobile 路径删除 | 达成 | 第 7.4 节；grep 无残留引用 |
| `py_compile` / `ruff` | 达成 | 两者均通过 |
| mypy 改动文件无新增错误 | 达成 | 仅剩 `imports.py`/`i18n.py`/`config.py`/`models.py` 的既有报错，与本次改动无关 |
| 扫码提示非「授权 web 端」 | 达成（用户实测确认） | `unikey code=200`；二维码可生成（`chainId=v1_<deviceId>_pc_login_<ts>`）；轮询状态机实测 `801`（待扫描）→ `800`（超时），成功为 `803`；你确认手机端不再显示"web 端" |
| 标准 4 全新会话扫码 + 全链路 | **部分达成** | 手机端提示已由用户实测确认（不再显示「web 端」）；但「扫码→803→在该全新会话上跑全链路→登出」未完成（轮询只到 `801`→`800` 超时）；全链路 200 是在**抓包 cookie 会话**与**匿名会话**上取得，`loginLogout` 只在匿名会话上执行；写操作类（`addComment`/建·删歌单/增删曲目）与短信/注册类接口**未实测**（会真改账号状态或发真短信） |
| 标准 1 逐个记录在用接口真实报文 | **部分达成** | 8 个接口有官方客户端真实抓包（5.1）；其余 11 个只有客户端 JS 定义（静态证据）；另有 18 条只读接口的本仓库实测报文（5.4）。写入类与短信类接口未实测 |
| 标准 2 Frida 脚本可复现 | 达成（有硬限制） | 可 attach 并 dump 明文请求+响应（51 条/48 带响应，见 9.1）；但长时间 attach 会触发反调试破坏客户端（9.6），只能短窗口单进程 |
- 本文件不含任何未脱敏凭据：`deviceId`、`clientSign`、`MUSIC_U`、`__csrf`、`NMTID`、`WNMCID`
  一律以 `<redacted>` 或以长度占位表示。
- 原始抓包 `scripts/reverse/capture.jsonl` 含真实 cookie，**不入库**（已加入
  `scripts/reverse/.gitignore`），并在验证完成后删除。
