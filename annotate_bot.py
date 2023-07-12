import sqlite3
import random
from pathlib import Path
from PIL import Image
import glob
import tensorflow as tf
import tensorflow_datasets as tfds
from telegram import ReplyKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, ConversationHandler


# Define your bot token here
TOKEN = 'YOUR TELEGRAM TOKEN'

# Define conversation states
NAME, GUESS, EDIT = range(3)

# Connect to the SQLite database
conn = sqlite3.connect('users.db')
cursor = conn.cursor()

# Create the users table if it doesn't exist
cursor.execute('''CREATE TABLE IF NOT EXISTS users
                  (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, name TEXT)''')
# Create the predictions table if it doesn't exist
cursor.execute('''CREATE TABLE IF NOT EXISTS predictions
                  (id INTEGER PRIMARY KEY AUTOINCREMENT,
                   user_id INTEGER,
                   predicted_label TEXT,
                   photo_index INTEGER,
                   reference TEXT,
                   FOREIGN KEY (user_id) REFERENCES users(id))''')
# Create the last-shown table if it doesn't exist
cursor.execute('''CREATE TABLE IF NOT EXISTS last_shown
                  (user_id INTEGER PRIMARY KEY,
                   last_photo_shown INTEGER)''')
conn.commit()

# Define the function to handle the /start command
def start(update, context):
    chat_id = update.effective_chat.id
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM users WHERE chat_id = ?', (chat_id,))
    result = cursor.fetchone()

    if result is None:
        # If the user is not registered, prompt them to enter their name
        context.bot.send_message(chat_id=chat_id, text="Welcome! Please enter your name:")
        return NAME
    else:
        # If the user is already registered, retrieve their ID
        user_id = result[0]
        context.bot.send_message(chat_id=chat_id, text=f'You are already registered with ID: {user_id}')

    # Create a custom keyboard with the desired commands
    keyboard = [['/start', '/annotate', '/edit']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

    # Send the custom keyboard to the user when they send a message
    update.message.reply_text('Please select a command:', reply_markup=reply_markup)
    return ConversationHandler.END

# Define the function to handle user registration
def register_user(update, context):
    chat_id = update.effective_chat.id
    name = update.message.text

    # Store the user in the database
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO users (chat_id, name) VALUES (?, ?)", (chat_id, name))
    conn.commit()

    # Retrieve the generated user ID
    user_id = cursor.lastrowid

    # Send a welcome message with the assigned ID
    context.bot.send_message(chat_id=chat_id, text=f'Thank you for registering, {name}! Your ID is: {user_id}')

    # Create a custom keyboard with the desired commands
    keyboard = [['/start', '/annotate', '/edit']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

    # Send the custom keyboard to the user when they send a message
    update.message.reply_text('Please select a command:', reply_markup=reply_markup)
    return ConversationHandler.END

# Define the function to handle the /request command
def request_photo_start(update, context):
    chat_id = update.effective_chat.id
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM users WHERE chat_id = ?', (chat_id,))
    result = cursor.fetchone()

    if result is not None:
        # If the user is registered, get the latest photo annotated
        cursor = conn.cursor()
        cursor.execute('SELECT last_photo_shown FROM last_shown WHERE user_id = ?', (result[0],))
        last_photo = cursor.fetchone()
        if last_photo is None:
            last_photo = -1
        else:
            last_photo = last_photo[0]
        #photo_index, photo_path, reference = get_photo_from_file(idx=last_photo+1)
        photo_index, photo_path, reference = get_photo_from_tfloader(user_id=result[0], idx=last_photo+1)
        if photo_path:
            # Send the photo to the user
            context.bot.send_photo(chat_id=chat_id, photo=open(photo_path, 'rb'))

            # Get 10 random labels
            labels = get_labels()

            # Create a custom keyboard with the labels as options
            keyboard = [[label] for label in labels]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

            # Store the photo index in the chat_data
            context.chat_data['photo_index'] = photo_index
            context.chat_data['reference'] = reference
            user_id = result[0]
            context.user_data['user_id'] = user_id
            context.user_data['status'] = 'annotate'

            # Transition to the GUESS state
            context.bot.send_message(chat_id=chat_id, 
                                     text=f"Image number {photo_index}. Guess the age:", 
                                     reply_markup=reply_markup)
            return GUESS
        else:
            context.bot.send_message(chat_id=chat_id, text="No photos available.")
    else:
        context.bot.send_message(chat_id=chat_id, text="You need to register first.")

    # Create a custom keyboard with the desired commands
    keyboard = [['/start', '/annotate', '/edit']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

    # Send the custom keyboard to the user when they send a message
    update.message.reply_text('Please select a command:', reply_markup=reply_markup)
    return ConversationHandler.END

def request_photo_guess(update, context):
    chat_id = update.effective_chat.id
    user_id = context.user_data['user_id']
    status = context.user_data['status']
    photo_index = context.chat_data['photo_index']
    reference = context.chat_data['reference']
    predicted_label = update.message.text

    # check if the prediction is valid
    all_labels = get_labels()
    if predicted_label in all_labels:
        # Save the user's prediction in the database
        save_prediction(photo_index, predicted_label, reference, user_id, status)

        context.bot.send_message(chat_id=chat_id, text="Your prediction has been saved.")

    else:
        context.bot.send_message(chat_id=chat_id, text="Your prediction is not valid! please check the class names.")

    # Create a custom keyboard with the desired commands
    keyboard = [['/start', '/annotate', '/edit']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

    # Send the custom keyboard to the user when they send a message
    update.message.reply_text('Please select a command:', reply_markup=reply_markup)
    return ConversationHandler.END

# Define the function to handle the /edit command
def request_photo_edit_start(update, context):
    chat_id = update.effective_chat.id
    context.bot.send_message(chat_id=chat_id, text="Please enter the photo ID:")
    return EDIT

def request_photo_edit_annotation(update, context):
    chat_id = update.effective_chat.id
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM users WHERE chat_id = ?', (chat_id,))
    result = cursor.fetchone()

    photo_id = update.message.text

    # Retrieve the photo with the given photo ID
    photo_index, photo_path, reference = get_photo_from_tfloader(user_id=result[0], idx=int(photo_id))

    if photo_path:
        # Send the photo to the user
        context.bot.send_photo(chat_id=chat_id, photo=open(photo_path, 'rb'))

        # Store the photo index and reference in the chat_data
        context.chat_data['photo_index'] = photo_index
        context.chat_data['reference'] = reference

        # Get 10 random labels
        labels = get_labels()

        # Create a custom keyboard with the labels as options
        keyboard = [[label] for label in labels]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

        # Store the photo index in the chat_data
        context.chat_data['photo_index'] = photo_index
        context.chat_data['reference'] = reference
        context.user_data['user_id'] = result[0]
        context.user_data['status'] = 'edit'

        # Transition to the GUESS state
        context.bot.send_message(chat_id=chat_id, 
                                 text=f"Image number {photo_index}. Please enter the new annotation:",
                                 reply_markup=reply_markup)
        return GUESS
    else:
        context.bot.send_message(chat_id=chat_id, text="Invalid photo ID.")

    # Create a custom keyboard with the desired commands
    keyboard = [['/start', '/annotate', '/edit']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

    # Send the custom keyboard to the user when they send a message
    update.message.reply_text('Please select a command:', reply_markup=reply_markup)

    return ConversationHandler.END


# Define the function to save the user's prediction in the database
def save_prediction(photo_index, predicted_label, reference, user_id, status):
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO predictions (user_id, predicted_label, photo_index, reference) VALUES (?, ?, ?, ?)",
                   (user_id, predicted_label, photo_index, str(reference)))
    
    if status == 'annotate':
        cursor.execute("INSERT OR REPLACE INTO last_shown (user_id, last_photo_shown) VALUES (?, ?)",
                    (user_id, photo_index))
    conn.commit()

def get_photo_from_file(idx=None):
    # Specify the directory where your photos are stored
    photos_directory = 'photos/'

    # Get a list of all photos in the directory
    photos = glob.glob(photos_directory + '*.jpg')

    if photos:
        if idx == None:
            # Select a random photo from the list
            idx = random.choice(range(len(photos)))
        elif idx == len(photos):
            idx = 0

        return idx, photos[idx], photos[idx]
    else:
        return None, None

def get_photo_from_tfloader(user_id, idx, seed=0):
    def load_dataset(dataset, split):
        # load a tensorflow dataset
        return tfds.load(dataset, split=split, shuffle_files=False, with_info=True)
    
    syn_train_ds, info_syn_train = load_dataset('DATASETNAME', 'train')


    # Get a list of random indices of the faces 
    random.seed(seed)
    rnd_indices = random.sample(range(syn_train_ds.cardinality().numpy()), syn_train_ds.cardinality().numpy()-1)
    if idx == len(rnd_indices):
        idx = 0

    # shuffle the list specific to each user
    random.seed(user_id)
    random.shuffle(rnd_indices)

    #get the image
    img = None
    syn_train_ds_iter = iter(syn_train_ds)
    for i in range(syn_train_ds.cardinality().numpy()):
        data = next(syn_train_ds_iter)
        if i == rnd_indices[idx]:
            img = data['image']
            reference = data['image_path']

    # save the retrieved photo temporarily to be used by the bot
    img = img.numpy()
    im = Image.fromarray(img)
    Path("photos/").mkdir(parents=True, exist_ok=True)
    PATH = f"photos/.{user_id}_current.jpeg"
    im.save(PATH)

    return idx, PATH, reference

def get_labels():
    # Define a list of example labels
    all_labels = ['16<=age<=22', '23<=age<=29', '30<=age<=36', '37<=age<=43', '44<=age<=50',
                   '51<=age<=57', '58<=age<=64', '65<=age<=71', '72<=age<=78', '79<=age<=85']

    # Get 10 random labels from the example labels list
    #random_labels = random.sample(example_labels, 10)
    return all_labels

def main():
    # Create an instance of the Updater class and pass in your bot token
    updater = Updater(token=TOKEN, use_context=True)

    # Get the dispatcher to register handlers
    dispatcher = updater.dispatcher


    # Register the /start command handler
    #start_handler = CommandHandler('start', start)
    #dispatcher.add_handler(start_handler)

    # Register the conversation handler for registration
    register_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            NAME: [MessageHandler(Filters.text & ~Filters.command, register_user)],
        },
        fallbacks=[],
    )
    dispatcher.add_handler(register_handler)

    # Register the conversation handler for request_photo
    request_photo_handler = ConversationHandler(
        entry_points=[CommandHandler('annotate', request_photo_start)],
        states={
            GUESS: [MessageHandler(Filters.text & ~Filters.command, request_photo_guess)],
        },
        fallbacks=[],
    )
    dispatcher.add_handler(request_photo_handler)

    # Register the /edit command handler
    edit_handler = ConversationHandler(
        entry_points=[CommandHandler('edit', request_photo_edit_start)],
        states={
            EDIT: [MessageHandler(Filters.text & ~Filters.command, request_photo_edit_annotation)],
            GUESS: [MessageHandler(Filters.text & ~Filters.command, request_photo_guess)],
        },
        fallbacks=[],
    )
    dispatcher.add_handler(edit_handler)

    # Create a custom keyboard with the desired commands
    keyboard = [['/start', '/annotate', '/edit']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

    # Send the custom keyboard to the user when they send a message
    message_handler = MessageHandler(Filters.text & (~Filters.command), lambda update, context: update.message.reply_text('Please select a command:', reply_markup=reply_markup))
    dispatcher.add_handler(message_handler)

    # Start the bot
    updater.start_polling()

    # Run the bot until you press Ctrl-C
    print ('the bot is running ...')
    print ('to stop press Ctrl-C')
    updater.idle()

if __name__ == '__main__':
    main()
