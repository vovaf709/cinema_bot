import aiohttp
from aiogram import Bot, types  # type: ignore
from aiogram.dispatcher import Dispatcher  # type: ignore
from aiogram.utils import executor  # type: ignore
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton  # type:ignore
from collections import defaultdict
import typing as tp
import json
import os


Film = tp.Dict[str, tp.Any]


# Phrases
GREETING = ("Привет!👋\nЯ могу помочь тебе найти информацию о фильмах и сериалах 🎬. "
            "Пиши мне их названия, а я расскажу тебе, все что знаю!\n\n"
            "Тут такое дело... Я еще совсем маленький и могу ошибаться, поэтому "
            "если я выдал тебе неправильный трейлер, отправь мне, пожалуйста, "
            "сообщение /wrong. Так я смогу осознать свои ошибки и больше их не повторять!")
NO_RESULT_HERE = 'Здесь нет нужного фильма/сериала'
NO_RESULT_RESPOND = 'Сожалею, что не смог помочь 😟'
NOT_FOUND = 'К сожалению я не нашел достаточно информации об этом фильме/сериале 😬'
WHICH = 'Наверное ты имеешь в виду один из этих фильмов/сериалов 🤨:'
FOUND_WITH_REPETITIONS = 'Есть несколько фильмов/сериалов с таким названием, уточни свой запрос 🧐'
THANKS = 'Спасибо, теперь я стал умнее 🤓'
WHAT = 'Не понимаю, в чем проблема 😕'
PLS_STOP = 'Хватит 😡'


# API-s
KINOPOISK_API = 'https://kinopoiskapiunofficial.tech/api/v2.1/films/'
TMDB_API = 'https://api.themoviedb.org/3/movie/'
YOUTUBE_API = 'https://www.googleapis.com/youtube/v3/search?part=snippet&maxResults='

youtube_api_key = os.environ['YOUTUBE_API_KEY']
tmdb_api_key = os.environ['TMDB_API_KEY']
headers = {'X-API-KEY': os.environ['X_API_KEY']}


# Global variables
bot = Bot(token=os.environ['BOT_TOKEN'])
dp = Dispatcher(bot)
films_global: tp.List[Film] = []  # Global variable for storing films in inline markup
delimiter = '_'  # Delimiter for callback data
max_num_buttons = 10  # Maximum number of buttons in the inline markup
description_max_length = 1000  # Maximum length of the film description
current_trailer_key = ''  # Pseudo-unique key of currently showed trailer
trailers: tp.Dict[str, int] = defaultdict(int)  # Dict {trailer_key: order of right trailer on youtube page or -1}
max_num_trailer = 10  # Maximum number of trailers considered by this bot
num_whats = 0
num_whats_to_get_evil = 3


@dp.message_handler(commands=['start', 'help'])
async def send_greeting(message: types.Message):
    """Sends greeting to user"""
    await bot.send_message(message.chat.id, GREETING)


@dp.message_handler(commands=['wrong'])
async def send_thanks(message: types.Message):
    """
    Considers trailer number trailers[current_trailer_key] invalid for this key and increasing
    trailers[current_trailer_key] to offer following trailer next time. If trailer number max_num_trailer is invalid
    it sets trailers[current_trailer_key] to -1 which means that bot will not try to show trailer for the film with
    corresponding trailer key. Also thanks the user and gets angry if user spams '/wrong' :}
    """
    global current_trailer_key
    global num_whats
    if current_trailer_key:
        trailers[current_trailer_key] += 1
        if trailers[current_trailer_key] == max_num_trailer:
            trailers[current_trailer_key] = -1
        current_trailer_key = ''
        await bot.send_message(message.chat.id, THANKS)
    else:
        if num_whats == num_whats_to_get_evil:
            await bot.send_message(message.chat.id, PLS_STOP)
        else:
            num_whats += 1
            await bot.send_message(message.chat.id, WHAT)


