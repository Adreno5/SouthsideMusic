# SouthsideMusic 宣传片 · 功能到镜头映射 + 候选分镜骨架（只读研究）

> 交付对象：后续制作 Agent（可直接当施工图）。
> 片型：16:9 / 1920×1080 构图 @60fps / 终渲 `--scale 2` 出 4K60 / 总长 20–25s（1200–1500f）。
> 资料根：`C:\Users\Adreno\.agents\skills\video-shotcraft`（下文简写 `SKILL_ROOT`）。
> 本报告只读产出，未修改 `src/`、`config.json`、`data/`，未写任何 Remotion 工程代码。

---

## 0. 一页结论

1. **推荐主方案 = 方案 A「熄灯之后」1440f / 24.0s**。理由是它把「暗色 Fluent + 封面取色」这一产品视觉直接变成叙事手段（光即叙事），单主角段（Q5）最强，且对真实素材的依赖最少（仅 2 段序列采集）。
2. **本片的真问题不是"选哪张卡"，而是"哪些动效必须是真实序列"。** 124 张卡里的填色/拖拽/波形类动效（`karaoke-fill-sync`、`oscilloscope-stream`、`value-stagger-gradient`）全部是**手搓文本或手绘数据**。把它们套到播放页歌词区、EQ 面板上，等于手搓复刻既有页面 → 违反 Q1。**结论：真实状态变化必须走逐帧采集（Tier B），卡只提供运镜/打光/版式语法。**
3. **20–25s 装不下 12 个功能。** 按 template 片 `AIFL_SHOTS`（10 镜 1085f@30，字卡占 4 席）实测比例，24s 片的功能镜上限是 **5–6 个**。主方案覆盖 6 项；剩余功能进方案 B/C 或衍生短版，取舍表见 §5.2。
4. **两个必须先解决的施工前置**（否则镜头排了也渲不出来）：
   - 现有 `promo-video/capture/harness.py` 目前失败：`harness.log` 记录 `flow start → no main window captured → MainWindow constructed`，即 `start_flow()` 在 `MainWindow` 构造前就跑了（时序竞态），Tier A 纹理一张都没落盘。
   - `letterspace-materialize` 的字库只有 13 个几何小写骨架字形（`v i d e o - s h t c r a f`），写 `SouthsideMusic` 缺 **u、m**；若要首字母大写还要补 **S、M**。片头用这张卡 = 先补 2–4 个手绘 path。
5. **60fps 换算的总则**：卡的 `@30fps` 帧常量代表**时间**，所以帧号一律 ×2（缓动曲线、百分比、px 位移量不变）；但**依赖"每帧位移"的量要重标**——运动模糊/残影系数按新 px/f 约减半，否则拖影过强（详见 §10）。
6. **R4 的三次名额在本片已分配完**：A02 主角归位 impact、A06 交叉淡化硬切、A10 字标 stamp。其余所有拍点只动元素层。

---

## 1. 视觉语言 spec（必须从产品生长，禁套 ink press）

### 1.1 可继承的产品 token
| 维度 | 取值 | 来源依据 |
|---|---|---|
| 底色 | 近黑带冷色相（`#0b0c10`–`#111318` 档），卡片面比底亮 4–7% | 产品暗色 Fluent UI；`ui-strip-away-outro` demo 的黑场 `INK='#0b0c10'` 可直接复用 |
| 主强调 | Windows 系统强调色（取真机 `HKCU\Software\Microsoft\Windows\DWM\AccentColor`，勿写死一个蓝） | 用户硬约束"Windows 强调色" |
| 副强调 | **封面取色渐变的双端色**（每镜从当前歌曲封面采样 2 色，主色 + 8% 亮度差） | 产品"封面取色渐变背景"，这是全片唯一允许的高饱和来源 |
| 字体 | HarmonyOS Sans SC（产品自带 `fonts/`），英文同族；标题 600–700，正文 400 | 用户硬约束 |
| 字号 | 镜内叙事字幕 **≥60px**（Q11 线 56px，取 60 档）；辅助 ≥34px；字卡主标 96–140px | Q11 |
| 圆角 | 卡片 16px、浮窗 12px、药丸/胶囊全圆 | 产品圆角卡片 |
| 材质 | 无纸纹、无印刷网点、无墨线；质感靠**光**（体积光柱/聚光/边缘发丝光）与**景深** | Q4 + 用户禁纸感 |
| 动效节拍 | 一切转场与元素动效卡拍；整画面级冲击全片 ≤3 处（R4） | 强鼓点 BGM |

### 1.2 明令禁用（这些卡/演示皮肤不得照搬）
| 禁用项 | 涉及卡 | 处理 |
|---|---|---|
| 纸感/墨线/刊头衬线 | `brand-ink-open`、`paper-title-card`、`print-texture-transitions · ink-bleed-reveal`、`riso-print-hits`、`marker-underline-title` 的纸墨版 | **不用**。字卡改用 `blur-slide` / `brace-expand` / `title-demote-to-label` |
| demo 的浅色/瓷白皮肤 | `blur-slide`（paper look）、`element-body-moves`（porcelain 桌面）、`list-stack-press`（白卡）、`type-and-filter`（米白 `#f9f6f1`）、`line-carry-transition`（米色纸+墨线） | **可选用，但必须重蒙皮**：底换近黑、纸换玻璃/亚克力面、墨线换强调色 4–8px 实线、取消所有纸纹 |
| 荧光黄绿马克块 | `orbit-ring-title-open`（`#c6f432`）、`spotlight-hero-card`（香槟金） | 换成封面取色主色或 Windows 强调色；一个镜头只给主角一次（Q4） |
| 通用 DOM 占位卡 | `orbit-ring-title-open` demo 的 8 张卡、`value-stagger-gradient` 的 16 根柱 | 换成真实页面切片；柱子只允许出现在**非产品段落**（如字卡底纹），不得替代 EQ/频谱 |

---

## 2. 素材口径：Tier A / Tier B（本片成败在此）

### 2.1 两级素材
| 级别 | 形态 | 能表达什么 | 不能表达什么 |
|---|---|---|---|
| **Tier A 静态纹理** | 2x 全页 PNG（`public/textures/pages/{name}.png`）+ 元素级 4x 切片 + `layout.json` 坐标 | 运镜（推/拉/摇/环绕/俯冲）、关灯与打光、逐块揭示、元素离面抬升、页面间转场、景深 | 任何**状态变化**（歌词往前推进、曲线被拖动、频谱跳动、列表被挤出） |
| **Tier B 逐帧序列** | 60fps PNG 序列（`seq/{name}/0001.png…`）或 120fps 采后抽帧 | 上述一切状态变化，**且帧号可与 BGM 拍点逐帧对齐** | 无法事后改内容，必须重录 |

**为什么用 PNG 序列而不是录屏 mp4**：录屏有编码延迟与掉帧，卡点片差 2–3f 就会被读作"无端抖动"（S5/判例）；PNG 序列按帧号索引，`<Img src={seq/(frame+offset)}>` 严格确定，且可一次采 120fps 再抽成 60fps 抗漂移。FFT 是 GL 部件，`QWidget.grab()` 抓不到，必须走 `grabFramebuffer()` 逐帧合成（harness 里 `composite_playing()` 已有这条路径）。

### 2.2 PageCam 用法与三个坑（`SKILL_ROOT/assets/lib/PageCam.tsx`，152 行）
- API：`{src, pageH, keys:[{frame,cx,cy,zoom,rotX,rotY,rotZ,persp}], children, blur, saturate, dof, frame}`；`children` 是**页面坐标系**叠加层（CSS px），这是唯一合法的"在真实纹理上画东西"的入口。
- 放大走 CSS `zoom`（布局级），坐标换算 `Tx = 960/zoom − cx`、`Ty = 540/zoom − cy`——这是 Q2 的解法，**不要**用 `transform: scale(zoom)`，否则文字先降采样后放大必糊。
- **坑 1**：`AbsoluteFill` 写死 `backgroundColor:'#faf7f2'`（纸白）。暗场产品必须改成 `#0b0c10`，否则缩放/圆角处漏白。
- **坑 2**：只吃静态 `<Img>`。要放 Tier B 序列，需自研 `PageCamSeq`（见 §8 G7）：把 `Img` 换成按帧取图的 `Img` 序列（或 `<OffthreadVideo>`），其余 transform/zoom 数学一行不改。
- **坑 3**：`dof` 是屏幕空间顶部梯度模糊，只做氛围；**永远不是清晰度解法**（Q2 实证无效）。

### 2.3 采集清单（对齐现有 harness 约定，需在拍摄前补齐）
> 现状：`harness.py` 只采 `home` / `playing` / `playing_b`，且因竞态未成功。建议把 `start_flow()` 的首行判断改成 `wait_until(lambda: HOLDER.get("mw") is not None, 30000)`。

