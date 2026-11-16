# SouthsideMusic 宣传片 · 设计 spec 与制作记录

## 1. 产品简报

- 产品：SouthsideMusic，Windows-only PySide6 桌面网易云音乐客户端（自有音频管线、逐字歌词、EQ/响度、交叉淡化、桌面歌词、本地收藏与重新发现、SMTC / SouthsideClient 桥）。
- 用途：GitHub Release / 官网 / 社交平台的 16:9 产品宣传片。
- 受众：Windows 上愿意折腾音质与歌词体验的网易云用户。
- 用户硬约束（本次已确认）：真实界面截图；16:9 4K60；约 20–25 秒；保留真实界面数据（作者本人账号与收藏）；BGM + 音效。
- 制作模式：自主自由创作（video-shotcraft pipeline 阶段 0–7，Agent 自主放行，交付前独立终检）。

## 2. 视觉方向

命名：**熄灯之后 / After Lights Out**。全片只有一束光，依次照亮字标、歌词、EQ、队列、历史与收尾合影；产品页面始终保持真实颜色，只有被光找到的区域发亮。

继承自产品自身的 token：

| 维度 | 取值 | 依据 |
|---|---|---|
| 底色 | #070911 / #0b1020 | 应用暗色 Fluent 主题 |
| 正文/标题 | #f2f6ff、rgba(226,234,255,.62) | 应用浅色文本与次级文本 |
| 强调色 | #7fb2ff（封面取色蓝） | Wolves 封面取色 + Windows 强调色 |
| 暖色 | #f4c9a0 | 交叉淡化第二页 7 Years 封面暖光 |
| 字体 | HarmonyOS Sans SC（产品自带 fonts/HARMONYOS_SANS_SC_REGULAR.ttf） | 产品 UI 字体 |
| 圆角 | 12–20px | 产品卡片/浮窗圆角 |
| 材质 | 无纸纹、无印刷网点；靠光、阴影、景深与轻微颗粒 | 产品 Fluent 质感 |

动效嗓音：低中能量、强控制、单点光效；相机永不手持抖动；全画幅级冲击仅 3 处（f0 / f443 / f942），其余拍点只动元素层。

## 3. 镜头表（1440f @60fps = 24.0s）

| # | 帧段 | 时长 | 功能 | 素材 | 画面/动效 | 字幕 |
|---|---|---|---|---|---|---|
| A01 | 0–168 | 168f | 品牌 | 原创 | 暗场极光，SouthsideMusic 逐字描出/上浮，hold；光带扫过 | — |
| A02 | 168–368 | 200f | 播放页 | 真实 pages/playing.png | 光池从封面移到歌词列，相机推近 0.95→1.5；字幕压在底部 | Wolves — Selena Gomez, Marshmello |
| A03 | 368–458 | 90f | 呼吸字卡 | 原创 | 三段文字 blur-slide | 逐字歌词 / 每个字的亮起 / 都落在鼓点上 |
| A04 | 458–602 | 144f | 逐字歌词 | 真实 seq/lyrics 14 帧 | 全屏歌词序列 + 相机推近，音画真实推进 | 逐字歌词 · 翻译同步 |
| A05 | 602–740 | 138f | EQ/响度 | 真实 pages/settings_eq.png | 3D 斜侧贴面、EQ 曲线高亮、光扫过 | EQ 曲线 · 响度归一化 |
| A06 | 740–872 | 132f | 智能交叉淡化 | 两张真实播放页 + pages/settings_crossfade.png | 强调色线从 A 页进度条延伸、围出 B 页卡框、B 页显影、A 页衰减 | 智能交叉淡化：BPM 与调性对齐，两首歌像一首 |
| A07 | 872–1004 | 132f | 播放队列 | 真实 pages/playlist.png | 相机推入右侧队列面板，真实队列行/封面/时长 | 播放队列：接下来放什么，你说了算 |
| A08 | 1004–1118 | 114f | 重新发现 | 真实 pages/rediscovery.png | 相机推近历史列表，光池照亮行，描边环标记真实"上次播放"日期 | 重新发现：翻出你三个月没听的那首 |
| A09 | 1118–1208 | 90f | 呼吸字卡 | 原创 | 同 A03 | 系统媒体键 / 键盘上那颗播放键 / 直接控制它 |
| A10 | 1208–1440 | 232f | 发布会合影 | 6 个真实元素切片 | 真实元素四面八方飞入围住字标，crane 收正，字标压印，hold ≥1s | 为 Windows 做的网易云客户端 |

## 4. 真实素材管线

