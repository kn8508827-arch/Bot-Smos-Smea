# 🤖 Telegram Bot សម្រាប់ «Team ស្មោះស្នេហ៍» 🇰🇭

គម្រោង Telegram Bot ផ្លូវការសម្រាប់ក្រុម **«Team ស្មោះស្នេហ៍»** បង្កើតឡើងដោយប្រើប្រាស់ភាសា **Python**, បណ្ណាល័យ **pyTelegramBotAPI**, ប្រព័ន្ធផ្ទុកទិន្នន័យជាប់លាប់ **SQLite**, និងបំពាក់ដោយ **ជំនួយការ AI សន្ទនាឆ្លាតវៃ (Google Gemini)**។

---

## 📁 រចនាសម្ព័ន្ធឯកសារ (Project Architecture)

```text
TeamSmaohSnehaBot/
├── .env.example        # គំរូកំណត់រចនាសម្ព័ន្ធ (Template - គ្មាន Token/Key ពិត)
├── .env                # ឯកសារសម្ងាត់ពិតប្រាកដ (ត្រូវបានការពារដោយ .gitignore)
├── .gitignore          # ការពារឯកសារសម្ងាត់ (.env), Database (.db), និង Logs
├── requirements.txt    # បញ្ជីបណ្ណាល័យ Python (pyTelegramBotAPI, httpx, Pillow, etc.)
├── config.py           # ទទួលបន្ទុកអាន Secret Variables និង AI Settings
├── database.py         # គ្រប់គ្រង SQLite (User, Chat History, Daily Usage Limits)
├── ai_assistant.py     # ម៉ូឌុល AI (Gemini 2.0 Flash / Fallback 1.5 Flash, Rate Limiting)
├── messages.py         # អត្ថបទសារឆ្លើយតបជាភាសាខ្មែរទាំងអស់
├── bot.py              # ចំណុចចាប់ផ្តើមចម្បងសម្រាប់ដំណើរការ Bot (Main Entry Point)
└── README.md           # សៀវភៅណែនាំគម្រោងនេះ
