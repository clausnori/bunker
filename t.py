import telebot
import random
import time
import traceback
import threading
import json
import os
from config import TOKEN
import requests
from telebot import types
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from card_table import CardTable

# Bot Configuration
bot = telebot.TeleBot(TOKEN)

Table = CardTable(
        card_path_dir="cards",               # Папка с картинками карт
        background_path="cards/fon.png",      # Картинка фона
        card_size=(300, 350),
        spacing=10,
        margin= 100
    )


# Путь к фоновому изображению (RIP шаблон без животного)
BACKGROUND_PATH = 'test.png'  # Используй отредактированный шаблон без животного

# Получение аватарки пользователя
def get_user_avatar(user_id):
    photos = bot.get_user_profile_photos(user_id)
    if photos.total_count > 0:
        file_id = photos.photos[0][0].file_id
        file_info = bot.get_file(file_id)
        file = requests.get(f'https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}')
        return Image.open(BytesIO(file.content))
    return None

# Обработка команды /dead
def send_rip_image(chat_id,user_id):
    user_avatar = get_user_avatar(user_id)
    if user_avatar is None:
        bot.send_message(chat_id, "Не удалось получить аватарку.")
        return

    # Загружаем фон и аватарку
    background = Image.open(BACKGROUND_PATH).convert("RGBA")
    avatar = user_avatar.resize((120, 120)).convert("RGBA")

    # Координаты вставки аватарки (примерно по центру памятника)
    position = (250, 70)  # Настроено под файл tombstone.png

    # Вставка аватарки
    background.paste(avatar, position, avatar)

    # Отправка результата
    output = BytesIO()
    output.name = 'rip_result.png'
    background.save(output, 'PNG')
    output.seek(0)
    bot.send_photo(chat_id, output)

# Constants
REGISTRATION_TIME = 30  # Registration time in seconds
DECK = ['A'] * 6 + ['K'] * 6 + ['Q'] * 6 + ['J'] * 2  # 6 Aces, 6 Kings, 6 Queens, 2 Jokers
CARD_NAMES = {
    'A': 'Ace', 
    'K': 'King', 
    'Q': 'Qwen', 
    'J': 'Joker🃏'
}
SAVE_FILE = 'liars_deck_state.json'

