---
name: remotion-create
description: Factory-safe Remotion create. Use the existing BookTok Remotion project; never scaffold a new app.
version: 4.0.512
---

# Remotion Create（工厂安全桥）

官方 `remotion-create` 在工厂里被这份说明替换。流水线已经有 Remotion 工程，**不要**再搭新项目。

## 禁止

- `npx create-video`
- 新建 blank Remotion 工程
- 自己执行 `npx remotion studio` / `npx remotion render`（由 `RemotionAdapter` 成片）
- 把输出改成 `.tsx` 工程或丢掉宿主 JSON

## 必须沿用

- 工程：`vtok_ai_factory/remotion/`
- Composition：`BookTokComposition` 或 `InfographicExplainer`
- 片头 / 片尾 / 主体资产 / 生文 / 生图 Skill 全部同时生效

## 做法

项目已存在，跳过脚手架。镜头节奏、字号、转场、`interpolate()` 等写法遵循 `remotion-markup` 等官方 Skill，但只写进宿主要求的 JSON 字段（如 `emotion`、镜头建议），不要改字段名、不要丢掉 scenes。
