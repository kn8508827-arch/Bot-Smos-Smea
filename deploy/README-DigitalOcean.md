# ដំឡើង Bot លើ DigitalOcean (Droplet)

ការដំឡើងនេះប្រើ code ដដែលពី GitHub។ បើប្រើ **BOT_TOKEN ថ្មី** (ឧ. @CoolerAI_Bot) នោះ bot ថ្មីអាចរត់ព្រមគ្នាជាមួយ bot ចាស់នៅ Railway បាន ដោយមិនប៉ះគ្នាទេ។

## 1. បង្កើត Droplet
DigitalOcean → **Create → Droplets**
- Region: **Singapore**
- Image: **Ubuntu 24.04 (LTS)**
- Size: **Basic → Regular → $6/mo** (1 GB RAM)
- Authentication: **Password** (ដាក់ password ខ្លាំងៗ ហើយកត់ទុក)
- បើក **Backups** (ស្រេចចិត្ត ប៉ុន្តែណែនាំឲ្យបើក)
- ចុច **Create Droplet**

## 2. បើក Console
ចុចលើ Droplet → **Access** → **Launch Droplet Console** (បើកក្នុង browser មិនចាំបាច់ដំឡើងកម្មវិធីអ្វីទេ)

## 3. ដំឡើង (copy បន្ទាត់នេះទាំងមូល ហើយចុច Enter)
```bash
curl -fsSL https://raw.githubusercontent.com/kn8508827-arch/Bot-Smos-Smea/main/deploy/install.sh | sudo bash
```
Script នឹងសួររក៖
- **BOT_TOKEN**: ពី @BotFather (អក្សរមិនបង្ហាញពេលវាយ ជារឿងធម្មតា)
- **GEMINI_API_KEY**: ពី aistudio.google.com
- **OWNER_ID**: លេខ Telegram ID របស់អ្នក (ដូចដែលមាននៅ Railway Variables)

ពេលឃើញ **✔ bot is running 🎉** ទៅ Telegram ហើយផ្ញើ `/start` ទៅ bot។

## ពាក្យបញ្ជាប្រើប្រចាំថ្ងៃ (វាយក្នុង Console)
| ពាក្យបញ្ជា | ធ្វើអ្វី |
|---|---|
| `coolerbot-update` | ទាញ code ថ្មីពី GitHub ហើយ restart (ប្រើក្រោយពី merge PR) |
| `coolerbot-logs` | មើល log ផ្ទាល់ (Ctrl+C ដើម្បីចេញ) |
| `coolerbot-backup` | backup database ឥឡូវនេះ (វាក៏ backup ដោយស្វ័យប្រវត្តិរាល់ថ្ងៃម៉ោង 03:30 ហើយរក្សាទុក 14 ច្បាប់ចុងក្រោយ) |
| `sudo nano /etc/coolerbot.env` | កែការកំណត់ (Ctrl+O រក្សាទុក, Ctrl+X ចេញ) បន្ទាប់មក `sudo systemctl restart coolerbot` |

## កន្លែងដែលទិន្នន័យស្ថិតនៅ
- Code: `/opt/coolerbot`
- Database: `/var/lib/coolerbot/bot_database.db`
- Backups: `/var/lib/coolerbot/backups/`
- ការកំណត់ (token/key): `/etc/coolerbot.env` (មានតែ root និង bot ទេដែលអានបាន)

Bot នឹង restart ដោយខ្លួនឯង ពេលគាំង ឬពេល server reboot។