# Game state
class GameState:
    def __init__(self):
        self.games = {}  # Active games by chat_id
        self.waiting_registration = {}  # Registration tracking
        self.registration_messages = {}  # Registration messages for updates
        self.registration_timers = {}  # Timers for registration countdown
        
        # Load saved state if exists
        self.load_state()
    
    def save_state(self):
        """Save current game state to file"""
        try:
            with open(SAVE_FILE, 'w', encoding='utf-8') as f:
                state = {
                    'games': {
                        chat_id: {
                            'players': game['players'],
                            'current_player': game['current_player'],
                            'table_cards': game['table_cards'],
                            'current_card': game['current_card'],
                            'status': game['status'],
                            'last_claim': game['last_claim'],
                            'roulette_bullets': game['roulette_bullets']
                        } for chat_id, game in self.games.items()
                    }
                }
                json.dump(state, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Error saving state: {e}")

    def load_state(self):
        """Load game state from file"""
        if os.path.exists(SAVE_FILE):
            try:
                with open(SAVE_FILE, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                    self.games = state.get('games', {})
            except Exception as e:
                print(f"Error loading state: {e}")
    
    def init_new_game(self, chat_id):
        """Initialize a new game"""
        self.games[chat_id] = {
            'players': {},
            'current_player': None,
            'table_cards': [],
            'current_card': None,
            'status': 'registration',
            'last_claim': {'player_id': None, 'card': None, 'count': 0},
            'roulette_bullets': {}
        }
        
        # Set up registration expiry
        self.waiting_registration[chat_id] = time.time() + REGISTRATION_TIME
        
        # Cancel previous timer if exists
        if chat_id in self.registration_timers:
            self.registration_timers[chat_id].cancel()
        
        # Start new timer
        self.registration_timers[chat_id] = threading.Timer(
            REGISTRATION_TIME, 
            end_registration, 
            args=[chat_id, self]
        )
        self.registration_timers[chat_id].start()
        
        return self.games[chat_id]
    
    def get_game(self, chat_id):
        """Get game data for a chat"""
        return self.games.get(chat_id)
    
    def is_player_in_any_game(self, user_id):
        """Check if a player is in any active game"""
        for chat_id, game_data in self.games.items():
            if user_id in game_data['players']:
                return chat_id, game_data
        return None, None
    
    def get_registered_players_text(self, chat_id):
        """Get formatted list of registered players"""
        game = self.games.get(chat_id)
        if not game:
            return ""
            
        return "\n".join([f"• {player_info['name']}" for 
                       player_id, player_info in game['players'].items()])
    
    def extend_registration(self, chat_id):
        """Extend registration time"""
        if chat_id not in self.waiting_registration:
            return False
            
        self.waiting_registration[chat_id] = time.time() + REGISTRATION_TIME
        
        # Reset timer
        if chat_id in self.registration_timers:
            self.registration_timers[chat_id].cancel()
        
        self.registration_timers[chat_id] = threading.Timer(
            REGISTRATION_TIME, 
            end_registration, 
            args=[chat_id, self]
        )
        self.registration_timers[chat_id].start()
        
        return True
    
    def register_player(self, chat_id, user_id, user_name):
        """Register a player to a game"""
        if chat_id not in self.games or self.games[chat_id]['status'] != 'registration':
            return False, "Сейчас нет активной регистрации в игру."
        
        # Check if already registered
        if user_id in self.games[chat_id]['players']:
            return False, f"@{user_name}, вы уже зарегистрированы в игре."
        
        # Register player
        self.games[chat_id]['players'][user_id] = {
            'name': user_name,
            'cards': [],
            'bullets': 0
        }
        
        return True, f"@{user_name}, вы зарегистрированы."

# Initialize game state
game_state = GameState()
# Helper functions
def get_next_player(chat_id, current_player_id):
    """Get next player in turn order"""
    game_data = game_state.games[chat_id]
    player_ids = list(game_data['players'].keys())
    
    if len(player_ids) == 1:
        return current_player_id
    
    current_index = player_ids.index(current_player_id)
    next_index = (current_index + 1) % len(player_ids)
    return player_ids[next_index]

def end_registration(chat_id, state):
    """End registration phase and start game if enough players"""
    # Check if registration still exists
    if chat_id not in state.waiting_registration:
        return
        
    try:
        # Clean up registration data
        del state.waiting_registration[chat_id]
        if chat_id in state.registration_messages:
            del state.registration_messages[chat_id]
        
        # Start game if enough players
        if chat_id in state.games and len(state.games[chat_id]['players']) >= 2:
            start_game(chat_id)
        else:
            bot.send_message(chat_id, "Недостаточно игроков для начала игры (минимум 2). Игра отменена.")
            if chat_id in state.games:
                del state.games[chat_id]
    except Exception as e:
        print(f"Error ending registration: {e}")
        bot.send_message(chat_id, "Произошла ошибка при запуске игры. Попробуйте снова.")
        if chat_id in state.games:
            del state.games[chat_id]

def start_game(chat_id):
    """Start the game after registration is complete"""
    game_data = game_state.games[chat_id]
    
    # Update game status
    game_data['status'] = 'playing'
    
    # Shuffle and deal cards
    deck = DECK.copy()
    random.shuffle(deck)
    
    player_ids = list(game_data['players'].keys())
    for player_id in player_ids:
        game_data['players'][player_id]['cards'] = deck[:5]
        deck = deck[5:]
        
        # Send private message with cards
        try:
            player_cards = game_data['players'][player_id]['cards']
            card_names = [CARD_NAMES[card] for card in player_cards]
            bot.send_message(player_id, 
                          f"Ваши карты: {', '.join(card_names)} ({', '.join(player_cards)})")
        except Exception as e:
            bot.send_message(chat_id, f"Не удалось отправить карты игроку @{game_data['players'][player_id]['name']}.")
    
    # Select random starting player and card
    game_data['current_player'] = random.choice(player_ids)
    game_data['current_card'] = random.choice(['A', 'K', 'Q'])
    
    # Send game start message
    try:
        with open('main.png', 'rb') as photo:
            bot.send_photo(chat_id, photo,
                        f"Игра 'Бар Лжецов' начинается!\n"
                        f"Первым ходит: @{game_data['players'][game_data['current_player']]['name']}")
    except Exception:
        # Send text-only message if photo not available
        bot.send_message(chat_id,
                       f"Игра 'Бар Лжецов' начинается!\n"
                       f"Первым ходит: @{game_data['players'][game_data['current_player']]['name']}")
    
    bot.send_message(chat_id, f"Объявленная карта: {CARD_NAMES[game_data['current_card']]}")
    
    # Initialize empty table and send first turn message
    game_data['table_cards'] = []
    send_turn_message(chat_id)
    
    # Save game state
    game_state.save_state()
    
def send_turn_message(chat_id):
    """Send message to current player for their turn"""
    game_data = game_state.games[chat_id]
    current_player_id = game_data['current_player']
    player_name = game_data['players'][current_player_id]['name']
    
    # Проверяем, есть ли у игрока карты, и если нет - добавляем
    if len(game_data['players'][current_player_id]['cards']) == 0:
        deal_cards_to_player(chat_id, current_player_id)
    
    # Создание клавиатуры с вариантами кол-ва карт
    markup = types.InlineKeyboardMarkup(row_width=3)
    cards_on_hand = len(game_data['players'][current_player_id]['cards'])
    
    # Если у игрока нет карт, прекращаем ход и переходим к следующему игроку
    if cards_on_hand == 0:
        bot.send_message(chat_id, f"@{player_name} не имеет карт. Переход к следующему игроку.")
        game_data['current_player'] = get_next_player(chat_id, current_player_id)
        send_turn_message(chat_id)
        return
    
    # Иначе продолжаем нормальный ход
    for i in range(1, min(cards_on_hand + 1, 4)):
        markup.add(types.InlineKeyboardButton(text=f"{i}", callback_data=f"place_{i}"))

    # Отправляем сообщение игроку
    player_cards = game_data['players'][current_player_id]['cards']
    card_names = " ".join(player_cards)
    text = (f"Ваш ход!\nСтол: {CARD_NAMES[game_data['current_card']]} ({game_data['current_card']})\n"
           f"Карты: {card_names}\nСколько карт положите?")
    try:
        bot.send_message(current_player_id, text, reply_markup=markup)
    except Exception:
        bot.send_message(chat_id, f"@{player_name}, открой ЛС с ботом.")

    # Отправляем сообщение в группу
    bot.send_message(chat_id, f"Ход: @{player_name} | Стол: {CARD_NAMES[game_data['current_card']]}")
    
    # Обновляем время последней активности
    game_data['last_activity'] = time.time()

def deal_cards_to_player(chat_id, player_id):
    """Deal new cards to a player who has none"""
    game_data = game_state.games[chat_id]
    player_name = game_data['players'][player_id]['name']
    
    # Определяем, сколько карт нужно раздать (стандартно 5)
    cards_to_deal = 5
    
    # Создаем новую мини-колоду, как в начале игры
    mini_deck = DECK.copy()
    random.shuffle(mini_deck)
    
    # Берем нужное количество карт
    new_cards = mini_deck[:cards_to_deal]
    
    # Добавляем карты игроку
    game_data['players'][player_id]['cards'] = new_cards
    
    # Отправляем сообщение игроку о новых картах
    try:
        card_names = [CARD_NAMES[card] for card in new_cards]
        bot.send_message(player_id, 
                      f"Вы получили новые карты: {', '.join(card_names)} ({', '.join(new_cards)})")
        bot.send_message(chat_id, f"@{player_name} получил новые карты.")
    except Exception as e:
        bot.send_message(chat_id, f"Не удалось отправить информацию о новых картах игроку @{player_name}.")
    
    game_state.save_state()

def check_claim(chat_id, checker_id):
    """Check the validity of the last claim"""
    game_data = game_state.games[chat_id]
    last_claim = game_data['last_claim']
    claimed = last_claim['player_id']
    
    # Проверяем наличие карт на столе
    if not game_data['table_cards']:
        bot.send_message(chat_id, "Ошибка: на столе нет карт для проверки.")
        return
        
    # Проверяем количество карт
    if len(game_data['table_cards']) < last_claim['count']:
        bot.send_message(chat_id, 
                      f"Ошибка: на столе {len(game_data['table_cards'])} карт, "
                      f"а заявлено {last_claim['count']}. Перезапустите игру.")
        return
        
    # Получаем последние выложенные карты
    laid = game_data['table_cards'][-last_claim['count']:]
    
    # Проверяем, соответствуют ли карты заявке (Джокеры считаются любой картой)
    print(laid)
    match = all(c == last_claim['card'] or c == 'J' for c in laid)

    # Определяем победителя и проигравшего
    loser_id = checker_id if match else claimed
    winner_id = claimed if match else checker_id
    
    # Проверяем, что оба игрока все еще существуют
    if loser_id not in game_data['players'] or winner_id not in game_data['players']:
        bot.send_message(chat_id, "Ошибка: один из игроков больше не участвует в игре.")
        return
        
    # Отправляем результат проверки
    status_msg = f"Это правда, действительно " if match else "Обман!!!"
    card_str = [CARD_NAMES[card] for card in laid]
    text= f"Хмм: {status_msg}\n на столе: {card_str}"
    test_cart(chat_id,text,laid)
    

    # Добавляем пулю проигравшему
    bullets = game_data['roulette_bullets'].get(loser_id, 0) + 1
    game_data['roulette_bullets'][loser_id] = bullets
    bot.send_message(chat_id, f"@{game_data['players'][loser_id]['name']} в рулетке — {bullets}/6")

    # Русская рулетка
    bot.send_message(chat_id, "Щелк.....")
    time.sleep(3)
    with open('rulet.gif', 'rb') as photo:
      bot.send_animation(chat_id, photo)
    time.sleep(4)
    
    if random.randint(1, 6) <= bullets:
        # Игрок выбывает
        bot.send_message(chat_id, f"💥Бах! @{game_data['players'][loser_id]['name']} дырявит свою голову.")
        send_rip_image(chat_id,loser_id)
        del game_data['players'][loser_id]
        
        # Проверяем, остался ли только один игрок (победитель)
        if len(game_data['players']) == 1:
            last_player_id = list(game_data['players'].keys())[0]
            bot.send_message(chat_id, f"🎉🎉🎉Игра окончена!\n Победитель: @{game_data['players'][last_player_id]['name']}")
            game_data['status'] = 'finished'
            game_state.save_state()
            return
        elif len(game_data['players']) == 0:
            bot.send_message(chat_id, "Игра окончена! Не осталось игроков.")
            game_data['status'] = 'finished'
            game_state.save_state()
            return
    else:
        bot.send_message(chat_id, f"На удивление жив 🤔!\n @{game_data['players'][loser_id]['name']} продолжает игру.")

    # Настройка нового раунда
    game_data['table_cards'] = []
    game_data['current_card'] = random.choice(['A', 'K', 'Q'])
    
    # Устанавливаем победителя проверки как следующего игрока, если он все еще в игре
    if winner_id in game_data['players']:
        game_data['current_player'] = winner_id
        
        # Проверяем наличие карт у победителя
        if len(game_data['players'][winner_id]['cards']) == 0:
            deal_cards_to_player(chat_id, winner_id)
            
        send_turn_message(chat_id)
    else:
        # Если победитель выбыл, выбираем нового текущего игрока
        if game_data['players']:
            game_data['current_player'] = list(game_data['players'].keys())[0]
            send_turn_message(chat_id)
        else:
            bot.send_message(chat_id, "Игра окончена! Не осталось игроков.")
            game_data['status'] = 'finished'
    
    # Обновляем время последней активности
    game_data['last_activity'] = time.time()
    game_state.save_state()

def process_card_selection(message, user_id, chat_id):
    """Process text-based card selection from player"""
    # Verify it's the right user
    if message.from_user.id != user_id:
        bot.register_next_step_handler_by_chat_id(message.chat.id, process_card_selection, user_id, chat_id)
        return

    # Check game state
    game_data = game_state.games.get(chat_id)
    if not game_data or game_data['current_player'] != user_id or game_data['status'] != 'playing':
        bot.send_message(message.chat.id, "Не ваш ход или игра завершена.")
        return



    # Parse input cards
    text = message.text.strip().upper()
    input_cards = text.split() if ' ' in text else list(text)

    # Verify card count
    if len(input_cards) != game_data['temp_count']:
        bot.send_message(message.chat.id, f"Нужно ввести {game_data['temp_count']} карт. Попробуйте еще раз:")
        bot.register_next_step_handler_by_chat_id(message.chat.id, process_card_selection, user_id, chat_id)
        return

    # Validate card symbols
    valid_cards = ["A", "K", "Q", "J"]
    if not all(card in valid_cards for card in input_cards):
        bot.send_message(message.chat.id, "Недопустимые символы. Используйте только: A, K, Q, J. Попробуйте еще раз:")
        bot.register_next_step_handler_by_chat_id(message.chat.id, process_card_selection, user_id, chat_id)
        return

    # Check if player has these cards
    player_cards = game_data['players'][user_id]['cards']
    
    if not player_cards:
        bot.send_message(message.chat.id, "У вас нет карт!")
        return
        
    player_cards_copy = player_cards.copy()
    selected_cards = []

    for card in input_cards:
        if card in player_cards_copy:
            selected_cards.append(card)
            player_cards_copy.remove(card)
        else:
            cards_str = ' '.join(player_cards)
            bot.send_message(message.chat.id, f"У вас нет карты {card}. \n Ваши карты: {cards_str}. Попробуйте еще раз:")
            bot.register_next_step_handler_by_chat_id(message.chat.id, process_card_selection, user_id, chat_id)
            return

    # Remove cards from player, add to table
    for card in selected_cards:
        game_data['players'][user_id]['cards'].remove(card)
        game_data['table_cards'].append(card)

    # Update last claim
    game_data['last_claim'] = {
        'player_id': user_id,
        'card': game_data['current_card'],
        'count': game_data['temp_count']
    }

    # Clean up temp data
    del game_data['temp_count']
    if 'temp_selected_cards' in game_data:
        del game_data['temp_selected_cards']

    # Send messages
    player_name = game_data['players'][user_id]['name']
    text = f"@{player_name} положил {len(selected_cards)} карты {CARD_NAMES[game_data['current_card']]}"
    test_cart(chat_id,text,create_back_list(selected_cards))

    try:
        bot.send_message(user_id, f"Вы положили: {' '.join(selected_cards)}")
    except Exception as e:
        print(f"Error sending card message: {e}")

    # Set up next player action
    next_player_id = get_next_player(chat_id, user_id)
    game_data['next_player'] = next_player_id
    next_name = game_data['players'][next_player_id]['name']

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(text="Ложь", callback_data="check"),
        types.InlineKeyboardButton(text="Пропустить", callback_data="pass")
    )

    bot.send_message(chat_id, f"@{next_name}, ход за тобой! Веришь ему и пропускаешь?", reply_markup=markup)
    game_state.save_state()


def create_back_list(selected_cards):
    return ["back"] * len(selected_cards)

def get_next_player(chat_id, current_player_id):
    """Get next player in turn order"""
    game_data = game_state.games[chat_id]
    player_ids = list(game_data['players'].keys())
    
    if len(player_ids) <= 1:
        return current_player_id
    
    current_index = player_ids.index(current_player_id) if current_player_id in player_ids else 0
    
    # Перебираем игроков по кругу, начиная со следующего после текущего
    for i in range(1, len(player_ids) + 1):
        next_index = (current_index + i) % len(player_ids)
        next_player_id = player_ids[next_index]
        # В данной версии всегда возвращаем следующего игрока
        return next_player_id
    
    # Если вдруг не нашли следующего игрока (что не должно произойти), вернем текущего
    return current_player_id


def test_cart(chat_id,text,cards):
  img = Table.render_table(cards)
  output = BytesIO()
  output.name = 'table.png'
  img.save(output, 'PNG')
  output.seek(0)
  bot.send_photo(chat_id, output,text)
  

# Command handlers
@bot.message_handler(commands=['start'])
def start_command(message):
    """Handle /start command"""
    text = ("Добро пожаловать в игру 'Клуб Лжецов'!\n\n"
           "Для начала новой игры используйте команду /new_game")
    bot.send_message(message.chat.id, text)

@bot.message_handler(commands=['new_game'])
def new_game_command(message):
    """Handle /new_game command to start registration"""
    chat_id = message.chat.id

    # Check if game is already in progress
    if chat_id in game_state.games and game_state.games[chat_id]['status'] != 'finished':
        bot.send_message(chat_id, "В чате уже идет игра.")
        return

    # Initialize new game
    game_state.init_new_game(chat_id)

    # Create registration button
    markup = types.InlineKeyboardMarkup()
    register_button = types.InlineKeyboardButton(text="Зарегистрироваться", callback_data="register")
    markup.add(register_button)
    
    # Send registration message
    text = f"Игра запущена!\n\n⏳ Регистрация:{REGISTRATION_TIME}сек\nНажми «Зарегистрироваться»"
    registration_msg = bot.send_message(chat_id, text, reply_markup=markup)
    game_state.registration_messages[chat_id] = registration_msg.message_id

@bot.message_handler(commands=['wait'])
def wait_command(message):
    """Handle /wait command to extend registration time"""
    chat_id = message.chat.id
    
    if game_state.extend_registration(chat_id):
        # Update registration message
        if chat_id in game_state.registration_messages:
            players_list = game_state.get_registered_players_text(chat_id)
            markup = types.InlineKeyboardMarkup()
            register_button = types.InlineKeyboardButton(text="Зарегистрироваться", callback_data="register")
            markup.add(register_button)
            
            text = (f"Регистрация в игру 'Клуб Лжецов' продлена!\n"
                   f"У вас есть {REGISTRATION_TIME} секунд на регистрацию.\n\n"
                   f"Зарегистрированные игроки:\n{players_list}")
            
            try:
                bot.edit_message_text(
                    text,
                    chat_id,
                    game_state.registration_messages[chat_id],
                    reply_markup=markup
                )
            except Exception as e:
                print(f"Error updating message: {e}")
        
        bot.send_message(chat_id, f"Регистрация продлена на {REGISTRATION_TIME} секунд.")
    else:
        bot.send_message(chat_id, "Сейчас нет активной регистрации для продления.")

@bot.message_handler(commands=['end_game'])
def end_game_command(message):
    """Handle /end_game command to force end a game"""
    chat_id = message.chat.id
    
    if chat_id in game_state.games and game_state.games[chat_id]['status'] != 'finished':
        game_state.games[chat_id]['status'] = 'finished'
        bot.send_message(chat_id, "Игра принудительно завершена.")
        game_state.save_state()
    else:
        bot.send_message(chat_id, "В этом чате нет активной игры для завершения.")

@bot.message_handler(commands=['status'])
def status_command(message):
    """Handle /status command to show game status"""
    chat_id = message.chat.id
    
    if chat_id in game_state.games:
        game_data = game_state.games[chat_id]
        
        if game_data['status'] == 'registration':
            remaining_time = int(game_state.waiting_registration.get(chat_id, 0) - time.time())
            players_list = game_state.get_registered_players_text(chat_id)
            status_message = (f"Статус: Регистрация\n"
                             f"Осталось времени: {remaining_time if remaining_time > 0 else 0} секунд\n"
                             f"Зарегистрированные игроки:\n{players_list}")
        elif game_data['status'] == 'playing':
            players_info = []
            for player_id, player_info in game_data['players'].items():
                player_status = " (текущий ход)" if player_id == game_data['current_player'] else ""
                bullets = game_data['roulette_bullets'].get(player_id, 0)
                bullets_info = f" [{bullets} патр.]" if bullets > 0 else ""
                players_info.append(f"• {player_info['name']}{player_status}{bullets_info} - {len(player_info['cards'])} карт")
            
            players_list = "\n".join(players_info)
            
            status_message = (f"Статус: Игра идет\n"
                             f"Текущая карта: {CARD_NAMES[game_data['current_card']]}\n"
                             f"Игроки:\n{players_list}")
        else:  # finished
            status_message = "Статус: Игра завершена"
        
        bot.send_message(chat_id, status_message)
    else:
        bot.send_message(chat_id, "В этом чате нет активной игры.")

@bot.message_handler(commands=['cards'])
def cards_command(message):
    """Handle /cards command to show player's cards"""
    user_id = message.from_user.id
    
    chat_id, game_data = game_state.is_player_in_any_game(user_id)
    if chat_id and game_data and game_data['status'] == 'playing':
        player_cards = game_data['players'][user_id]['cards']
        card_names = [f"{CARD_NAMES[card]} ({card})" for card in player_cards]
        
        bot.send_message(user_id, f"Ваши карты: {', '.join(card_names)}\n"
          f"При выборе карт для хода используйте короткие обозначения: A, K, Q, J")
    else:
        bot.send_message(user_id, "Вы не участвуете ни в одной активной игре или игра еще не началась.")

# Callback handlers
@bot.callback_query_handler(func=lambda call: call.data == "register")
def register_button_callback(call):
    """Handle player registration button press"""
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    
    # Get username or fallback to first name
    if call.from_user.username:
        user_name = call.from_user.username
    else:
        user_name = call.from_user.first_name or "Player"
    
    success, message = game_state.register_player(chat_id, user_id, user_name)
    
    # Update registration message
    if success and chat_id in game_state.registration_messages:
        players_list = game_state.get_registered_players_text(chat_id)
        markup = types.InlineKeyboardMarkup()
        register_button = types.InlineKeyboardButton(text="Зарегистрироваться", callback_data="register")
        markup.add(register_button)
        
        text = (f"⏳ Регистрация:{REGISTRATION_TIME}сек\nНажми «Зарегистрироваться» \n"
               f"Зарегистрированные игроки:\n{players_list}")
        
        try:
            bot.edit_message_text(
                text,
                chat_id,
                game_state.registration_messages[chat_id],
                reply_markup=markup
            )
        except Exception as e:
            print(f"Error updating registration message: {e}")
        
        # Send private message to player
        try:
            bot.send_message(user_id, f"Вы успешно зарегистрировались в игру 'Клуб Лжецов'")
        except Exception:
            # Player might not have started chat with bot
            bot.send_message(chat_id, f"@{user_name}, вы зарегистрированы. Для получения карт начните личный чат с ботом.")
    
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("place_"))
def place_cards_callback(call):
    """Handle player choosing how many cards to place"""
    user_id = call.from_user.id
    count = int(call.data.split('_')[1])
    
    # Find the game where this player is current
    for chat_id, game_data in game_state.games.items():
        if game_data['current_player'] == user_id and game_data['status'] == 'playing':
            # Save count for future use
            game_data['temp_count'] = count
            game_data['temp_selected_cards'] = []
            
            # Get name of current card
            current_card_name = CARD_NAMES[game_data['current_card']]
            
            # Ask player to input cards as text
            text = (f"Вы решили положить {count} карт.\n"
                   f"Текущая заявленная карта: {current_card_name}\n"
                   f"Например: 'A K Q' или 'QJ' (для выбора Дамы и Джокера)")
            
            bot.edit_message_text(
                text,
                call.message.chat.id,
                call.message.message_id
            )
            
            # Register next step for text input
            bot.register_next_step_handler_by_chat_id(call.message.chat.id, process_card_selection, user_id, chat_id)
            
            bot.answer_callback_query(call.id)
            return
    
    bot.answer_callback_query(call.id, "Сейчас не ваш ход или игра завершена.")
