# 🤖 Team ស្មោះស្នេហ៍ Telegram Bot

Telegram bot របស់ Team ស្មោះស្នេហ៍ ប្រើ Python, `pyTelegramBotAPI`, SQLite និង Google Gemini សម្រាប់ជំនួយការសន្ទនា និងវិភាគរូបភាពពី Telegram។ Bot ដំណើរការដោយ **long polling** តាម `telebot.TeleBot.infinity_polling()` ក្នុង `bot.py`; វាមិនប្រើ Flask ឬ webhook ទេ។ ការកំណត់ Railway ក៏ចាប់ផ្តើមដោយ `python bot.py` ដែរ។

## ដំណើរការក្នុងម៉ាស៊ីន

1. ដំឡើង Python dependencies៖ `python -m pip install -r requirements.txt`។
2. កំណត់ `BOT_TOKEN` ក្នុង environment ឬឯកសារ `.env` ក្នុងថតគម្រោង។ `config.py` អាន `.env` តាម `python-dotenv`។
3. ដើម្បីប្រើជំនួយការ Gemini សម្រាប់អត្ថបទ សំឡេង និងរូបភាព សូមកំណត់ `GEMINI_API_KEY` ផងដែរ។
4. ចាប់ផ្តើម bot ដោយ `python bot.py`។ Bot នឹងរៀបចំ SQLite ហើយចាប់ផ្តើម polling។

រក្សា token និង API key នៅក្នុង secret environment របស់ម៉ាស៊ីនដំណើរការ។ `.env` ត្រូវបាន ignore ដោយ Git; កុំបញ្ចូល secret ទៅក្នុង repository ឬ persona file។

## Cooler Bot 2050 persona

Persona លំនាំដើមស្ថិតនៅ `persona/cooler_bot_2050.txt` ជាឯកសារ UTF-8។ អាចកំណត់ `COOLER_PERSONA_PATH` ដើម្បីប្រើទីតាំងផ្សេង។ ផ្លូវ relative ត្រូវបានស្វែងរកពីថតគម្រោងដែលមាន `ai_assistant.py` និងពី working directory; ផ្លូវ absolute ក៏អាចប្រើបាន។ Persona ត្រូវបានអានម្តងក្នុងមួយ process ហើយ cache រហូតដល់ bot ចាប់ផ្តើមឡើងវិញ។

ប្រសិនបើឯកសារមិនមាន ឬទទេ bot ប្រើ persona ខ្លីដែលមានស្រាប់ក្នុង `ai_assistant.py`។ ច្បាប់ភាសា សុវត្ថិភាព និងការបញ្ជាក់ម្ចាស់ត្រូវបានភ្ជាប់បន្ថែមដោយ runtime ដូច្នេះ persona file មិនអាចប្តូរ rate limit, ការគ្រប់គ្រង API key ឬការផ្ទៀងផ្ទាត់ម្ចាស់បានទេ។

## Environment variables

`config.py` អានអថេរខាងក្រោម។ តម្លៃដែលមានក្នុងជួរឈរ Default គឺតម្លៃបច្ចុប្បន្ននៅពេលមិនកំណត់។

