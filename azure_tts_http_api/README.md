# TTS HTTP API

通过 Playwright 打开 [text-to-speech.cn](https://www.text-to-speech.cn/) 抓取页面会话与接口参数，封装为本地 HTTP API，方便脚本调用。

默认走站点接口（`getSpeek.php`），也保留 Azure Speech REST 作为可选后端。

## 工作原理

1. 使用 Chromium 访问 `https://www.text-to-speech.cn/`
2. 从页面脚本提取 `token` 与 Cookie
3. 按抓包结果 POST 到 `/getSpeek.php`
4. 下载返回的 `download` 音频链接

## 安装

```bash
pip install -r /Users/zhujianwei/projects/workspace/tts_http_api/requirements.txt
playwright install chromium
```

## 启动服务

```bash
python3 /Users/zhujianwei/projects/workspace/tts_http_api/tts_api.py --host 127.0.0.1 --port 8787
```

可选环境变量：

```bash
export TTS_PROVIDER=site          # site（默认）或 azure
export TTS_BROWSER_HEADLESS=1     # 0 为有界面浏览器
```

## HTTP 调用

### 生成语音（返回 MP3）

```bash
curl -sS http://127.0.0.1:8787/tts \
  -H 'Content-Type: application/json' \
  -o output.mp3 \
  -d '{
    "text": "你好，这是一次语音合成测试。",
    "language": "zh-cn",
    "voice": "zh-CN-XiaoxiaoNeural",
    "style": "cheerful",
    "rate": 0,
    "pitch": 0,
    "kbitrate": "audio-24khz-160kbitrate-mono-mp3"
  }'
```

### Dry Run（只看请求结构，不真正合成）

```bash
curl -sS http://127.0.0.1:8787/tts \
  -H 'Content-Type: application/json' \
  -d '{"dry_run": true, "text": "你好"}'
```

### 返回 JSON（含 base64 音频）

```bash
curl -sS http://127.0.0.1:8787/tts \
  -H 'Content-Type: application/json' \
  -d '{"text": "你好", "return_json": true}'
```

### 获取语音列表

```bash
curl -sS http://127.0.0.1:8787/voices
```

### 查看抓包摘要

```bash
curl -sS http://127.0.0.1:8787/capture
```

### 触发一次浏览器抓包（会真实生成一条测试语音）

```bash
curl -sS http://127.0.0.1:8787/capture \
  -H 'Content-Type: application/json' \
  -d '{"text": "抓包测试"}'
```

## 命令行直接生成

```bash
python3 /Users/zhujianwei/projects/workspace/tts_http_api/tts_api.py \
  --text "你好，命令行测试" \
  --voice zh-CN-XiaoxiaoNeural \
  --language "中文（普通话，简体）" \
  --output output.mp3
```

## 浏览器抓包工具

不启动 HTTP 服务，也可单独运行抓包脚本：

```bash
python3 /Users/zhujianwei/projects/workspace/tts_http_api/browser_capture.py \
  --output /Users/zhujianwei/projects/workspace/tts_http_api/output/capture-summary.json
```

已有 HAR 文件时，仍可使用：

```bash
python3 /Users/zhujianwei/projects/workspace/tts_http_api/har_capture_summary.py capture.har
```

## Azure 模式（可选）

如需改用官方 Azure Speech：

```bash
export TTS_PROVIDER=azure
export AZURE_SPEECH_KEY="your Azure Speech key"
export AZURE_SPEECH_REGION="eastasia"
```

## 参数说明

`POST /tts` 请求体字段。除 `text` 外均可选。

> `language` / `voice` 列表来自站点 `/getSpeekList.php`（当前 154 种语言、728 个语音）。站点更新后请以 `GET /voices` 为准。

### 命令行参数（`tts_api.py --text ...`）

| 参数 | 默认值 | 对应 JSON 字段 |
| --- | --- | --- |
| `--provider` | `site` | `provider` |
| `--text` | — | `text` |
| `--language` | `中文（普通话，简体）` | `language` |
| `--voice` | `zh-CN-XiaoxiaoNeural` | `voice` |
| `--rate` | `0` | `rate` |
| `--pitch` | `0` | `pitch` |
| `--style` | `0` | `style` |
| `--role` | `0` | `role` |
| `--styledegree` | `1` | `styledegree` |
| `--volume` | `75` | `volume` |
| `--silence` | `""` | `silence` |
| `--kbitrate` | `audio-24khz-160kbitrate-mono-mp3` | `kbitrate` |
| `--dry-run` | `false` | `dry_run` |
| `--output` | `output.mp3` | 输出文件名（仅 CLI） |

### 通用控制参数

| 参数 | 类型 | 默认值 | 可选项 / 说明 |
| --- | --- | --- | --- |
| `text` | string | — | 必填。待合成文本，最长约 10000 字（站点限制） |
| `provider` | string | `site` | `site`（text-to-speech.cn） / `azure`（Azure Speech REST） |
| `dry_run` | boolean | `false` | `true` 只返回请求结构，不生成音频 |
| `return_json` | boolean | `false` | `true` 返回 JSON（含 base64 音频），而非原始音频流 |
| `type` | string | — | 设为 `SSML` 时按 SSML 模式提交（见下方） |
| `ssml` | string | — | SSML 内容；`type=SSML` 时可替代 `text` |
| `user_id` | string | `""` | 授权用户 ID（URL 参数 `?user_id=` 对应值），普通用户留空 |
| `replice` | string | `"1"` | 站点内部参数，默认 `1` |
| `token` | string | 自动 | 由程序从页面获取，无需手动填写 |
| `yzm` | string | 自动 | 由程序自动填充，无需手动填写 |

### `language` 语言

站点使用**中文显示名**（非 BCP-47 代码）。支持别名，也可只传 `voice` 由程序反查。

**别名：**

| 别名 | 映射到 |
| --- | --- |
| `zh-cn` / `zh_cn` / `mandarin` / `普通话` / `中文` | `中文（普通话，简体）` |
| `en-us` / `en_us` / `english` | `英语（美国）` |
| `ja-jp` / `ja_jp` / `japanese` | `日语（日本）` |

**全部 154 种语言（站点原名）：**

| 语言名 `language` | 可用语音数 |
| --- | --- |
| `Afrikaans (South Africa)` | 2 |
| `Albanian (Albania)` | 2 |
| `Amharic (Ethiopia)` | 2 |
| `Arabic (Algeria)` | 2 |
| `Arabic (Bahrain)` | 2 |
| `Arabic (Egypt)` | 2 |
| `Arabic (Iraq)` | 2 |
| `Arabic (Jordan)` | 2 |
| `Arabic (Kuwait)` | 2 |
| `Arabic (Lebanon)` | 2 |
| `Arabic (Libya)` | 2 |
| `Arabic (Morocco)` | 2 |
| `Arabic (Oman)` | 2 |
| `Arabic (Qatar)` | 2 |
| `Arabic (Saudi Arabia)` | 2 |
| `Arabic (Syria)` | 2 |
| `Arabic (Tunisia)` | 2 |
| `Arabic (United Arab Emirates)` | 2 |
| `Arabic (Yemen)` | 2 |
| `Armenian (Armenia)` | 2 |
| `Assamese (India)` | 2 |
| `Azerbaijani (Latin, Azerbaijan)` | 2 |
| `Bangla (Bangladesh)` | 2 |
| `Basque` | 2 |
| `Bengali (India)` | 2 |
| `Bosnian (Bosnia and Herzegovina)` | 2 |
| `Bulgarian (Bulgaria)` | 2 |
| `Burmese (Myanmar)` | 2 |
| `Catalan` | 3 |
| `Croatian (Croatia)` | 2 |
| `Czech (Czechia)` | 2 |
| `Danish (Denmark)` | 2 |
| `Dutch (Belgium)` | 2 |
| `Dutch (Netherlands)` | 5 |
| `English (Australia)` | 17 |
| `English (Canada)` | 2 |
| `English (Hong Kong SAR)` | 2 |
| `English (India)` | 19 |
| `English (Ireland)` | 2 |
| `English (Kenya)` | 2 |
| `English (New Zealand)` | 2 |
| `English (Nigeria)` | 2 |
| `English (Philippines)` | 2 |
| `English (Singapore)` | 2 |
| `English (South Africa)` | 2 |
| `English (Tanzania)` | 2 |
| `English (United Kingdom)` | 21 |
| `English (United States)` | 131 |
| `Estonian (Estonia)` | 2 |
| `Filipino (Philippines)` | 4 |
| `Finnish (Finland)` | 3 |
| `French (Belgium)` | 2 |
| `French (Canada)` | 6 |
| `French (France)` | 21 |
| `French (Switzerland)` | 2 |
| `Galician` | 2 |
| `Georgian (Georgia)` | 2 |
| `German (Austria)` | 2 |
| `German (Germany)` | 21 |
| `German (Switzerland)` | 2 |
| `Greek (Greece)` | 2 |
| `Gujarati (India)` | 2 |
| `Hebrew (Israel)` | 2 |
| `Hindi (India)` | 13 |
| `Hungarian (Hungary)` | 6 |
| `Icelandic (Iceland)` | 2 |
| `Indonesian (Indonesia)` | 4 |
| `Inuktitut (Latin, Canada)` | 2 |
| `Inuktitut (Syllabics, Canada)` | 2 |
| `Irish (Ireland)` | 2 |
| `Italian (Italy)` | 24 |
| `Japanese (Japan)` | 10 |
| `Javanese (Latin, Indonesia)` | 2 |
| `Kannada (India)` | 2 |
| `Kazakh (Kazakhstan)` | 2 |
| `Khmer (Cambodia)` | 2 |
| `Korean (Korea)` | 14 |
| `Lao (Laos)` | 2 |
| `Latvian (Latvia)` | 2 |
| `Lithuanian (Lithuania)` | 2 |
| `Macedonian (North Macedonia)` | 2 |
| `Malay (Malaysia)` | 4 |
| `Malayalam (India)` | 2 |
| `Maltese (Malta)` | 2 |
| `Marathi (India)` | 2 |
| `Mongolian (Mongolia)` | 2 |
| `Nepali (Nepal)` | 2 |
| `Norwegian Bokmål (Norway)` | 3 |
| `Odia (India)` | 2 |
| `Pashto (Afghanistan)` | 2 |
| `Persian (Iran)` | 2 |
| `Polish (Poland)` | 3 |
| `Portuguese (Brazil)` | 24 |
| `Portuguese (Portugal)` | 4 |
| `Punjabi (India)` | 2 |
| `Romanian (Romania)` | 6 |
| `Russian (Russia)` | 5 |
| `Serbian (Cyrillic, Serbia)` | 2 |
| `Serbian (Latin, Serbia)` | 2 |
| `Sinhala (Sri Lanka)` | 2 |
| `Slovak (Slovakia)` | 2 |
| `Slovenian (Slovenia)` | 2 |
| `Somali (Somalia)` | 2 |
| `Spanish (Argentina)` | 2 |
| `Spanish (Bolivia)` | 2 |
| `Spanish (Chile)` | 2 |
| `Spanish (Colombia)` | 2 |
| `Spanish (Costa Rica)` | 2 |
| `Spanish (Cuba)` | 2 |
| `Spanish (Dominican Republic)` | 2 |
| `Spanish (Ecuador)` | 2 |
| `Spanish (El Salvador)` | 2 |
| `Spanish (Equatorial Guinea)` | 2 |
| `Spanish (Guatemala)` | 2 |
| `Spanish (Honduras)` | 2 |
| `Spanish (Mexico)` | 21 |
| `Spanish (Nicaragua)` | 2 |
| `Spanish (Panama)` | 2 |
| `Spanish (Paraguay)` | 2 |
| `Spanish (Peru)` | 2 |
| `Spanish (Puerto Rico)` | 2 |
| `Spanish (Spain)` | 23 |
| `Spanish (United States)` | 2 |
| `Spanish (Uruguay)` | 2 |
| `Spanish (Venezuela)` | 2 |
| `Sundanese (Indonesia)` | 2 |
| `Swahili (Kenya)` | 2 |
| `Swahili (Tanzania)` | 2 |
| `Swedish (Sweden)` | 3 |
| `Tamil (India)` | 2 |
| `Tamil (Malaysia)` | 2 |
| `Tamil (Singapore)` | 2 |
| `Tamil (Sri Lanka)` | 2 |
| `Telugu (India)` | 2 |
| `Thai (Thailand)` | 5 |
| `Turkish (Türkiye)` | 4 |
| `Ukrainian (Ukraine)` | 2 |
| `Urdu (India)` | 2 |
| `Urdu (Pakistan)` | 2 |
| `Uzbek (Latin, Uzbekistan)` | 2 |
| `Vietnamese (Vietnam)` | 2 |
| `Welsh (United Kingdom)` | 2 |
| `Zulu (South Africa)` | 2 |
| `中文（上海话，简体）` | 2 |
| `中文（东北话，简体）` | 2 |
| `中文（台湾话，繁体）` | 3 |
| `中文（四川话，简体）` | 1 |
| `中文（山东话，简体）` | 1 |
| `中文（广东话，简体）` | 2 |
| `中文（广西，简体）` | 1 |
| `中文（普通话，简体）` | 56 |
| `中文（河南话，简体）` | 1 |
| `中文（陕西话，简体）` | 1 |
| `中文（香港话，繁体）` | 3 |

### `voice` 语音

Azure Neural 语音 `ShortName`。需与 `language` 匹配；不传 `language` 时会自动反查。


#### Afrikaans (South Africa)

| `voice` 值 | 显示名 |
| --- | --- |
| `af-ZA-AdriNeural` | Adri |
| `af-ZA-WillemNeural` | Willem |

#### Albanian (Albania)

| `voice` 值 | 显示名 |
| --- | --- |
| `sq-AL-AnilaNeural` | Anila |
| `sq-AL-IlirNeural` | Ilir |

#### Amharic (Ethiopia)

| `voice` 值 | 显示名 |
| --- | --- |
| `am-ET-MekdesNeural` | መቅደስ |
| `am-ET-AmehaNeural` | አምሀ |

#### Arabic (Algeria)

| `voice` 值 | 显示名 |
| --- | --- |
| `ar-DZ-AminaNeural` | أمينة |
| `ar-DZ-IsmaelNeural` | إسماعيل |

#### Arabic (Bahrain)

| `voice` 值 | 显示名 |
| --- | --- |
| `ar-BH-LailaNeural` | ليلى |
| `ar-BH-AliNeural` | علي |

#### Arabic (Egypt)

| `voice` 值 | 显示名 |
| --- | --- |
| `ar-EG-SalmaNeural` | سلمى |
| `ar-EG-ShakirNeural` | شاكر |

#### Arabic (Iraq)

| `voice` 值 | 显示名 |
| --- | --- |
| `ar-IQ-RanaNeural` | رنا |
| `ar-IQ-BasselNeural` | باسل |

#### Arabic (Jordan)

| `voice` 值 | 显示名 |
| --- | --- |
| `ar-JO-SanaNeural` | سناء |
| `ar-JO-TaimNeural` | تيم |

#### Arabic (Kuwait)

| `voice` 值 | 显示名 |
| --- | --- |
| `ar-KW-NouraNeural` | نورا |
| `ar-KW-FahedNeural` | فهد |

#### Arabic (Lebanon)

| `voice` 值 | 显示名 |
| --- | --- |
| `ar-LB-LaylaNeural` | ليلى |
| `ar-LB-RamiNeural` | رامي |

#### Arabic (Libya)

| `voice` 值 | 显示名 |
| --- | --- |
| `ar-LY-ImanNeural` | إيمان |
| `ar-LY-OmarNeural` | أحمد |

#### Arabic (Morocco)

| `voice` 值 | 显示名 |
| --- | --- |
| `ar-MA-MounaNeural` | منى |
| `ar-MA-JamalNeural` | جمال |

#### Arabic (Oman)

| `voice` 值 | 显示名 |
| --- | --- |
| `ar-OM-AyshaNeural` | عائشة |
| `ar-OM-AbdullahNeural` | عبدالله |

#### Arabic (Qatar)

| `voice` 值 | 显示名 |
| --- | --- |
| `ar-QA-AmalNeural` | أمل |
| `ar-QA-MoazNeural` | معاذ |

#### Arabic (Saudi Arabia)

| `voice` 值 | 显示名 |
| --- | --- |
| `ar-SA-ZariyahNeural` | زارية |
| `ar-SA-HamedNeural` | حامد |

#### Arabic (Syria)

| `voice` 值 | 显示名 |
| --- | --- |
| `ar-SY-AmanyNeural` | أماني |
| `ar-SY-LaithNeural` | ليث |

#### Arabic (Tunisia)

| `voice` 值 | 显示名 |
| --- | --- |
| `ar-TN-ReemNeural` | ريم |
| `ar-TN-HediNeural` | هادي |

#### Arabic (United Arab Emirates)

| `voice` 值 | 显示名 |
| --- | --- |
| `ar-AE-FatimaNeural` | فاطمة |
| `ar-AE-HamdanNeural` | حمدان |

#### Arabic (Yemen)

| `voice` 值 | 显示名 |
| --- | --- |
| `ar-YE-MaryamNeural` | مريم |
| `ar-YE-SalehNeural` | صالح |

#### Armenian (Armenia)

| `voice` 值 | 显示名 |
| --- | --- |
| `hy-AM-AnahitNeural` | Անահիտ |
| `hy-AM-HaykNeural` | Հայկ |

#### Assamese (India)

| `voice` 值 | 显示名 |
| --- | --- |
| `as-IN-YashicaNeural` | যাশিকা |
| `as-IN-PriyomNeural` | প্ৰিয়ম |

#### Azerbaijani (Latin, Azerbaijan)

| `voice` 值 | 显示名 |
| --- | --- |
| `az-AZ-BanuNeural` | Banu |
| `az-AZ-BabekNeural` | Babək |

#### Bangla (Bangladesh)

| `voice` 值 | 显示名 |
| --- | --- |
| `bn-BD-NabanitaNeural` | নবনীতা |
| `bn-BD-PradeepNeural` | প্রদ্বীপ |

#### Basque

| `voice` 值 | 显示名 |
| --- | --- |
| `eu-ES-AinhoaNeural` | Ainhoa |
| `eu-ES-AnderNeural` | Ander |

#### Bengali (India)

| `voice` 值 | 显示名 |
| --- | --- |
| `bn-IN-TanishaaNeural` | তানিশা |
| `bn-IN-BashkarNeural` | ভাস্কর |

#### Bosnian (Bosnia and Herzegovina)

| `voice` 值 | 显示名 |
| --- | --- |
| `bs-BA-VesnaNeural` | Vesna |
| `bs-BA-GoranNeural` | Goran |

#### Bulgarian (Bulgaria)

| `voice` 值 | 显示名 |
| --- | --- |
| `bg-BG-KalinaNeural` | Калина |
| `bg-BG-BorislavNeural` | Борислав |

#### Burmese (Myanmar)

| `voice` 值 | 显示名 |
| --- | --- |
| `my-MM-NilarNeural` | နီလာ |
| `my-MM-ThihaNeural` | သီဟ |

#### Catalan

| `voice` 值 | 显示名 |
| --- | --- |
| `ca-ES-JoanaNeural` | Joana |
| `ca-ES-EnricNeural` | Enric |
| `ca-ES-AlbaNeural` | Alba |

#### Croatian (Croatia)

| `voice` 值 | 显示名 |
| --- | --- |
| `hr-HR-GabrijelaNeural` | Gabrijela |
| `hr-HR-SreckoNeural` | Srećko |

#### Czech (Czechia)

| `voice` 值 | 显示名 |
| --- | --- |
| `cs-CZ-VlastaNeural` | Vlasta |
| `cs-CZ-AntoninNeural` | Antonín |

#### Danish (Denmark)

| `voice` 值 | 显示名 |
| --- | --- |
| `da-DK-ChristelNeural` | Christel |
| `da-DK-JeppeNeural` | Jeppe |

#### Dutch (Belgium)

| `voice` 值 | 显示名 |
| --- | --- |
| `nl-BE-DenaNeural` | Dena |
| `nl-BE-ArnaudNeural` | Arnaud |

#### Dutch (Netherlands)

| `voice` 值 | 显示名 |
| --- | --- |
| `nl-NL-FennaNeural` | Fenna |
| `nl-NL-MaartenNeural` | Maarten |
| `nl-NL-ColetteNeural` | Colette |
| `nl-NL-Fleur:MAI-Voice-2` | Fleur MAI-Voice-2 |
| `nl-NL-Sander:MAI-Voice-2` | Sander MAI-Voice-2 |

#### English (Australia)

| `voice` 值 | 显示名 |
| --- | --- |
| `en-AU-NatashaNeural` | Natasha |
| `en-AU-WilliamNeural` | William |
| `en-AU-AnnetteNeural` | Annette |
| `en-AU-CarlyNeural` | Carly |
| `en-AU-DarrenNeural` | Darren |
| `en-AU-DuncanNeural` | Duncan |
| `en-AU-ElsieNeural` | Elsie |
| `en-AU-FreyaNeural` | Freya |
| `en-AU-JoanneNeural` | Joanne |
| `en-AU-KenNeural` | Ken |
| `en-AU-KimNeural` | Kim |
| `en-AU-NeilNeural` | Neil |
| `en-AU-TimNeural` | Tim |
| `en-AU-TinaNeural` | Tina |
| `en-AU-Isla:MAI-Voice-2` | Isla MAI-Voice-2 |
| `en-AU-WilliamMultilingualNeural` | William Multilingual |
| `en-au-cyanspark:DragonHDOmniLatestNeural` | Cyanspark Dragon HD Omni Latest |

#### English (Canada)

| `voice` 值 | 显示名 |
| --- | --- |
| `en-CA-ClaraNeural` | Clara |
| `en-CA-LiamNeural` | Liam |

#### English (Hong Kong SAR)

| `voice` 值 | 显示名 |
| --- | --- |
| `en-HK-YanNeural` | Yan |
| `en-HK-SamNeural` | Sam |

#### English (India)

| `voice` 值 | 显示名 |
| --- | --- |
| `en-IN-AartiIndicNeural` | Aarti Indic |
| `en-IN-ArjunIndicNeural` | Arjun Indic |
| `en-IN-NeerjaIndicNeural` | Neerja Indic |
| `en-IN-PrabhatIndicNeural` | Prabhat Indic |
| `en-IN-AaravNeural` | Aarav |
| `en-IN-AashiNeural` | Aashi |
| `en-IN-AartiNeural` | Aarti |
| `en-IN-ArjunNeural` | Arjun |
| `en-IN-AnanyaNeural` | Ananya |
| `en-IN-KavyaNeural` | Kavya |
| `en-IN-KunalNeural` | Kunal |
| `en-IN-NeerjaNeural` | Neerja |
| `en-IN-PrabhatNeural` | Prabhat |
| `en-IN-RehaanNeural` | Rehaan |
| `en-IN-Diya:DragonHDLatestNeural` | Diya Dragon HD Latest |
| `en-IN-Meera:DragonHDLatestNeural` | Meera Dragon HD Latest |
| `en-IN-Aarti:DragonHDLatestNeural` | Aarti Dragon HD Latest |
| `en-IN-Arjun:DragonHDLatestNeural` | Arjun Dragon HD Latest |
| `en-IN-Neerja:DragonHDLatestNeural` | Neerja Dragon HD Latest |

#### English (Ireland)

| `voice` 值 | 显示名 |
| --- | --- |
| `en-IE-EmilyNeural` | Emily |
| `en-IE-ConnorNeural` | Connor |

#### English (Kenya)

| `voice` 值 | 显示名 |
| --- | --- |
| `en-KE-AsiliaNeural` | Asilia |
| `en-KE-ChilembaNeural` | Chilemba |

#### English (New Zealand)

| `voice` 值 | 显示名 |
| --- | --- |
| `en-NZ-MollyNeural` | Molly |
| `en-NZ-MitchellNeural` | Mitchell |

#### English (Nigeria)

| `voice` 值 | 显示名 |
| --- | --- |
| `en-NG-EzinneNeural` | Ezinne |
| `en-NG-AbeoNeural` | Abeo |

#### English (Philippines)

| `voice` 值 | 显示名 |
| --- | --- |
| `en-PH-RosaNeural` | Rosa |
| `en-PH-JamesNeural` | James |

#### English (Singapore)

| `voice` 值 | 显示名 |
| --- | --- |
| `en-SG-LunaNeural` | Luna |
| `en-SG-WayneNeural` | Wayne |

#### English (South Africa)

| `voice` 值 | 显示名 |
| --- | --- |
| `en-ZA-LeahNeural` | Leah |
| `en-ZA-LukeNeural` | Luke |

#### English (Tanzania)

| `voice` 值 | 显示名 |
| --- | --- |
| `en-TZ-ImaniNeural` | Imani |
| `en-TZ-ElimuNeural` | Elimu |

#### English (United Kingdom)

| `voice` 值 | 显示名 |
| --- | --- |
| `en-GB-SoniaNeural` | Sonia |
| `en-GB-RyanNeural` | Ryan |
| `en-GB-LibbyNeural` | Libby |
| `en-GB-AbbiNeural` | Abbi |
| `en-GB-AlfieNeural` | Alfie |
| `en-GB-BellaNeural` | Bella |
| `en-GB-ElliotNeural` | Elliot |
| `en-GB-EthanNeural` | Ethan |
| `en-GB-HollieNeural` | Hollie |
| `en-GB-MaisieNeural` | Maisie |
| `en-GB-NoahNeural` | Noah |
| `en-GB-OliverNeural` | Oliver |
| `en-GB-OliviaNeural` | Olivia |
| `en-GB-ThomasNeural` | Thomas |
| `en-GB-MiaNeural` | Mia |
| `en-GB-AdaMultilingualNeural` | Ada Multilingual |
| `en-GB-OllieMultilingualNeural` | Ollie Multilingual |
| `en-GB-Ada:DragonHDLatestNeural` | Ada Dragon HD Latest |
| `en-GB-Ollie:DragonHDLatestNeural` | Ollie Dragon HD Latest |
| `en-GB-Ryan:DragonHDLatestNeural` | Ryan Dragon HD Latest |
| `en-GB-Sonia:DragonHDLatestNeural` | Sonia Dragon HD Latest |

#### English (United States)

| `voice` 值 | 显示名 |
| --- | --- |
| `en-US-AvaNeural` | Ava |
| `en-US-AndrewNeural` | Andrew |
| `en-US-EmmaNeural` | Emma |
| `en-US-BrianNeural` | Brian |
| `en-US-JennyNeural` | Jenny |
| `en-US-GuyNeural` | Guy |
| `en-US-AriaNeural` | Aria |
| `en-US-DavisNeural` | Davis |
| `en-US-JaneNeural` | Jane |
| `en-US-JasonNeural` | Jason |
| `en-US-KaiNeural` | Kai |
| `en-US-LunaNeural` | Luna |
| `en-US-SaraNeural` | Sara |
| `en-US-TonyNeural` | Tony |
| `en-US-NancyNeural` | Nancy |
| `en-US-AIGenerate1Neural` | AIGenerate1 |
| `en-US-AIGenerate2Neural` | AIGenerate2 |
| `en-US-AmberNeural` | Amber |
| `en-US-AnaNeural` | Ana |
| `en-US-AshleyNeural` | Ashley |
| `en-US-BrandonNeural` | Brandon |
| `en-US-ChristopherNeural` | Christopher |
| `en-US-CoraNeural` | Cora |
| `en-US-ElizabethNeural` | Elizabeth |
| `en-US-EricNeural` | Eric |
| `en-US-JacobNeural` | Jacob |
| `en-US-MichelleNeural` | Michelle |
| `en-US-MonicaNeural` | Monica |
| `en-US-RogerNeural` | Roger |
| `en-US-SteffanNeural` | Steffan |
| `en-US-BlueNeural` | Blue |
| `en-US-Ethan:MAI-Voice-2` | Ethan MAI-Voice-2 |
| `en-US-Grant:MAI-Voice-1` | Grant MAI-Voice-1 |
| `en-US-Grant:MAI-Voice-2` | Grant MAI-Voice-2 |
| `en-US-Harper:MAI-Voice-2` | Harper MAI-Voice-2 |
| `en-US-Iris:MAI-Voice-1` | Iris MAI-Voice-1 |
| `en-US-Iris:MAI-Voice-2` | Iris MAI-Voice-2 |
| `en-US-Jasper:MAI-Voice-1` | Jasper MAI-Voice-1 |
| `en-US-Jasper:MAI-Voice-2` | Jasper MAI-Voice-2 |
| `en-US-Joy:MAI-Voice-1` | Joy MAI-Voice-1 |
| `en-US-June:MAI-Voice-1` | June MAI-Voice-1 |
| `en-US-Olivia:MAI-Voice-2` | Olivia MAI-Voice-2 |
| `en-US-Reed:MAI-Voice-1` | Reed MAI-Voice-1 |
| `en-US-AvaMultilingualNeural` | Ava Multilingual |
| `en-US-AndrewMultilingualNeural` | Andrew Multilingual |
| `en-US-AmandaMultilingualNeural` | Amanda Multilingual |
| `en-US-AdamMultilingualNeural` | Adam Multilingual |
| `en-US-EmmaMultilingualNeural` | Emma Multilingual |
| `en-US-PhoebeMultilingualNeural` | Phoebe Multilingual |
| `en-US-AlloyTurboMultilingualNeural` | Alloy Turbo Multilingual |
| `en-US-EchoTurboMultilingualNeural` | Echo Turbo Multilingual |
| `en-US-FableTurboMultilingualNeural` | Fable Turbo Multilingual |
| `en-US-OnyxTurboMultilingualNeural` | Onyx Turbo Multilingual |
| `en-US-NovaTurboMultilingualNeural` | Nova Turbo Multilingual |
| `en-US-ShimmerTurboMultilingualNeural` | Shimmer Turbo Multilingual |
| `en-US-BrianMultilingualNeural` | Brian Multilingual |
| `en-US-CoraMultilingualNeural` | Cora Multilingual |
| `en-US-ChristopherMultilingualNeural` | Christopher Multilingual |
| `en-US-BrandonMultilingualNeural` | Brandon Multilingual |
| `en-US-DavisMultilingualNeural` | Davis Multilingual |
| `en-US-DerekMultilingualNeural` | Derek Multilingual |
| `en-US-DustinMultilingualNeural` | Dustin Multilingual |
| `en-US-EvelynMultilingualNeural` | Evelyn Multilingual |
| `en-US-JennyMultilingualNeural` | Jenny Multilingual |
| `en-US-LewisMultilingualNeural` | Lewis Multilingual |
| `en-US-LolaMultilingualNeural` | Lola Multilingual |
| `en-US-NancyMultilingualNeural` | Nancy Multilingual |
| `en-US-RyanMultilingualNeural` | Ryan Multilingual |
| `en-US-SamuelMultilingualNeural` | Samuel Multilingual |
| `en-US-SerenaMultilingualNeural` | Serena Multilingual |
| `en-US-SteffanMultilingualNeural` | Steffan Multilingual |
| `en-US-AshTurboMultilingualNeural` | Ash Turbo Multilingual |
| `en-US-Ava:DragonHDLatestNeural` | Ava Dragon HD Latest |
| `en-US-Andrew:DragonHDLatestNeural` | Andrew Dragon HD Latest |
| `en-US-Adam:DragonHDLatestNeural` | Adam Dragon HD Latest |
| `en-US-Alloy:DragonHDLatestNeural` | Alloy Dragon HD Latest |
| `en-US-Aria:DragonHDLatestNeural` | Aria Dragon HD Latest |
| `en-US-Bree:DragonHDLatestNeural` | Bree Dragon HD Latest |
| `en-US-Brian:DragonHDLatestNeural` | Brian Dragon HD Latest |
| `en-US-Davis:DragonHDLatestNeural` | Davis Dragon HD Latest |
| `en-US-Emma:DragonHDLatestNeural` | Emma Dragon HD Latest |
| `en-US-Emma2:DragonHDLatestNeural` | Emma2 Dragon HD Latest |
| `en-US-Jane:DragonHDLatestNeural` | Jane Dragon HD Latest |
| `en-US-Jenny:DragonHDLatestNeural` | Jenny Dragon HD Latest |
| `en-US-Nova:DragonHDLatestNeural` | Nova Dragon HD Latest |
| `en-US-Phoebe:DragonHDLatestNeural` | Phoebe Dragon HD Latest |
| `en-US-Serena:DragonHDLatestNeural` | Serena Dragon HD Latest |
| `en-US-Steffan:DragonHDLatestNeural` | Steffan Dragon HD Latest |
| `en-US-Andrew:DragonHDOmniLatestNeural` | Andrew Dragon HD Omni Latest |
| `en-US-Caleb:DragonHDOmniLatestNeural` | Caleb Dragon HD Omni Latest |
| `en-US-Dana:DragonHDOmniLatestNeural` | Dana Dragon HD Omni Latest |
| `en-US-Lewis:DragonHDOmniLatestNeural` | Lewis Dragon HD Omni Latest |
| `en-US-Phoebe:DragonHDOmniLatestNeural` | Phoebe Dragon HD Omni Latest |
| `en-US-Jimmie:DragonHDFlashLatestNeural` | Jimmie Dragon HD Flash Latest |
| `en-US-Tiana:DragonHDFlashLatestNeural` | Tiana Dragon HD Flash Latest |
| `en-US-Tyler:DragonHDFlashLatestNeural` | Tyler Dragon HD Flash Latest |
| `en-US-Andrew2:DragonHDLatestNeural` | Andrew2 Dragon HD Latest |
| `en-Multitalker:DragonHDLatestNeural` | English Multitalker Dragon HD Latest |
| `en-US-Andrew-Preview:DragonHDLatestNeural` | DragonHD Andrew Preview |
| `en-US-Andrew3:DragonHDLatestNeural` | Andrew3 Dragon HD Latest |
| `en-us-ashlyra:DragonHDOmniLatestNeural` | Ashlyra Dragon HD Omni Latest |
| `en-US-Ava-Preview:DragonHDLatestNeural` | DragonHD Ava Preview |
| `en-us-ava:DragonHDOmniLatestNeural` | Ava Dragon HD Omni Latest |
| `en-US-Ava3:DragonHDLatestNeural` | Ava3 Dragon HD Latest |
| `en-us-blushzephyr:DragonHDOmniLatestNeural` | Blushzephyr Dragon HD Omni Latest |
| `en-us-burgundysolar:DragonHDOmniLatestNeural` | Burgundysolar Dragon HD Omni Latest |
| `en-us-copperaria:DragonHDOmniLatestNeural` | Copperaria Dragon HD Omni Latest |
| `en-us-coralbreeze:DragonHDOmniLatestNeural` | Coralbreeze Dragon HD Omni Latest |
| `en-us-coralspark:DragonHDOmniLatestNeural` | Coralspark Dragon HD Omni Latest |
| `en-us-emma:DragonHDOmniLatestNeural` | Emma Dragon HD Omni Latest |
| `en-US-Evelyn:DragonHDLatestNeural` | Evelyn Dragon HD Latest |
| `en-us-goldenspark:DragonHDOmniLatestNeural` | Goldenspark Dragon HD Omni Latest |
| `en-us-jelly:DragonHDOmniLatestNeural` | Jelly Dragon HD Omni Latest |
| `en-US-Jimmie:DragonHDLatestNeural` | Jimmie Dragon HD Latest |
| `en-US-Juno:DragonHDLatestNeural` | Juno Dragon HD Latest |
| `en-US-Mila:DragonHDLatestNeural` | Mila Dragon HD Latest |
| `en-us-noirtulipan:DragonHDOmniLatestNeural` | Noirtulipan Dragon HD Omni Latest |
| `en-us-rojocomet:DragonHDOmniLatestNeural` | Rojocomet Dragon HD Omni Latest |
| `en-us-sagemeadow:DragonHDOmniLatestNeural` | Sagemeadow Dragon HD Omni Latest |
| `en-US-Serena-Preview:DragonHDLatestNeural` | DragonHD Serena Preview |
| `en-us-slatenocturne:DragonHDOmniLatestNeural` | Slatenocturne Dragon HD Omni Latest |
| `en-us-solarclover:DragonHDOmniLatestNeural` | Solarclover Dragon HD Omni Latest |
| `en-us-tealcadenza:DragonHDOmniLatestNeural` | Tealcadenza Dragon HD Omni Latest |
| `en-US-Tessa:DragonHDLatestNeural` | Tessa Dragon HD Latest |
| `en-US-Tiana:DragonHDLatestNeural` | Tiana Dragon HD Latest |
| `en-US-Tyler:DragonHDLatestNeural` | Tyler Dragon HD Latest |
| `en-US-Vance:DragonHDLatestNeural` | Vance Dragon HD Latest |
| `en-us-verdeadamant:DragonHDOmniLatestNeural` | Verdeadamant Dragon HD Omni Latest |
| `en-us-vermilionlaurel:DragonHDOmniLatestNeural` | Vermilionlaurel Dragon HD Omni Latest |
| `fr-Multitalker:DragonHDLatestNeural` | French Multitalker Dragon HD Latest |
| `zh-Multitalker:DragonHDLatestNeural` | Chinese Multitalker Dragon HD Latest |

#### Estonian (Estonia)

| `voice` 值 | 显示名 |
| --- | --- |
| `et-EE-AnuNeural` | Anu |
| `et-EE-KertNeural` | Kert |

#### Filipino (Philippines)

| `voice` 值 | 显示名 |
| --- | --- |
| `fil-PH-BlessicaNeural` | Blessica |
| `fil-PH-AngeloNeural` | Angelo |
| `fil-PH-Angelo:DragonHDLatestNeural` | Angelo Dragon HD Latest |
| `fil-PH-Blessica:DragonHDLatestNeural` | Blessica Dragon HD Latest |

#### Finnish (Finland)

| `voice` 值 | 显示名 |
| --- | --- |
| `fi-FI-SelmaNeural` | Selma |
| `fi-FI-HarriNeural` | Harri |
| `fi-FI-NooraNeural` | Noora |

#### French (Belgium)

| `voice` 值 | 显示名 |
| --- | --- |
| `fr-BE-CharlineNeural` | Charline |
| `fr-BE-GerardNeural` | Gerard |

#### French (Canada)

| `voice` 值 | 显示名 |
| --- | --- |
| `fr-CA-SylvieNeural` | Sylvie |
| `fr-CA-JeanNeural` | Jean |
| `fr-CA-AntoineNeural` | Antoine |
| `fr-CA-ThierryNeural` | Thierry |
| `fr-CA-Sylvie:DragonHDLatestNeural` | Sylvie Dragon HD Latest |
| `fr-CA-Thierry:DragonHDLatestNeural` | Thierry Dragon HD Latest |

#### French (France)

| `voice` 值 | 显示名 |
| --- | --- |
| `fr-FR-DeniseNeural` | Denise |
| `fr-FR-HenriNeural` | Henri |
| `fr-FR-AlainNeural` | Alain |
| `fr-FR-BrigitteNeural` | Brigitte |
| `fr-FR-CelesteNeural` | Celeste |
| `fr-FR-ClaudeNeural` | Claude |
| `fr-FR-CoralieNeural` | Coralie |
| `fr-FR-EloiseNeural` | Eloise |
| `fr-FR-JacquelineNeural` | Jacqueline |
| `fr-FR-JeromeNeural` | Jerome |
| `fr-FR-JosephineNeural` | Josephine |
| `fr-FR-MauriceNeural` | Maurice |
| `fr-FR-YvesNeural` | Yves |
| `fr-FR-YvetteNeural` | Yvette |
| `fr-FR-Marc:MAI-Voice-2` | Marc MAI-Voice-2 |
| `fr-FR-Soleil:MAI-Voice-2` | Soleil MAI-Voice-2 |
| `fr-FR-VivienneMultilingualNeural` | Vivienne Multilingue |
| `fr-FR-RemyMultilingualNeural` | Rémy Multilingue |
| `fr-FR-LucienMultilingualNeural` | Lucien Multilingual |
| `fr-FR-Vivienne:DragonHDLatestNeural` | Vivienne Dragon HD Latest |
| `fr-FR-Remy:DragonHDLatestNeural` | Remy Dragon HD Latest |

#### French (Switzerland)

| `voice` 值 | 显示名 |
| --- | --- |
| `fr-CH-ArianeNeural` | Ariane |
| `fr-CH-FabriceNeural` | Fabrice |

#### Galician

| `voice` 值 | 显示名 |
| --- | --- |
| `gl-ES-SabelaNeural` | Sabela |
| `gl-ES-RoiNeural` | Roi |

#### Georgian (Georgia)

| `voice` 值 | 显示名 |
| --- | --- |
| `ka-GE-EkaNeural` | ეკა |
| `ka-GE-GiorgiNeural` | გიორგი |

#### German (Austria)

| `voice` 值 | 显示名 |
| --- | --- |
| `de-AT-IngridNeural` | Ingrid |
| `de-AT-JonasNeural` | Jonas |

#### German (Germany)

| `voice` 值 | 显示名 |
| --- | --- |
| `de-DE-KatjaNeural` | Katja |
| `de-DE-ConradNeural` | Conrad |
| `de-DE-AmalaNeural` | Amala |
| `de-DE-BerndNeural` | Bernd |
| `de-DE-ChristophNeural` | Christoph |
| `de-DE-ElkeNeural` | Elke |
| `de-DE-GiselaNeural` | Gisela |
| `de-DE-KasperNeural` | Kasper |
| `de-DE-KillianNeural` | Killian |
| `de-DE-KlarissaNeural` | Klarissa |
| `de-DE-KlausNeural` | Klaus |
| `de-DE-LouisaNeural` | Louisa |
| `de-DE-MajaNeural` | Maja |
| `de-DE-RalfNeural` | Ralf |
| `de-DE-TanjaNeural` | Tanja |
| `de-DE-Klaus:MAI-Voice-2` | Klaus MAI-Voice-2 |
| `de-DE-Mia:MAI-Voice-2` | Mia MAI-Voice-2 |
| `de-DE-SeraphinaMultilingualNeural` | Seraphina Mehrsprachig |
| `de-DE-FlorianMultilingualNeural` | Florian Mehrsprachig |
| `de-DE-Seraphina:DragonHDLatestNeural` | Seraphina Dragon HD Latest |
| `de-DE-Florian:DragonHDLatestNeural` | Florian Dragon HD Latest |

#### German (Switzerland)

| `voice` 值 | 显示名 |
| --- | --- |
| `de-CH-LeniNeural` | Leni |
| `de-CH-JanNeural` | Jan |

#### Greek (Greece)

| `voice` 值 | 显示名 |
| --- | --- |
| `el-GR-AthinaNeural` | Αθηνά |
| `el-GR-NestorasNeural` | Νέστορας |

#### Gujarati (India)

| `voice` 值 | 显示名 |
| --- | --- |
| `gu-IN-DhwaniNeural` | ધ્વની |
| `gu-IN-NiranjanNeural` | નિરંજન |

#### Hebrew (Israel)

| `voice` 值 | 显示名 |
| --- | --- |
| `he-IL-HilaNeural` | הילה |
| `he-IL-AvriNeural` | אברי |

#### Hindi (India)

| `voice` 值 | 显示名 |
| --- | --- |
| `hi-IN-AaravNeural` | आरव  |
| `hi-IN-AnanyaNeural` | अनन्या |
| `hi-IN-AartiNeural` | आरती |
| `hi-IN-ArjunNeural` | अर्जुन |
| `hi-IN-KavyaNeural` | काव्या |
| `hi-IN-KunalNeural` | कुनाल  |
| `hi-IN-RehaanNeural` | रेहान |
| `hi-IN-SwaraNeural` | स्वरा |
| `hi-IN-MadhurNeural` | मधुर |
| `hi-IN-Arjun:MAI-Voice-2` | Arjun MAI-Voice-2 |
| `hi-IN-Dhruv:MAI-Voice-2` | Dhruv MAI-Voice-2 |
| `hi-IN-Kavya:MAI-Voice-2` | Kavya MAI-Voice-2 |
| `hi-IN-Priya:MAI-Voice-2` | Priya MAI-Voice-2 |

#### Hungarian (Hungary)

| `voice` 值 | 显示名 |
| --- | --- |
| `hu-HU-NoemiNeural` | Noémi |
| `hu-HU-TamasNeural` | Tamás |
| `hu-HU-Bence:MAI-Voice-2` | Bence MAI-Voice-2 |
| `hu-HU-Levente:MAI-Voice-2` | Levente MAI-Voice-2 |
| `hu-HU-Lilla:MAI-Voice-2` | Lilla MAI-Voice-2 |
| `hu-HU-Réka:MAI-Voice-2` | Réka MAI-Voice-2 |

#### Icelandic (Iceland)

| `voice` 值 | 显示名 |
| --- | --- |
| `is-IS-GudrunNeural` | Guðrún |
| `is-IS-GunnarNeural` | Gunnar |

#### Indonesian (Indonesia)

| `voice` 值 | 显示名 |
| --- | --- |
| `id-ID-GadisNeural` | Gadis |
| `id-ID-ArdiNeural` | Ardi |
| `id-ID-Ardi:DragonHDLatestNeural` | Ardi Dragon HD Latest |
| `id-ID-Gadis:DragonHDLatestNeural` | Gadis Dragon HD Latest |

#### Inuktitut (Latin, Canada)

| `voice` 值 | 显示名 |
| --- | --- |
| `iu-Latn-CA-SiqiniqNeural` | ᓯᕿᓂᖅ |
| `iu-Latn-CA-TaqqiqNeural` | ᑕᖅᑭᖅ |

#### Inuktitut (Syllabics, Canada)

| `voice` 值 | 显示名 |
| --- | --- |
| `iu-Cans-CA-SiqiniqNeural` | ᓯᕿᓂᖅ |
| `iu-Cans-CA-TaqqiqNeural` | ᑕᖅᑭᖅ |

#### Irish (Ireland)

| `voice` 值 | 显示名 |
| --- | --- |
| `ga-IE-OrlaNeural` | Orla |
| `ga-IE-ColmNeural` | Colm |

#### Italian (Italy)

| `voice` 值 | 显示名 |
| --- | --- |
| `it-IT-ElsaNeural` | Elsa |
| `it-IT-IsabellaNeural` | Isabella |
| `it-IT-DiegoNeural` | Diego |
| `it-IT-BenignoNeural` | Benigno |
| `it-IT-CalimeroNeural` | Calimero |
| `it-IT-CataldoNeural` | Cataldo |
| `it-IT-FabiolaNeural` | Fabiola |
| `it-IT-FiammaNeural` | Fiamma |
| `it-IT-GianniNeural` | Gianni |
| `it-IT-GiuseppeNeural` | Giuseppe |
| `it-IT-ImeldaNeural` | Imelda |
| `it-IT-IrmaNeural` | Irma |
| `it-IT-LisandroNeural` | Lisandro |
| `it-IT-PalmiraNeural` | Palmira |
| `it-IT-PierinaNeural` | Pierina |
| `it-IT-RinaldoNeural` | Rinaldo |
| `it-IT-Luca:MAI-Voice-2` | Luca MAI-Voice-2 |
| `it-IT-Rosa:MAI-Voice-2` | Rosa MAI-Voice-2 |
| `it-IT-AlessioMultilingualNeural` | Alessio Multilingual |
| `it-IT-IsabellaMultilingualNeural` | Isabella Multilingual |
| `it-IT-GiuseppeMultilingualNeural` | Giuseppe Multilingual |
| `it-IT-MarcelloMultilingualNeural` | Marcello Multilingual |
| `it-IT-Isabella:DragonHDLatestNeural` | Isabella Dragon HD Latest |
| `it-IT-Alessio:DragonHDLatestNeural` | Alessio Dragon HD Latest |

#### Japanese (Japan)

| `voice` 值 | 显示名 |
| --- | --- |
| `ja-JP-NanamiNeural` | 七海 |
| `ja-JP-KeitaNeural` | 圭太 |
| `ja-JP-AoiNeural` | 碧衣 |
| `ja-JP-DaichiNeural` | 大智 |
| `ja-JP-MayuNeural` | 真夕 |
| `ja-JP-NaokiNeural` | 直紀 |
| `ja-JP-ShioriNeural` | 志織 |
| `ja-JP-MasaruMultilingualNeural` | 勝 多言語 |
| `ja-JP-Nanami:DragonHDLatestNeural` | Nanami Dragon HD Latest |
| `ja-JP-Masaru:DragonHDLatestNeural` | Masaru Dragon HD Latest |

#### Javanese (Latin, Indonesia)

| `voice` 值 | 显示名 |
| --- | --- |
| `jv-ID-SitiNeural` | Siti |
| `jv-ID-DimasNeural` | Dimas |

#### Kannada (India)

| `voice` 值 | 显示名 |
| --- | --- |
| `kn-IN-SapnaNeural` | ಸಪ್ನಾ |
| `kn-IN-GaganNeural` | ಗಗನ್ |

#### Kazakh (Kazakhstan)

| `voice` 值 | 显示名 |
| --- | --- |
| `kk-KZ-AigulNeural` | Айгүл |
| `kk-KZ-DauletNeural` | Дәулет |

#### Khmer (Cambodia)

| `voice` 值 | 显示名 |
| --- | --- |
| `km-KH-SreymomNeural` | ស្រីមុំ |
| `km-KH-PisethNeural` | ពិសិដ្ឋ |

#### Korean (Korea)

| `voice` 值 | 显示名 |
| --- | --- |
| `ko-KR-SunHiNeural` | 선히 |
| `ko-KR-InJoonNeural` | 인준 |
| `ko-KR-BongJinNeural` | 봉진 |
| `ko-KR-GookMinNeural` | 국민 |
| `ko-KR-HyunsuNeural` | 현수 |
| `ko-KR-JiMinNeural` | 지민 |
| `ko-KR-SeoHyeonNeural` | 서현 |
| `ko-KR-SoonBokNeural` | 순복 |
| `ko-KR-YuJinNeural` | 유진 |
| `ko-KR-Haena:MAI-Voice-2` | Haena MAI-Voice-2 |
| `ko-KR-Junho:MAI-Voice-2` | Junho MAI-Voice-2 |
| `ko-KR-HyunsuMultilingualNeural` | Hyunsu Multilingual |
| `ko-KR-SunHi:DragonHDLatestNeural` | SunHi Dragon HD Latest |
| `ko-KR-Hyunsu:DragonHDLatestNeural` | Hyunsu Dragon HD Latest |

#### Lao (Laos)

| `voice` 值 | 显示名 |
| --- | --- |
| `lo-LA-KeomanyNeural` | ແກ້ວມະນີ |
| `lo-LA-ChanthavongNeural` | ຈັນທະວົງ |

#### Latvian (Latvia)

| `voice` 值 | 显示名 |
| --- | --- |
| `lv-LV-EveritaNeural` | Everita |
| `lv-LV-NilsNeural` | Nils |

#### Lithuanian (Lithuania)

| `voice` 值 | 显示名 |
| --- | --- |
| `lt-LT-OnaNeural` | Ona |
| `lt-LT-LeonasNeural` | Leonas |

#### Macedonian (North Macedonia)

| `voice` 值 | 显示名 |
| --- | --- |
| `mk-MK-MarijaNeural` | Марија |
| `mk-MK-AleksandarNeural` | Александар |

#### Malay (Malaysia)

| `voice` 值 | 显示名 |
| --- | --- |
| `ms-MY-YasminNeural` | Yasmin |
| `ms-MY-OsmanNeural` | Osman |
| `ms-MY-Osman:DragonHDLatestNeural` | Osman Dragon HD Latest |
| `ms-MY-Yasmin:DragonHDLatestNeural` | Yasmin Dragon HD Latest |

#### Malayalam (India)

| `voice` 值 | 显示名 |
| --- | --- |
| `ml-IN-SobhanaNeural` | ശോഭന |
| `ml-IN-MidhunNeural` | മിഥുൻ |

#### Maltese (Malta)

| `voice` 值 | 显示名 |
| --- | --- |
| `mt-MT-GraceNeural` | Grace |
| `mt-MT-JosephNeural` | Joseph |

#### Marathi (India)

| `voice` 值 | 显示名 |
| --- | --- |
| `mr-IN-AarohiNeural` | आरोही |
| `mr-IN-ManoharNeural` | मनोहर |

#### Mongolian (Mongolia)

| `voice` 值 | 显示名 |
| --- | --- |
| `mn-MN-YesuiNeural` | Есүй |
| `mn-MN-BataaNeural` | Батаа |

#### Nepali (Nepal)

| `voice` 值 | 显示名 |
| --- | --- |
| `ne-NP-HemkalaNeural` | हेमकला |
| `ne-NP-SagarNeural` | सागर |

#### Norwegian Bokmål (Norway)

| `voice` 值 | 显示名 |
| --- | --- |
| `nb-NO-PernilleNeural` | Pernille |
| `nb-NO-FinnNeural` | Finn |
| `nb-NO-IselinNeural` | Iselin |

#### Odia (India)

| `voice` 值 | 显示名 |
| --- | --- |
| `or-IN-SubhasiniNeural` | ସୁଭାସିନୀ |
| `or-IN-SukantNeural` | ସୁକାନ୍ତ |

#### Pashto (Afghanistan)

| `voice` 值 | 显示名 |
| --- | --- |
| `ps-AF-LatifaNeural` | لطيفه |
| `ps-AF-GulNawazNeural` |  ګل نواز |

#### Persian (Iran)

| `voice` 值 | 显示名 |
| --- | --- |
| `fa-IR-DilaraNeural` | دلارا |
| `fa-IR-FaridNeural` | فرید |

#### Polish (Poland)

| `voice` 值 | 显示名 |
| --- | --- |
| `pl-PL-AgnieszkaNeural` | Agnieszka |
| `pl-PL-MarekNeural` | Marek |
| `pl-PL-ZofiaNeural` | Zofia |

#### Portuguese (Brazil)

| `voice` 值 | 显示名 |
| --- | --- |
| `pt-BR-FranciscaNeural` | Francisca |
| `pt-BR-AntonioNeural` | Antônio |
| `pt-BR-BrendaNeural` | Brenda |
| `pt-BR-DonatoNeural` | Donato |
| `pt-BR-ElzaNeural` | Elza |
| `pt-BR-FabioNeural` | Fabio |
| `pt-BR-GiovannaNeural` | Giovanna |
| `pt-BR-HumbertoNeural` | Humberto |
| `pt-BR-JulioNeural` | Julio |
| `pt-BR-LeilaNeural` | Leila |
| `pt-BR-LeticiaNeural` | Leticia |
| `pt-BR-ManuelaNeural` | Manuela |
| `pt-BR-NicolauNeural` | Nicolau |
| `pt-BR-ThalitaNeural` | Thalita |
| `pt-BR-ValerioNeural` | Valerio |
| `pt-BR-YaraNeural` | Yara |
| `pt-BR-Caio:MAI-Voice-2` | Caio MAI-Voice-2 |
| `pt-BR-Luana:MAI-Voice-2` | Luana MAI-Voice-2 |
| `pt-BR-Pedro:MAI-Voice-2` | Pedro MAI-Voice-2 |
| `pt-BR-Rafael:MAI-Voice-2` | Rafael MAI-Voice-2 |
| `pt-BR-MacerioMultilingualNeural` | Macerio Multilingual |
| `pt-BR-ThalitaMultilingualNeural` | Thalita multilíngue |
| `pt-BR-Thalita:DragonHDLatestNeural` | Thalita Dragon HD Latest |
| `pt-BR-Macerio:DragonHDLatestNeural` | Macerio Dragon HD Latest |

#### Portuguese (Portugal)

| `voice` 值 | 显示名 |
| --- | --- |
| `pt-PT-RaquelNeural` | Raquel |
| `pt-PT-DuarteNeural` | Duarte |
| `pt-PT-FernandaNeural` | Fernanda |
| `pt-PT-Rui:MAI-Voice-2` | Rui MAI-Voice-2 |

#### Punjabi (India)

| `voice` 值 | 显示名 |
| --- | --- |
| `pa-IN-OjasNeural` | ਓਜਸ |
| `pa-IN-VaaniNeural` | ਵਾਨੀ |

#### Romanian (Romania)

| `voice` 值 | 显示名 |
| --- | --- |
| `ro-RO-AlinaNeural` | Alina |
| `ro-RO-EmilNeural` | Emil |
| `ro-RO-Andrei:MAI-Voice-2` | Andrei MAI-Voice-2 |
| `ro-RO-Elena:MAI-Voice-2` | Elena MAI-Voice-2 |
| `ro-RO-Ioana:MAI-Voice-2` | Ioana MAI-Voice-2 |
| `ro-RO-Radu:MAI-Voice-2` | Radu MAI-Voice-2 |

#### Russian (Russia)

| `voice` 值 | 显示名 |
| --- | --- |
| `ru-RU-SvetlanaNeural` | Светлана |
| `ru-RU-DmitryNeural` | Дмитрий |
| `ru-RU-DariyaNeural` | Дария |
| `ru-RU-Lev:MAI-Voice-2` | Lev MAI-Voice-2 |
| `ru-RU-Masha:MAI-Voice-2` | Masha MAI-Voice-2 |

#### Serbian (Cyrillic, Serbia)

| `voice` 值 | 显示名 |
| --- | --- |
| `sr-RS-SophieNeural` | Софија |
| `sr-RS-NicholasNeural` | Никола |

#### Serbian (Latin, Serbia)

| `voice` 值 | 显示名 |
| --- | --- |
| `sr-Latn-RS-NicholasNeural` | Nicholas |
| `sr-Latn-RS-SophieNeural` | Sophie |

#### Sinhala (Sri Lanka)

| `voice` 值 | 显示名 |
| --- | --- |
| `si-LK-ThiliniNeural` | තිළිණි |
| `si-LK-SameeraNeural` | සමීර |

#### Slovak (Slovakia)

| `voice` 值 | 显示名 |
| --- | --- |
| `sk-SK-ViktoriaNeural` | Viktória |
| `sk-SK-LukasNeural` | Lukáš |

#### Slovenian (Slovenia)

| `voice` 值 | 显示名 |
| --- | --- |
| `sl-SI-PetraNeural` | Petra |
| `sl-SI-RokNeural` | Rok |

#### Somali (Somalia)

| `voice` 值 | 显示名 |
| --- | --- |
| `so-SO-UbaxNeural` | Ubax |
| `so-SO-MuuseNeural` | Muuse |

#### Spanish (Argentina)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-AR-ElenaNeural` | Elena |
| `es-AR-TomasNeural` | Tomas |

#### Spanish (Bolivia)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-BO-SofiaNeural` | Sofia |
| `es-BO-MarceloNeural` | Marcelo |

#### Spanish (Chile)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-CL-CatalinaNeural` | Catalina |
| `es-CL-LorenzoNeural` | Lorenzo |

#### Spanish (Colombia)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-CO-SalomeNeural` | Salome |
| `es-CO-GonzaloNeural` | Gonzalo |

#### Spanish (Costa Rica)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-CR-MariaNeural` | María |
| `es-CR-JuanNeural` | Juan |

#### Spanish (Cuba)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-CU-BelkysNeural` | Belkys |
| `es-CU-ManuelNeural` | Manuel |

#### Spanish (Dominican Republic)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-DO-RamonaNeural` | Ramona |
| `es-DO-EmilioNeural` | Emilio |

#### Spanish (Ecuador)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-EC-AndreaNeural` | Andrea |
| `es-EC-LuisNeural` | Luis |

#### Spanish (El Salvador)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-SV-LorenaNeural` | Lorena |
| `es-SV-RodrigoNeural` | Rodrigo |

#### Spanish (Equatorial Guinea)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-GQ-TeresaNeural` | Teresa |
| `es-GQ-JavierNeural` | Javier |

#### Spanish (Guatemala)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-GT-MartaNeural` | Marta |
| `es-GT-AndresNeural` | Andrés |

#### Spanish (Honduras)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-HN-KarlaNeural` | Karla |
| `es-HN-CarlosNeural` | Carlos |

#### Spanish (Mexico)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-MX-DaliaNeural` | Dalia |
| `es-MX-JorgeNeural` | Jorge |
| `es-MX-BeatrizNeural` | Beatriz |
| `es-MX-CandelaNeural` | Candela |
| `es-MX-CarlotaNeural` | Carlota |
| `es-MX-CecilioNeural` | Cecilio |
| `es-MX-GerardoNeural` | Gerardo |
| `es-MX-LarissaNeural` | Larissa |
| `es-MX-LibertoNeural` | Liberto |
| `es-MX-LucianoNeural` | Luciano |
| `es-MX-MarinaNeural` | Marina |
| `es-MX-NuriaNeural` | Nuria |
| `es-MX-PelayoNeural` | Pelayo |
| `es-MX-RenataNeural` | Renata |
| `es-MX-YagoNeural` | Yago |
| `es-MX-Alejo:MAI-Voice-2` | Alejo MAI-Voice-2 |
| `es-MX-Valeria:MAI-Voice-2` | Valeria MAI-Voice-2 |
| `es-MX-DaliaMultilingualNeural` | Dalia Multilingual |
| `es-MX-JorgeMultilingualNeural` | Jorge Multilingual |
| `es-MX-Ximena:DragonHDLatestNeural` | Ximena Dragon HD Latest |
| `es-MX-Tristan:DragonHDLatestNeural` | Tristan Dragon HD Latest |

#### Spanish (Nicaragua)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-NI-YolandaNeural` | Yolanda |
| `es-NI-FedericoNeural` | Federico |

#### Spanish (Panama)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-PA-MargaritaNeural` | Margarita |
| `es-PA-RobertoNeural` | Roberto |

#### Spanish (Paraguay)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-PY-TaniaNeural` | Tania |
| `es-PY-MarioNeural` | Mario |

#### Spanish (Peru)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-PE-CamilaNeural` | Camila |
| `es-PE-AlexNeural` | Alex |

#### Spanish (Puerto Rico)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-PR-KarinaNeural` | Karina |
| `es-PR-VictorNeural` | Víctor |

#### Spanish (Spain)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-ES-ElviraNeural` | Elvira |
| `es-ES-AlvaroNeural` | Álvaro |
| `es-ES-AbrilNeural` | Abril |
| `es-ES-ArnauNeural` | Arnau |
| `es-ES-DarioNeural` | Dario |
| `es-ES-EliasNeural` | Elias |
| `es-ES-EstrellaNeural` | Estrella |
| `es-ES-IreneNeural` | Irene |
| `es-ES-LaiaNeural` | Laia |
| `es-ES-LiaNeural` | Lia |
| `es-ES-NilNeural` | Nil |
| `es-ES-SaulNeural` | Saul |
| `es-ES-TeoNeural` | Teo |
| `es-ES-TrianaNeural` | Triana |
| `es-ES-VeraNeural` | Vera |
| `es-ES-XimenaNeural` | Ximena |
| `es-ES-Marta:MAI-Voice-2` | Marta MAI-Voice-2 |
| `es-ES-ArabellaMultilingualNeural` | Arabella Multilingual |
| `es-ES-IsidoraMultilingualNeural` | Isidora Multilingual |
| `es-ES-TristanMultilingualNeural` | Tristan Multilingual |
| `es-ES-XimenaMultilingualNeural` | Ximena Multilingual |
| `es-ES-Ximena:DragonHDLatestNeural` | Ximena Dragon HD Latest |
| `es-ES-Tristan:DragonHDLatestNeural` | Tristan Dragon HD Latest |

#### Spanish (United States)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-US-PalomaNeural` | Paloma |
| `es-US-AlonsoNeural` | Alonso |

#### Spanish (Uruguay)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-UY-ValentinaNeural` | Valentina |
| `es-UY-MateoNeural` | Mateo |

#### Spanish (Venezuela)

| `voice` 值 | 显示名 |
| --- | --- |
| `es-VE-PaolaNeural` | Paola |
| `es-VE-SebastianNeural` | Sebastián |

#### Sundanese (Indonesia)

| `voice` 值 | 显示名 |
| --- | --- |
| `su-ID-TutiNeural` | Tuti |
| `su-ID-JajangNeural` | Jajang |

#### Swahili (Kenya)

| `voice` 值 | 显示名 |
| --- | --- |
| `sw-KE-ZuriNeural` | Zuri |
| `sw-KE-RafikiNeural` | Rafiki |

#### Swahili (Tanzania)

| `voice` 值 | 显示名 |
| --- | --- |
| `sw-TZ-RehemaNeural` | Rehema |
| `sw-TZ-DaudiNeural` | Daudi |

#### Swedish (Sweden)

| `voice` 值 | 显示名 |
| --- | --- |
| `sv-SE-SofieNeural` | Sofie |
| `sv-SE-MattiasNeural` | Mattias |
| `sv-SE-HilleviNeural` | Hillevi |

#### Tamil (India)

| `voice` 值 | 显示名 |
| --- | --- |
| `ta-IN-PallaviNeural` | பல்லவி |
| `ta-IN-ValluvarNeural` | வள்ளுவர் |

#### Tamil (Malaysia)

| `voice` 值 | 显示名 |
| --- | --- |
| `ta-MY-KaniNeural` | கனி |
| `ta-MY-SuryaNeural` | சூர்யா |

#### Tamil (Singapore)

| `voice` 值 | 显示名 |
| --- | --- |
| `ta-SG-VenbaNeural` | வெண்பா |
| `ta-SG-AnbuNeural` | அன்பு |

#### Tamil (Sri Lanka)

| `voice` 值 | 显示名 |
| --- | --- |
| `ta-LK-SaranyaNeural` | சரண்யா |
| `ta-LK-KumarNeural` | குமார் |

#### Telugu (India)

| `voice` 值 | 显示名 |
| --- | --- |
| `te-IN-ShrutiNeural` | శ్రుతి |
| `te-IN-MohanNeural` | మోహన్ |

#### Thai (Thailand)

| `voice` 值 | 显示名 |
| --- | --- |
| `th-TH-PremwadeeNeural` | เปรมวดี |
| `th-TH-NiwatNeural` | นิวัฒน์ |
| `th-TH-AcharaNeural` | อัจฉรา |
| `th-TH-Krit:MAI-Voice-2` | Krit MAI-Voice-2 |
| `th-TH-Nattapong:MAI-Voice-2` | Nattapong MAI-Voice-2 |

#### Turkish (Türkiye)

| `voice` 值 | 显示名 |
| --- | --- |
| `tr-TR-EmelNeural` | Emel |
| `tr-TR-AhmetNeural` | Ahmet |
| `tr-TR-Aydın:MAI-Voice-2` | Aydın MAI-Voice-2 |
| `tr-TR-Elif:MAI-Voice-2` | Elif MAI-Voice-2 |

#### Ukrainian (Ukraine)

| `voice` 值 | 显示名 |
| --- | --- |
| `uk-UA-PolinaNeural` | Поліна |
| `uk-UA-OstapNeural` | Остап |

#### Urdu (India)

| `voice` 值 | 显示名 |
| --- | --- |
| `ur-IN-GulNeural` | گل |
| `ur-IN-SalmanNeural` | سلمان |

#### Urdu (Pakistan)

| `voice` 值 | 显示名 |
| --- | --- |
| `ur-PK-UzmaNeural` | عظمیٰ |
| `ur-PK-AsadNeural` | اسد |

#### Uzbek (Latin, Uzbekistan)

| `voice` 值 | 显示名 |
| --- | --- |
| `uz-UZ-MadinaNeural` | Madina |
| `uz-UZ-SardorNeural` | Sardor |

#### Vietnamese (Vietnam)

| `voice` 值 | 显示名 |
| --- | --- |
| `vi-VN-HoaiMyNeural` | Hoài My |
| `vi-VN-NamMinhNeural` | Nam Minh |

#### Welsh (United Kingdom)

| `voice` 值 | 显示名 |
| --- | --- |
| `cy-GB-NiaNeural` | Nia |
| `cy-GB-AledNeural` | Aled |

#### Zulu (South Africa)

| `voice` 值 | 显示名 |
| --- | --- |
| `zu-ZA-ThandoNeural` | Thando |
| `zu-ZA-ThembaNeural` | Themba |

#### 中文（上海话，简体）

| `voice` 值 | 显示名 |
| --- | --- |
| `wuu-CN-XiaotongNeural` | 晓彤 |
| `wuu-CN-YunzheNeural` | 云哲 |

#### 中文（东北话，简体）

| `voice` 值 | 显示名 |
| --- | --- |
| `zh-CN-liaoning-XiaobeiNeural` | 晓北 辽宁 |
| `zh-CN-liaoning-YunbiaoNeural` | 云彪 辽宁 |

#### 中文（台湾话，繁体）

| `voice` 值 | 显示名 |
| --- | --- |
| `zh-TW-HsiaoChenNeural` | 曉臻 |
| `zh-TW-YunJheNeural` | 雲哲 |
| `zh-TW-HsiaoYuNeural` | 曉雨 |

#### 中文（四川话，简体）

| `voice` 值 | 显示名 |
| --- | --- |
| `zh-CN-sichuan-YunxiNeural` | 云希 四川 |

#### 中文（山东话，简体）

| `voice` 值 | 显示名 |
| --- | --- |
| `zh-CN-shandong-YunxiangNeural` | 云翔 |

#### 中文（广东话，简体）

| `voice` 值 | 显示名 |
| --- | --- |
| `yue-CN-XiaoMinNeural` | 晓敏 |
| `yue-CN-YunSongNeural` | 云松 |

#### 中文（广西，简体）

| `voice` 值 | 显示名 |
| --- | --- |
| `zh-CN-guangxi-YunqiNeural` | 云奇 广西 |

#### 中文（普通话，简体）

| `voice` 值 | 显示名 |
| --- | --- |
| `zh-CN-XiaoxiaoNeural` | 晓晓(年轻女) |
| `zh-CN-YunxiNeural` | 云希(年轻男) |
| `zh-CN-YunjianNeural` | 云健(成年男) |
| `zh-CN-XiaoyiNeural` | 晓伊(年轻女) |
| `zh-CN-YunyangNeural` | 云扬(成年男) |
| `zh-CN-XiaochenNeural` | 晓辰(年轻女) |
| `zh-CN-XiaohanNeural` | 晓涵(成年女) |
| `zh-CN-XiaomengNeural` | 晓梦(年轻女) |
| `zh-CN-XiaomoNeural` | 晓墨(成年女) |
| `zh-CN-XiaoqiuNeural` | 晓秋(老年女) |
| `zh-CN-XiaorouNeural` | 晓柔(成年女) |
| `zh-CN-XiaoruiNeural` | 晓睿(老年女) |
| `zh-CN-XiaoshuangNeural` | 晓双(儿童女) |
| `zh-CN-XiaoxiaoDialectsNeural` | 晓晓(陕西) |
| `zh-CN-XiaoyanNeural` | 晓颜(年轻女) |
| `zh-CN-XiaoyouNeural` | 晓悠(儿童女) |
| `zh-CN-XiaozhenNeural` | 晓甄(年轻女) |
| `zh-CN-YunfengNeural` | 云枫(年轻男) |
| `zh-CN-YunhaoNeural` | 云皓(成年男) |
| `zh-CN-YunjieNeural` | 云杰(年轻男) |
| `zh-CN-YunxiaNeural` | 云夏(儿童男) |
| `zh-CN-YunyeNeural` | 云野(老年男) |
| `zh-CN-YunzeNeural` | 云泽(老年男) |
| `zh-CN-Bo:MAI-Voice-2` | Bo MAI-Voice-2 |
| `zh-CN-Lan:MAI-Voice-2` | Lan MAI-Voice-2 |
| `zh-CN-Mei:MAI-Voice-2` | Mei MAI-Voice-2 |
| `zh-CN-Wei:MAI-Voice-2` | Wei MAI-Voice-2 |
| `zh-CN-XiaochenMultilingualNeural` | 晓辰(年轻女新AI) |
| `zh-CN-XiaoshuangMultilingualNeural` | 晓双 多语言 |
| `zh-CN-XiaoxiaoMultilingualNeural` | 晓晓(年轻女新AI) |
| `zh-CN-XiaoyouMultilingualNeural` | 晓悠 多语言 |
| `zh-CN-XiaoyuMultilingualNeural` | 晓宇(儿童男新AI) |
| `zh-CN-YunfanMultilingualNeural` | 云帆(成年男) |
| `zh-CN-YunxiaoMultilingualNeural` | 云霄(成年男) |
| `zh-CN-YunyiMultilingualNeural` | 云逸(年轻男新AI) |
| `zh-CN-Ivoryserenade:DragonHDOmniLatestNeural` | Ivoryserenade Dragon HD Omni Latest |
| `zh-CN-Xiaoshuang:DragonHDOmniLatestNeural` | Xiaoshuang Dragon HD Omni Latest |
| `zh-CN-Xiaoxiao:DragonHDFlashLatestNeural` | Xiaoxiao Dragon HD Flash Latest |
| `zh-CN-Xiaoxiao2:DragonHDFlashLatestNeural` | Xiaoxiao2 Dragon HD Flash Latest |
| `zh-CN-Xiaochen:DragonHDLatestNeural` | Xiaochen Dragon HD Latest |
| `zh-CN-Yunxiao:DragonHDFlashLatestNeural` | Yunxiao Dragon HD Flash Latest |
| `zh-CN-Yunyi:DragonHDFlashLatestNeural` | Yunyi Dragon HD Flash Latest |
| `zh-CN-Yunfan:DragonHDLatestNeural` | Yunfan Dragon HD Latest |
| `zh-CN-Xiaoyue:DragonHDOmniLatestNeural` | Xiaoyue Dragon HD Omni Latest |
| `zh-CN-Yunqi:DragonHDOmniLatestNeural` | Yunqi Dragon HD Omni Latest |
| `zh-CN-Lingqing:DragonHDFlashLatestNeural` | Lingqing Dragon HD Flash Latest |
| `zh-CN-Xiaochen:DragonHDFlashLatestNeural` | Xiaochen Dragon HD Flash Latest |
| `zh-CN-Xiaohan:DragonHDFlashLatestNeural` | Xiaohan Dragon HD Flash Latest |
| `zh-CN-Xiaoshuang:DragonHDFlashLatestNeural` | Xiaoshuang Dragon HD Flash Latest |
| `zh-CN-Xiaoyi:DragonHDFlashLatestNeural` | Xiaoyi Dragon HD Flash Latest |
| `zh-CN-Xiaoyou:DragonHDFlashLatestNeural` | Xiaoyou Dragon HD Flash Latest |
| `zh-CN-Xiaoyu:DragonHDFlashLatestNeural` | Xiaoyu Dragon HD Flash Latest |
| `zh-CN-Yunhan:DragonHDFlashLatestNeural` | Yunhan Dragon HD Flash Latest |
| `zh-CN-Yunxi:DragonHDFlashLatestNeural` | Yunxi Dragon HD Flash Latest |
| `zh-CN-Yunxia:DragonHDFlashLatestNeural` | Yunxia Dragon HD Flash Latest |
| `zh-CN-Yunye:DragonHDFlashLatestNeural` | Yunye Dragon HD Flash Latest |

#### 中文（河南话，简体）

| `voice` 值 | 显示名 |
| --- | --- |
| `zh-CN-henan-YundengNeural` | 云登 |

#### 中文（陕西话，简体）

| `voice` 值 | 显示名 |
| --- | --- |
| `zh-CN-shaanxi-XiaoniNeural` | 晓妮 |

#### 中文（香港话，繁体）

| `voice` 值 | 显示名 |
| --- | --- |
| `zh-HK-HiuMaanNeural` | 曉曼 |
| `zh-HK-WanLungNeural` | 雲龍 |
| `zh-HK-HiuGaaiNeural` | 曉佳 |

### `kbitrate` / `output_format` 输出质量

| 值 | 说明 |
| --- | --- |
| `audio-16khz-32kbitrate-mono-mp3` | 16khz-32kbitrate(mp3) |
| `audio-16khz-128kbitrate-mono-mp3` | 16khz-128kbitrate(mp3) |
| `audio-24khz-160kbitrate-mono-mp3` | 24khz-160kbitrate(mp3) |
| `audio-48khz-192kbitrate-mono-mp3` | 48khz-192kbitrate(mp3) |
| `riff-16khz-16bit-mono-pcm` | 16khz-16bit-mono-pcm(wav) |
| `riff-24khz-16bit-mono-pcm` | 24khz-16bit-mono-pcm(wav) |
| `riff-48khz-16bit-mono-pcm` | 48khz-16bit-mono-pcm(wav) |

默认值：`audio-24khz-160kbitrate-mono-mp3`

### `role` 模仿

| 值 | 说明 |
| --- | --- |
| `0` | 请选择模仿（非必选） |
| `Girl` | 模拟女孩 |
| `Boy` | 模拟男孩 |
| `YoungAdultFemale` | 模拟年轻成年女性 |
| `YoungAdultMale` | 模拟年轻成年男性 |
| `OlderAdultFemale` | 模拟年长的成年女性 |
| `OlderAdultMale` | 模拟年长的成年男性 |
| `SeniorFemale` | 模拟老年女性 |
| `SeniorMale` | 模拟老年男性 |

### `style` 感情

| 值 | 说明 |
| --- | --- |
| `0` | 请选择感情（非必选） |
| `affectionate` | 以较高的音调和音量表达温暖而亲切的语气 |
| `angry` | 表达生气和厌恶的语气 |
| `assistant` | 热情而轻松的语气 |
| `calm` | 以沉着冷静的态度说话 |
| `chat` | 表达轻松随意的语气 |
| `cheerful` | 表达积极愉快的语气 |
| `customerservice` | 友好热情的语气 |
| `depressed` | 调低音调和音量来表达忧郁、沮丧的语气 |
| `disgruntled` | 表达轻蔑和抱怨的语气 |
| `embarrassed` | 在说话者感到不舒适时表达不确定、犹豫的语气 |
| `empathetic` | 表达关心和理解 |
| `envious` | 当你渴望别人拥有的东西时，表达一种钦佩的语气 |
| `fearful` | 以较高的音调、较高的音量和较快的语速来表达恐惧、紧张的语气 |
| `gentle` | 以较低的音调和音量表达温和、礼貌和愉快的语气 |
| `lyrical` | 以优美又带感伤的方式表达情感 |
| `narration-professional` | 以专业、客观的语气朗读内容 |
| `narration-relaxed` | 为内容阅读表达一种舒缓而悦耳的语气 |
| `newscast` | 以正式专业的语气叙述新闻 |
| `newscast-casual` | 以通用、随意的语气发布一般新闻 |
| `newscast-formal` | 以正式、自信和权威的语气发布新闻 |
| `sad` | 表达悲伤语气 |
| `serious` | 表达严肃和命令的语气 |
| `shouting` | 就像从遥远的地方说话或在外面说话，但能让自己清楚地听到 |
| `advertisement-upbeat` | 用兴奋和精力充沛的语气推广产品或服务 |
| `sports-commentary` | 用轻松有趣的语气播报体育赛事 |
| `sports-commentary-excited` | 用快速且充满活力的语气播报体育赛事精彩瞬间 |
| `whispering` | 说话非常柔和，发出的声音小且温柔 |
| `terrified` | 表达一种非常害怕的语气，语速快且声音颤抖。 听起来说话人处于不稳定的疯狂状态 |
| `unfriendly` | 表达一种冷淡无情的语气 |

> 实际可用感情取决于具体语音，可通过 `GET /voices` + `/getStyle.php` 查询某语音的 `StyleList`。

### `styledegree` 感情强度

| 值 | 说明 |
| --- | --- |
| `1` | 默认（感情强度） |
| `0.5` | 弱 |
| `1.5` | 强 |
| `2` | 超强 |

### `volume` 音量

| 值 | 说明 |
| --- | --- |
| `75` | 默认 |
| `x-soft` | 超弱 |
| `soft` | 弱 |
| `loud` | 强 |
| `x-loud` | 超强 |

### `silence` 句末停顿

| 值 | 说明 |
| --- | --- |
| `(空)` | 默认（批量在每个句子的结束符号后停顿） |
| `20ms` | 20ms |
| `50ms` | 50ms |
| `100ms` | 100ms |
| `150ms` | 150ms |
| `200ms` | 200ms |
| `500ms` | 500ms |
| `1000ms` | 1000ms |
| `2000ms` | 2000ms |
| `3000ms` | 3000ms |
| `5000ms` | 5000ms |

### `rate` / `speed` 语速

| 值 | 说明 |
| --- | --- |
| `0` | 默认语速 |
| 整数或带 `%` 字符串 | 相对百分比，如 `-20`、`50`、`+10%`。负数减慢，正数加快 |

### `pitch` 音调

| 值 | 说明 |
| --- | --- |
| `0` | 默认音调 |
| 整数或带 `%` 字符串 | 相对百分比，如 `-10`、`15`、`+5%`。负数降低，正数升高 |

### `predict` 自动预测

| 值 | 说明 |
| --- | --- |
| `0` | 关闭 |
| `1` | 开启（开启后感情语速等选项均会失效） |

> 开启后站点会忽略 `style` / `role` / `rate` 等参数，仅对支持的中文语音生效。

### SSML 模式额外说明

| 参数 | 可选项 |
| --- | --- |
| `type` | `SSML` |
| `text` / `ssml` | 完整 SSML 文档 |
| `kbitrate` | 同上表 7 种格式 |

## 注意事项

- 首次调用会启动浏览器获取 token，约需数秒；之后会复用会话（默认 30 分钟）。
- token 失效时会自动刷新并重试。
- 站点有每日字数与频率限制，请合理使用。
- 站点声明禁止未授权抓包调用，仅供个人学习调试。
