# SouthsideMusic 宣传片 · BGM 选曲与卡点网格分析

研究对象：`C:/Users/Adreno/.agents/skills/video-shotcraft/assets/audio/bgm/` 下 5 首备选。
片长 20–25s，1920×1080 @60fps 构图、终渲 4K60。所有帧号以**整片第 0 帧 = BGM 起点**为基准。

> 分析脚本：[`analyze.py`](analyze.py)（librosa 1.0.0，hop 256 / n_fft 1024，44.1kHz mono 原生采样率）
> 原始产物：[`analysis.raw.json`](analysis.raw.json)（5 首全量打点与结构，审计链）
> 可直接消费的时间线：[`analysis.json`](analysis.json)

---

## 0. 结论速览

- **选曲：`cat-walk.mp3`（Arulo · House）**；全曲中位 129.20 BPM，切片段局部拟合 **129.99 BPM**。
- **切片：源曲 01:29.548 – 01:53.550，52 拍 = 24.00s = 1440 帧 @60fps**，起点正好落在 drop 的第一拍（第 0 帧即重音）。
- 网格：切片内局部最小二乘拟合残差 **1.6ms 均值 / 5.5ms 最大**，拍间 0.461572s（27.694 帧/拍）。
- 结构骨架：品牌开场 b0–b8（0–222f）/ 单主角立传 b8–b20（222–554f）/ 功能爬升 b20–b34（554–942f）/ 峰值收场 b34–b52（942–1440f）。
- 全画面 slam 3 处：**f0（drop）、f443（b16）、f942（b34，break 回归）**，间隔 16 / 18 拍（满足 ≥16 拍）。
- 唯一真正的音乐呼吸位：**f831–f942（b30–b34，4 拍，RMS −16→−22dB 后回弹 +9.6dB）**；片尾 b48–b52 为自然衰减。
- 风险：**未试听**（本机只做测量，无监听条件）；选中依据是测量特征 + 目录元数据 + 授权可溯源性。试听清单见 §8。

---

## 1. 方法（对齐 music-beat-sync §1–§4）

1. `librosa.load(sr=None, mono=True)`，原生 44.1kHz，不做重采样。
2. `hpss` 取打击成分（长曲按 30s 分块、2s 重叠，避免 STFT 内存爆），所有 onset 在打击成分上测。
3. `beat_track(tightness=400, trim=False, units="time")` 得到拍锚点；**不信 tempo 标量**。
4. 网格 = 对拍序列做最小二乘直线拟合 `t_i = t0 + i·T`。**按 64 拍窗口 / 32 拍步长分段拟合**——本批曲目存在 0.1–1.5% 的变速（cat-walk 在 59–89s 有 ~1.5 BPM 漂移），整曲一条直线会产生 55ms 级残差，分段后降到 1.8–2.9ms。
5. 三分类瞬态：kick 40–160Hz / snare（150–500Hz 体 ∩ 1–3kHz 打击面，取几何平均）/ hihat 6–14kHz；阈值用**局部中值 + MAD 自适应**（0.5s 窗，delta=5×MAD，最小间隔 kick 0.18s / snare 0.12s / hihat 0.05s）。
6. 结构：逐拍 RMS(dB) → 递归均值分割（最小段 12 拍 = 3 小节，切分增益 ≥1.6dB 才接受），标注 peak / groove / low。
7. 帧换算只在最后一步发生：`frame = round((t_src − clip_start) × 60)`。

验收指标（切片内）：

| 指标 | 值 | 门槛 | 判定 |
|---|---|---|---|
| 网格拟合残差（均值/最大） | **1.6 / 5.5 ms** | ≤10ms | 通过 |
| 拍点命中真实瞬态（precision，±30ms） | **84.8%**（cat-walk 全曲窗口中位数） | ≥98%（原文档口径） | 未达，原因见下 |
| 打点相对网格细分相位偏移（中位） | kick **3.2ms** / snare **1.9ms** / hihat **0.6ms** | ±10ms | 通过 |
| 瞬态覆盖率（切片内打点被网格细分接受的比例） | kick 76/89 = **85%**、snare 102/112 = **91%**、hihat 180/185 = **97%** | — | 记录 |
| 首拍有效性 | f0 即真实 drop 重音 | 必须 | 通过 |