| 文件名 | 页面/状态 | 级别 | 用于镜头 |
|---|---|---|---|
| `pages/playing.png` | 播放页展开，真实封面+逐字歌词+翻译+频谱+音质标签 | A | A02 A04 |
| `pages/eq.png` | 音频设置：EQ 曲线 + LUFS 读数 + 立体声/混响 | A | A05 |
| `pages/crossfade.png` | 交叉淡化设置（时长/曲线/BPM/调性/AGC） | A | A06 |
| `pages/rediscover.png` | 重新发现收藏（真实历史，低播放/久未听） | A | A08 |
| `pages/search.png` | 搜索页（有结果网格） | A | A10 备选 |
| `pages/queue.png` | 播放队列面板 | A | B05 |
| `pages/onerad.png` | Onerad 助手面板（静态对话纹理，不需真回答） | A | B07 |
| `pages/lyric_editor.png` | 歌词编辑器（粘贴态 + 打拍态两版） | A | C05 |
| `windows/desktop_lyrics.png` | 桌面歌词悬浮窗（带透明边的窗口截图） | A | A07 C04 |
| `seq/lyrics/{n}.png` | 逐字歌词推进 3–4s（120fps 采、抽 60） | **B** | A04 C03 |
| `seq/eq_drag/{n}.png` | EQ 曲线拖拽 2–3s | **B** | A05 |
| `seq/fft/{n}.png` | 频谱/节拍可视化 3s（GL 逐帧） | **B** | A02 尾 / A04 背景 |
| `seq/tap/{n}.png` | 歌词编辑器空格打拍 3s | **B** | C05 |

**数据口径（Q1 硬要求）**：采集的是**真实登录账号**。开拍前必须换演示账号或手工脱敏：用户名、云歌单名、历史记录里的私人歌名、头像。定版文案里的歌名也一并虚构。

---

## 3. 方案 A「熄灯之后」1440f / 24.0s（★ 推荐）

叙事：**灯先亮在字标上，再亮在封面上**——全片只有一束光，它依次照亮产品的各个面。不解释"我们有什么"，只演"光落在哪里，那里就是重点"。

### 3.1 时间轴总表
| 镜号 | 帧段(60fps) | 时长 | @30fps 等效 | 段位 | 镜头卡 · style-key | 素材 |
|---|---|---|---|---|---|---|
| A01 | 0–168 | 168f / 2.80s | 84f | ① 品牌开场 | `letterspace-materialize` · `letterspace-materialize` | 原创 |
| A02 | 168–368 | 200f / 3.33s | 100f | ② 单主角 | `spotlight-hero-card` · `spotlight-hero-card` | A(+B 尾) |
| A03 | 368–458 | 90f / 1.50s | 45f | 呼吸字卡 | `blur-slide` · `blur-slide` | 原创 |
| A04 | 458–602 | 144f / 2.40s | 72f | ③ 功能爬升 | **自研**（语法参考 `type-rhythm-sync` · `karaoke-fill-sync`） | **B** |
| A05 | 602–740 | 138f / 2.30s | 69f | ③ | **自研**（语法参考 `value-stagger-gradient`） | **B** |
| A06 | 740–872 | 132f / 2.20s | 66f | ③ | `line-carry-transition` · `line-carry-transition` + 双 PageCam | A |
| A07 | 872–1004 | 132f / 2.20s | 66f | ③ | `element-body-moves` · `contact-shadow-lift` | A |
| A08 | 1004–1118 | 114f / 1.90s | 57f | ③ | `timeline-travel` · `timeline-travel` | A |
| A09 | 1118–1208 | 90f / 1.50s | 45f | 呼吸字卡 | `blur-slide` · `blur-slide` | 原创 |
| A10 | 1208–1440 | 232f / 3.87s | 116f | ④ 发布会收场 | `outro-group-photo-launch` · `outro-group-photo-launch` | A |
| — | 合计 1440 | 24.00s | 720f | — | — | — |

段位占比：① 11.7% / ② 13.9% / ③ 58.3%（含 2 张字卡；纯功能镜 45.8%）/ ④ 16.1%——①②③ 均在 promo-energy-arc 推荐区间内，④ 略高于 13–16%（6 元素合影 + 1s hold 的硬开销，24s 篇幅下可接受）。方案 A 相对 arc 模板的唯一结构性偏离：③ 段只有 5 个功能镜（arc 建议 55–65% 分给更多镜），原因是 24s 篇幅；已在 §5.2 用覆盖取舍表说明。

### 3.2 逐镜施工卡

**A01 · 0–168 · 品牌字标描画**
- 功能信息：品牌名登场（无功能）。
- 页面状态：无页面；原创暗场（封面取色极光/微光雾 + 颗粒 0.06）。
- 卡与变体：`letterspace-materialize` · `letterspace-materialize`
- 主动作：全字符同帧起笔 → 笔画连续生长 98f → 齐收 → hold 60f（1.00s，R1 达标）；底景极缓推近 2%。
- 转场：出镜 = 字标整体失焦淡出 12f，交棒 A02 的"关灯页面"（等效 `shot-transitions · flash-cut` 的暗场版，不闪白）。
- 字幕：无（品牌卡不配字幕，C1 例外项）。
- SFX 意向：`riser` 低频铺底（0–98f）+ 描画完成一记 `sparkle`（单点）。
- 备注：**必须先补字形**（u、m；若要大写 S、M）。若补不动，退回备选 `lead-word-zoom-assemble`（无需字库，168f 直接可用）。

**A02 · 168–368 · 主角 Now Playing 大封面**
- 功能信息：大封面 + 逐字歌词 + 频谱 + 音质标签，一个画面交代"这是一个能听歌的地方"。
- 页面状态：`pages/playing.png`，展开态，真实封面与真实歌词；整页压暗到 6–10%，只有卡形光池内是本色。
- 卡与变体：`spotlight-hero-card` · `spotlight-hero-card`（精简弧，见 §7.2）
- 主动作（**引号内均为镜内相对帧**，绝对帧 = 168 + 相对帧）：光池游走 3 站 → **f40 锁定**大封面（+6% 脉冲）→ f48–74 推进 26f（zoom 0.8→2.2）+ 卡片 rise 过冲（f84–100）→ 悬停 bob 48f → f148–176 reseat 落回原位 → hold 24f（至 f200）；轮廓光束只跑 **1 圈**（f92–124，原卡 2 圈）。
- 转场：入镜 = A01 的失焦交棒（同一束光延续）；出镜 = 切 A03（硬切，落在鼓点上）。
- 字幕：镜内字幕「大封面 · 逐字歌词 · 音质一键切换」（34px 辅助级，左下）；不抢。
- SFX 意向（绝对帧）：开灯 `light-clunk`（f170，相对 f2）；弹起 `whoosh`（f264，相对 f96，rise 中段）；光束 `sparkle`（f288，相对 f120，lap 收尾）；**归位 `impact`（f344，相对 f176 = LAND）← R4 名额 1**。
- 备注：卡形光必须画在 PageCam 内（页面空间），注记层单独套同一台相机画在灯光之上，否则被 multiply 压成灰字。

**A03 · 368–458 · 字卡 1（逐字歌词）**
- 卡与变体：`blur-slide` · `blur-slide`（中文重切 3 段）
- 文案（3 段，主标 96px + 辅 34px）：「逐字歌词」「每个字的亮起」「都落在鼓点上」
- 主动作：三段 stagger 8f，每段 y 40px + blur 18→0 + opacity 同一条 ease-out；辅助行错峰跟进；尾 hold 24f。
- 转场：出镜 = 直接切 A04 歌词特写（同构图延续，读作"字卡解释完，镜头进到那片区域"）。
- SFX：文字落定一记 `transition-soft`（轻）。
- 备注：卡 demo 是纸面衬线刊头，**必须重蒙皮**：底近黑、无刊头/刊脚、字体换 HarmonyOS Sans SC。

**A04 · 458–602 · 逐字歌词推进（★ 自研 / Tier B）**
- 功能信息：YRC 逐字高亮 + 翻译行 + 节拍可视化同屏。
- 页面状态：`seq/lyrics/{n}.png` 序列（3–4s），PageCam 推近歌词区（zoom 1.6–2.0，`cy` 落到歌词行）。
- 卡与变体：无卡可套；语法参考 `type-rhythm-sync` · `karaoke-fill-sync`（**只参考"词随声走"的时间表结构，不套它的手搓文本层**）。
- 主动作：相机从上一镜的字卡位置推近歌词区（24f）→ 序列播放 96f（真实高亮自己往前走）→ 翻译行在序列里天然淡入 → 尾部 24f 相机微抬，带出频谱区。
- 转场：出镜 = 向右上摇移接 A05（同页面不同面板，`whip-pan` 轻款 16f，不糊到不可读）。
- 字幕：「逐字歌词 · 翻译同步」（60px，压在歌词区上方的 scrim 上）。
- SFX：不需要额外音效——**键盘/播放的真实拟音已在序列里**；只在推近起点给一记 `whoosh` 轻。
- 备注：这是全片"真实感"的支点，宁可短也不能假。

**A05 · 602–740 · 音频面板（EQ 曲线 + LUFS）**
- 功能信息：EQ 曲线可拖、响度归一化（LUFS）、立体声/混响参数。
- 页面状态：`seq/eq_drag/{n}.png`（真实拖拽 2–3s）+ 静态 `pages/eq.png` 兜底。
- 卡与变体：**自研**；语法参考 `value-stagger-gradient` · `value-stagger-gradient`（**只用它的"参数梯度"读感**：拖动时相邻频段跟着动，不做柱状重绘）。
- 主动作：斜侧机位（rotY 18°、rotX 6°）贴面推近 EQ 曲线区 → 曲线被真实拖动（序列）→ 拖到顶点时一道 200px 强调色 sheen 沿曲线扫过（**裁进面板圆角**，Q4）→ LUFS 数字在画面右侧被真实刷新。
- 转场：出镜 = `shot-transitions · mask-wipe`（用 EQ 面板的圆角矩形当窗口，穿进 A06 的交叠世界）。
- 字幕：「EQ 曲线 · 响度归一化：每首歌都响在同一个音量」（60px）。
- SFX：拖动 `whoosh` 细（跟手）；到位 `transition-snap`；**不给每个频段配 pop**（S2 防机枪）。
- 备注：LUFS 数值必须来自真实界面，不许后期贴数字（Q10 同源）。