| Variable | Default | ប្រើសម្រាប់ |
| --- | --- | --- |
| `BOT_TOKEN` | គ្មាន | ចាំបាច់សម្រាប់ភ្ជាប់ Telegram bot។ |
| `GEMINI_API_KEY` | គ្មាន | បើក Gemini សម្រាប់អត្ថបទ សំឡេង និងការវិភាគរូបភាព; បើគ្មាន key bot បង្ហាញសារថា AI មិនទាន់កំណត់។ |
| `STABILITY_API_KEY` | គ្មាន | API key សម្រាប់មុខងារកែរូបភាព Stability AI។ |
| `COOLER_PERSONA_PATH` | `persona/cooler_bot_2050.txt` | ទីតាំង persona file; អាចជា absolute ឬ relative path។ |
| `AI_MODEL` | `gemini-2.0-flash` | Gemini model សម្រាប់ Chat; fallback គឺ `gemini-3.6-flash`។ |
| `AI_VISION_MODEL` | `gemini-3.8-flash` | Gemini Vision model ដាច់ដោយឡែកសម្រាប់ការវិភាគរូបភាព; fallback គឺ `gemini-3.6-flash`។ |
| `AI_RATE_LIMIT_SECONDS` | `3` | ចន្លោះអប្បបរមារវាងសំណើពីអ្នកប្រើដដែល។ |
| `AI_GLOBAL_RATE_LIMIT_SECONDS` | `0.5` | ចន្លោះអប្បបរមារវាងសំណើ Gemini ទូទាំង bot។ |
| `AI_DAILY_LIMIT_PER_USER` | `50` | ចំនួនចម្លើយ AI ជោគជ័យអតិបរមាក្នុងមួយថ្ងៃសម្រាប់អ្នកប្រើម្នាក់។ តម្លៃ `0` បិទកម្រិតនេះ។ |
| `AI_CONTEXT_MESSAGE_LIMIT` | `8` | ចំនួនសារប្រវត្តិសន្ទនាដែលប្រើជាបរិបទ។ |
| `AI_CONTEXT_CHAR_LIMIT` | `4000` | កម្រិតតួអក្សរសម្រាប់បរិបទសន្ទនា។ |
| `AI_SUMMARY_ENABLED` | `False` | បើ `true`, `1`, ឬ `yes` នឹងបញ្ចូលសេចក្តីសង្ខេបសន្ទនាពី database ក្នុងបរិបទ។ |
| `OWNER_ID` | គ្មាន | Telegram numeric user ID សម្រាប់ការបញ្ជាក់អត្តសញ្ញាណម្ចាស់ក្នុង system instruction។ មិនដាក់លេខនេះក្នុង prompt ឬ log ទេ។ |
| `BOT_DB_PATH` | `bot_database.db` | ទីតាំង SQLite database; លើ Railway ឬ Render ត្រូវកំណត់ឱ្យនៅក្នុង mounted volume។ |
| `DAILYCARE_ENABLED` | `False` | តម្លៃដំបូងសម្រាប់ការបើកសារសកល; admin អាចប្តូរបានតាម `/dailycare` ហើយតម្លៃនឹងរក្សាទុកក្នុង database។ |
| `DAILYCARE_RATE_LIMIT_SECONDS` | `1` | ចន្លោះពេលរវាងការផ្ញើ DM ទៅសមាជិកនីមួយៗ។ |
| `DAILYCARE_MAX_SEND_ATTEMPTS` | `3` | ចំនួនសាកល្បងអតិបរមាសម្រាប់ Telegram rate-limit response ដែលមាន `retry_after`។ |
| `DAILYCARE_RETRY_MAX_WAIT_SECONDS` | `30` | មិនរង់ចាំ retry បើ Telegram ស្នើឱ្យរង់ចាំលើសចំនួនវិនាទីនេះ។ |
| `DAILYCARE_BATCH_SIZE` | `50` | ចំនួនអ្នកទទួលអតិបរមាក្នុងការទាញមួយ batch; កម្មវិធីបន្តទាញតាម user ID រហូតដល់អស់អ្នកទទួល។ |
| `DAILYCARE_LOCK_TTL_SECONDS` | `90` | រយៈពេល lease របស់ scheduler ក្នុង SQLite; bot បន្ត lease ដោយ heartbeat។ |
| `AI_VOICE_MODEL` | គ្មាន | Model ស្ដាប់ Voice Note មុនគេ; បន្ទាប់មក `AI_VISION_MODEL`, `gemini-3.8-flash`, `gemini-3.6-flash`, `AI_MODEL`។ |
| `TTS_MODEL` | គ្មាន | Model អានជាសំឡេង (/say); default `gemini-3.8-flash-lite-tts` រួច `gemini-3.8-flash-tts`។ |
| `TTS_DAILY_LIMIT` | `20` | ចំនួន /say ឬ 🔊 ក្នុងមួយថ្ងៃ ក្នុងម្នាក់ (`0` = គ្មានកម្រិត; Owner គ្មានកម្រិត)។ |
| `KB_AI_MODEL` | គ្មាន | Model សម្រាប់ AI ឆ្លើយតាមឯកសារ (/learn /ask)។ |
| `KB_MAX_CHARS` | `150000` | ទំហំឯកសារសរុបអតិបរមាក្នុងមួយ Chat/Group។ |
| `BOT_THREADS` | `8` | ចំនួន Worker ដំណើរការសារព្រមគ្នា (ពីមុន 2)។ |
| `SKIP_PENDING_UPDATES` | `false` | `true` = បោះចោលសារដែលផ្ញើមកពេល Bot កំពុង Restart។ |
| `OPS_ALERT_MINUTES` | `15` | ផ្ញើ Error ប្រភេទដដែលទៅ Owner យ៉ាងច្រើនម្ដងក្នុងរយៈពេលនេះ។ |
| `MUSIC_MODEL` | `lyria-3.5` | Model ធ្វើបទពេញ (/song); fallback `lyria-3-clip-preview`។ ត្រូវការ Gemini API paid tier (~$0.08/បទ)។ |
| `BEAT_MODEL` | `lyria-3-clip-preview` | Model ធ្វើ Beat ៣០ វិនាទី (/beat) (~$0.04)។ |
| `SONG_DAILY_LIMIT` | `2` | ចំនួនបទ + Beat ក្នុងមួយថ្ងៃ ក្នុងម្នាក់ (Owner គ្មានកម្រិត; ធ្វើមិនបានមិនកាត់)។ |
| `BAKONG_API_TOKEN` | គ្មាន | Token Bakong Open API (api-bakong.nbc.gov.kh/register; ប្ដូររៀងរាល់ ៩០ ថ្ងៃ) — បើក KHQR បង់ស្វ័យប្រវត្តិ។ |
| `BAKONG_ACCOUNT_ID` | គ្មាន | Bakong ID ទទួលលុយ (ឧ. `name@aba`)។ |
| `BAKONG_ACCOUNT_INFO` / `BAKONG_ACQUIRING_BANK` | គ្មាន | Sub-field 01/02 នៃ KHQR (ធនាគារខ្លះត្រូវការ)។ |
| `BAKONG_MERCHANT_NAME` | `Cooler Bot` | ឈ្មោះបង្ហាញក្នុងកម្មវិធីធនាគារ។ |
| `BAKONG_API_BASE_URL` | `https://api-bakong.nbc.gov.kh` | ប្ដូរទៅ Relay បើ Bakong ទទួលតែ IP កម្ពុជា។ |
| `BAKONG_DAILY_LIMIT` | `95` | ចំនួនសំណើ Bakong API ក្នុងមួយថ្ងៃ (Bakong អនុញ្ញាត ១០០)។ Bill ពិនិត្យតាមកាលវិភាគ ហើយរួមគ្នាក្នុងសំណើតែមួយ។ |
| `SUB_FREE_GROUPS` | គ្មាន | ID Group (បំបែកដោយ ,) ដែលមិនត្រូវបង់ និង Bot មិនចេញ។ Group ដែល Owner ជាអ្នកបង្កើត មិនត្រូវបង់ដោយស្វ័យប្រវត្តិ។ |
| `HUB_URL` | `…/Blue-Gold-Rush/hub/` | ទំព័រ Cooler Hub (Mini App)។ |
| `HUB_MENU_BUTTON` | `true` | ដាក់ប៊ូតុង 🚀 Hub ក្បែរប្រអប់វាយសារ (Private)។ |
| `CARDS_ENABLED` | `true` | ផ្ញើរូបកាតស្អាត (VIP, /report, ព័ត៌មានព្រឹក)។ អក្សរខ្មែរលើរូបត្រូវការ `libfribidi0` (Railway: `RAILPACK_DEPLOY_APT_PACKAGES="ffmpeg libfribidi0"`) បើគ្មាន រូបប្រើអក្សរអង់គ្លេស។ |
| `BRIEF_HOUR` | `7` | ម៉ោងផ្ញើព័ត៌មានពេលព្រឹក (/morning)។ |
| `IMAGINE_MODEL` | `gemini-nano-banana-2.1` | Model គូររូប (/imagine)។ |
| `IMAGINE_DAILY_LIMIT` | `5` | ចំនួនរូប/ថ្ងៃ/នាក់។ |
| `SUB_REPORT_HOUR` | `8` | ម៉ោង (កម្ពុជា) ផ្ញើរបាយការណ៍ប្រចាំថ្ងៃទៅ Owner; `-1` = បិទ។ |