> 关于 84.8%：原文档的 match≥98% 面向"鼓点几乎每拍都有"的纯电子曲。本曲 40–160Hz 里 kick 与反拍贝斯重叠，检测器点到的瞬态有一半是八分/十六分位置的贝斯与 hat，按四分拍网格算命中率天然到不了 98%。**能直接用的是残差 1.6ms、相位偏移 3.2ms、覆盖率 85%+**；交付 JSON 里的 kick/snare/hihat 已按网格细分过滤（见 §7），不是原始噪声点。

---

## 2. 五首横向对比

| 曲目 | 时长 | BPM | 网格残差(中位) | 覆盖率 | 命中率 | 平均误差 | kick/s | 四踩 | 2·4 拍 snare | 打击占比 | 低频占比 | 能量对比 | 授权可溯源 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **cat-walk** | 124.0s | **129.20** | **2.17ms** | 0.402 | 0.848 | 3.89ms | 3.19 | 0.65 | 0.84 | 0.06 | 0.76 | **33.2dB** | ✅ mixkit/371 |
| bgm-tech-house | 288.7s | 124.53 | 2.02ms | 0.321 | 0.862 | 5.37ms | 2.69 | 0.47 | 0.91 | 0.13 | 0.57 | 23.5dB | ⚠️ 无法逐曲反查 |
| house-vibez | 111.5s | 121.60 | **1.78ms** | 0.419 | **0.891** | 4.22ms | 2.59 | 0.58 | **0.99** | 0.10 | 0.60 | **10.4dB** | ✅ mixkit/745 |
| tonight-hiphop | 113.5s | 105.47 | 2.94ms | 0.359 | 0.908 | 6.83ms | 2.42 | 0.56 | 0.91 | 0.07 | 0.80 | 17.2dB | ✅ mixkit/841 |
| g-eazy-nba-type | 104.3s | 86.86 | **55.54ms** | 0.089 | 0.308 | 16.8ms | 3.14 | 0.64 | 0.79 | 0.11 | 0.85 | 23.6dB | ✅ mixkit/403 |

- **g-eazy-nba-type 直接出局**：86.9 BPM 的 swing/laid-back 律动，网格残差 55ms、命中率 0.31，做不了 ≤3 帧的卡点；且 86 BPM 的 8 拍 = 5.5s，24s 只有 4.4 个乐句，信息密度太低。
- **house-vibez 是"最稳的床"**：121.6 BPM，全曲 BPM 波动 0.07，2·4 拍 snare 命中 0.99，网格最好。但**能量对比只有 10.4dB、99s 内没有任何 breakdown/drop**——用它必须靠画面自己造起伏，做不出"呼吸→炸场"。
- **bgm-tech-house 是最贴 genre 的**（真 tech-house，124.5，打击占比 0.13，频谱最均衡），结构上 162s 处有一次 +2dB 的抬升。但授权记录写的是"首批沿用，无法逐曲反查、商用前复核"——宣传片是要发出去的物料，这条是硬风险。
- **tonight-hiphop** 105 BPM hip-hop，低频 0.80、动态 17.2dB，情绪偏 street，不是"精致桌面软件"的语汇。

### 各曲能量结构（自动分段，秒）

**cat-walk**（选中）
```
low   0.2–14.6   (-12.2dB)   intro 滤波铺底
low  14.6–25.2   ( -2.1dB)
groove 25.2–30.7 (+0.3dB)
peak  30.7–58.9  (+4.8dB)   ← 第一个满编
low  58.9–88.6   (-2.1dB)   ← 含 59–89s 的变速/半能量段
peak  88.6–103.4 (+5.7dB)   ← 【推荐切片从这里起】
peak 103.4–113.1 (+3.8dB)
outro 113.1–118.2 (-19.2dB)
```
切片内细看（逐拍 RMS）：b0–b29 稳定在 −12.5dB；**b30–b33 掉到 −16.0 → −22.1dB（1 小节 break）**；b34 弹回 −12.5dB（+9.6dB，全片最大能量跳变）；b48 起渐弱到 −22dB。

**bgm-tech-house**
```
intro 0.2–6.5 → low 6.5–14.3 → groove 14.3–69.5 → low 69.5–88.3 → low 88.3–115.9
groove 115.9–162.4 → peak 162.4–239.0 → 渐弱 239.0–279.4
```
**house-vibez**：groove 0.1–98.9（几乎全曲一个能量面）→ low 98.9–104.8 → outro 104.8–110.1
**tonight-hiphop**：intro 0.3–7.2 → low 7.2–51.2（四段）→ groove 51.2–106.1 → outro 106.1–112.3
**g-eazy-nba-type**：intro 0–12.9 → peak 12.9–44.3 → low 44.3–75.5 → peak 75.5–86.3 → outro 95.3–103.0