@bot.callback_query_handler(func=lambda call: call.data == "check")
def check_callback(call):
    """Handle player checking the previous player's claim"""
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    
    # Check if game exists and is active
    if chat_id not in game_state.games or game_state.games[chat_id]['status'] != 'playing':
        bot.answer_callback_query(call.id, "Игра не активна.")
        return
        
    game_data = game_state.games[chat_id]
    
    # Check if it's this player's turn to check
    if game_data.get('next_player') != user_id:
        bot.answer_callback_query(call.id, "Сейчас не ваш ход.")
        return
    
    # Check if there's a claim to check
    if not game_data['last_claim']['player_id']:
        bot.answer_callback_query(call.id, "Нет заявки для проверки.")
        return
        
    # Remove the inline keyboard
    try:
        bot.edit_message_reply_markup(
            chat_id,
            call.message.message_id,
            reply_markup=None
        )
    except Exception as e:
        print(f"Error removing keyboard: {e}")
    
    # Perform check
    bot.answer_callback_query(call.id)
    check_claim(chat_id, user_id)

@bot.callback_query_handler(func=lambda call: call.data == "pass")
def pass_callback(call):
    """Handle player passing their turn to check"""
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    
    # Проверяем существование и активность игры
    if chat_id not in game_state.games or game_state.games[chat_id]['status'] != 'playing':
        bot.answer_callback_query(call.id, "Игра не активна.")
        return
        
    game_data = game_state.games[chat_id]
    
    # Проверяем, чей сейчас ход проверки/пропуска
    if game_data.get('next_player') != user_id:
        bot.answer_callback_query(call.id, "Сейчас не ваш ход.")
        return
    
    # Удаляем инлайн-клавиатуру
    try:
        bot.edit_message_reply_markup(
            chat_id,
            call.message.message_id,
            reply_markup=None
        )
    except Exception as e:
        print(f"Error removing keyboard: {e}")
    
    # Получаем последние выложенные карты
    last_claim = game_data['last_claim']
    if last_claim['count'] > 0 and game_data['table_cards']:
        # Получаем последние выложенные карты
        laid_cards = game_data['table_cards'][-last_claim['count']:]
        laid_cards_str = [CARD_NAMES[card] for card in laid_cards]
        # Проверяем правдивость последней заявки
        was_honest = all(c == last_claim['card'] or c == 'J' for c in laid_cards)
        honesty_result = "он не врет" if was_honest else "он обманул вас"
        
        # Показываем карты и результат проверки
        bot.send_message(chat_id, f"Кинул: {laid_cards_str} - {honesty_result}!")
    
    # Обновляем состояние игры для следующего игрока
    game_data['current_player'] = user_id
    
    # Отправляем уведомление о пропуске
    player_name = game_data['players'][user_id]['name']
    bot.send_message(chat_id, f"@{player_name} пропускает проверку.")
    
    bot.answer_callback_query(call.id)
    
    # Проверяем наличие карт у текущего игрока
    if len(game_data['players'][user_id]['cards']) == 0:
        deal_cards_to_player(chat_id, user_id)
    
    # Начинаем следующий ход
    send_turn_message(chat_id)
    
    # Обновляем время последней активности
    game_data['last_activity'] = time.time()
    game_state.save_state()