**A06 · 740–872 · 智能交叉淡化（★ 自研 + 线接力语法）**
- 功能信息：交叉淡化时长/曲线/BPM/调性匹配/AGC。
- 页面状态：两张真实播放页纹理（A 歌 / B 歌，封面不同、取色不同）+ `pages/crossfade.png` 作画中画。
- 卡与变体：`line-carry-transition` · `line-carry-transition`（语义改造：线=淡入淡出的能量曲线）+ 双 PageCam。
- 主动作：A 页的进度条延伸出画 → 强调色 6px 实线横移（64f，速度 ≈17px/f@60）→ 线在移动中拐角围出 B 页的卡框 → 框内 B 页从 0.42 长到 1、A 页同时按曲线衰减 → 闭合后 hold 24f。**"交叉淡化"由两页的交叠本身表达，不画产品里的设置曲线**。
- 转场：出镜 = 框闭合后硬切 A07（落在拍点）。
- 字幕：「智能交叉淡化：BPM 与调性对齐，两首歌像一首」（60px，压在框下沿）。
- SFX：线出画 `whoosh`（拉长 1.2s）；闭合 `transition-snap`；**R4 名额 2** 给闭合瞬间的半帧亮度脉冲（仅这一处）。
- 备注：线必须从墨色重蒙皮成强调色；纸底换暗场；否则立刻变成 ink press。

**A07 · 872–1004 · 桌面歌词悬浮窗**
- 功能信息：always-on-top 桌面歌词。
- 页面状态：`windows/desktop_lyrics.png`（真实悬浮窗，带透明边）+ 一层模糊的桌面/播放页背景。
- 卡与变体：`element-body-moves` · `contact-shadow-lift`
- 主动作：斜俯拍 24°→20°，浮窗沿法线抬升 96px（屏幕可见 40–50px）+ scale 1.05，**接触阴影同步放大变淡并向背光侧偏移** → 悬停 bob 50f → 落回 8f + 2f 微压。
- 转场：出镜 = 浮窗落定后切 A08（硬切）。
- 字幕：「桌面歌词：永远置顶，不挡你手上的事」（60px，置于浮窗左下留白）。
- SFX：抬起轻吸、落回轻放（拟音级，volume 低）。
- 备注：一次只抬一个元素（P4）；浮窗截图必须带真实歌词与翻译，不能是空窗。

**A08 · 1004–1118 · 重新发现收藏（★ 时间轴语义改造）**
- 功能信息：基于真实听歌历史，翻出"低播放/很久没听"。
- 页面状态：`pages/rediscover.png` + 4 张真实歌曲卡切片（含封面、歌名、"3 个月未听"标签）。
- 卡与变体：`timeline-travel` · `timeline-travel`（把"版本刻度"换成"上次播放时间刻度"）
- 主动作：空轴 + 首刻度 12f → 加速掠过（变速断点 `[0,0.15,0.88,1]→[0,0.055,0.9,1]`）→ 每过一格卡片 spring 弹立（提前 6f@30 = 12f@60 起弹）→ 急停"3 个月前" → 推近 zoom 1→1.28（20f）→ hold 30f。
- 转场：出镜 = 推近到位的卡片直接切 A09 字卡。
- 字幕：「重新发现：翻出你三个月没听的那首」（60px）。
- SFX：每次弹立一记轻打点（3 次，音量阶梯递减）；急停一记 `impact` 轻（**不占 R4 名额**，因为不动整画面）。
- 备注：停点那张卡必须是出版级真内容（Q10），掠过段可以糊。

**A09 · 1118–1208 · 字卡 2（SMTC 无界面能力）**
- 卡与变体：`blur-slide` · `blur-slide`
- 文案（3 段）：「系统媒体键」「键盘上那颗播放键」「直接控制它」
- 主动作：同 A03，但 stagger 6f、hold 30f（总 90f）。
- 转场：出镜 = 整行上浮淡出 10f，接 A10 的"灯全开"。
- SFX：`transition-soft`。
- 备注：WebSocket 桥也是无界面能力，若要塞进同一张卡，改成两行（第二行「手机/游戏端同步」），但**不要**变成功能清单。

**A10 · 1208–1440 · 发布会合影收场**
- 功能信息：已展示功能各出代表元素 + 字标定版。
- 页面状态：6 个真实元素切片（大封面 / 歌词行 / EQ 曲线面板 / 交叉淡化曲线 / 桌面浮窗 / 历史卡）。
- 卡与变体：`outro-group-photo-launch` · `outro-group-photo-launch`
- 主动作：6 元素从四方飞入（cue 每 6f@60 一个，各飞 24f，`bezier(0.34,1.4,0.44,1)` 真过冲）→ 落地接触影收紧 + 1.5% 轻压 → 字标 letterpress 逐字（delay 84+i·3.6、16f）→ rule 长出 116–140f → crane `rotateX 4°→0 + scale 1.06→1` → **sign-off hold 60f（1.0s，R1）**。
- 转场：全片终点；末 6f 极淡出到 #0b0c10（给播放器首帧留净场）。
- 字幕：标语一行（34–40px，宽字距）：「SouthsideMusic · 为 Windows 做的网易云客户端」。
- SFX：`riser` 铺进组装 → **`impact-deep-whoosh` 钉字标（全片峰值，R4 名额 3）** → `sparkle` 点 rule。
- 备注：outro tagline 与字卡文案查重（P4）；合影里每个功能都要有代表元素（Q8 自检）。

---

## 4. 方案 B / 方案 C（备选骨架，同规格）

### 4.1 方案 B「你的库有多大」1500f / 25.0s
叙事：**先给规模，再给细节**——环、牌、堆、急刹，全片在证明"这里面东西很多，而且都归你"。

| 镜号 | 帧段 | 时长 | @30 等效 | 段位 | 卡 · style-key | 主动作要点 | 转场 | 字幕（功能名+收益） | SFX 意向 |
|---|---|---|---|---|---|---|---|---|---|
| B01 | 0–180 | 180f/3.0s | 90f | ① 开场 | `orbit-ring-title-open` · `orbit-ring-title-open` | 8 张真实页面卡 45° 公转，环撑开 48f，f48 才开播；标题两行 blur-slide + 马克块横扫 1 次 | 出镜整行失焦 20f | 无 | `riser` + 马克块 `sparkle` |
| B02 | 180–380 | 200f/3.33s | 100f | ② 单主角 | `spotlight-hero-card` · `spotlight-hero-card` | 主角换成"今日单曲"真实卡，其余同 A02 | 硬切 | 「今日单曲」（34px） | 同 A02 |
| B03 | 380–470 | 90f/1.5s | 45f | 字卡 | `blur-slide` · `blur-slide` | 「一万首」「都在一个列表里」「不用翻第二个软件」 | 硬切 | 字卡 | `transition-soft` |
| B04 | 470–610 | 140f/2.33s | 70f | ③ | `deck-deal-flyin` · `deck-deal-flyin` | 牌堆特写 orbit 40f → 预备拍下压 48px → 26 张真实歌单卡硬加速发牌（cue `72+8k−0.1584k(k−1)`）→ 满板俯瞰 hold 30f | 硬切 | 「收藏夹 · 云歌单，一个侧栏全在」 | 每张落桌 `whoosh` 阶梯递减 |
| B05 | 610–744 | 134f/2.23s | 67f | ③ | `list-stack-press` · `list-stack-press` | 5 张真实队列卡从下升入摞起（CUES 36/60/84/108/132@60），每张压弹整摞 6px，计数器同步 +1 | 硬切 | 「播放队列：接下来放什么，你说了算」 | 落地 3 次 `transition-snap` + 计数器 `click` |
| B06 | 744–878 | 134f/2.23s | 67f | ③ | **自研**（`karaoke-fill-sync` 语法） | 同 A04，压缩到 134f | 摇移 | 「逐字歌词」 | 同 A04 |
| B07 | 878–1012 | 134f/2.23s | 67f | ③ | `doc-park-left-pill-deal` · `doc-park-left-pill-deal` | 真实 Onerad 面板左移驻留 35% → 右侧 3 枚药丸按 `T0` 慢发牌 → 每条字幕逐词加深 | 硬切 | 「Onerad：让它替你读歌单、挑歌」 | 药丸 3 记轻打点 |
| B08 | 1012–1146 | 134f/2.23s | 67f | ③ | `scroll-brake-moves` · `brake-reticle-lock` | 真实历史列表高速滚 → 急停"很久没听"那一行 → 同帧准星咬合 | 硬切 | 「重新发现：很久没听的，它记得」 | 急停 `impact` 轻 + 咬合 `click` |
| B09 | 1146–1236 | 90f/1.50s | 45f | 字卡 | `blur-slide` · `blur-slide` | 「系统媒体键 / WebSocket 桥」「手机和游戏里也能听」「不用切窗口」 | 淡出 | 字卡 | `transition-soft` |
| B10 | 1236–1500 | 264f/4.40s | 132f | ④ | `outro-group-photo-launch` · `outro-group-photo-launch` | 7 元素合影（比 A 多一个队列元素），字标 hold 60f | — | 同 A10 | 同 A10 |

**B 的风险（不推荐作主方案的原因）**：B04（发牌批量入场）与 B05（堆叠批量入场）都是"批量元素落入"——同一手法出现两次，违反 P4"一种手法全片只当一次主角"；26 张真实切片的人工成本最高；且开场环 + 单主角 + 发牌 + 堆叠 = 连续三镜高密度，③ 段前 20% 就在高位，能量曲线没有起伏（arc 明确警告"③ 只堆高能量镜读作嘈杂"）。