---

## 3. 选曲取舍

**选 `cat-walk.mp3`。**

| 维度 | 判断 |
|---|---|
| 鼓点强度 | 四踩 0.65、2·4 拍 snare 0.84、kick 3.19/s（含反拍低频），鼓组清楚，能撑住"被砸"的剪辑点 |
| 质感/是否像游戏 | 129.99 BPM house，低频占比 0.76、频谱重心 2542Hz——偏低频、暗、克制，不是明亮 chiptune/EDM 音色。**仍需试听确认无卡通 pluck/rise 音色**（本机无法监听） |
| 卡点可用性 | 切片内残差 1.6ms、打点相位偏移 ≤3.2ms；52 拍正好 1440 帧；b30–b34 有 1 小节真空 break，b34 回归是全曲最大 +9.6dB 跳变——天然给"呼吸→炸场" |
| 动态结构 | 33.2dB 能量对比，5 首最高；有 intro/drop/break/outro 的真实分段，不是一条平线 |
| 授权 | Mixkit Stock Music Free License，免署名可商用；来源可溯源（Arulo / mixkit 371），比"无法逐曲反查"的 bgm-tech-house 安全 |
| 代价 | ① 129.99 BPM 比 tech-house 常见的 124 快 ~5%，单拍 0.4616s，一个 8 拍乐句只有 3.69s，剪辑点要更密；② 低频重（0.76），混音时 BGM 容易糊住 SFX，需要侧链或把 BGM 压到 −20LUFS 量级；③ 切片之前的 85–89s 段拍点不稳（beat_track 在该段漂到 0.505s/拍），所以**不能用"drop 前 4 拍"做冷开场预卷**，只能从 drop 本身起。 |

**备选（若试听后 cat-walk 音色不合适）**：
1. `bgm-tech-house` —— 最贴 tech-house，124.5 BPM、结构段落最完整（162s 处 +2dB 抬升）；但先解决授权溯源，否则不能商用。
2. `house-vibez` —— 网格与 2·4 拍最干净，适合"画面自己造节奏"的版本；但它没有 breakdown，必须放弃"呼吸位"设计。

---

## 4. 推荐片段与截取策略

- **源区间：01:29.5478 – 01:53.5504**（cat-walk），**52 拍整 = 24.0026s = 1440 帧 @60fps**
- 起点 b193 = 89.548s，正是 drop 第一拍；终点 b245 = 113.550s，落在片尾自然衰减段内。
- 切片内局部拟合：`T = 0.461572s`，`t0 = 89.548634s`，残差 1.6ms 均值 / 5.5ms 最大。

截取（保留 44.1k 原样，交给 Remotion 前再决定是否 48k）：

```bash
ffmpeg -i "C:/Users/Adreno/.agents/skills/video-shotcraft/assets/audio/bgm/cat-walk.mp3" \
  -ss 89.5478 -t 24.0026 -af "afade=t=in:st=0:d=0.003" \
  -c:a pcm_s16le -ar 48000 -ac 2 \
  "public/audio/bgm-catwalk-clip.wav"
```

- `-ss` 放在 `-i` 之后（解码器精确 seek），否则 mp3 帧边界会带 ±26ms 误差。
- 淡入只给 **3ms**：实测切点处 RMS 已在 0.21 并持续上升（拍锚点就是攻击起点，body 峰在 +30ms），20ms 淡入会把第一击削钝。3ms 只为消除切点爆音。
- **不要**在片尾加淡出——源曲 b48–b52 本身就在衰减（−15.4 → −22.1dB），再叠淡出会提前把尾巴吃掉。
- 进入 Remotion 后 BGM 从第 0 帧起播，`beatOffsetFrames = 0`，不需要 `startFrom` 之外的偏移。
- 首渲后按 sound-design §4.6 用交叉相关实测**输出音轨偏移**（AAC priming，48kHz 典型 ≈2048 samples ≈2.56 帧@60），写进 `OUTPUT_AUDIO_OFFSET_SEC`，**不要改源分析值**。

---

## 5. 镜头结构建议（4 段骨架，全部帧号可直接用）

