# telegram-bot-amazon - Python version
# Gioxx, 2024, https://github.com/gioxx/telegram-bot-amazon-python
# In all ways inspired by the original work of LucaTNT (https://github.com/LucaTNT/telegram-bot-amazon), converted to Python, updated to work with newer versions of the software.
# Credits: all this would not have been possible (in the same times) without the invaluable help of Claude 3.5 Sonnet.

from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from urllib.parse import urlparse, parse_qs, urlencode
import aiohttp
import asyncio
import os
import random
import re
import sys

# Configuration
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
AMAZON_TAG = os.environ.get('AMAZON_TAG')
CHANNEL_NAME = os.environ.get('CHANNEL_NAME')
SHORTEN_LINKS = os.environ.get('SHORTEN_LINKS', 'false').lower() == 'true'
BITLY_TOKEN = os.environ.get('BITLY_TOKEN')
RAW_LINKS = os.environ.get('RAW_LINKS', 'false').lower() == 'true'
CHECK_FOR_REDIRECTS = os.environ.get('CHECK_FOR_REDIRECTS', 'false').lower() == 'true'
CHECK_FOR_REDIRECT_CHAINS = os.environ.get('CHECK_FOR_REDIRECT_CHAINS', 'false').lower() == 'true'
MAX_REDIRECT_CHAIN_DEPTH = int(os.environ.get('MAX_REDIRECT_CHAIN_DEPTH', 2))
GROUP_REPLACEMENT_MESSAGE = os.environ.get('GROUP_REPLACEMENT_MESSAGE', 'Message by {USER} with Amazon affiliate link:\n\n{MESSAGE}')
AMAZON_TLD = os.environ.get('AMAZON_TLD', 'com')
SUPPORT_DEV = os.environ.get('SUPPORT_DEV', 'true').lower() == 'true'

# Regex patterns
FULL_URL_REGEX = re.compile(r'https?://(([^\s]*)\.)?amazon\.([a-z.]{2,5})(\/d\/([^\s]*)|\/([^\s]*)\/?(?:dp|o|gp|-)\/)(aw\/d\/|product\/)?(B[0-9A-Z]{9})([^\s]*)', re.IGNORECASE)
SHORT_URL_REGEX = re.compile(r'https?://(([^\s]*)\.)?(amzn\.to|amzn\.eu)(/d)?/([0-9A-Za-z]+)', re.IGNORECASE)
URL_REGEX = re.compile(r'https?:\/\/(www\.)?[-a-zA-Z0-9@:%._+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_+.~#?&//=]*)', re.IGNORECASE)
RAW_URL_REGEX = re.compile(f'https?://(([^\\s]*)\\.)?amazon\\.{AMAZON_TLD}/?([^\\s]*)', re.IGNORECASE)

# Users to ignore
USERNAMES_TO_IGNORE = [username.lower() for username in os.environ.get('IGNORE_USERS', '').split(',') if username.startswith('@')]
USER_IDS_TO_IGNORE = [int(user_id) for user_id in os.environ.get('IGNORE_USERS', '').split(',') if user_id.isdigit()]

# Check
CODE_VERSION = '1.1.0'

def get_amazon_tag(original_tag):
    """
    Returns either the original tag or a different one based on random probability,
    only if SUPPORT_DEV is True
    
    Args:
        original_tag (str): The original Amazon affiliate tag from env vars
        
    Returns:
        str: Either the original tag or an alternate one
    """
    # If SUPPORT_DEV is False, always return the original tag
    if not SUPPORT_DEV:
        return original_tag
        
    # Probability of using an alternate tag
    if random.random() < 0.35:  # 35% probability
        # List of alternate tags to use
        alternate_tags = [
            "gioxx-21"
        ]
        selected_tag = random.choice([tag for tag in alternate_tags if tag != original_tag])
        log(f"Using alternate affiliate tag: {selected_tag}")
        return selected_tag
    
    return original_tag

async def shorten_url(url):
    """Shorten a URL using the bit.ly API."""
    
    headers = {
        'Authorization': f'Bearer {BITLY_TOKEN}',
        'Content-Type': 'application/json',
    }
    body = {'long_url': url, 'domain': 'bit.ly'}
    async with aiohttp.ClientSession() as session:
        async with session.post('https://api-ssl.bitly.com/v4/shorten', headers=headers, json=body) as response:
            result = await response.json()
            if 'link' in result:
                return result['link']
            else:
                log(f"Error in bitly response {result}")
                return url

def build_amazon_url(asin):    
    """
    Constructs an Amazon product URL using the provided ASIN and an affiliate tag.

    Args:
        asin (str): The Amazon Standard Identification Number for the product.

    Returns:
        str: A formatted URL string pointing to the Amazon product page with the affiliate tag appended.
    """
    selected_tag = get_amazon_tag(AMAZON_TAG)
    return f'https://www.amazon.{AMAZON_TLD}/dp/{asin}?tag={selected_tag}'