### 4.2 方案 C「一条歌词的旅行」1200f / 20.0s
叙事：**全片只有一条线索：一句歌词从播放页出发，飘到桌面、被编辑、最后被导出成片**。功能覆盖最窄但因果链最完整。

| 镜号 | 帧段 | 时长 | @30 等效 | 段位 | 卡 · style-key | 主动作要点 | 转场 | 字幕 | SFX |
|---|---|---|---|---|---|---|---|---|---|
| C01 | 0–150 | 150f/2.5s | 75f | ① | `letterspace-materialize` · 同名 | 描画 92f + hold 50f | 失焦交棒 | 无 | `riser`+`sparkle` |
| C02 | 150–342 | 192f/3.2s | 96f | ② | `spotlight-hero-card` · 同名 | 主角=播放页歌词区（比 A02 更聚焦） | 硬切 | 「逐字歌词」 | 同 A02 |
| C03 | 342–486 | 144f/2.4s | 72f | ③ | **自研**（`karaoke-fill-sync` 语法） | 同 A04 | 浮窗弹出 | 「每个字都踩在拍上」 | 同 A04 |
| C04 | 486–606 | 120f/2.0s | 60f | ③ | `element-body-moves` · `contact-shadow-lift` | 歌词行从页面"飞"成桌面浮窗（离面抬升 + 阴影生长） | 硬切 | 「桌面歌词：永远置顶」 | 抬起吸气 |
| C05 | 606–726 | 120f/2.0s | 60f | ③ | **自研**（`row-embed` 语法） | 编辑器里歌词逐行嵌入 + 空格打拍戳点（真实序列） | 硬切 | 「歌词编辑器：空格打拍，逐句对时」 | 每次打拍一记真实键声 |
| C06 | 726–822 | 96f/1.6s | 48f | 字卡 | `brace-expand` · `brace-expand` | 花括号弹开揭示「导出成片」 | 硬切 | 「导出成片」「歌词直接烤进画面」 | `sparkle` |
| C07 | 822–1080 | 258f/4.3s | 129f | ④ | `ui-strip-away-outro` · `ui-strip-away-outro` | 点「导出」→ 6 层 UI 每 8f 一层蒸发 → 按钮滑到屏心 ×1.5 → 字标接棒 | — | 无（C1 例外） | `click-camera`（导出点击，全片最响）+ `impact` |
| C08 | 1080–1200 | 120f/2.0s | 60f | ④ 延展 | `logo-shrink-wordmark-lockup` · 同名 | 字标呼吸 + 标语落版（延续不是第二个收尾） | 淡出 | 「SouthsideMusic」 | `sparkle` 收 |

**C 的风险**：④ 段（C07 258f + C08 120f）占 **31.5%**，远超 arc 的 13–16%（`ui-strip-away-outro` 的蒸发仪式本身要 4s+），功能段被挤压；只覆盖 5 个功能，若这支片要"讲清产品是什么"则不够；`ui-strip-away-outro` 要求触发动作是**终结性语义**（Publish/导出 ✓），但它的"减法收尾"与 arc 的"发布会合影峰值"是两条互斥路线（卡内明写"不可同片双收尾"）——选 C 就等于放弃 Q8 的峰值收场。另外 C05 的打拍自研是三个方案里最重的一处自研。

---

## 5. 推荐主方案与覆盖取舍

### 5.1 为什么是 A
| 判据 | A「熄灯之后」 | B「你的库有多大」 | C「一条歌词的旅行」 |
|---|---|---|---|
| 能量曲线贴 arc | **最贴**（低开→单主角→交替→峰值，③ 有真正的低点字卡） | 偏平（前 45% 一直在高位） | 贴，但 ③ 只有 3 镜、爬升感弱 |
| 单主角强度（Q5） | **最强**（光是叙事手段，主角=封面） | 强（但有环+发牌抢戏） | 强（主角=歌词区，稍窄） |
| P4 手法唯一性 | **通过**（光/线/抬升/时间轴各一次） | **不通过**（发牌+堆叠两次批量入场） | 通过 |
| 真实素材依赖 | **2 段序列**（歌词、EQ） | 2 段 + 26 张切片 | 3 段（歌词、打拍、导出态） |
| 功能覆盖 | 6 项 | **7 项** | 5 项 |
| 视觉语言贴合度 | **最高**（暗场 Fluent + 封面取色，天生一对） | 高（但舞台感偏"产品发布会"） | 高（更柔软、更作者向） |
| 施工风险 | 低（唯一硬点是字库补画） | **高**（切片工作量 + 批量入场调参） | 中高（打拍自研 + 放弃峰值收场） |

**结论：A 作为主方案；B 的 B07（Onerad 逐条结论）与 B08（历史急刹）建议以"替换镜"形式备用**——若客户想加强"AI 助手"卖点，可用 B07 换掉 A08；若想加强"历史/情怀"，保留 A08。C 建议留作 15s 竖版或社媒短版的骨架。

### 5.2 功能覆盖取舍（12 项功能 → 进/不进 + 理由）
| # | 功能 | 主方案 A | 理由 |
|---|---|---|---|
| 1 | 播放页 Now Playing（大封面/歌词/频谱/音质） | ✅ A02 + A04 | 产品门面，必须给；单主角段承载 |
| 2 | 逐字歌词 YRC + 翻译 | ✅ A04（Tier B） | 最强差异点，值得单独一镜 144f |
| 3 | EQ 曲线 + LUFS/立体声/混响 | ✅ A05（Tier B） | "专业音频"的唯一证据；也给封面取色留了暗场空间 |
| 4 | 智能交叉淡化 | ✅ A06（自研） | 差异化最高、最"音乐人"的功能；用转场语法表达成本最低 |
| 5 | 桌面歌词悬浮窗 | ✅ A07 | 视觉最直观、外行一眼懂；且天然有"离面"的物理戏 |
| 6 | 重新发现收藏 | ✅ A08 | 唯一带情感叙事的功能，正好卡在峰值前（arc 建议信息镜放倒数 2–3 位） |
| 7 | SMTC + WebSocket 桥（无界面） | ✅ A09 字卡 | 无界面 → 只能字卡；但不进字卡就等于没讲 |
| 8 | 搜索页 | ⚠️ 未进（A10 备选/竖版） | 交互镜 146f 起，24s 里放不下第 6 个功能镜 |
| 9 | 播放队列 | ⚠️ 未进（B05） | 与 A07/A08 抢"列表"语义；同片放两个列表镜会重复 |
| 10 | Onerad 助手 | ⚠️ 未进（B07 替换镜） | 静态纹理撑不起 5s，除非演示真实回答（本次不做） |
| 11 | 歌词编辑器（打拍） | ⚠️ 未进（C05） | 自研最重的一处；20–25s 里性价比低 |
| 12 | 歌曲/歌词视频导出 | ⚠️ 未进（C07） | 与合影收场互斥（两个收尾） |
| 13 | 本地收藏夹 + 云歌单侧边栏 | ⚠️ 并入 A10 合影元素 | 单独一镜需要侧栏纹理 + 列表动效，收益低于前 7 项 |

> 若必须覆盖 ≥9 项：改用 25s 的 B 骨架并砍掉 B04/B05 之一（保留 Onerad），或做成 25s + 6 个功能镜的混合版。**不要试图在 24s 里塞 10 个功能**——每个功能镜降到 60f 以下时，观众连"这是什么"都读不出来。

---

## 6. 功能 → 镜头卡映射表（首选 / 备选 + 依据）

| 功能 | 首选卡 · style-key | 备选卡 · style-key | 选择依据（能量/可读性/是否需真实页面） |
|---|---|---|---|
| 播放页大封面 | `spotlight-hero-card` · `spotlight-hero-card` | `magician-card-flourish` · 同名 | 中能量、质感最高，正合 ② 段位（Q5 单主角）；备选是纯黑场"变出来"，仪式强但会把封面变成魔术道具，且需要纯黑底不接产品语境 |
| 逐字歌词 + 翻译 | **无卡**（自研 Tier B） | `type-rhythm-sync` · `karaoke-fill-sync`（仅当允许重排文本时） | `karaoke-fill-sync` 填的是手搓文本层；套到真实歌词区=伪造产品状态（Q1）。备选会把"真实"降级成"示意"，只在社媒短版里可接受 |
| EQ + LUFS | **无卡**（自研 Tier B） | `value-stagger-gradient` · 同名（仅非产品段落） | 同上。该卡的柱状梯度语法可迁移到"标注层/刻度层"，不可替代 EQ 曲线本体 |
| 智能交叉淡化 | `line-carry-transition` · `line-carry-transition`（+双 PageCam） | `bezier-source-converge-merge` · 同名 | 功能语义=两页交叠，用转场直接演出来最省且不撒谎；备选的"多源汇聚"语法只能表达"合成一条"，弱于实际语义 |
| 桌面歌词浮窗 | `element-body-moves` · `contact-shadow-lift` | `depth-layer-moves` · `multiplane` | always-on-top 的物理证据=接触阴影离面（低中能量，不抢）；备选是分层视差，只能给"厚度"不能给"置顶" |
| 收藏夹/云歌单侧栏 | `list-reveal` · `list-reveal` | `deck-deal-flyin` · `deck-deal-flyin` | 侧栏是竖直菜单，list-reveal 的"逐项找位 + 容器漂移"原生对口、低能量；备选是"内容量大"的高能拍，会把侧栏讲成卡片墙 |
| 重新发现收藏 | `timeline-travel` · `timeline-travel` | `scroll-brake-moves` · `brake-reticle-lock` | 功能语义是"时间"，横向轴最直给（急停=找到了）；备选是纵向急刹+准星，打击感更强但"时间"读不出来 |
| 搜索页 | `type-and-filter` · `type-and-filter` | `transition-hidden-cut` · `invisible-cut` | 全片唯一"真人操作"镜（R3 语速 3f/字符）；备选只是接缝技法，不承载功能 |
| 播放队列 | `list-stack-press` · `list-stack-press` | `research-card-stack-scroll` · 同名 | 队列=持续积累，压弹给"重量"；备选是高速叠压（压迫感），更像"资料堆积" |
| Onerad 助手 | `doc-park-left-pill-deal` · `doc-park-left-pill-deal` | `glass-pill-dictation-typing` · 同名 | 助手给"结论"，该卡正是"来源驻留 + 逐条结论"；备选只演"一个输入框登场"，讲不了结论 |
| 歌词编辑器打拍 | **无卡**（自研，`row-embed` 语法） | `type-rhythm-sync` · `font-weight-pump` | 空格打拍是产品独有交互，无卡覆盖；备选的字重脉冲只能表达"跟着拍动"，不能表达"把拍记下来" |
| 导出歌词视频 | `ui-strip-away-outro` · 同名 | `hashtag-to-pill-materialize` · 同名 | "发布/完成"的减法语义（复杂性归零）最贴导出；备选是"输入→实体→归位"三段式，适合打 tag 不适合导出 |
| SMTC / WebSocket 桥 | `blur-slide` · `blur-slide` | `glitch-cycle` · `glitch-cycle` | 无界面只能靠字卡（C2：必须写功能名+收益）；备选的乱码/系统自述口吻适合"桥接/协议"，但噪音高、易廉价 |