| 段落 | beat | 秒 | 帧 @60 | 音乐事实 |
|---|---|---|---|---|
| **品牌开场** brand | b0–b8 | 0.000–3.693 | **0–222** | 切片起点即 drop 重音（f0）；8 拍满能量，适合字标砸落 + 产品轮廓 |
| **单主角立传** hero | b8–b20 | 3.693–9.232 | **222–554** | 稳定的四踩+2·4 拍 snare；b16（f443）有一次可用的中段重音 |
| **功能爬升** climb | b20–b34 | 9.232–15.694 | **554–942** | b20 起功能逐项步进；**b30–b34（f831–942）是 1 小节真空 break**（−16→−22dB），是唯一真正的呼吸位 |
| **峰值收场** peak | b34–b52 | 15.694–24.003 | **942–1440** | b34 回归（+9.6dB 全片最大跳变）；b46（f1274）末次重音；b48–b52 自然衰减做尾卡 |

> 说明：b8 / b20 / b52 是**编辑切分点**（都落在小节线上，4 拍整数倍），音乐在 b0–b30 之间是稳定 groove，没有第二段 drop。骨架贴着"drop 起 → 呼吸 → 回归"排，不假装音乐有更多起伏。

### 全画面级 slam（≤3 处，间隔 ≥16 拍）

| # | 帧 | beat | 音乐事件 | 用法 |
|---|---|---|---|---|
| 1 | **0** | b0 | 切片起点 drop | 开场字标/产品剪影硬切落下。f0 无法做负向补偿，impact 的 SFX 容差 ±2 帧，见 §7 |
| 2 | **443** | b16 | 中段重音（kick 1.37 / snare 1.00） | 主角镜头收束：整画面骤缩一次，接 0.5s hold |
| 3 | **942** | b34 | break 回归（+9.6dB） | 峰值收场第一击：全画面 slam，之后不再有任何整画面冲击 |

- **b46（f1274，末次重音）不做全画面 slam**——它与 b34 只隔 12 拍，违反 ≥16 拍。改为**元素层**字标落定（scale 1.06→1.00 + 一次高光），不震屏、不闪帧。
- 其余每个 kick/snare 只作为**元素层**动效与切点时机，不做画面幅度脉冲（aesthetic-rules R4：kick 命中表是候选池不是触发器）。

### 呼吸位与字卡落点

| 位置 | 帧 | 时长 | 用途 |
|---|---|---|---|
| **主呼吸** | **831–942**（b30–b34） | 1.85s | 音乐真空 break，全片唯一的"静"。字卡一组（3–5 词）落在 f860 左右，f930 前必须起动画离开，b34 回归时画面要有东西被"炸"出来 |
| **尾卡** | **1329–1440**（b48–b52） | 1.85s | 片尾自然衰减段，字标 + 一行 tagline（URL/版本号），保持静止，不要再加入场动效 |
| 可选中段 hold | 222–332（b8–b12） | 1.83s | 开场 slam 之后的第一次"停"；**不是音乐呼吸位**，只是画面节奏需要。若开场已经够慢就删掉 |

### 时间线常量（Remotion 直接抄）

```ts
export const FPS = 60;
export const SOURCE_BEAT0_SEC = 0;          // 切片已对齐到拍，源分析 t0 折算为 0
export const BEAT_INT_SEC = 0.461572;       // 129.9905 BPM
export const OUTPUT_AUDIO_OFFSET_SEC = 0;   // 首渲后按 sound-design §4.6 实测填入
export const beatF = (n: number) =>
  Math.round((SOURCE_BEAT0_SEC + OUTPUT_AUDIO_OFFSET_SEC + n * BEAT_INT_SEC) * FPS);

export const SHOTS = {
  brand: { from: beatF(0),  to: beatF(8)  },   // 0    – 222
  hero:  { from: beatF(8),  to: beatF(20) },   // 222  – 554
  climb: { from: beatF(20), to: beatF(34) },   // 554  – 942
  peak:  { from: beatF(34), to: beatF(52) },   // 942  – 1440
};
export const SLAMS  = [beatF(0), beatF(16), beatF(34)];        // 0, 443, 942
export const BREATH = { cardA: [beatF(30), beatF(34)],        // 831 – 942
                        cardB: [beatF(48), beatF(52)] };      // 1329 – 1440
export const LOGO_HIT = beatF(46);                            // 1274（元素层，不是 slam）
```

---

## 6. `analysis.json` 说明