@dp.message_handler()
async def get_info(message: types.Message) -> types.Message:
    """
    Main function for handling user's message
    """
    print(message.text)
    global current_trailer_key
    global num_whats
    num_whats = 0
    current_trailer_key = ''
    if message.text == NO_RESULT_HERE:
        await bot.send_message(message.chat.id, NO_RESULT_RESPOND)
    else:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(KINOPOISK_API +
                                   'search-by-keyword?'
                                   'keyword=' + message.text + '&page=1') as resp:
                if resp.status != 200:
                    await bot.send_message(message.chat.id, NOT_FOUND)
                else:
                    body = json.loads(await resp.text())

                    # Heuristic: films with zero ratings are probably broken
                    body['films'] = [film for film in body['films'] if film.get('rating') not in ('0.0', None)]
                    if not body['films']:
                        await bot.send_message(message.chat.id, NOT_FOUND)
                    else:
                        film = find_film(body['films'], message.text)
                        if film:
                            if is_unique(body['films'], film['nameRu']):
                                # Show info if there is only one film with such name
                                await show_info(message.chat.id, film)
                            else:
                                # Show possible variants with same name
                                await message.reply(FOUND_WITH_REPETITIONS,
                                                    reply_markup=create_inline_markup(body['films'],
                                                                                      message.text,
                                                                                      message.chat.id))
                        else:
                            # Find films with similar names
                            await bot.send_message(message.chat.id,
                                                   WHICH,
                                                   reply_markup=create_reply_markup(body['films']))


@dp.callback_query_handler()
async def process_callback_from_inline_button(callback_query: types.CallbackQuery) -> types.Message:
    """
    Callback handler for inline keyboard buttons
    :param callback_query: callback from particular inline keyboard button
    :return: message
    """
    global films_global
    await bot.answer_callback_query(callback_query.id)
    if delimiter not in callback_query.data:
        await bot.send_message(int(callback_query.data), NO_RESULT_RESPOND)
    else:
        id, film_index = callback_query.data.split(delimiter)
        await show_info(int(id), films_global[int(film_index)])
    films_global = []


async def get_imdb_id(film: Film) -> tp.Optional[str]:
    """
    Gets IMDB id of the film passed
    :param film: film whose id needs to find
    :return: IMDB id as a string or None if id was not found
    """
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(KINOPOISK_API + str(film['filmId'])) as resp:
            if resp.status == 200:
                return json.loads(await resp.text())['externalId']['imdbId']
    return None


async def get_view_url(film: Film) -> tp.Optional[str]:
    """
    Gets view url of the film passed from TMDB(https://www.themoviedb.org/?language=ru)
    :param film: film whose view url needs to find
    :return: view url or None if there is none
    """
    imdb_id = await get_imdb_id(film)
    if imdb_id:
        tmdb_request = f'{TMDB_API}{imdb_id}/watch/providers?api_key={tmdb_api_key}'
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(tmdb_request) as resp:
                if resp.status == 200:
                    results = json.loads(await resp.text())['results']
                    if results.get('RU'):
                        return results['RU'].get('link')
    return None


async def get_trailer_url(film: Film) -> tp.Optional[str]:
    """
    Tries to find url of the film trailer from the first youtube search page
    :param film: film whose trailer url needs to find
    :return: trailer url or None if bot don't want to search it(trailers[current_trailer_key] == -1)
    """
    global current_trailer_key
    if trailers[current_trailer_key] != -1:
        current_trailer_search_key = (film['nameRu'] +
                                      ' ' +
                                      str(film.get('year')) * (film.get('year') is not None))

        current_trailer_key = current_trailer_search_key + str(film.get('rating')) * (film.get('rating') is not None)

        youtube_request = (YOUTUBE_API +
                           f'{trailers[current_trailer_key] + 1}&q=' +
                           current_trailer_search_key +
                           ' трейлер' +
                           '&type=video&key=' +
                           youtube_api_key)

        async with aiohttp.ClientSession() as session:
            async with session.get(youtube_request) as resp:
                print(f'YouTube response is {resp.status}')
                trailer_url = 'https://www.youtube.com/watch?v=' + \
                              json.loads(await resp.text())['items'][trailers[current_trailer_key]]['id']['videoId'] \
                    if resp.status == 200 else None

        if not trailers[current_trailer_key]:
            del trailers[current_trailer_key]

        return trailer_url
    return None


