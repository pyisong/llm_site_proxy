---
name: booktok-remotion
description: BookTok Remotion 成片。镜头节奏与构图跟生文/生图 Skill、主体资产、片头片尾同时生效；禁止另起 Remotion 项目。
category: motion
---

# BookTok Remotion 成片（叠加生效）

本 Skill 只约束 **工厂成片边界**。官方 `remotion-*` 负责写法；工厂已有 `vtok_ai_factory/remotion/` 与 `RemotionAdapter`。

未勾选任何成片 Skill 时，生产路径与原来完全一致。勾选官方 `remotion-*` 后，仍走同一套脚本/分镜/成片流水线，只是运动与构图按官方 Skill 来写。

## 叠加规则（强制）

被选中的 Skills 与宿主参数 **全部同时生效**，禁止互相覆盖、禁止二选一。

| 来源 | 负责 | 不可被本 Skill 改掉 |
|------|------|---------------------|
| 生文 Skills | 口吻、选题角度、旁白文风 | JSON 脚本/分镜契约 |
| 生图 Skills | 单帧画面风格、配色、构图气质 | 主体资产身份 |
| 主体资产 | 人物/道具必须入镜 | 资产 ID 与形象 |
| 片头模板 / 成套 endcard | 开场设计 | `intro_template_id` / `endcard_pack_id` |
| 片尾模板 + 点题句 | 收束卡片 | `outro_template_id` / `outro_summary` |
| 本 Skill | 工厂边界：工程、片头片尾、主体、JSON 契约 | — |
| 官方 remotion-* | 镜头节奏、转场、字号、`interpolate()` 等写法 | 不得另起项目 |

冲突时：

1. 宿主片头/片尾/主体资产锁定项优先。
2. 生文 / 生图 Skill 管风格；官方 remotion-* 管「怎么动、怎么排」。
3. 禁止 `npx create-video`，禁止新建 blank Remotion 工程；`remotion-create` 在工厂里只是安全桥。
4. 禁止自己 `npx remotion studio` / `npx remotion render`；禁止把输出改成 `.tsx` 或丢掉宿主 JSON。

## 成片约束

- 引擎固定走工厂 Remotion：`BookTokComposition` 或 `InfographicExplainer`。
- 动画用 `useCurrentFrame()` + `interpolate()`，不用 CSS `transition`/`animation`。
- 重要字：1080 宽片主标题 ≥84px、关键说明 ≥44px；安全区左右 ≥80px、上下 ≥100px。
- 每镜只强调一件事；片头、正片、片尾三段都要有，不能为了「更像宣传片」砍掉片头或片尾。
- 有主体资产时，正片镜头必须能认出该主体；不要换成无关模特或空镜。
- 有片头口播时，开场信息（书名/作者/国籍/承诺）必须进画面与旁白，不要改写成纯 Logo 闪白。

## 输出

仍然输出宿主要求的可解析 JSON（脚本 / 分镜 / 视觉 prompt）。可在现有字段上补充运动提示（如 `emotion`、镜头建议），不要改字段名、不要丢掉 scenes。