```jsonc
{
  "track": "cat-walk",            // 曲名（文件 stem）
  "file": ".../bgm/cat-walk.mp3", // 源文件绝对路径（未复制进业务目录）
  "sampleRate": 44100,
  "bpm": 129.9905,
  "beatOffsetFrames": 0,          // BGM 从整片第 0 帧起，且第 0 帧就是拍
  "durationSec": 24.0026,         // 切片长度（= 正片 BGM 长度）
  "section":  { "name": "brand", "start_beat": 0, "end_beat": 8,
                "startSec": 0.0008, "endSec": 3.6934, "startFrame": 0, "endFrame": 222 },
  "sections": [ /* 4 段骨架，字段同上，name = brand|hero|climb|peak */ ],
  "beats":   [0, 28, 55, ... 1440],   // 53 个拍点帧（0..52）；beats[n] 即 beatF(n)；1440 是排他终点
  "beatIntervalFrames": 27.6943,
  "kicks":   [...], "snares": [...], "hihats": [...],  // 帧号，已按网格细分过滤
  "strongHits": [0, 443, 942],        // 全画面 slam 帧
  "strongHitBeats": [0, 16, 34],
  "drumPhase": { "kick": {"candidates":89,"kept":76,"dropped":13,"median_offset_ms":3.2}, ... },
  "clip":   { "sourceStartSec": 89.5478, "sourceEndSec": 113.5504, "lengthSec": 24.0026, ... },
  "grid":   { "bpm": 129.9905, "periodSec": 0.461572, "t0SourceSec": 89.548634,
              "clipFitResidualMsMean": 1.615, "clipFitResidualMsMax": 5.528, ... },
  "params": { "hop_length": 256, "n_fft": 1024, "fps": 60, ... },
  "provenance": { ... }
}
```

要点：
- **帧号全部是整片时间线帧**（BGM 从 0 起、无 offset）。拍号 = `beats` 的下标。
- `kicks/snares/hihats` 是"落在网格细分上"的打点：kick/snare 只保留 {整拍, 反拍}（±0.09 拍），hihat 保留 {0, ¼, ½, ¾}。被丢掉的 13%/9%/3% 是低频贝斯泄漏与攻击拖影，不要拿来钉帧。
- 这些打点仍带 ±1–2 帧的检测抖动；**要钉死帧的动效用 `beats` / `strongHits`**，单个 kick 只当元素层时机。
- `section` 是首段（品牌开场）的快捷字段，完整 4 段看 `sections`。

---

## 7. SFX 需求清单（Freesound 选材，不下载）

前提：BGM 已占满 40–14kHz，SFX **只钉画面独有动作**；同一拍不要叠加多个长尾。全部 CC0 优先，48kHz/24bit，避免游戏音包音色（aesthetic-rules S1：禁的是音色，不是动作拟音）。

| # | 对应动作/镜头 | 声源/材质 | 建议时长 | 位置（帧 / 拍） | Freesound 搜索词 | 注意 |
|---|---|---|---|---|---|---|
| 1 | 开场 drop 落地（字标砸下） | 影院低频冲击，非 braam 音效包 | 0.8–1.5s | **f0** / b0 | `cinematic impact low boom` `deep sub impact` | 峰值必须在 f0；f0 无法负补偿，正片重音别放前 2 帧 |
| 2 | 镜头推入播放器主界面 | 短促空气感 whoosh | 0.3–0.7s | f222 / b8 | `short soft whoosh` `air pass by` | 干、无长尾；不要 EDM sweep |
| 3 | 封面取色渐变铺开 | 玻璃/微光颗粒 | 0.6–1.2s | f332 / b12 | `subtle shimmer` `glass sparkle soft` | 单点、克制；不要群发 sparkle |
| 4 | 逐字歌词逐字亮起 | 真实纸张/玻璃细碎拟音 | 每字 0.05–0.08s，5–8 字 | f443 起 / b16 | `paper tick` `glass tick soft` | 禁 synth blip / 打字机游戏音；字密时收成 2–3 下 |
| 5 | 频谱/节拍可视化点亮 | cinematic riser | 1.2–2.0s，峰落 f554 | f471→f554 / b17→b20 | `cinematic riser` `tension riser short` | 不要 riser 尾巴盖住下一拍鼓 |
| 6 | EQ / 响度旋钮调节 | 真实机械旋钮/开关 | 0.1–0.3s | f665 / b24 | `mechanical switch click` `knob click` | 真实机械质感，不要 beep/confirmation tone |
| 7 | 智能交叉淡化 | 滤波扫过的 whoosh | 0.4–0.8s | f776 / b28 | `filter sweep whoosh` `transition whoosh short` | 不要 DJ scratch / 游戏切换音 |
| 8 | break 前抽真空 | 反向镲/吸气，收在 f831 | 0.6–1.0s | f776→f831 | `reverse cymbal swell` `air suck reverse` | 必须收干净，尾巴不能进 break |
| 9 | 呼吸字卡落纸 | 干燥纸卡滑动 | 0.3–0.6s | f831 / b30 | `dry paper slide` `card lay down` | 单次动作；真实纸声，不要 UI 提示音 |
| 10 | break 回归 slam | 更短更硬的低频冲击 | 1.0–1.5s | **f942** / b34 | `punchy impact` `tight cinematic hit` | 与 #1 音色要区分开，避免同一个 sample 复用 |
| 11 | 重新发现收藏（卡片翻出） | 纸卡/卡片轻响 | 0.2–0.4s | f1108 / b40 | `card shuffle short` `paper flick` | 轻，别和 2·4 拍 snare 抢位置 |
| 12 | 桌面歌词浮出 / 收尾字标 | 柔和金属/玻璃 + 冲击尾巴 | 0.3–0.5s（字标 1.0–2.0s） | f1219 / b44；f1274 / b46 | `soft metal ting` `glass chime quiet`；`logo impact cinematic` | 不要 notification 提示音；收尾 impact 的自然混响可以越过片尾，别硬切 |