def build_raw_amazon_url(element):
    """
    Constructs an Amazon product URL using the provided element and an affiliate tag.

    Args:
        element (dict): A dictionary containing the 'full_url' and optionally 'expanded_url' and 'asin' keys.

    Returns:
        str: A formatted URL string pointing to the Amazon product page with the affiliate tag appended.
    """
    url = element.get('expanded_url') or element['full_url']
    parsed_url = urlparse(url)
    query = parse_qs(parsed_url.query)
    selected_tag = get_amazon_tag(AMAZON_TAG)
    query['tag'] = [selected_tag]
    new_query = urlencode(query, doseq=True)
    return parsed_url._replace(query=new_query).geturl()

async def get_amazon_url(element):
    """
    Asynchronously constructs a complete Amazon URL for a given element and shortens it if necessary.

    Args:
        element (dict): A dictionary containing keys 'asin' and/or 'full_url' to determine the Amazon URL type.

    Returns:
        str: A complete Amazon product URL, optionally shortened if SHORTEN_LINKS is enabled.
    """
    url = build_amazon_url(element['asin']) if element.get('asin') else build_raw_amazon_url(element)
    return await shorten_url(url) if SHORTEN_LINKS else url

def log(msg):
    """
    Logs a message to the standard output, prepending the current date and time.

    Args:
        msg (str): The message to be logged
    """
    date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{date} {msg}", flush=True)
    sys.stdout.flush()

async def get_long_url(short_url, chain_depth=0):
    """
    Asynchronously resolves a shortened URL to its full URL.

    Args:
        short_url (str): The shortened URL to be resolved.
        chain_depth (int, optional): The current depth of the redirect chain. Defaults to 0.

    Returns:
        dict: A dictionary containing the full URL and the original short URL.
    """
    try:
        chain_depth += 1
        async with aiohttp.ClientSession() as session:
            async with session.get(short_url, allow_redirects=False) as response:
                if response.status in [301, 302, 303, 307, 308]:
                    full_url = response.headers.get('location')
                    if CHECK_FOR_REDIRECT_CHAINS and chain_depth < MAX_REDIRECT_CHAIN_DEPTH:
                        next_redirect = await get_long_url(full_url, chain_depth)
                        return {'full_url': next_redirect['full_url'], 'short_url': short_url}
                    else:
                        return {'full_url': full_url, 'short_url': short_url}
                else:
                    # If there is no redirection, consider the original URL as the full URL
                    return {'full_url': short_url, 'short_url': short_url}
    except Exception as e:
        log(f"Short URL {short_url} -> ERROR: {e}")
        return None

def get_asin_from_full_url(url):
    """
    Extracts the ASIN from a full Amazon URL.

    Args:
        url (str): The full Amazon URL to be parsed.

    Returns:
        str: The ASIN or the original URL if no ASIN could be extracted.
    """
    match = FULL_URL_REGEX.search(url)
    return match.group(8) if match else url

def build_mention(user):
    """
    Builds a mention string for a Telegram user.

    Args:
        user (telegram.User): The user to be mentioned.

    Returns:
        str: A string that can be used to mention the user in a message.
    """
    return f"@{user.username}" if user.username else f"{user.first_name} {user.last_name or ''}"

def is_group(chat):
    """Checks if a chat is a group or a supergroup.

    Args:
        chat (telegram.Chat): The chat to be checked.

    Returns:
        bool: True if the chat is a group or supergroup, False otherwise.
    """
    return chat.type in ['group', 'supergroup']

async def build_message(chat, message, replacements, user):
    """
    Builds a message string for a Telegram message based on the provided chat type and information.

    Args:
        chat (telegram.Chat): The chat to be checked.
        message (str): The original message containing affiliate links.
        replacements (list): A list of dictionaries containing the 'full_url' and optionally 'expanded_url' and 'asin' keys.
        user (telegram.User): The user that posted the message.

    Returns:
        str: A formatted message string to be sent as a reply to the original message.
    """
    if is_group(chat):
        affiliate_message = message
        for element in replacements:
            sponsored_url = await get_amazon_url(element)
            affiliate_message = affiliate_message.replace(element['full_url'], sponsored_url)

        return GROUP_REPLACEMENT_MESSAGE.replace('\\n', '\n').replace('{USER}', build_mention(user)).replace('{MESSAGE}', affiliate_message).replace('{ORIGINAL_MESSAGE}', message)
    else:
        if len(replacements) > 1:
            text = '\n'.join(f"• {await get_amazon_url(element)}" for element in replacements)
        else:
            text = await get_amazon_url(replacements[0])
        return text