## 7. 选中卡的实现情报（含 60fps 换算）

> 路径约定：卡片 = `SKILL_ROOT/references/shots/{category}/{name}.md`；demo = `SKILL_ROOT/demos/...`。
> 全部 25 条路径已逐一 `existsSync` 验证存在（2026-10-31）。
> "命门"= 卡上标注不得降档的参数；"重蒙皮"= 因产品暗场 Fluent 而必须改的皮肤（不改动效参数）。

### 7.1 `letterspace-materialize` · `letterspace-materialize`
- 卡：`references/shots/opening/letterspace-materialize.md`
- demo：`demos/opening/letterspace-materialize/LetterspaceMaterialize.tsx`（209 行）
- 关键时值：`START=16` 起画、`DUR=52` 描画、总时长 105f@30（静置 16 + 描画 52 + hold 37）→ **@60fps：210f（起画 32 / 描画 104 / hold 74 = 1.23s）**；`GLYPH_SCALE=1.3`、`TRACK=30`px、笔画 `5.5/GLYPH_SCALE`、strokeEase `bezier(0.5,0,0.3,1)`
- 命门：**全字符同帧起笔同帧收**（零错峰）+ **pathLength 归一**（不同长度笔画同帧齐收）+ 终态静置 ≥30f@30（=60f@60）
- 重蒙皮：底景 `#120f24` 的暮色山脊 → 换成封面取色渐变 + 极轻颗粒；字形是全小写几何单线，若写 `SouthsideMusic` 需补 **u、m**（大写 S、M 另画）
- 坑：现成字体轮廓是双线勾边，描画观感完全不同，**必须手绘 path**；多子笔画字母（i 的点）要排在路径末尾

### 7.2 `spotlight-hero-card` · `spotlight-hero-card`
- 卡：`references/shots/opening/spotlight-hero-card.md`
- demo：`demos/opening/spotlight-hero-card/SpotlightHeroCard.tsx`（294 行）
- 关键时值（@30fps 原值）：`LOCK=34` `PUSH0=40` `PUSH1=58` `RISE0=56` `RISE1=66` `RESEAT0=124` `LAND=142`，总 172f；光池半径 400→300、锁定脉冲 +6%、关灯 0.90→0.94、卡形光暗场 `translateZ 1px`、`persp=1200`、相机 `rotY 34°/rotX 8°/zoom 0.8→2.6`、rise `bezier(0.2,1.25,0.3,1)`、reseat `bezier(0.4,0,0.3,1.05)`、光束 lap1 68–82f / lap2 88–108f
- **@60fps 全量版**：344f，LOCK 68 / PUSH 80–116 / RISE 112–132 / RESEAT 248 / LAND 284，两圈光束 136–164 与 176–216
- **@60fps 精简版（方案 A 采用，200f）**：LOCK 40 / PUSH 48–74 / RISE 84–100 / RESEAT 148 / LAND 176 / hold 24；**光束只跑 1 圈（92–124f）**
- 命门（不得降档）：① 关灯用 multiply 0.90–0.94 保色相，不是变暗滤镜；② 主角卡必须用**卡形软孔**（页面空间、`translateZ` 介于页面与卡之间）从边到边打亮，不是圆光池罩着；③ 3D 注记必须单独套同一台相机画在灯光层**之上**；④ 影子随高度生长且方向与光源一致；⑤ 光效只给主角一次（Q4）
- 重蒙皮：香槟金 `accent2` → 封面取色主色；纸底 `PATCH` → 暗场玻璃面；注记文案与本项目无关，换「封面 / 歌词 / 音质」三点
- 坑：卡 `translateZ` 至少 +2px 才不会与暗场共面闪烁；注记衬底右缘止于卡左缘之前

### 7.3 `blur-slide` · `blur-slide`
- 卡：`references/shots/typography/blur-slide.md`
- demo：`demos/typography/blur-slide/BlurSlide.tsx`（148 行）
- 关键时值：`BLUR_SLIDE_DURATION=120`@30 → **240f@60**；主标词间 4f、单字窗 22f（主角词 30f）、`dy=64`px、`blur=18`px；副标 2.4f/20f/`dy 26`/`blur 9`；主副重叠 26f
- 命门：**三通道（y / blur / opacity）共用同一个 `p`**；副标在标题收完前错峰跟进（排队=两拍）
- 适配：中文必须**按语义手切 3–5 段**（`split(' ')` 在中文上得到整块）；字数变了要同步压 gap（>8 段把 gap 压到 2–2.5f@30）
- 重蒙皮：demo 是暖白纸 + 墨 + 朱红刊头 → 换近黑底 + HarmonyOS Sans SC + 无刊头/刊脚

### 7.4 `element-body-moves` · `contact-shadow-lift`
- 卡：`references/shots/ui-entrance/element-body-moves.md`（**注意 style-key 不是卡名**）
- demo：`demos/ui-entrance/element-body-moves/ContactShadowLift.tsx`（218 行）
- 关键时值：`CONTACT_SHADOW_LIFT_DURATION=150`@30 → **300f@60**；`LIFT_Z=96`（屏幕可见 40–50px）、`LIFT_S=1.05`、`SCANS=[16,50]`、`PICK=86`；接触核 α 0.32→0.14 blur 3→25、环境影 α 0.09→0.22 blur 14→54 向右下偏；落地 scale 0.99 压 2f 再 5f 回弹；桌面 `rotateX 24°→20°`
- 命门：**屏幕可见位移 ≥12px（判例下限），本卡用 40–50px**；一次只抬一张
- 重蒙皮：porcelain 瓷白桌面 → 暗场桌面/模糊播放页；白卡 → 真实桌面歌词浮窗截图（带透明边）
- 坑：`box-shadow` 跟卡走给不出"影子留在桌上"，阴影必须是独立实体层

### 7.5 `timeline-travel` · `timeline-travel`
- 卡：`references/shots/data/timeline-travel.md`
- demo：`demos/data/timeline-travel/TimelineTravel.tsx`（291 行）
- 关键时值：`DURATION=160`@30 → **320f@60**；`TRAVEL_START=12`、`TRAVEL_END=104`（急停）、`ZOOM_END=114`；变速断点 `[0,0.15,0.88,1]→[0,0.055,0.9,1]`；`TICK_GAP=1400`、`AXIS_Y=700`、卡 360×240、`STEM=36`；弹立提前 6f@30（=12f@60）；急停推近 `1→1.28` 10f@30（=20f@60）
- 命门：中段必须足够快（匀速=廉价 PPT）；**急停前先留 ≥30f@30 静止预算再倒推行程**；停点卡必须是出版级真内容（Q10）
- 语义改造：刻度从"版本号"换成"上次播放时间"（3 个月前 / 半年前 / 去年），弹立卡换成真实历史歌曲卡
- 坑：中段糊可叠 `CameraMotionBlur` 只包快段；8bit 下亮场甩镜会偏黄，别用

### 7.6 `line-carry-transition` · `line-carry-transition`
- 卡：`references/shots/transition/line-carry-transition.md`
- demo：`demos/transition/line-carry-transition/LineCarryTransition.tsx`
- 关键时值：总 190f@30 → **380f@60**；进度条走满 36f、冲出 10f、横移 56f（1920px，均速 34px/f@30）、收框 24f、框内 30f、静止 24f；线体 8px、方头直角 miter、笔头位于 x≈1480
- 命门：**峰速 ≤80px/f@30（=160px/f@60）**；拐角直角硬拐不倒圆；**框闭合后内容才出**；末尾笔头墨点必须条件卸载
- 重蒙皮：米色纸 + 墨线 + 赤陶 → 暗场 + **强调色 6px 实线**（不改几何）；线的语义在全片必须统一（要么一直是"能量曲线"，要么一直是"进度条"）
- 用法：方案 A06 把它当"交叉淡化的能量曲线"，一条线从 A 页进度条延伸到 B 页卡框，**同片只此一次**（招牌转场）