async def show_info(id: int, film: Film) -> types.Message:
    """
    Sends film information into chat
    :param id: chat id
    :param film: film information dictionary structured according to 'Film" schema
    described at https://kinopoiskapiunofficial.tech/documentation/api/
    :return: message
    """
    trailer_url = await get_trailer_url(film)
    view_url = await get_view_url(film)

    caption = f'{film["nameRu"]}\n\n'

    if film.get('rating'):
        caption += f'Рейтинг: {film["rating"]}\n'
    if film.get('genres'):
        caption += f'Жанр: {", ".join(genre["genre"].capitalize() for genre in film["genres"])}\n'
    if film.get('filmLength'):
        caption += f'Длительность: {film["filmLength"]}\n'
    if film.get('year'):
        caption += f'Год производства: {film["year"]}\n'
    if film.get('countries'):
        caption += f'Страна: {", ".join(country["country"] for country in film["countries"])}\n\n'
    if film.get('description'):
        caption += f'Краткое описание:\n {film.get("description")}\n\n'
    if view_url:
        caption += f'Ссылка для просмотра: {view_url}'

    if film.get('posterUrlPreview'):
        async with aiohttp.ClientSession() as session:
            async with session.get(film['posterUrlPreview']) as resp:
                resp_bytes = await resp.read()
                # GIF in respond means that we obtained broken poster
                if resp_bytes.find(b'GIF') != -1:
                    await bot.send_message(id, text=caption)
                else:
                    await bot.send_photo(id,
                                         film['posterUrlPreview'],
                                         caption=caption[:description_max_length] + '...' * (len(caption) >
                                                                                             description_max_length))
    if trailer_url:
        await bot.send_message(id, text='Трейлер: ' + trailer_url)


def create_inline_markup(films: tp.List[Film], film_name: str, id: int) -> InlineKeyboardMarkup:
    """
    Creates markup of specified(in global variable max_button_size) size with same specified film name
    and additional information of the films from the list passed
    :param films: list if films
    :param film_name: film name
    :param id: chat id
    :return: inline keyboard markup with num_buttons buttons labeled by specified film name and additional information
    """
    global films_global
    num_buttons: int = 0
    markup: InlineKeyboardMarkup = InlineKeyboardMarkup()
    for film in films:
        if film['nameRu'].strip().lower() == film_name.strip().lower():
            films_global.append(film)
            text = film_name + '; '
            if film.get('genres'):
                text += ", ".join(genre["genre"].capitalize()
                                  for genre in film["genres"]) + '; '
            if film.get('year'):
                text += film["year"]
            markup.add(InlineKeyboardButton(text=text,
                                            callback_data=(str(id) +
                                                           delimiter +
                                                           str(num_buttons))))
            num_buttons += 1
            if num_buttons == max_num_buttons:
                break
    markup.add(InlineKeyboardButton(text=NO_RESULT_HERE, callback_data=str(id)))

    return markup


def create_reply_markup(films: tp.List[Film]) -> ReplyKeyboardMarkup:
    """
    Creates markup with unique russian names of the films from the list passed
    :param films: list if films
    :return: reply keyboard markup with buttons labeled by film names
    """
    unique_films: tp.List[str] = []
    markup: ReplyKeyboardMarkup = ReplyKeyboardMarkup(one_time_keyboard=True)
    for film in films:
        if film['nameRu'].strip().lower() not in unique_films:
            markup = markup.add(KeyboardButton(film['nameRu']))
            unique_films.append(film['nameRu'].strip().lower())
    markup.add(KeyboardButton(NO_RESULT_HERE))

    return markup


def find_film(films: tp.List[Film], film_name: str) -> tp.Optional[Film]:
    """
    Finds out if there is a film with such russian name in the passed list of films
    :param films: list of films
    :param film_name: name of the film to find
    :return: first film with such name or None if there is no such film
    """
    for film in films:
        if film['nameRu'].strip().lower() == film_name.strip().lower():
            return film
    return None


def is_unique(films: tp.List[Film], film_name: str) -> bool:
    """
    Finds out if specified film has unique russian name in passed list of films
    :param films: list of films
    :param film_name: name of the film to analyze
    :return: False if there are 2 or more films with such name, True otherwise
    """
    counter = 0
    for film in films:
        counter += film['nameRu'] == film_name

    return counter < 2


if __name__ == '__main__':
    executor.start_polling(dp)