ឈ្មោះ `AI_PROVIDER`, `OPENAI_API_KEY`, `ADMIN_IDS`, និង `DRY_RUN_MODE` ក៏ត្រូវបានអានក្នុង `config.py` ប៉ុន្តែមិនត្រូវបានប្រើដោយ code path បច្ចុប្បន្នទេ។ ការកំណត់ `AI_PROVIDER` មិនប្តូរ Gemini ទៅ provider ផ្សេងឡើយ។

## Pro upgrade (សមាជិក សំឡេង ឯកសារ ស្ថិរភាព)

- 🔔 **រក្សាភ្ញៀវ**: រំលឹកមុនផុតកំណត់ ៣ ថ្ងៃ និង ១ ថ្ងៃ ហើយពេលផុតកំណត់ (មានប៊ូតុងបង់បន្ត); `/stats` ចំណូលថ្ងៃនេះ/ខែនេះ/ខែមុន សមាជិកសកម្ម អ្នកជិតផុត និងអ្នកមិនទាន់បន្ត; របាយការណ៍ប្រចាំថ្ងៃម៉ោង 8 ព្រឹក; រំលឹកបង្កាន់ដៃរង់ចាំលើស ៣ ម៉ោង
- 💳 **KHQR ស្វ័យប្រវត្តិ**: `/pay` បង្ហាញ KHQR មានចំនួនលុយ → Bot សួរ Bakong រៀងរាល់ ៥ វិនាទី → បើកសមាជិកភ្លាម (Owner ទទួលដំណឹង); `/khqrtest` សាក ១០០ រៀល; គ្មាន Token = ប្រើបង្កាន់ដៃដូចមុន
- 🛡️ **Cooler Guard**: `/guard` (Admin Group) លុប Link បោក/Forward/ពាក្យអាក្រក់, Flood → បិទមាត់, Captcha សមាជិកថ្មី, `/warn /mute /ban /unban /setwelcome /badword /allowlink`
- 📅 **ប្រតិទិនខ្មែរ**: `/calendar` ថ្ងៃចន្ទគតិ (port ពី momentkh), ថ្ងៃសីល, ថ្ងៃឈប់សម្រាក (អនុក្រឹត្យ ២០២៦ + បុណ្យតាមចន្ទគតិ), `/goodday`; ☀️ `/morning` ព័ត៌មានម៉ោង ៧ (ឥតគិតថ្លៃ)
- 🎨 `/imagine` គូររូប · 📝 `/summary` សង្ខេប Group · 🧠 `/memory` `/forget` · 🎁 មិត្ត ៣ នាក់បង់ = ៧ ថ្ងៃឥតគិតថ្លៃ
- 🧰 **ជំនួយប្រចាំថ្ងៃ**: 🚨 `/scam` ឆែកសារ/Link/រូបបោកប្រាស់ (ឥតគិតថ្លៃ) · 🎤 `/totext` Voice → អក្សរខ្មែរ · 🌐 `/translate` រូប/អក្សរ → ខ្មែរ · 📚 `/homework` ពន្យល់លំហាត់ពីរូប · ⏰ `/remind` និយាយធម្មតា (ម្ដង/រាល់ថ្ងៃ/សប្ដាហ៍/ខែ) `/reminders` · 💵 `/cash` `/sale` `/undo` `/report` (Private = សៀវភៅផ្ទាល់ខ្លួន, Group = សៀវភៅហាង)
- 🎧 **Cooler Music**: `/song ប្រធានបទ` → ជ្រើសស្ទីល (Rap, Pop, ស្នេហា, Party, Chill, ខ្មែរបុរាណ Mix) → Gemini សរសេរទំនុកខ្មែរថ្មី → Lyria ច្រៀង/Rap ជាមួយភ្លេងពេញ (MP3 + ទំនុក); `/beat` Beat ៣០ វិនាទី; ប៊ូតុង 🎧 ក្រោម /rap
- 🎙️ **សំឡេង**: Voice Note បង្ហាញ «អ្វីដែល AI ឮ» + ប៊ូតុង 🔊; `/say` អានអត្ថបទជាសំឡេងខ្មែរ; `/voice` ស្ដាប់សំឡេង Cooler Bot (សំឡេងតែមួយ៖ ក្មេងប្រុស ១៨-២២ ឆ្នាំ; ប្តូរបានដោយ `TTS_VOICE`)។ បើ Server មាន `ffmpeg` (Railway: `RAILPACK_DEPLOY_APT_PACKAGES=ffmpeg`) Bot ផ្ញើជា Voice bubble, បើគ្មាន ផ្ញើជា Audio file
- 📚 **AI ឆ្លើយតាមឯកសារ**: `/learn` (អត្ថបទ, PDF, .txt, រូបថត), `/ask`, `/kb`, `/kbclear`, `/autoanswer on|off` ក្នុង Group (Admin ប៉ុណ្ណោះ)
- 🛡️ **ស្ថិរភាព**: Worker 8, Error ក្នុង Handler មិនធ្វើឲ្យ Polling ឈប់ ហើយផ្ញើដំណឹងទៅ Owner; `/status` (uptime, error ២៤ ម៉ោង, 🩺 សាក AI)