- promo-video/capture/harness.py：接管 src/main.py 启动（patch QApplication.exec + MainWindow.__init__），跳过依赖检查与更新检查，用 QTimer 状态机驱动页面；以 QT_SCALE_FACTOR=2（系统 150% → DPR 3）离屏渲染，窗口 1920×1080 逻辑 → 5760×3240 纹理。
- 页面：home / playing(Wolves) / playing(7 Years) / favorites / rediscovery / settings_top / settings_eq / settings_crossfade / playlist。
- 元素切片 + layout.json（逻辑坐标 1920×1080）。
- 序列：seq/lyrics 14 帧（真实播放推进，JPEG 1920 宽）。
- 限制（已记录）：QOpenGLWidget 逐帧抓取极慢，桌面歌词浮窗 grabFramebuffer 只得到透明条（无文字）；EQ 拖拽序列的 JPEG 落盘失败。为保证不伪造，A07 改为同样真实的播放队列镜头；A05 使用真实静态 EQ 页面 + 相机/扫光，不做假拖拽。
- 数据口径：用户明确选择保留真实界面数据；片中不含 cookie/密钥/IP，账号昵称 Adreno9135 为作者本人，属已确认口径。

## 5. 声音

- BGM：cat-walk（Arulo, House, Mixkit free license, mixkit 371），源曲 01:29.5478 起切 24.0026s（52 拍 @130 BPM），0.003s 淡入；分析见 research/bgm/。
- 段落：brand 0–222 / hero 222–554 / climb 554–942 / peak 942–1440（@60fps）。
- SFX：10 条 Freesound 素材（8 CC0 + 2 CC BY 4.0），来源与署名见 public/audio/FREESOUND-CREDITS.md，实测与建议帧位见 public/audio/audio-manifest.json。
- 终交付两版：带 BGM + 无 BGM（保留 SFX），由 bgm inputProp 控制。

## 6. 渲染

- Remotion 4.0.484；composition 1920×1080 @60fps；--scale=2 输出 3840×2160@60。
- 浏览器：本机 Edge（Config.setBrowserExecutable）+ swangle（软件 GL），避免首次下载 Chromium。
- 输出：out/promo.mp4（带 BGM）、out/promo-nobgm.mp4（无 BGM）。

## 7. 与 video-shotcraft 准则的偏离/说明

- 未使用 Ink Press 模板（用户选自主自由创作）；视觉完全从产品暗色 Fluent + 封面取色生长。
- A01/A03/A09/A10 为品牌/字卡/收尾原创段，非产品页面；产品镜头全部真实截图。
- 缺 letterspace-materialize 的 u/m 字形，A01 改为 HarmonyOS 字体逐字浮出+光带（品牌段允许原创，未降级产品镜头）。
- Tier B 歌词序列仅 14 帧（GL 读回瓶颈），按真实时间轴插值/交叉淡化播放，不加速、不伪造内容。


## 8. 功能 → 镜头卡映射（video-shotcraft）

| 镜头 | 参考卡 · style-key | demo | 适配说明 |
|---|---|---|---|
| A01 | 自研（品牌段） | — | letterspace 字库缺 u/m，改 HarmonyOS 逐字浮出 + 光带；非产品页面 |
| A02 | spotlight-hero-card · spotlight-hero-card | demos/opening/spotlight-hero-card/SpotlightHeroCard.tsx | 光池/推进/卡形光语法；主角换成真实播放页歌词区 |
| A03/A09 | blur-slide · blur-slide | demos/typography/blur-slide/BlurSlide.tsx | 三通道（y/blur/opacity）同 p；中文手动切三段；暗场重蒙皮 |
| A04 | 自研 Tier B（语法参考 type-rhythm-sync · karaoke-fill-sync） | demos/typography/type-rhythm-sync/KaraokeFillSync.tsx | 只借词级时间表结构；内容来自真实 seq/lyrics 帧 |
| A05 | 自研（语法参考 value-stagger-gradient） | demos/ui-entrance/value-stagger-gradient/ValueStaggerGradient.tsx | 真实 EQ 页面 + 3D 贴面 + 单点扫光；不伪造拖拽 |
| A06 | line-carry-transition · line-carry-transition | demos/transition/line-carry-transition/LineCarryTransition.tsx | 线从 A 页延伸到 B 页卡框；框闭合后 B 页显影；暗场强调色重蒙皮 |
| A07 | 自研（真实队列面板浮出） | — | 队列页真实面板切片 + 相机/光；避免与设置页并置穿帮 |
| A08 | timeline-travel · timeline-travel（语义改造） | demos/data/timeline-travel/TimelineTravel.tsx | 相机推近 + 真实"上次播放"日期描边环；不做假时间轴 |
| A10 | outro-group-photo-launch · outro-group-photo-launch | demos/outro/outro-group-photo-launch/OutroGroupPhotoLaunch.tsx | bezier(0.34,1.4,0.44,1) 真过冲、crane、sign-off hold；6 个真实元素 |

## 9. 音频偏移实测与补偿

- 探针：Remotion 4.0.484 + H.264/AAC 48kHz，Composition Probe，impact-deep-hit 单条。
- 实测峰值 4.44f；音源峰值滞后 1.8f → **输出偏移 +2.64f**（与 AAC priming 2048 samples 指纹一致）。
- 所有 SFX from 统一 -3f（0 处无法负补偿，保留 +2.6f 残差，已在 manifest 记录）。
- BGM 自身未前移，仍约有 2.6f（43ms）相对画面的延迟；切点相对音乐略早，未做源裁剪（避免削掉 drop 首击）。
- 带 BGM 版 sample peak 已压到 -0.004 dBFS 以内；建议发布平台做 -1 dBTP 二次限制。