# Error handling wrapper
def error_handler(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            # Log the error
            error_text = f"Error in {func.__name__}: {e}\n{traceback.format_exc()}"
            print(error_text)
            
            # Try to notify chat if possible
            try:
                if args and hasattr(args[0], 'chat') and hasattr(args[0].chat, 'id'):
                    chat_id = args[0].chat.id
                    bot.send_message(chat_id, "Произошла ошибка. Пожалуйста, попробуйте еще раз или перезапустите игру командой /new_game.")
            except Exception:
                pass  # If we can't notify, just continue
            
            return None
    return wrapper

# Apply error handling to message handlers
bot.message_handler = error_handler(bot.message_handler)

# Help command
@bot.message_handler(commands=['help'])
def help_command(message):
    """Show help information"""
    help_text = (
        "🃏 Клуб Лжецов - игра об обмане и наблюдательности\n\n"
        "Команды:\n"
        "/new_game - Начать новую игру\n"
        "/wait - Продлить время регистрации\n"
        "/status - Показать статус текущей игры\n"
        "/cards - Посмотреть свои карты (в ЛС)\n"
        "/end_game - Принудительно завершить игру\n"
        "/help - Показать эту справку\n\n"
        "Правила игры:\n"
        "1. Каждый игрок получает 5 карт.\n"
        "2. На каждом ходу объявляется целевая карта (Туз, Король или Дама).\n"
        "3. Игроки по очереди кладут от 1 до 3 карт, утверждая, что они соответствуют объявленной.\n"
        "4. Следующий игрок может либо проверить утверждение, либо пропустить ход.\n"
        "5. При проверке:\n"
        "   - Если игрок солгал, он получает пулю в русской рулетке.\n"
        "   - Если игрок говорил правду, проверяющий получает пулю.\n"
        "6. Джокер считается любой картой.\n"
        "7. После 6 пуль игрок выбывает.\n"
        "8. Последний оставшийся игрок побеждает."
    )
    bot.send_message(message.chat.id, help_text)

@bot.message_handler(commands=['rules'])
def rules_command(message):
    """Show detailed rules"""
    rules_text = (
        "🎲 *Правила игры 'Клуб Лжецов'*\n\n"
        "*Состав колоды:*\n"
        "• 6 Тузов (A)\n"
        "• 6 Королей (K)\n"
        "• 6 Дам (Q)\n"
        "• 2 Джокера (J)\n\n"
        "*Цель игры:* Остаться последним выжившим игроком.\n\n"
        "*Ход игры:*\n"
        "1. Каждому игроку раздается по 5 карт.\n"
        "2. Случайно выбирается первый игрок и целевая карта (Туз, Король или Дама).\n"
        "3. На каждом ходу игрок должен положить на стол от 1 до 3 карт, заявляя, что они соответствуют текущей целевой карте.\n"
        "4. Следующий по очереди игрок может:\n"
        "   • *Проверить* заявку предыдущего игрока\n"
        "   • *Пропустить* проверку и сделать свой ход\n\n"
        "*Проверка:*\n"
        "• Если все карты соответствуют заявленным (Джокер считается любой картой), проверяющий игрок проигрывает.\n"
        "• Если хотя бы одна карта не соответствует заявке, проигрывает игрок, положивший карты.\n\n"
        "*Русская рулетка:*\n"
        "• Проигравший получает одну пулю в барабан револьвера.\n"
        "• Когда у игрока накапливается 6 пуль, он выбывает из игры.\n\n"
        "*Новый раунд:*\n"
        "• После проверки начинается новый раунд с новой целевой картой.\n"
        "• Победивший в проверке игрок ходит первым.\n\n"
        "*Победа:* Последний оставшийся игрок объявляется победителем."
    )
    bot.send_message(message.chat.id, rules_text, parse_mode='Markdown')

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    """Handle any other text messages"""
    # Check if this might be a card selection response
    if message.chat.type == 'private':
        user_id = message.from_user.id
        chat_id, game_data = game_state.is_player_in_any_game(user_id)
        
        if (chat_id and game_data and 
            game_data['status'] == 'playing' and 
            game_data['current_player'] == user_id and
            'temp_count' in game_data):
            # This might be a card selection, process it
            process_card_selection(message, user_id, chat_id)
            return
    
    # For group chats, don't respond to random messages
    if message.chat.type != 'private':
        return
        
    # For private messages not related to game, show help
    bot.send_message(message.chat.id, 
                   "Используйте /help для получения списка команд.\n"
                   "Для начала игры используйте команду /new_game в групповом чате.")

def check_for_inactive_games():
    """Periodic task to clean up inactive games"""
    current_time = time.time()
    inactive_chats = []
    
    # Check for games in registration that passed their time
    for chat_id, registration_time in list(game_state.waiting_registration.items()):
        if current_time > registration_time:
            end_registration(chat_id, game_state)
    
    # Check for inactive games (no activity for 12 hours)
    for chat_id, game_data in game_state.games.items():
        if game_data['status'] == 'playing' and 'last_activity' in game_data:
            if current_time - game_data.get('last_activity', 0) > 12 * 3600:  # 12 hours
                inactive_chats.append(chat_id)
    
    # Clean up inactive games
    for chat_id in inactive_chats:
        try:
            bot.send_message(chat_id, "Игра автоматически завершена из-за отсутствия активности.")
            game_state.games[chat_id]['status'] = 'finished'
        except Exception as e:
            print(f"Error sending message to inactive chat {chat_id}: {e}")
    
    # Save state after cleanup
    if inactive_chats:
        game_state.save_state()

# Start the bot with polling
if __name__ == "__main__":
    print("Bot is starting...")
    
    # Set up a timer to check for inactive games every hour
    cleanup_thread = threading.Timer(3600, check_for_inactive_games)
    cleanup_thread.daemon = True  # So it doesn't block program exit
    cleanup_thread.start()
    
    try:
        bot.polling(none_stop=True, interval=0, timeout=20)
    except Exception as e:
        print(f"Bot polling error: {e}")
        traceback.print_exc()
    finally:
        # Save game state when exiting
        game_state.save_state()
        print("Bot stopped.")