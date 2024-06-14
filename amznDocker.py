# telegram-bot-amazon - Python version
# Gioxx, 2024, https://github.com/gioxx/telegram-bot-amazon-python
# In all ways inspired by the original work of LucaTNT (https://github.com/LucaTNT/telegram-bot-amazon), converted to Python, updated to work with newer versions of the software.
# Credits: all this would not have been possible (in the same times) without the invaluable help of Claude 3 Sonnet.

import os
import re
import asyncio
import sys
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode

import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

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

# Regex patterns
FULL_URL_REGEX = re.compile(r'https?://(([^\s]*)\.)?amazon\.([a-z.]{2,5})(\/d\/([^\s]*)|\/([^\s]*)\/?(?:dp|o|gp|-)\/)(aw\/d\/|product\/)?(B[0-9A-Z]{9})([^\s]*)', re.IGNORECASE)
SHORT_URL_REGEX = re.compile(r'https?://(([^\s]*)\.)?(amzn\.to|amzn\.eu)(/d)?/([0-9A-Za-z]+)', re.IGNORECASE)
URL_REGEX = re.compile(r'https?:\/\/(www\.)?[-a-zA-Z0-9@:%._+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_+.~#?&//=]*)', re.IGNORECASE)
RAW_URL_REGEX = re.compile(f'https?://(([^\\s]*)\\.)?amazon\\.{AMAZON_TLD}/?([^\\s]*)', re.IGNORECASE)

# Users to ignore
USERNAMES_TO_IGNORE = [username.lower() for username in os.environ.get('IGNORE_USERS', '').split(',') if username.startswith('@')]
USER_IDS_TO_IGNORE = [int(user_id) for user_id in os.environ.get('IGNORE_USERS', '').split(',') if user_id.isdigit()]

async def shorten_url(url):
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
    return f'https://www.amazon.{AMAZON_TLD}/dp/{asin}?tag={AMAZON_TAG}'

def build_raw_amazon_url(element):
    url = element.get('expanded_url') or element['full_url']
    parsed_url = urlparse(url)
    query = parse_qs(parsed_url.query)
    query['tag'] = [AMAZON_TAG]
    new_query = urlencode(query, doseq=True)
    return parsed_url._replace(query=new_query).geturl()

async def get_amazon_url(element):
    url = build_amazon_url(element['asin']) if element.get('asin') else build_raw_amazon_url(element)
    return await shorten_url(url) if SHORTEN_LINKS else url

def log(msg):
    date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{date} {msg}", flush=True)
    sys.stdout.flush()

async def get_long_url(short_url, chain_depth=0):
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
    match = FULL_URL_REGEX.search(url)
    return match.group(8) if match else url

def build_mention(user):
    return f"@{user.username}" if user.username else f"{user.first_name} {user.last_name or ''}"

def is_group(chat):
    return chat.type in ['group', 'supergroup']

async def build_message(chat, message, replacements, user):
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
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()

if __name__ == '__main__':
    main()