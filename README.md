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

ឈ្មោះ `AI_PROVIDER`, `OPENAI_API_KEY`, `ADMIN_IDS`, និង `DRY_RUN_MODE` ក៏ត្រូវបានអានក្នុង `config.py` ប៉ុន្តែមិនត្រូវបានប្រើដោយ code path បច្ចុប្បន្នទេ។ ការកំណត់ `AI_PROVIDER` មិនប្តូរ Gemini ទៅ provider ផ្សេងឡើយ។

## រចនាសម្ព័ន្ធសំខាន់

- `bot.py` — Telegram handlers និង long polling entry point។
- `ai_assistant.py` — Gemini requests, persona/system instruction, chat context, throttling និង daily usage limit។
- `persona/cooler_bot_2050.txt` — persona ជាភាសាខ្មែរដែលអាចកែបាន។
- `config.py` — environment settings និង `.env` loading។
- `database.py` — SQLite storage សម្រាប់សមាជិក ប្រវត្តិសន្ទនា និង usage។
- `photo_studio.py` — photo transformation flow។
- `test_local.py` — offline unit tests សម្រាប់មុខងារសំខាន់ៗ។

ដំណើរការតេស្តដោយ `python -m unittest test_local.py`។