## Daily Care notifications

- សមាជិកត្រូវជជែកជាមួយ Bot ជាមុនសិន។ ការជជែកមិនមែនជាការយល់ព្រមទទួលសារទេ; ស្ថានភាពដំបូងគឺ **បិទ** ហើយសមាជិកអាចប្រើ `/notifications` ដើម្បីបើក ឬបិទបានគ្រប់ពេល។
- Bot ចុះឈ្មោះអ្នកប្រើពីសារ និង callback ដែលវាទទួលបានពិតប្រាកដ។ ការប្រើ group មិនជំនួស private DM route ដែលអ្នកប្រើបានបង្កើតជាមួយ Bot ទេ។
- សារប្រចាំថ្ងៃមានម៉ោង 07:00, 12:00, 17:30, 19:00, 21:00 និង 22:00 តាម `Asia/Phnom_Penh`។ Gemini បង្កើតសារខ្លីមួយក្នុងមួយម៉ោងផ្ញើ ហើយប្រើសារខ្មែរបម្រុងប្រសិនបើ AI មិនអាចប្រើបាន។
- Admin ដែលមានក្នុង `ADMIN_IDS` អាចប្រើ `/dailycare` ដើម្បីមើលស្ថានភាព បើក/បិទការផ្ញើទាំងមូល និងផ្ញើសាកល្បងមកខ្លួនឯង។ ការផ្ញើសកលចាប់ផ្តើមបិទ ហើយការបើកមិនបើកការជូនដំណឹងឱ្យសមាជិកជំនួសពួកគេទេ។
- Bot ផ្ញើ DM តែទៅសមាជិកដែលបានជជែកក្នុង private chat និងបានជ្រើសរើសទទួល។ ការចុចប៊ូតុងក្នុង group មិនធ្វើឱ្យ group chat ក្លាយជាគោលដៅ DM ទេ។
- Scheduler ប្រើ lease ក្នុង SQLite ដើម្បីកុំឱ្យ process ពីរដែលប្រើ database ដូចគ្នាចាប់ផ្តើមកាលវិភាគស្របពេលគ្នា; delivery key តែមួយក្នុងមួយអ្នកប្រើ/ថ្ងៃ/slot រារាំងការផ្ញើស្ទួន។

