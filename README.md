# 🤖 Team ស្មោះស្នេហ៍ Telegram Bot

Telegram bot របស់ Team ស្មោះស្នេហ៍ ប្រើ Python, `pyTelegramBotAPI`, SQLite និង Google Gemini សម្រាប់ជំនួយការសន្ទនា។ Bot ដំណើរការដោយ **long polling** តាម `telebot.TeleBot.infinity_polling()` ក្នុង `bot.py`; វាមិនប្រើ Flask ឬ webhook ទេ។ ការកំណត់ Railway ក៏ចាប់ផ្តើមដោយ `python bot.py` ដែរ។

## ដំណើរការក្នុងម៉ាស៊ីន

1. ដំឡើង Python dependencies៖ `python -m pip install -r requirements.txt`។
2. កំណត់ `BOT_TOKEN` ក្នុង environment ឬឯកសារ `.env` ក្នុងថតគម្រោង។ `config.py` អាន `.env` តាម `python-dotenv`។
3. ដើម្បីប្រើជំនួយការ Gemini សូមកំណត់ `GEMINI_API_KEY` ផងដែរ។
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
| `GEMINI_API_KEY` | គ្មាន | បើកជំនួយការសន្ទនា Gemini; បើគ្មាន key bot បង្ហាញសារថា AI មិនទាន់កំណត់។ |
| `STABILITY_API_KEY` | គ្មាន | API key សម្រាប់មុខងារកែរូបភាព Stability AI។ |
| `COOLER_PERSONA_PATH` | `persona/cooler_bot_2050.txt` | ទីតាំង persona file; អាចជា absolute ឬ relative path។ |
| `AI_MODEL` | `gemini-2.0-flash` | Gemini model សម្រាប់សន្ទនា។ កូដបច្ចុប្បន្នប្រើ `gemini-1.5-flash` ជា fallback។ |
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

ឈ្មោះ `AI_PROVIDER`, `OPENAI_API_KEY`, `ADMIN_IDS`, និង `DRY_RUN_MODE` ក៏ត្រូវបានអានក្នុង `config.py` ប៉ុន្តែមិនត្រូវបានប្រើដោយ code path បច្ចុប្បន្នទេ។ ការកំណត់ `AI_PROVIDER` មិនប្តូរ Gemini ទៅ provider ផ្សេងឡើយ។

## Daily Care notifications

- សមាជិកត្រូវជជែកជាមួយ Bot ជាមុនសិន។ ការជជែកមិនមែនជាការយល់ព្រមទទួលសារទេ; ស្ថានភាពដំបូងគឺ **បិទ** ហើយសមាជិកអាចប្រើ `/notifications` ដើម្បីបើក ឬបិទបានគ្រប់ពេល។
- Bot ចុះឈ្មោះអ្នកប្រើពីសារ និង callback ដែលវាទទួលបានពិតប្រាកដ។ ការប្រើ group មិនជំនួស private DM route ដែលអ្នកប្រើបានបង្កើតជាមួយ Bot ទេ។
- សារប្រចាំថ្ងៃមានម៉ោង 07:00, 12:00, 17:30, 19:00, 21:00 និង 22:00 តាម `Asia/Phnom_Penh`។ Gemini បង្កើតសារខ្លីមួយក្នុងមួយម៉ោងផ្ញើ ហើយប្រើសារខ្មែរបម្រុងប្រសិនបើ AI មិនអាចប្រើបាន។
- Admin ដែលមានក្នុង `ADMIN_IDS` អាចប្រើ `/dailycare` ដើម្បីមើលស្ថានភាព បើក/បិទការផ្ញើទាំងមូល និងផ្ញើសាកល្បងមកខ្លួនឯង។ ការផ្ញើសកលចាប់ផ្តើមបិទ ហើយការបើកមិនបើកការជូនដំណឹងឱ្យសមាជិកជំនួសពួកគេទេ។
- Bot ផ្ញើ DM តែទៅសមាជិកដែលបានជជែកក្នុង private chat និងបានជ្រើសរើសទទួល។ ការចុចប៊ូតុងក្នុង group មិនធ្វើឱ្យ group chat ក្លាយជាគោលដៅ DM ទេ។
- Scheduler ប្រើ lease ក្នុង SQLite ដើម្បីកុំឱ្យ process ពីរដែលប្រើ database ដូចគ្នាចាប់ផ្តើមកាលវិភាគស្របពេលគ្នា; delivery key តែមួយក្នុងមួយអ្នកប្រើ/ថ្ងៃ/slot រារាំងការផ្ញើស្ទួន។

### Railway storage prerequisite

Railway production service `Bot-Smos-Smea` កំពុងកំណត់ **1 replica**។ មិនឃើញមាន Volume ភ្ជាប់ក្នុង service settings ទេ។ ដូច្នេះ `bot_database.db` នៅទីតាំងលំនាំដើមស្ថិតក្នុង ephemeral filesystem ហើយអាចបាត់ពេល redeploy; scheduler នឹងមិនចាប់ផ្តើមលើ Railway រហូតដល់ SQLite ស្ថិតក្នុង mounted Volume។

មុនបើក Daily Care លើ Railway៖

1. បម្រុងទុក database បច្ចុប្បន្ន ហើយរក្សាទុកឯកសារនោះ។ កុំប្តូរទៅ path ថ្មីមុនពេលចម្លង database ចាស់ចូល Volume ដើម្បីរក្សាសមាជិក និងទិន្នន័យដែលមានស្រាប់។
2. ភ្ជាប់ Railway Volume នៅ `/app/data` ហើយកំណត់ `BOT_DB_PATH=/app/data/bot_database.db`។ ដាក់ backup database ចាស់នៅ path នេះមុនដំណើរការ version ថ្មី។
3. រក្សា service ឱ្យនៅ **1 replica** សម្រាប់ SQLite។ Lease ក្នុង SQLite អាចការពារ process ដែលចែករំលែក database file ដូចគ្នា ប៉ុន្តែវាមិនអាចសម្របសម្រួល instance ដែលមាន database file ដាច់ពីគ្នាបានទេ។ បើចង់ scale ច្រើន replica ត្រូវប្តូរទៅ database រួមដូចជា PostgreSQL ជាមុន។
4. បន្ទាប់ពី volume និង database ត្រឹមត្រូវ ទើបបើក Daily Care ដោយ `/dailycare`; សមាជិកនីមួយៗត្រូវ opt in ដោយខ្លួនឯងតាម `/notifications`។

សូមមើល [Railway Volumes](https://docs.railway.com/volumes) សម្រាប់ការកំណត់ persistent storage។

## រចនាសម្ព័ន្ធសំខាន់

- `bot.py` — Telegram handlers និង long polling entry point។
- `ai_assistant.py` — Gemini requests, persona/system instruction, chat context, throttling និង daily usage limit។
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
- Render ត្រូវបង្កើត Background Worker ពី `render.yaml` ដោយប្រើ branch `main`; បញ្ចូល `BOT_TOKEN` ជា secret និង `GEMINI_API_KEY` ប្រសិនបើចង់ប្រើ AI។ Blueprint ភ្ជាប់ persistent disk នៅ `/app/data` សម្រាប់ SQLite។ កុំបង្កើន worker instances ខណៈកំពុងប្រើ SQLite។
- `railway.json` នៅតែគាំទ្រ deployment ចាស់លើ Railway។ សម្រាប់ Render deploy ពី GitHub ហើយពិនិត្យ build/deploy logs ក្នុង Render Dashboard; repository នេះមិនមាន Render account access ដើម្បីពិនិត្យ runtime logs ឬ environment values ជំនួសអ្នកទេ។
