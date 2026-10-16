import photo_studio
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    bot.send_chat_action(message.chat.id, 'typing')
    
    # ទាញយករូបភាពទំហំធំបំផុត
    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    
    os.makedirs("temp_photos", exist_ok=True)
    temp_path = f"temp_photos/{message.from_user.id}.jpg"
    with open(temp_path, 'wb') as f:
        f.write(downloaded_file)
        
    # បង្ហាញប៊ូតុងជ្រើសរើសម៉ូដស្ទីល
    markup = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton("👔 Luxury Portrait", callback_data="style_luxury"),
        InlineKeyboardButton("🌟 K-Drama Star", callback_data="style_kdrama"),
        InlineKeyboardButton("👑 Royal Khmer", callback_data="style_royal_khmer"),
        InlineKeyboardButton("⚔️ Khmer Warrior", callback_data="style_khmer_warrior"),
        InlineKeyboardButton("🌆 Cyberpunk", callback_data="style_cyberpunk"),
        InlineKeyboardButton("🎨 Anime Style", callback_data="style_anime")
    ]
    markup.add(*buttons)
    bot.reply_to(message, "📸 រូបថតត្រូវបានទទួលជោគជ័យ! សូមជ្រើសរើសម៉ូដស្ទីលដែលអ្នកចង់កែច្នៃ៖", reply_markup=markup)