## Deploy to Railway

Bot នេះប្រើ Telegram long polling។ វាមិនចាំបាច់មាន public domain ឬ `PORT` ទេ។ `railway.json` កំណត់ start command ជា `python bot.py`; Railway អាចរកឃើញ Python dependencies ពី `requirements.txt` ដោយស្វ័យប្រវត្តិ។

1. ក្នុង Railway ចុច **New Project → Deploy from GitHub Repo** ហើយជ្រើស `kn8508827-arch/Bot-Smos-Smea` និង branch `main`។
2. នៅផ្ទាំង **Variables** របស់ service បន្ថែម `BOT_TOKEN` និង `GEMINI_API_KEY` ដើម្បីប្រើមុខងារ AI។ `AI_VISION_MODEL` អាចទុកតាម default ឬកំណត់ model ផ្សេងដែលគាំទ្រ image input។ កុំដាក់តម្លៃសម្ងាត់ក្នុង Git ឬផ្ញើតាម chat។
3. ភ្ជាប់ Railway Volume ទៅ service ដោយ mount path `/app/data` ហើយកំណត់ `BOT_DB_PATH=/app/data/bot_database.db`។ ត្រូវភ្ជាប់ Volume មុនពេលចាប់ផ្តើមរក្សាទុកទិន្នន័យដែលចង់រក្សាទុក ព្រោះ filesystem ខាងក្រៅ Volume អាចបាត់ពេល deploy ថ្មី។ បើ service ចាស់មាន database ត្រូវបម្រុងទុក ហើយចម្លងចូល Volume មុនប្តូរ `BOT_DB_PATH`។
4. រក្សា service ឱ្យដំណើរការតែមួយ replica ខណៈប្រើ SQLite។ កុំបង្កើត public domain សម្រាប់ bot នេះ។
5. Deploy ហើយពិនិត្យ deploy logs: គួរឃើញ bot ចាប់ផ្តើម polling ដោយគ្មាន error។ បន្ទាប់មកសាក `/start` ក្នុង Telegram។