### 7.7 `outro-group-photo-launch` · `outro-group-photo-launch`
- 卡：`references/shots/outro/outro-group-photo-launch.md`
- demo：`demos/outro/outro-group-photo-launch/OutroGroupPhotoLaunch.tsx`（344 行）
- 关键时值：`DURATION=145`@30 → **290f@60**；元素 cue 从 4 起每 3f 一个（=每 6f@60），各飞 12f（=24f）；`FLY_EASE=bezier(0.34,1.4,0.44,1)`；退后排 42–50f（opacity −12% / saturate −8%）；crane `0–40f rotateX 4°→0 + scale 1.06→1`，之后缓推 +0.035；字标 delay `42+i·1.8`、8f；rule 58–70f；`sign-off hold 30f`（=**60f@60，R1**）；金尘 20 颗 + 前景 bokeh 6
- 命门：**飞入缓动 y1 必须 >1**（旧 `bezier(0.25,0.9,0.3,1)` "never crossed 1" 没有真过冲）；落定角度收进 ±5°；**每个已展示功能都要有代表元素**；hold 满 1s
- 重蒙皮：demo 字标是 video-shotcraft 标志 + 全小写 118px；换 SouthsideMusic 字标与标语；香槟金尘 → 封面取色暖光（或保留极淡金色，避免高饱和）
- 坑：初版收尾几乎总是偏保守（Q8 三级加码）；outro tagline 与字卡查重（P4）

### 7.8 `orbit-ring-title-open` · `orbit-ring-title-open`
- 卡：`references/shots/opening/orbit-ring-title-open.md`
- demo：`demos/opening/orbit-ring-title-open/OrbitRingTitleOpen.tsx`（390 行）
- 关键时值：150f@30 → **300f@60**；`RX=720 RY=380`（RY/RX≈0.53）、卡 400×225、`N=8`、`ROT_SPEED=0.3` rad/s、`RING_IN=24`、`RING_SCALE_FROM=0.62`、`PLAY_START=24`、`DEPTH_SCALE=0.12`、马克块 `MARKER_AT=50`、`HANDOFF_EXIT/EXIT_AT=118`
- 命门：**卡身永不倾斜**（只换位置不换角度）；缩放/z 序/明暗必须由同一个 `depth=sin θ` 驱动；f24 前卡内容冻结首帧、f24 后才开播；马克块只给一次
- 重蒙皮：8 张通用 DOM 卡 → 8 张真实页面切片（播放页/设置/搜索/队列/助手/历史/导出/桌面歌词）；荧光黄绿 → 强调色；标题两行换中文（nowrap 前提不变）
- 坑：卡内容首帧必须自带静态骨架，否则冻结期是 8 张空白卡

### 7.9 `deck-deal-flyin` · `deck-deal-flyin`
- 卡：`references/shots/ui-entrance/deck-deal-flyin.md`
- demo：`demos/ui-entrance/deck-deal-flyin/DeckDealFlyin.tsx`
- 关键时值：150f@30 → **300f@60**；orbit `0→30f`（`rotX 48/rotY −30/rotZ 9/zoom 1.9` → `42/24/−6/1.8`，`persp 1100`）；`STACK_STEP=3`px × 26 张；预备拍 26–36f 下压 48px + 回拉 30px；cue `36 + 4k − 0.0792·k(k−1)` → **@60：`72 + 8k − 0.1584·k(k−1)`**；单卡 deal 8f + settle 4f + press 2f
- 命门：**预备拍幅度必须过肉眼阈值**（4px 初版完全无感，48/30px 才过）；发牌必须**硬加速**（R2）；卡纹理必须是真实页面切片（Q1），extras 复用真卡错位摆放；落定强制 identity transform
- 重蒙皮：石墨拉丝金属桌面 + 冷灰 → 保留（与暗场 Fluent 相容），底色改 `#0b0c10` 档；灯从牌堆亮到页面板的光圈揭开可保留
- 坑：`@remotion/motion-blur` 仅在 ≥30px/f 的快段包，慢段不包

### 7.10 `list-stack-press` · `list-stack-press`
- 卡：`references/shots/ui-entrance/list-stack-press.md`
- demo：`demos/ui-entrance/list-stack-press/ListStackPress.tsx`（239 行）
- 关键时值：段 18–88f@30（约 3s）→ **@60：36–176f**；`CUES=[18,30,42,54,66]` 12f 等距 → **@60：[36,60,84,108,132]（24f 等距）**；每张飞 22f（=44f）、`bezier(0.45,0.05,0.25,1.12)`；压弹 6px/8f（=16f）；glaze 74–87f；相机 `zoom 1.35→0.86`
- 命门：**压弹是"重量"的唯一来源**，砍掉就变成简单排版；计数单格滚动 8f **必须短于落卡间距**（12f → @60 是 8f < 24f，安全）；数字带双份 + `tabular-nums`；信息类列表必须**正视**（Q6）
- 重蒙皮：白卡 → 真实队列卡（封面 + 歌名 + 时长）；强调色高亮条保留但裁进圆角（Q4）
- 坑：末尾元素的节拍按剩余帧压缩，不照抄前面

### 7.11 `doc-park-left-pill-deal` · `doc-park-left-pill-deal`
- 卡：`references/shots/ui-entrance/doc-park-left-pill-deal.md`
- demo：`demos/ui-entrance/doc-park-left-pill-deal/DocParkLeftPillDeal.tsx`
- 关键时值：174f@30 → **348f@60**；驻留窗 0.06–0.24（inOutCubic，`translateX −55%` + `scale 0.92`）；`T0=[0.26,0.48,0.70]`（**@60 帧号 90 / 167 / 243**）；药丸透明窗 0.035、位移窗 0.062（outBack，y 14→0 / scale 0.94→1）；字幕 cs=f+0.05；自动滚动 `−t·120`px 连续匀速
- 命门：**发牌间隔=旁白句长**（0.22 ≈ 38f@30）；字幕在下一张到来前清场；自动滚动必须连续（取模会回跳）
- 重蒙皮：demo 是 480 宽画布（药丸 `left:214 top:54+k·48`、宽 172）→ **按 1920 整套重算**，否则与驻留文档重叠
- 坑：药丸数写死 3；改任一张起点会连带改前一张字幕时长（有意联动）

### 7.12 `scroll-brake-moves` · `brake-reticle-lock`
- 卡：`references/shots/data/scroll-brake-moves.md`
- demo：`demos/data/scroll-brake-moves/BrakeReticleLock.tsx`（300 行）
- 关键时值：`DUR=150`@30 → **300f@60**；`SCROLL_START=12`、`BRAKE=59`（=118@60）；`PITCH=156`（行高 120 + 间距 36）、`ROW_H=120`、`TARGET_ROW=30`、`FINAL_SCROLL=TARGET_ROW·PITCH−480`；角标咬合位外扩 10px
- 命门：**角标起跳帧 = 列表首次停住帧（严格同帧）**，错 2f 以上就散；停位目标行在画面纵向正中；高速段内容可占位，**停点必须真内容**
- 与 timeline-travel 分工：本卡纵向停"最新"、那张横向看"历史"；同片双用时先后错开且各 ≤1 次
- 重蒙皮：demo 是深色列表（已接近产品），只换文案与强调色

### 7.13 `ui-strip-away-outro` · `ui-strip-away-outro`
- 卡：`references/shots/outro/ui-strip-away-outro.md`
- demo：`demos/outro/ui-strip-away-outro/UiStripAwayOutro.tsx`（358 行）
- 关键时值：`DURATION=144`@30 → **288f@60**；`CLICK=34`（=68）；6 层每 4f 一层（=8f）、每层 `STRIP_DUR=14`（=28），`Easing.in(quad)`；位移 50–140px（侧栏 −140,0 / 面板 −90,20 / 工具条两端 ∓80,−60 / 画布底 0,40 / 中段 0,−50）；按钮迁移 18f 到屏心 ×1.5、白晕 30→70；`BTN_FADE=CLICK+52`、`LOGO_IN=CLICK+62`
- 命门：**外围→中心**的蒸发顺序 + 每层方向性位移；按钮是唯一幸存者且必须迁移；触发动作必须"终结性"（导出 ✓）
- 与 `outro-group-photo-launch` **二选一，不可同片双收尾**
- 重蒙皮：Framer 蓝 → 强调色；黑场 `#0b0c10` 可直接用

### 7.14 `logo-shrink-wordmark-lockup` · `logo-shrink-wordmark-lockup`
- 卡：`references/shots/outro/logo-shrink-wordmark-lockup.md`
- demo：`demos/outro/logo-shrink-wordmark-lockup/LogoShrinkWordmarkLockup.tsx`（224 行）
- 关键时值：132f@30 → **264f@60**；收束 `scale 5.4→1`（t 0.02–0.28）+ 刹车过冲 +6%（0.26–0.37）；愈合 0.10–0.28；左移 −90.5px（0.34–0.47）；字母 `0.46+i·(0.24/n)`、各 0.10；标语 0.72–0.84
- 命门：**愈合必须在落位前完成**；图标让位量由字标实测宽推出；logo 不随环旋转
- 重蒙皮：字标 15 字符全小写 → `SouthsideMusic` 14 字符（区分大小写），stagger 自动反算；标语换中文