async def delete_and_send(update: Update, context, text):
    """
    Deletes the original message and sends a new one with the affiliated link.

    Args:
        update (Update): The update that triggered the message.
        context (CallbackContext): The context of the callback.
        text (str): The text of the new message to be sent.

    Returns:
        bool: True if the message was deleted, False otherwise.
    """
    chat = update.message.chat
    message_id = update.message.message_id
    chat_id = chat.id
    deleted = False

    if is_group(chat):
        await context.bot.delete_message(chat_id, message_id)
        deleted = True

    reply_to_message_id = update.message.reply_to_message.message_id if update.message.reply_to_message else None

    if update.message.caption and is_group(chat):
        await context.bot.send_photo(chat_id, update.message.photo[-1].file_id, caption=text, reply_to_message_id=reply_to_message_id)
        if CHANNEL_NAME:
            await context.bot.send_photo(CHANNEL_NAME, update.message.photo[-1].file_id, caption=text, reply_to_message_id=reply_to_message_id)
    else:
        await context.bot.send_message(chat_id, text, reply_to_message_id=reply_to_message_id)
        if CHANNEL_NAME:
            await context.bot.send_message(CHANNEL_NAME, text, reply_to_message_id=reply_to_message_id)

    return deleted

def replace_text_links(message):
    """
    Replaces Telegram text links with the actual URLs.

    Args:
        message (telegram.Message): The message to be processed.

    Returns:
        str: The text of the message with URLs replaced.
    """
    if message.entities:
        text = message.text
        offset_shift = 0
        for entity in message.entities:
            if entity.type == 'text_link':
                offset = entity.offset + offset_shift
                length = entity.length
                new_text = text[:offset] + entity.url + text[offset + length:]
                offset_shift += len(entity.url) - length
                text = new_text
        return text
    return message.text

async def handle_message(update: Update, context):
    """
    Handles a message update by replacing Amazon links with affiliated links.

    If the message is in a group and the user is not in the IGNORE_USERS list, it will delete the original message and send a new one with the affiliated links.

    Args:
        update (Update): The Telegram update containing the message.
        context (CallbackContext): The context of the callback.

    Returns:
        None
    """
    try:
        msg = update.message
        from_username = msg.from_user.username.lower() if msg.from_user.username else ""
        from_id = msg.from_user.id

        if (from_username not in USERNAMES_TO_IGNORE and from_id not in USER_IDS_TO_IGNORE) or not is_group(msg.chat):
            text = replace_text_links(msg)
            text = text or msg.caption
            caption_saved_as_text = text == msg.caption

            if CHECK_FOR_REDIRECTS:
                long_url_replacements = []
                for match in URL_REGEX.finditer(text):
                    if not SHORT_URL_REGEX.match(match.group()) and not RAW_URL_REGEX.match(match.group()):
                        log(f"Found non-Amazon URL {match.group()}")
                        long_url = await get_long_url(match.group())
                        long_url_replacements.append(long_url)

                for element in long_url_replacements:
                    if element and element['full_url']:
                        text = text.replace(element['short_url'], element['full_url'])

            replacements = []
            if RAW_LINKS:
                for match in RAW_URL_REGEX.finditer(text):
                    replacements.append({'asin': None, 'full_url': match.group()})
            else:
                for match in FULL_URL_REGEX.finditer(text):
                    replacements.append({'asin': match.group(8), 'full_url': match.group()})

            for match in SHORT_URL_REGEX.finditer(text):
                short_url = match.group()
                url = await get_long_url(short_url)
                if url:
                    if RAW_LINKS:
                        replacements.append({'asin': None, 'expanded_url': url['full_url'], 'full_url': short_url})
                    else:
                        asin = get_asin_from_full_url(url['full_url'])
                        if not asin or asin == url['full_url']:
                            # If I cannot extract the ASIN, I'll try to get it from the short URL
                            asin = match.group(5)  # Group 5 should contain the identifier after /d/
                        replacements.append({'asin': asin, 'full_url': short_url})

            if replacements:
                text = await build_message(msg.chat, text, replacements, msg.from_user)
                deleted = await delete_and_send(update, context, text)

                if len(replacements) > 1:
                    for element in replacements:
                        log(f"Long URL {element['full_url']} -> ASIN {element['asin']} from {build_mention(msg.from_user)}{' (original message deleted)' if deleted else ''}")
                else:
                    log(f"Long URL {replacements[0]['full_url']} -> ASIN {replacements[0]['asin']} from {build_mention(msg.from_user)}{' (original message deleted)' if deleted else ''}")
        else:
            log(f"Ignored message from {build_mention(msg.from_user)} because it is included in the IGNORE_USERS env variable")
    except Exception as e:
        log("ERROR, please file a bug report at https://github.com/gioxx/telegram-bot-amazon-python")
        print(e)

def main():
    """
    Entry point of the script.

    It creates a Telegram bot instance using the token provided in the TELEGRAM_BOT_TOKEN environment variable,
    adds a message handler that calls handle_message() for all non-command text messages, and
    starts the bot in polling mode.
    """
    log(f"Starting bot version {CODE_VERSION} ...")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()

if __name__ == '__main__':
    main()