`BOT_TOKEN` ជាតម្លៃចាំបាច់។ `ADMIN_IDS` អាចទុកចោល បើមិនប្រើមុខងារ admin។ `OWNER_ID` អាចទុកចោលបាន ប៉ុន្តែត្រូវកំណត់ជា Telegram user ID ជាលេខក្នុង Railway Variables ដើម្បីឱ្យ Bot ស្គាល់ម្ចាស់ និងហៅថា Papa។ Daily Care ចាប់ផ្តើមបិទ ហើយត្រូវការទាំង Volume និង admin ដែលបានកំណត់ មុនពេលបើកតាម `/dailycare`។ សមាជិកម្នាក់ៗត្រូវជ្រើសរើសទទួលដោយខ្លួនឯងតាម `/notifications`។

សូមមើល [Railway Volumes](https://docs.railway.com/volumes) សម្រាប់ mount និង persistent storage។

## រចនាសម្ព័ន្ធសំខាន់

- `bot.py` — Telegram handlers និង long polling entry point។
- `ai_assistant.py` — Gemini requests សម្រាប់ Chat/Voice ដើម និង Gemini Vision ផ្លូវដាច់ដោយឡែក, persona/system instruction, chat context, throttling និង daily usage limit។
- `persona/cooler_bot_2050.txt` — persona ជាភាសាខ្មែរដែលអាចកែបាន។
- `config.py` — environment settings និង `.env` loading។
- `database.py` — SQLite storage សម្រាប់សមាជិក ប្រវត្តិសន្ទនា និង usage។
- `scheduler.py` — កាលវិភាគ Daily Care និងការផ្ញើសារដោយមានការការពារ duplicate។
- `photo_studio.py` — photo transformation flow។
- `location_service.py` — ស្វែងរកស្ថានីយសាំង និងសាក EV ពីទិន្នន័យ OpenStreetMap។
- `test_local.py` — offline unit tests សម្រាប់មុខងារសំខាន់ៗ។
- `test_location_service.py` — offline tests សម្រាប់ distance, Khmer/English queries និងស្វែងរក fuel/EV។

ដំណើរការតេស្តដោយ `python -m unittest test_local.py test_dailycare.py test_location_service.py`។

## ស្វែងរកស្ថានីយប្រេង និងសាក EV

- Bot ប្រើ OpenStreetMap តាម Overpass API សម្រាប់ fuel/EV station និង Nominatim សម្រាប់រកទីតាំងដែលវាយជាឈ្មោះ។ មិនត្រូវការ Google Maps API key ឬ `EV_STATIONS_DATA_URL` ទេ; Google Maps ត្រូវបានប្រើតែសម្រាប់បង្កើតតំណផ្លូវ។
- ចុច `/menu`, `/fuel`, `/ev` ឬប៊ូតុង Khmer ដើម្បីស្វែងរក។ អ្នកអាចចែករំលែកទីតាំងក្នុង private chat ឬវាយខេត្ត/ទីតាំងជាភាសាខ្មែរ និងអង់គ្លេស។ ទីតាំងអ្នកប្រើមិនត្រូវបានរក្សាទុកក្នុង SQLite; ស្ថានភាពសន្ទនាសម្រាប់ប៊ូតុងបន្ទាប់ផុតកំណត់ក្នុងរយៈពេល 15 នាទី។
- ទិន្នន័យស្ថានីយសាធារណៈត្រូវបាន cache ក្នុង memory 30 នាទី និង geocoding 24 ម៉ោង; cache ត្រូវបានកំណត់ទំហំ ហើយបាត់នៅពេល process ចាប់ផ្តើមឡើងវិញ។ សេវាសាធារណៈអាចមាន rate limit ឬពេលខ្លះមិនអាចប្រើបាន។
- ព័ត៌មានម៉ោង, លេខទូរស័ព្ទ, connector និង charging power បង្ហាញតែពេលមាន tag ក្នុង OpenStreetMap។ សូមផ្ទៀងផ្ទាត់ជាមួយស្ថានីយផ្ទាល់។ Bot មិនផ្តល់តម្លៃ ឬ live charger availability ហើយទិន្នន័យអាចមិនមាន/មិនទាន់សម័យ។
- សម្រាប់ Railway deployment, សូមធ្វើតាមផ្នែក [Deploy to Railway](#deploy-to-railway) ខាងលើ។
- សម្រាប់ Render, ប្រើ `render.yaml` ដើម្បីបង្កើត Background Worker និង persistent disk នៅ `/app/data`; បញ្ចូល `BOT_TOKEN` ជា secret និង `GEMINI_API_KEY` បើចង់ប្រើ AI។ កុំបង្កើន worker instances ខណៈកំពុងប្រើ SQLite។