### 7.15 `brace-expand` · `brace-expand`
- 卡：`references/shots/typography/brace-expand.md`
- demo：`demos/typography/brace-expand/BraceExpand.tsx`
- 关键时值：120f@30 → **240f@60**；0–6f 光标闪烁、**6f 硬切**替换成 0.4 倍内缘相贴的 `{}`、静置 8f；`14f spring(damping 14, stiffness 130)` 弹开（≈8f 到位、11f 过冲峰 9%、24f 收敛）；`HALF=676 / INNER=64`；文字 `clip.width=2x` 严格绑括号内缘 + 14px 羽化；字距 −0.05→−0.03em（40–64f）；眉题 44f、副标 54f、70–120f 缓推 1→1.012
- 命门：**位移与缩放相乘**（`x=(HALF−INNER)·ex·sc`）；绝对定位不用 flex；文案宽度要贴近内腔 1224px
- 重蒙皮：电光蓝括号 + 冷光 → 强调色括号；等宽眉题保留（技术感）

### 7.16 `shot-transitions`（技法卡，接缝用）
- 卡：`references/shots/transition/shot-transitions.md`
- demo：A `template/src/aifl/Main.tsx` + `assets/lib/FlashCut.tsx`（21 行）；B `demos/transition/shot-transitions/WhipBrakeReal.tsx`；C `demos/transition/shot-transitions/PortalWipeV2.tsx`
- 关键时值：A `from = 切点 − 5`、两侧各 5f → **@60：from = 切点 − 10、两侧各 10f**、`FlashCut` 组件 10f 长；B 前 70% 路程用 30% 时间（峰值 ~300px/f@30 = 600px/f@60）+ 后 30% ease-out 长尾 48f（=96f@60，卡上写明 >60f@30 偷走后镜，正好卡在边界）；C 窗放大 40f（=80f）、`bezier(0.7,0,0.3,1)`、窗内 0.42→1 反向补偿、卡面 opacity `1−t·3.6`
- 命门：**转场不叠加**（一个接缝只用一式）；转场帧从相邻镜头预算划走；C 式窗内必须是"被点开元素的详情世界"
- 方案 A 接缝分配：A01→A02 失焦交棒（A 的暗场版）；A04→A05 `whip-pan` 轻；A05→A06 `mask-wipe`（EQ 面板当窗）；其余硬切卡拍

### 7.17 仅作语法参考（不整卡套用）的三张
| 卡 · style-key | demo | 只借什么 | 为什么不整卡套 |
|---|---|---|---|
| `type-rhythm-sync` · `karaoke-fill-sync` | `demos/typography/type-rhythm-sync/KaraokeFillSync.tsx`（142 行） | 词级 `[start,end]` 时间表 + 柔边 4% 遮罩 + 活跃词下划线 + 48 根旁白波形 | 它填的是手搓文本；套在真实歌词区=伪造状态（Q1）。录制真实高亮时，**时间表结构**可用来对齐 BGM 词边界 |
| `value-stagger-gradient` · 同名 | `demos/ui-entrance/value-stagger-gradient/ValueStaggerGradient.tsx`（154 行） | `staggerVal(i,n,a,b,ease)` 的"值梯度"思路（`d=i·0.02`、中心脉冲 `amp=lerp(1−distC,.06,.42)`） | 16 根柱是抽象数据，不能替代 EQ/频谱真实纹理；只可用于标注层/非产品段落 |
| `row-embed` · `row-embed` | `demos/ui-entrance/row-embed/RowEmbed.tsx` | 行从空中降下、`rotateX` 收平、嵌入瞬间底边亮一道强调色缝 | 歌词编辑器的行是产品真实行，嵌入动作应在产品内完成（Tier B），本卡只借"缝亮"的收尾语法 |

---

## 8. 缺口清单（无卡可用 → 必须自研）

> 判定标准：功能涉及**产品真实控件的状态变化**，且 skill 内无任何卡能在不伪造 UI 的前提下表达。

| 编号 | 功能 | 为什么没卡 | 自研方案 | 实现风险 |
|---|---|---|---|---|
| **G1** | 逐字歌词推进（YRC 高亮 + 翻译） | `karaoke-fill-sync` 等全部填色卡都作用在手搓文本节点上；真实歌词是产品渲染的字形，无法用 mask 叠层替代（字体度量/抗锯齿对不上） | **Tier B 逐帧序列**：`seq/lyrics/{n}.png` 120fps 采 3–4s → 抽 60 → `PageCamSeq` 推近；翻译行天然在序列里淡入 | 中。风险：录制期歌词滚动速度不可控（可能一秒跳两行），需在采集前把播放进度固定到一句长歌词；序列与 BGM 拍点对不齐时只能整体平移（不能变速，变速会破坏"真实"） |
| **G2** | FFT 频谱 / 节拍可视化 | `chart-live-moves · oscilloscope-stream` 是手绘曲线；真实频谱是 GL canvas 每帧变化 | `grabFramebuffer()` 逐帧合成 GL 内容到 PNG 序列（harness 的 `composite_playing()` 已有此路径），再做 `seq/fft/{n}.png` | 中高。风险：GL 合成与窗口抓图不同步会撕裂；**绝不允许**用合成波形"补"一段，那是最容易被识破的假 |
| **G3** | EQ 曲线拖拽 | 曲线是产品 SVG，拖拽是真实交互；任何"画一条贝塞尔"的做法都是伪造控件 | `seq/eq_drag/{n}.png`（2–3s）+ 斜侧贴面推近 + 一道被圆角裁剪的 sheen | 中。风险：拖拽幅度太小看不出"在改"，需在采集时手动拖出一个明显的峰；LUFS 读数必须同屏刷新 |
| **G4** | 交叉淡化的"能量曲线" | 卡片只有 `bezier-source-converge-merge`（多源汇聚）与 `line-carry-transition`（线接力），都无法单独表达"两条音轨按曲线交叠" | 双 PageCam（A 页/B 页）+ 强调色线 + 曲线的 **mask 形状**驱动 B 页显影；不画产品设置面板里的曲线 | 中高。风险：两层页面同时动容易读成"两段视频并排"；必须让 A 页先占据主体、B 页从框里长出（纵深关系），否则是并排不是淡化 |
| **G5** | 歌词编辑器空格打拍 | 无任何卡涉及"按键落下 → 时间戳被记录"；`font-weight-pump` 只能表达"跟着拍动" | 真实编辑器纹理 + 每次空格一记真实键声 + 行尾时间戳逐个点亮（序列或元素层）；借 `row-embed` 的"缝亮"收尾 | 高。风险：这是全片最"产品专属"的交互，做得假会立刻掉档；建议只在 C 方案或竖版里做 |
| **G6** | PageCam 支持序列纹理 | `assets/lib/PageCam.tsx` 只吃静态 `<Img>` | 自研 `PageCamSeq.tsx`（≤40 行）：把 `Img` 换成分帧取图，transform/zoom 数学一行不改 | 低。风险：CSS `zoom` + 逐帧换图可能触发重新栅格化导致抖动，需用 `remotion still` 抽帧验文字锐度；建议序列帧预解码（`<Img>` 而非 `<Video>`） |
| **G7** | 片头字库补画 | `letterspace-materialize` 只有 13 个几何小写字形 | 手绘 `u`、`m`（如需大写再补 `S`、`M`），每个 `{w, d}` 单线 path，`pathLength` 归一 | 中。风险：字形风格必须与现有一致（字碗正圆、基线 59、x 高 22）；画不像就退回 `lead-word-zoom-assemble` |
| **G8** | 桌面歌词浮窗的真实透明边 | 产品是独立 always-on-top 窗口，主窗口 `grab()` 抓不到 | 单独对浮窗 `grab()` 并存 PNG（带 alpha 或纯色底），再叠在模糊背景上 | 低。风险：纯色底会露出直角边，需在合成时按圆角遮罩 |

**一条硬纪律**：以上任何一项若采集失败，**宁可删掉该镜头**，也不要用合成动效"演"产品状态。删镜头只是少一个功能，假动效会让整片可信度归零。

---

## 9. 片头 / 片尾 / 字卡专章

### 9.1 片头（① 段）三选一
| 方案 | 卡 · style-key | demo | 时长(@60) | 适用 | 风险 |
|---|---|---|---|---|---|
| **首选** | `letterspace-materialize` · 同名 | `demos/opening/letterspace-materialize/LetterspaceMaterialize.tsx` | 210f（A 案压到 168f） | 静谧、高级、纯品牌；与暗场 Fluent 天然相容 | 需补字形 u/m（+S/M） |
| 备选 1 | `lead-word-zoom-assemble` · 同名 | `demos/typography/lead-word-zoom-assemble/LeadWordZoomAssemble.tsx` | 168f（84f@30） | 不想画字形时；首词占满画面，能量更高 | 中高能量开场，会削掉 ② 的对比 |
| 备选 2 | `stroke-segment-build` · 同名 | `demos/opening/stroke-segment-build/StrokeSegmentBuild.tsx` | 330f（165f@30） | 悬念型"断笔成字" | 同样需字形；且 330f 太长，24s 片放不下 |

> 不推荐 `brand-ink-open`（墨线/压印/打字机）与 `text-as-mask`（需一段可播的产品画面当字腔，330f@60 太长）。

### 9.2 片尾（④ 段）二选一
| 路线 | 卡 · style-key | demo | 时长(@60) | 何时选 |
|---|---|---|---|---|
| **加法（推荐）** | `outro-group-photo-launch` · 同名 | `demos/outro/outro-group-photo-launch/OutroGroupPhotoLaunch.tsx` | 290f（A 案 242f） | 多功能产品、要"发布会峰值"（Q8）；本片选它 |
| 减法 | `ui-strip-away-outro` · 同名 | `demos/outro/ui-strip-away-outro/UiStripAwayOutro.tsx` | 288f | 单一"导出/完成"语义、要"复杂性归零"；C 案选它 |

