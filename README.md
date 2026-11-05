# 老虎AI全链路内容操盘手 · tiger-video-expert

[![许可: CC BY-NC-SA 4.0](https://img.shields.io/badge/%E8%AE%B8%E5%8F%AF-CC%20BY--NC--SA%204.0-ef9421.svg)](https://creativecommons.org/licenses/by-nc-sa/4.0/)
[![GitHub](https://img.shields.io/badge/GitHub-adwilds%2Ftiger--video--expert-181717.svg)](https://github.com/adwilds/tiger-video-expert)

把"拍完的素材"推进到**可编辑的剪映草稿 + 四个平台的发布稿**。
包含一个技能（执行链路）和一个专家（判断与拆工序）。

从**我是谁**开始，到**下一条该拍什么**结束，中间不跳步。

## 完整链路

十段，每段都有固定的输入输出。上游三段的产出决定下游值不值得做，所以它们不是可选项。

| # | 阶段 | 谁做 | 输入 | 输出 | 命令 |
|---|---|---|---|---|---|
| 1 | 身份 | 人 | 账号定位、目标观众、变现目标 | `profile.json` + `身份卡.md` | `profile init` / `check` |
| 2 | 情报 | 脚本抓、人或 agent 喂 | RSS/Atom 源、手工条目 | `intel/raw.jsonl` | `intel fetch` / `intel add` |
| 3 | 选题 | **脚本排序，人做判断** | 情报库 + 身份卡 | `00-intel/选题候选.md`、`选题卡.md` | `intel rank` / `topic pick` |
| 4 | 脚本 | 人或专家 | 选题卡 | `01-script/script.md` | — |
| 5 | 预估 | 脚本体检、人看结论 | 脚本 + 你的历史数据 | `06-publish/预估.md` | `forecast` |
| 6 | 素材 | 人 | 脚本，或已有实拍 | `02-raw/main.mp4`（+ `cut.mp4`） | — |
| 7 | 转写切点 | 脚本 | 素材 | `transcript.json`、`切点对照表.md` | `ingest` / `cut` |
| 8 | 草稿 | 脚本 | 切点表 + 画面计划 | 剪映草稿、`plan.json` | `build` |
| 9 | 分发 | 脚本 | 草稿 + 平台规则 | 四平台发布稿、检查表 | `verify` / `pack` |
| 10 | 复盘 | 脚本聚合，人决策 | 各平台后台数据 | `metrics.csv`、降权/首发建议 | `metrics` |

几段的分界线要说清，否则容易用错：

- **身份决定情报筛什么。** 赛道关键词是打分里权重最高的一项（35%）。不写身份，抓回来的东西只能按「新」和「热」排，等于没有筛。
- **脚本排序，人做判断。** `intel rank` 只算五个能算的客观特征：赛道匹配、热度、时效、可验证、可拍。「反常识」「值不值得做」是语义判断，机器算不出来，硬算就是假装有结论。
- **`topic pick` 不做决定，只做归位。** 它把候选落成一张按模板排好的选题卡，**四栏是空的**。填不填、做不做、要不要换角度，是人写上去的。
- **`forecast` 不是播放量预测。** 它只做两件事：把脚本拆成钩子/结构/密度/平台适配四项做体检，再拿**你自己账号**的历史中位数按结构分上下浮动。没有 ≥3 条历史数据就不给绝对数字——拿「新号一般几百播放」凑一个数，看起来像预测，实际是猜。

同一条情报被多家媒体报道时，`intel rank` 会合并成一条并标出「N 个源在说同一件事」——不合并的话，候选表前三行就是同一条内容，去重的工作又推回给人了。

## 一条命令一步

```bash
# 上游：我是谁 -> 情报 -> 选题
python scripts/pipeline.py profile init                      # 建身份卡模板（工作区级，一次）
python scripts/pipeline.py profile check                     # 校验：观众写成「年轻人」会被拦下
python scripts/pipeline.py intel  fetch .                    # 抓 RSS/Atom 到情报库
python scripts/pipeline.py intel  add   . --title "..."      # 手工/agent 喂一条
python scripts/pipeline.py intel  rank  .                    # 五项特征打分排序
python scripts/pipeline.py topic  pick  . 1                  # 落成选题卡（四栏留空，自己填）

# 下游：脚本 -> 预估 -> 素材 -> 粗剪 -> 草稿 -> 发布 -> 复盘
python scripts/pipeline.py doctor          # 环境和剪映草稿库自检
python scripts/pipeline.py init   <目录> --name 第01期
python scripts/pipeline.py forecast <目录>  # 结构体检 + 历史基线外推（不是播放量预测）
python scripts/pipeline.py ingest <目录>   # 转写 + 音轨标准化
python scripts/pipeline.py cut    <目录>   # 算切点
python scripts/pipeline.py build  <目录>   # 铺画面 + 出草稿
python scripts/pipeline.py verify <目录>   # 交付前校验
python scripts/pipeline.py pack   <目录>   # 四平台发布稿
python scripts/pipeline.py metrics report --all .   # 数据回填与迭代
python scripts/pipeline.py board                    # 生成工作台 老虎AI爆款·内容专家.html
```

`pipeline.py status <目录>` 随时告诉你走到哪、下一步该跑什么。

## 它替你做的四件难事

**1. 切点算的是"保留什么"，不是"删什么"。**
两种错误的代价不对称：漏删一句废话=啰嗦两秒；误删一个"但是"=因果断裂，而且成片里听不出来。所以默认保留，只有明确命中删除规则才切，保护词命中即永不删。

**2. 长停顿压缩而不是丢掉。**
机械删除静默会让人听着喘不上气。这里把 3 秒的思考停顿压到 0.8 秒，保留呼吸感。

**3. 跳切用换机位盖住。**
每剪掉一段，画面上就有一个跳点，观众看到的是"人突然窜了一下"。`build` 在每个断开处自动切第二机位；没有第二机位就把时间点写进 `B-roll待补清单.md`，不硬接。

**4. 音画一起切。**
原音轨 60 秒、剪完 45 秒，直接挂整条音轨必然漂移。音频按同一套保留区间切段，一段对一段，粗剪完不用手动对轴。

## 结构

```
tiger-video-expert/
├── SKILL.md                    # 技能主控：三开关 + 形态判断 + 工序 + 精剪 SOP + plan.json 格式
├── profile.json                # 身份卡（唯一真源，账号级；身份卡.md 由它生成）
├── intel-sources.json          # 情报源清单（RSS/Atom，自己往里加）
├── intel/raw.jsonl             # 情报库，跨期累积，第二期不用重抓
├── 老虎AI爆款·内容专家.html      # 只读总览，`board` 生成，双击打开
├── 知识库/                      # Obsidian vault：选题判断 / 脚本结构 / 切点规则 / 复盘方法
├── assets/                     # 品牌资源（logo-128.png / favicon）
├── run.cmd                     # 包装器，免激活 venv
├── scripts/
│   ├── pipeline.py             # 单入口 CLI，把下面几步串起来
│   ├── profile.py              # 身份卡：校验必填项，生成人读版
│   ├── intel.py                # 情报：抓取、去重合并、五项打分、落选题卡
│   ├── forecast.py             # 预估：结构体检 + 历史基线外推（不给假数字）
│   ├── draft_compat.py         # 剪映草稿库兼容层（布局探测 / 写对文件名 / 修元信息）
│   ├── audio.py                # 高通 + 降噪 + 两遍 loudnorm 到 -14 LUFS
│   ├── transcribe.py           # 视频 -> 逐字转写（抽音轨 + faster-whisper + SRT）
│   ├── cutlist.py              # 转写 -> 切点表 + 时间线重映射
│   ├── draft_builder.py        # 编排层：切点/plan -> 剪映草稿（转场/动画/滤镜/字幕样式）
│   ├── check_plan.py           # 交付前校验：素材/画布/时间轴/音画/字幕/平台时长
│   ├── pack.py                 # 一份内容 -> 四平台成稿 + 发布前检查表
│   ├── metrics.py              # 数据回填：记数 + 中位数 + 降权/首发建议
│   ├── selftest_pipeline.py    # 端到端自检，不依赖真实素材
│   └── selfcheck.py            # 最小自检：只验草稿引擎和环境
├── docs/
│   ├── 操作手册.md             # 从素材到发布，用户视角
│   └── 产品规划-v1.md          # 完整人机分工与实施顺序
├── expert/
│   ├── .codebuddy-plugin/plugin.json
│   ├── agents/content-pipeline.md
│   └── avatars/expert.png
└── examples/
    ├── demo/                   # 一条跑通的完整项目（含双机位假素材）
    └── selftest/
```

`init` 建的每一期项目长这样，编号就是执行顺序：

```
第01期/
├── 00-intel/        选题候选.md、topics.json、选题卡.md
├── 01-script/       script.md
├── 02-raw/          main.mp4、cut.mp4
├── 03-transcript/   transcript.json、voice.srt、voice_std.wav
├── 04-cut/          cutlist.json、切点对照表.md
├── 05-plan/         plan.json、B-roll待补清单.md
├── 06-publish/      post.json、预估.md、四个平台稿、发布前检查.md
└── 07-metrics/      metrics.csv
```

## 快速开始

```bash
python -m venv .venv
.venv/Scripts/python -m pip install pyJianYingDraft Pillow imageio-ffmpeg faster-whisper av pymediainfo

# 端到端自检（不需要真实素材，约 1 分钟）
.venv/Scripts/python scripts/selftest_pipeline.py
```

## 依赖

| 依赖 | 用途 | 许可 |
|---|---|---|
| Python 3.9+ | 运行时 | PSF |
| pyJianYingDraft | 剪映草稿数据结构与生成 | Apache-2.0 |
| Pillow | 产物校验、头像生成 | MIT |
| faster-whisper + ctranslate2 | 本地语音转写，CPU 可跑，无需 GPU | MIT |
| imageio-ffmpeg | 自带 ffmpeg 二进制，抽音轨、降噪、响度、混音 | BSD-2 |
| av | 读取视频元信息，替代 ffprobe | BSD-3 |
| pymediainfo | 音画时长校验（可选，缺了就跳过该项） | BSD-2 |

`ffmpeg` 不需要单独安装，`imageio-ffmpeg` 自带可执行文件。若只做图片口播，`faster-whisper` 也可不装。

## 许可

[![CC BY-NC-SA 4.0](https://licensebuttons.net/l/by-nc-sa/4.0/88x31.png)](https://creativecommons.org/licenses/by-nc-sa/4.0/)

**CC BY-NC-SA 4.0**（署名 — 非商业性使用 — 相同方式共享 4.0 国际）
全文见 [LICENSE](LICENSE)，条款落到具体行为的说明见 [NOTICE.md](NOTICE.md)，
源码文件头已标 `SPDX-License-Identifier: CC-BY-NC-SA-4.0`。

一句话版本：

- **署名**：转载、改编、二次分发都要标明作者和出处
- **非商业**：不能卖、不能做付费课程/社群内容、不能用于接单交付、不能引流变现
- **相同方式共享**：改了别人的衍生作品必须用同一许可开放，不能闭源打包卖

**商用授权**：版权归作者本人所有，**作者自己不受非商业条款限制**。企业内训、代运营交付、
二次售卖、贴牌集成等场景请先联系作者取得书面授权（见 [NOTICE.md](NOTICE.md)）。

**关于把 CC 许可用在软件上**：Creative Commons 官方不建议将 CC 许可用于源代码——它不含专利授权，
与代码分发惯例也有摩擦。本仓库是「脚本工具 + 内容方法论 + 知识库」捆在一起的整体资产，
统一一个许可比拆成两套更容易解释和遵守，所以整体采用 CC BY-NC-SA 4.0。
第三方依赖（pyJianYingDraft / Apache-2.0、faster-whisper / MIT 等）各自沿用原许可，不受本条影响。

## 已知边界

- 只负责**生成**草稿，不做剪映 UI 自动化，不碰导出按钮。
- **剪映版本决定写什么，不靠猜**。剪映 6.0 起把 `draft_content.json` 改成加密载荷（base64 串，不以 `{` 开头）。本项目不逆向解密，而是读注册表拿到剪映版本，按版本代际决定落盘方式：`≥ 6.0` 双写明文 `draft_content.json` + `draft_info.json`，剪映打开时自行升级加密；`< 6.0` 只写 `draft_content.json`。判错了用 `build --layout plaintext|info` 手动覆盖，不用改代码。
  **已在剪映 11.8.0.14711 上实测**：明文时间轴能正常打开，剪映保存时会把它改写为加密载荷并生成 `.backup/`、`Timelines/` 等标准结构。
- **草稿库位置读剪映自己的配置**（`globalSetting → currentCustomDraftPath`）。剪映允许把草稿目录改到别的盘，此时默认的 `%LOCALAPPDATA%` 路径是不对的——写进去剪映列表里根本不会出现。项目配置里可以写 `%JIANYING_DRAFTS%`，运行时按本机配置解析。
- **不覆盖你在剪映里的精剪改动**。草稿一旦被剪映打开并保存，`draft_content.json` 就变成加密载荷，此时 `build` 会**拒绝执行**（退出码 2）而不是整目录重建。要保留就另存一份，确实要覆盖加 `--force`。
- 若剪映报"草稿内容已损坏"，说明该版本不再接受明文时间轴。此时改用剪映自己新建草稿、再把素材拖进去，或把剪映降到仍接受明文的版本并关闭自动更新。
- 发布环节**不自动化**，只出成稿和检查表，最后一下点击留给人。平台端自动发布的失效方式是静默的——账号被限流你不一定知道。
- 不保证播放量、完播率、涨粉。只保证结构成立、事实站得住、工序能跑。