统一注意：
- 每条的 `Sequence.from = 目标帧 − 音源峰值滞后帧 − 输出音轨偏移帧`（sound-design §4.6），峰值滞后逐文件实测。
- 同一小节内不要叠两个以上 >0.6s 的长尾；break 段（f831–942）保持"几乎无声"，只留 #9。
- 下载后写 `audio-manifest.json` + `FREESOUND-CREDITS.md`，渲染时不联网。

---

## 8. 复现命令与已知限制

```bash
cd D:/PythonProjects/SouthsideMusic/promo-video/research/bgm

# 1) 五首全量分析（系统 python 已装 librosa 1.0.0）
D:/Python/python.exe analyze.py analyze --out analysis.raw.json

# 2) bar 级能量视图（选片段用）
D:/Python/python.exe analyze.py bars --track cat-walk --from 80 --to 115

# 3) 生成可直接消费的时间线（本次交付用的就是这条命令）
D:/Python/python.exe analyze.py timeline --raw analysis.raw.json \
  --track cat-walk --clip-start-sec 89.548 --length-sec 24.3 \
  --sections brand:0,8 hero:8,20 climb:20,34 peak:34,52 --hits 0,16,34 \
  --out analysis.json
```

若换机器没有 librosa：

```bash
D:/Python/Scripts/uv.exe run --with librosa --with soundfile --with numpy --with scipy \
  python analyze.py analyze --out analysis.raw.json
```

关键参数：`hop_length=256`（5.8ms/帧）、`n_fft=1024`、`sr=44100` 原生、`tightness=400`、
分段拟合 `64 拍/32 拍步长`、`fit_tol=30ms`、瞬态阈值 `delta=5×MAD, base_win=0.5s`。

**已知限制（必须人工确认的）**：
1. **未试听**。本机只有测量能力，没有监听环境。选曲依据是频谱/节奏/结构测量 + Mixkit 目录元数据（House / Arulo）+ 授权可溯源性。
2. 试听时重点听三件事：① 40–160Hz 里 kick 与反拍贝斯是否糊成一团（低频占比 0.76，是本曲最大隐患）；② 有没有卡通 pluck、rise、game-over 类音色；③ 89.5s 的 drop 是否真的"炸"，还是只是电平抬高（RMS 显示 +7dB，但可能是压缩器而非编曲）。
3. `kicks/snares/hihats` 是候选池不是触发器：kick 3.19/s（含反拍贝斯），snare 4.28/s（crack 频段混入 hat），密度高于真实鼓件；钉帧请用 `beats`/`strongHits`。
4. 129.99 BPM 的 8 拍乐句只有 3.69s，比 124 BPM 的 tech-house 紧 5%；如果剪辑发现"每个镜头都来不及看清"，优先把 b20–b34 的爬升段砍成 12 拍，或换 `house-vibez`（121.6 BPM，8 拍 = 3.95s）。