**两者不可同片双收尾。** 若选加法路线，字标定版不再另起 `logo-shrink-wordmark-lockup`（那会变成第二个结尾）；若要更强的"盖章感"，把 `logo-shrink-wordmark-lockup` 的**收束动作**（scale 5.4→1 + 6% 刹车）借进合影的字标登场，不整卡套用。

### 9.3 呼吸字卡（2–4 张，单张 90–100f@60）
| 用途 | 卡 · style-key | 文案（中文，功能名+收益；3 段） |
|---|---|---|
| A03 歌词引导 | `blur-slide` | 逐字歌词 / 每个字的亮起 / 都落在鼓点上 |
| A09 无界面能力 | `blur-slide` | 系统媒体键 / 键盘上那颗播放键 / 直接控制它 |
| （备）技术桥接 | `glitch-cycle` · `glitch-cycle` | 连接中 / SouthsideClient 桥 / 手机与游戏同步 —— 乱码口吻做"系统自述"，只在需要"机器感"时用 |
| （备）章节引导 | `title-demote-to-label` · `title-demote-to-label` | 大标题先当主角再缩到左上角当节标签，适合 B 案的分段 |

**字卡规则**：单张 ≥90f@60（1.5s，R1 对内容卡的底线是 ≥1s，字卡可比字标略短但不可短于 1.5s 否则读不完）；与 outro tagline 查重（P4）；中文必须手切 3–5 段，不能逐字。

---

## 10. 60fps 换算总则（制作 Agent 必读）

### 10.1 要 ×2 的
| 类别 | 例子 |
|---|---|
| 一切帧号常量 | `LOCK=34→68`、`LAND=142→284`、`SCANS=[16,50]→[32,100]`、`CUES=[18,30,…]→[36,60,…]`、`TRAVEL 12–104→24–208`、`CLICK=34→68`、`BRAKE=59→118` |
| 帧区间/窗口 | 描画 52→104、单字窗 22→44、驻留窗、hold（`sign-off 30→60`） |
| 词/元素 stagger 帧距 | `4f→8f`（blur-slide 词间）、`3f→6f`（合影元素 cue）、`4f→8f`（蒸发层间） |
| 卡点 BGM 的 `beatF(n)` | 全表 ×2；SFX `from` 全表 ×2（S3：画面一改就重钉） |
| 转场占用 | flash-cut `切点−5` → `切点−10`，两侧各 10f |

### 10.2 不要 ×2 的
| 类别 | 说明 |
|---|---|
| 缓动控制点 | `bezier(0.34,1.4,0.44,1)`、`bezier(0.45,0.05,0.25,1.12)`、`[0,0.15,0.88,1]→[0,0.055,0.9,1]` 都是**时间比例**，不变 |
| 百分比/比例参数 | `0.26/0.48/0.70`（药丸 T0）、`0.06–0.24`（驻留窗）、`scale 5.4→1`、`amp=lerp(1−distC,.06,.42)` |
| px 位移/尺寸 | `dy=64`、`LIFT_Z=96`、`HALF=676`、`CARD 400×225`、`STACK_STEP=3` |
| 角度/透明度/色值 | `rotY 34°`、`α 0.32→0.14`、`multiply 0.90→0.94` |

### 10.3 要"重标"的（最容易错）
- **速度类系数**：卡上写的 `300px/f`、`34px/f`、`8px/f` 是 @30fps 的。@60fps 下同样的"每秒速度"对应 **一半的 px/帧**；但卡点片常用的是"每帧视觉位移强度"，**建议保持 px/f 数值不变**（即视觉速度翻倍）只在快摇段用，慢段按 px/秒 重算。
- **运动模糊/残影**：依赖"帧间位移"的模糊量（`0.3×px/帧`、`SpeedBlur amount 0.22`、`blur 6px 残影`）在帧率翻倍后帧间位移减半 → **模糊会变弱**，需按新 px/f 回调，不是简单 ×2。
- **数字滚动**：`DigitRoll` 单格 8f 的"滚定时间"要 ×2（16f），但必须仍**短于落卡间距**（24f）——本片 16 < 24 安全。
- **弹簧**：`spring({fps})` 若用 Remotion `spring`，其 `fps` 参数吃的是当前 fps，**不要手改 damping/stiffness**；自写 `springAt` 则需按帧号 ×2。

---

## 11. 验收自检（对方案 A 的逐条预判）

| 编号 | 状态 | 说明 / 需盯的点 |
|---|---|---|
| R1 落定呼吸 | ✅ | A01 字标 hold 60f（1.00s）、A10 sign-off 60f（1.00s）；字卡 A03/A09 各 90f（1.50s） |
| R2 加速非匀速 | ✅ | A04 序列匀速（真实播放，允许）；A08 变速断点；A10 飞入硬加速 |
| R3 宁慢勿快 | ⚠️ | **本片最大风险**：24s 内 A02 只给 200f（3.33s），勉强过"主体动作弧 ≥3s"；A04/A05 各 144/138f。若首检觉得快，优先从 A10 借 30f |
| R4 整画面冲击 ≤3 | ✅ | 仅 A02 归位、A06 闭合、A10 字标 stamp 三处，已分配 |
| Q1 真实截图 | ✅ | 全部产品页走 Tier A/B；仅字标/字卡/合影背景原创 |
| Q2 纹理锐度 | ⚠️ | 2x 全页纹理 + A02 主角卡 4x 元素切片；A05 斜侧推近需验文字边缘；放大走 CSS `zoom` |
| Q3 无手持抖动 | ✅ | 全片无 handheld |
| Q4 光效克制 | ✅ | sheen 仅 A05 一次（裁进圆角）；光束仅 A02 一次；无逐条 glint |
| Q5 单主角 | ✅ | A02 一镜一主角，完整弧 |
| Q6 机位服务可读性 | ⚠️ | A05 斜侧 18° 是"风格化机位"，需验 EQ 标签仍可读；信息镜（A08）全程正视 |
| Q7 特写四件套 | ✅ | A02（侧倾+体积+光）+ A05（贴面）满足；背景用暗色玻璃/金属反差 |
| Q8 发布会合影 | ✅ | A10，6 元素围字标 + crane + 舞台光 + 金尘 |
| Q9 落位真实槽位 | ✅ | A07 落回原位、A08 停在真实刻度、A10 元素落格 |
| Q10 文档级 mock | ✅ | 所有页面内容来自真实界面；无手搓文档 |
| Q11 字高 | ⚠️ | 镜内字幕定 60px、辅助 34px；**逐帧量**（含相机 zoom 与透视压缩）；A05 斜侧镜头要按 `cos(rotY)` 折算 |
| C1 文案重写 | ⏳ | 画面锁定后按最终镜头重写；A01/A10 不加解说（例外项） |
| C2 具体功能名 | ✅ | 每条字幕都带功能名（逐字歌词/EQ/交叉淡化/桌面歌词/重新发现/SMTC） |
| C3 3D 注记同空间 | ✅ | A02 注记进页面坐标系、同相机透视 |
| P4 手法唯一 | ✅ | 光/线/抬升/时间轴/合影各一次；无重复镜头 |
| S3 音画同步 | ⏳ | 画面锁死后 SFX 全表重钉；60fps 下 `from` 全 ×2 并实测输出偏移（S5） |

---

## 12. 前置清单与下一步

**必须先生效（阻塞项）**
1. 修 `promo-video/capture/harness.py` 的启动竞态：`start_flow()` 里 `HOLDER.get("mw")` 为 `None` 时应 `wait_until(lambda: HOLDER.get("mw") is not None, 30000)`，而不是直接 `finish()`。当前 `out/harness.log` 显示 `no main window captured` 后 1s 才 `MainWindow constructed`。
2. 采集前决定演示账号/脱敏口径（真实账号数据不得入片，Q1）。
3. 补画 `letterspace-materialize` 的 u、m 字形（或改用 `lead-word-zoom-assemble`）。

**建议的执行顺序**
1. 先出 Tier A 的 9 张页面纹理（`pages/*.png`）+ `windows/desktop_lyrics.png`；
2. 用 Tier A 渲 A01/A02/A07/A08/A10 五个镜头的静帧，验证构图与字高（`remotion still`）；
3. 再录 Tier B 两段（`seq/lyrics`、`seq/eq_drag`），做 A04/A05；
4. A06 自研镜最后做（依赖双页纹理 + 线接力）；
5. 画面锁定后一次性钉 SFX 表（60fps 帧号），再出带 BGM / 无 BGM 两版。

**可复用的既有资产**
- `assets/lib/PageCam.tsx`（152 行，copy 进工程后改背景色 + 加序列变体）
- `assets/lib/FlashCut.tsx`（21 行，硬切盖闪）
- `assets/lib/ClipCard.tsx`（201 行，若某些"真实动效"最终用录屏 mp4 而非 PNG 序列时可用）
- `template/src/aifl/Main.tsx` 的 `AIFL_SHOTS` 段位表与 SFX 帧表结构（比例与钉帧写法可直接照搬）

---

### 附：一句话给制作 Agent
**全片只有一束光、一条线、一次抬升、一条时间轴和一次合影；其余全部是真实页面在光里被看见。** 任何"看起来像产品在动"的地方，都必须是采集来的真帧，不是画出来的。
